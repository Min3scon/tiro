using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;

namespace TiroLauncher
{
    // state.json, shared with the app (tiro/update/state.py) and the installer. Keep the three in step.
    [DataContract]
    public sealed class Trial
    {
        [DataMember(Name = "version")] public string Version;
        [DataMember(Name = "attempts")] public int Attempts;
        [DataMember(Name = "token")] public string Token;
    }

    [DataContract]
    public sealed class State
    {
        [DataMember(Name = "format")] public int Format = 1;
        [DataMember(Name = "current")] public string Current;
        [DataMember(Name = "previous")] public string Previous;
        [DataMember(Name = "pending")] public string Pending;
        [DataMember(Name = "trial")] public Trial Trial;
        [DataMember(Name = "bad")] public List<string> Bad = new List<string>();
        [DataMember(Name = "unconfirmed")] public int Unconfirmed;  // normal starts the app hasn't confirmed yet
    }

    /// <summary>
    /// The Tiro.exe that shortcuts, the Run key and Apps &amp; features point at. It starts the version state.json
    /// names. A newly downloaded version is started on trial: it must report that it came up properly (tray shown,
    /// speech model loaded and tested) or, after two failed starts, Tiro goes back to the previous version and never
    /// tries that one again. After two crashed starts of any version, Tiro starts in safe mode.
    /// </summary>
    static class Program
    {
        const int TrialTimeoutSec = 300;
        static readonly string[] GuiArgs = { "--autostart", "--after-restart", "--settings", "--after-update", "--safe-mode" };

        static string Root => AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        static string StatePath => Path.Combine(Root, "state.json");
        static string UpdateDir => Path.Combine(Root, ".update");
        static string VersionDir(string v) => Path.Combine(Root, "app-" + v);
        static string VersionExe(string v) => Path.Combine(VersionDir(v), "Tiro.exe");
        static string OkMarker(string v) => Path.Combine(UpdateDir, "started-" + v);
        static string TrialMarker(string token) => Path.Combine(UpdateDir, "trial-ok-" + token);

        [DllImport("user32.dll")] static extern short GetAsyncKeyState(int vk);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int MessageBoxW(IntPtr h, string text, string caption, uint type);

        [STAThread]
        static int Main(string[] args)
        {
            try
            {
                return Run(args);
            }
            catch (Exception e)
            {
                Log("launcher failed: " + e);
                return 1;
            }
        }

        static int Run(string[] args)
        {
            Directory.CreateDirectory(UpdateDir);
            var state = Load() ?? Recover();
            if (state == null || !File.Exists(VersionExe(state.Current)))
            {
                state = Recover();
                if (state == null)
                {
                    MessageBoxW(IntPtr.Zero, "Tiro's program files are missing. Run TiroSetup.exe to repair Tiro.", "Tiro", 0x10);
                    return 2;
                }
                Save(state);
            }
            var lower = args.Select(a => a.ToLowerInvariant()).ToList();
            if (!lower.All(a => GuiArgs.Contains(a)))
                return RunAndWait(state.Current, args);  // a command (--quit, --set, --selftest...): pass it through

            if (lower.Contains("--after-restart"))
            {
                // "Restart to update": the old copy is quitting; give it time instead of handing over to it
                var until = DateTime.UtcNow.AddSeconds(15);
                while (AlreadyRunning() && DateTime.UtcNow < until) System.Threading.Thread.Sleep(200);
            }
            if (AlreadyRunning())
            {
                Start(state.Current, args);  // it hands over to the running copy (shows Settings) and exits
                return 0;
            }
            var extra = new List<string>();
            bool interactive = !lower.Contains("--autostart") && !lower.Contains("--after-restart");
            if (lower.Contains("--safe-mode") || (interactive && (GetAsyncKeyState(0x10) & 0x8000) != 0))
                extra.Add("--safe-mode");  // hold Shift while starting Tiro for safe mode
            var plain = args.Where(a => a.ToLowerInvariant() != "--safe-mode").ToArray();

            if (File.Exists(OkMarker(state.Current))) state.Unconfirmed = 0;
            if (state.Pending != null && state.Pending != state.Current && !state.Bad.Contains(state.Pending)
                && File.Exists(VersionExe(state.Pending)))
                return TryNew(state, plain, extra);

            if (state.Unconfirmed >= 2 && !extra.Contains("--safe-mode"))
            {
                Log($"{state.Current} did not start properly twice: safe mode");
                extra.Add("--safe-mode");
            }
            state.Unconfirmed++;
            TryDelete(OkMarker(state.Current));
            Save(state);
            Start(state.Current, plain.Concat(extra));
            return 0;
        }

        /// <summary>Start a new version on trial; keep it if it reports in, otherwise go back.</summary>
        static int TryNew(State state, string[] args, List<string> extra)
        {
            var v = state.Pending;
            var trial = state.Trial != null && state.Trial.Version == v ? state.Trial : new Trial { Version = v };
            trial.Attempts++;
            trial.Token = Guid.NewGuid().ToString("N");
            state.Trial = trial;
            Save(state);
            var marker = TrialMarker(trial.Token);
            TryDelete(marker);
            Log($"trying {v} (attempt {trial.Attempts})");
            var p = Start(v, args.Concat(extra).Concat(new[] { "--update-trial", trial.Token }));
            var deadline = DateTime.UtcNow.AddSeconds(TrialTimeoutSec);
            while (DateTime.UtcNow < deadline)
            {
                if (File.Exists(marker))
                {
                    Commit(v);
                    TryDelete(marker);
                    return 0;
                }
                if (!p.WaitForExit(250)) continue;
                if (File.Exists(marker))
                {
                    Commit(v);
                    TryDelete(marker);
                    return 0;
                }
                var code = p.ExitCode;
                var s = Load() ?? state;
                if (code == 0)
                {
                    // e.g. it found another Tiro running and handed over: not a failure
                    if (s.Trial != null) s.Trial.Attempts = Math.Max(0, s.Trial.Attempts - 1);
                    Save(s);
                    return 0;
                }
                Log($"{v} exited with code {code} before it was ready");
                if (trial.Attempts >= 2)
                {
                    RollBack(s, v, args, extra);
                    return 0;
                }
                return TryNew(s, args, extra);
            }
            Log($"{v} is still starting after {TrialTimeoutSec} s; the next start decides");
            return 0;
        }

        static void Commit(string v)
        {
            var s = Load();
            if (s == null) return;
            if (s.Current != v) s.Previous = s.Current;
            s.Current = v;
            s.Pending = null;
            s.Trial = null;
            s.Unconfirmed = 0;
            s.Bad.Remove(v);
            Save(s);
            Log($"now running {v} (previous {s.Previous})");
        }

        static void RollBack(State s, string v, string[] args, List<string> extra)
        {
            if (!s.Bad.Contains(v)) s.Bad.Add(v);
            s.Pending = null;
            s.Trial = null;
            Save(s);
            Log($"{v} failed to start twice: going back to {s.Current}");
            Start(s.Current, args.Concat(extra).Concat(new[] { "--rolled-back-from", v }));
        }

        static bool AlreadyRunning()
        {
            var prefix = Root + "\\app-";
            foreach (var p in Process.GetProcessesByName("Tiro"))
            {
                try
                {
                    if (p.Id != Process.GetCurrentProcess().Id && p.MainModule.FileName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        return true;
                }
                catch { }  // access denied (other user / elevated): ignore
            }
            return false;
        }

        static Process Start(string v, IEnumerable<string> args)
        {
            var psi = new ProcessStartInfo(VersionExe(v), Join(args)) { UseShellExecute = false, WorkingDirectory = VersionDir(v) };
            psi.EnvironmentVariables["TIRO_LAUNCHER"] = Path.Combine(Root, "Tiro.exe");
            return Process.Start(psi);
        }

        static int RunAndWait(string v, string[] args)
        {
            using (var p = Start(v, args))
            {
                p.WaitForExit();
                return p.ExitCode;
            }
        }

        static string Join(IEnumerable<string> args) => string.Join(" ", args.Select(Quote));

        static string Quote(string a)
        {
            if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
            var sb = new StringBuilder("\"");
            int slashes = 0;
            foreach (var c in a)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') sb.Append('\\', slashes * 2 + 1).Append('"');
                else sb.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            sb.Append('\\', slashes * 2).Append('"');
            return sb.ToString();
        }

        // ------------------------------------------------------------------------------------------ state file
        static State Load()
        {
            try
            {
                using (var f = File.OpenRead(StatePath))
                {
                    var s = (State)new DataContractJsonSerializer(typeof(State)).ReadObject(f);
                    if (s == null || string.IsNullOrEmpty(s.Current)) return null;
                    if (s.Bad == null) s.Bad = new List<string>();
                    return s;
                }
            }
            catch
            {
                return null;
            }
        }

        static void Save(State s)
        {
            var tmp = StatePath + ".tmp";
            using (var f = File.Create(tmp))
                new DataContractJsonSerializer(typeof(State)).WriteObject(f, s);
            if (File.Exists(StatePath)) File.Replace(tmp, StatePath, null);
            else File.Move(tmp, StatePath);
        }

        /// <summary>No usable state.json: run the newest complete version on disk.</summary>
        static State Recover()
        {
            var best = Directory.Exists(Root)
                ? Directory.GetDirectories(Root, "app-*")
                    .Select(d => Path.GetFileName(d).Substring(4))
                    .Where(v => !v.EndsWith(".partial") && File.Exists(VersionExe(v)))
                    .OrderByDescending(VersionKey).FirstOrDefault()
                : null;
            if (best == null) return null;
            Log($"state.json missing or unreadable: using {best}");
            return new State { Current = best };
        }

        static Tuple<int, int, int, int, int> VersionKey(string v)
        {
            int Part(string[] p, int i) => i < p.Length && int.TryParse(p[i], out var n) ? n : 0;
            var main = v.Split('-')[0].Split('.');
            var pre = v.Contains("-") ? v.Substring(v.IndexOf('-') + 1).Split('.') : new string[0];
            int rank = pre.Length == 0 ? 3 : pre[0] == "rc" ? 2 : pre[0] == "beta" ? 1 : 0;
            return Tuple.Create(Part(main, 0), Part(main, 1), Part(main, 2), rank, Part(pre, 1));
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch { }
        }

        static void Log(string line)
        {
            try
            {
                var path = Path.Combine(UpdateDir, "launcher.log");
                if (File.Exists(path) && new FileInfo(path).Length > 200_000) File.Delete(path);
                File.AppendAllText(path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {line}{Environment.NewLine}");
            }
            catch { }
        }
    }
}
