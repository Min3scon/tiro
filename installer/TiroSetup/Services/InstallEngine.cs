using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Threading;
using System.Threading.Tasks;

namespace TiroSetup.Services
{
    public sealed class InstallOptions
    {
        public bool UseGpu;
        public string ModelKey = "parakeet-tdt-0.6b-v2";
        public string InstallDir = Shell.DefaultInstallDir;
        public bool StartWithWindows = true;
        public bool DesktopShortcut;
        public bool NoShell;  // tests only: no shortcuts, no Apps & features entry, no autostart
    }

    public enum StepState { Pending, Active, Done, Skipped, Failed }

    public sealed class InstallStep
    {
        public string Title;
        public string Detail;
        public long Bytes;  // download size (0 for local steps)
        public StepState State;
    }

    public sealed class InstallProgress
    {
        public double Fraction;     // overall 0..1
        public string Status = "";  // what is happening right now
        public string Speed = "";   // "42 MB/s · about 1 min left"
    }

    [DataContract]
    public sealed class SelfTestResult
    {
        [DataMember(Name = "ok")] public bool Ok;
        [DataMember(Name = "device")] public string Device;
        [DataMember(Name = "device_label")] public string DeviceLabel;
        [DataMember(Name = "variant")] public string Variant;
        [DataMember(Name = "decode_ms")] public double DecodeMs;
        [DataMember(Name = "audio_s")] public double AudioSeconds;
        [DataMember(Name = "load_s")] public double LoadSeconds;
        [DataMember(Name = "text")] public string Text;
        [DataMember(Name = "error")] public string Error;
        [DataMember(Name = "fallback_reason")] public string FallbackReason;
    }

    /// <summary>Downloads, installs and verifies Tiro. Reports progress for the UI (or the silent log).</summary>
    public sealed class InstallEngine
    {
        readonly Manifest manifest;
        readonly InstallOptions opt;
        readonly string temp;
        public readonly List<InstallStep> Steps = new List<InstallStep>();
        public event Action<InstallStep> StepChanged;
        public event Action<InstallProgress> ProgressChanged;
        public SelfTestResult TestResult;
        public bool FellBackToCpu;
        /// <summary>The launcher (shortcuts, autostart and Apps &amp; features point here).</summary>
        public string TiroExe => Path.Combine(opt.InstallDir, "Tiro.exe");
        /// <summary>This version's own folder (versioned layout: app-X.Y.Z next to the launcher).</summary>
        public string AppDir => InstallState.VersionDir(opt.InstallDir, manifest.Version);
        string AppExe => Path.Combine(AppDir, "Tiro.exe");

        int iApp = -1, iGpu = -1, iModel = -1, iInstall, iSetup, iTest;
        long totalDownload, doneDownloadBase;
        readonly Stopwatch clock = new Stopwatch();
        readonly Queue<Tuple<double, long>> samples = new Queue<Tuple<double, long>>();

        public InstallEngine(Manifest manifest, InstallOptions opt)
        {
            this.manifest = manifest;
            this.opt = opt;
            temp = Path.Combine(Path.GetTempPath(), "TiroSetup", manifest.Version);
            var model = manifest.Models[opt.ModelKey];
            iApp = Add("Download Tiro", "The app itself", manifest.App.Size);
            if (opt.UseGpu) iGpu = Add("Download the NVIDIA GPU runtime", "CUDA 13 and cuDNN libraries for your graphics card", manifest.Gpu.Size);
            iModel = Add("Download the speech model", $"{model.Title} · {model.Subtitle}" + (opt.UseGpu ? "" : " · compact CPU edition"),
                         MissingModelBytes(model));
            iInstall = Add("Install", "Put everything in place", 0);
            iSetup = Add("Set up Windows", "Start menu, startup and Apps & features", 0);
            iTest = Add("Test speech recognition", "Transcribe a real recording to make sure it all works", 0);
            totalDownload = Steps.Sum(s => s.Bytes);
        }

        int Add(string title, string detail, long bytes)
        {
            Steps.Add(new InstallStep { Title = title, Detail = detail, Bytes = bytes });
            return Steps.Count - 1;
        }

        long MissingModelBytes(ModelEntry model)
        {
            var dir = Path.Combine(opt.InstallDir, "models", model.Folder);
            return model.Files(opt.UseGpu).Where(f => !Present(Path.Combine(dir, f.Name), f.Size)).Sum(f => f.Size);
        }

        static bool Present(string path, long size) => File.Exists(path) && new FileInfo(path).Length == size;

        public long RequiredDiskBytes()
        {
            long need = manifest.App.InstalledBytes + Steps[iModel].Bytes + manifest.App.Size;
            if (opt.UseGpu) need += manifest.Gpu.InstalledBytes + manifest.Gpu.Size;
            return need;
        }

        void Set(int i, StepState s)
        {
            if (i < 0) return;
            Steps[i].State = s;
            StepChanged?.Invoke(Steps[i]);
        }

        void Report(string status, long downloadedNow)
        {
            var t = clock.Elapsed.TotalSeconds;
            samples.Enqueue(Tuple.Create(t, downloadedNow));
            while (samples.Count > 2 && t - samples.Peek().Item1 > 3) samples.Dequeue();
            var speed = "";
            if (samples.Count > 1)
            {
                var first = samples.Peek();
                var rate = (downloadedNow - first.Item2) / Math.Max(0.2, t - first.Item1);
                if (rate > 1)
                {
                    var left = (totalDownload - downloadedNow) / rate;
                    speed = $"{rate / 1048576:0.0} MB/s · " + Eta(left);
                }
            }
            var frac = totalDownload > 0 ? 0.9 * downloadedNow / totalDownload : 0.9;
            ProgressChanged?.Invoke(new InstallProgress { Fraction = frac, Status = status, Speed = speed });
        }

        static string Eta(double seconds)
        {
            if (seconds < 10) return "almost done";
            if (seconds < 90) return $"about {Math.Ceiling(seconds / 10) * 10:0} s left";
            return $"about {Math.Ceiling(seconds / 60):0} min left";
        }

        async Task Download(int step, string url, string dest, long size, string sha, CancellationToken ct, Downloader dl)
        {
            Set(step, StepState.Active);
            var label = Steps[step].Title;
            await dl.DownloadAsync(url, dest, size, sha, got => Report($"{label} · {Human(got)} of {Human(size)}", doneDownloadBase + got), ct);
            doneDownloadBase += size;
            Set(step, StepState.Done);
        }

        public static string Human(long bytes) =>
            bytes >= 1073741824 ? $"{bytes / 1073741824.0:0.0} GB" : $"{Math.Max(1, bytes / 1048576):0} MB";

        public async Task RunAsync(CancellationToken ct)
        {
            clock.Start();
            Directory.CreateDirectory(temp);
            Directory.CreateDirectory(opt.InstallDir);
            var model = manifest.Models[opt.ModelKey];
            var appZip = Path.Combine(temp, Path.GetFileName(new Uri(manifest.App.Url).LocalPath));
            var gpuZip = Path.Combine(temp, Path.GetFileName(new Uri(manifest.Gpu.Url).LocalPath));

            using (var dl = new Downloader())
            {
                await Download(iApp, manifest.App.Url, appZip, manifest.App.Size, manifest.App.Sha256, ct, dl);
                if (opt.UseGpu) await Download(iGpu, manifest.Gpu.Url, gpuZip, manifest.Gpu.Size, manifest.Gpu.Sha256, ct, dl);

                Set(iModel, StepState.Active);
                var modelDir = Path.Combine(opt.InstallDir, "models", model.Folder);
                foreach (var f in model.Files(opt.UseGpu))
                {
                    var dest = Path.Combine(modelDir, f.Name);
                    if (Present(dest, f.Size)) continue;
                    var before = doneDownloadBase;
                    await dl.DownloadAsync(f.Url, dest, f.Size, f.Sha256,
                        got => Report($"Speech model · {f.Name} · {Human(got)} of {Human(f.Size)}", before + got), ct);
                    doneDownloadBase += f.Size;
                }
                Set(iModel, StepState.Done);
            }

            // ---- install (side by side: the version that runs today stays intact as the "previous" one)
            Set(iInstall, StepState.Active);
            Report("Closing Tiro if it's running…", doneDownloadBase);
            var staging = Path.Combine(temp, "app");
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
            Extract(appZip, staging);
            await Shell.StopTiroAsync(Path.Combine(staging, "Tiro.exe"), opt.InstallDir, ct);
            Report("Installing files…", doneDownloadBase);
            var root = opt.InstallDir;
            var existing = Shell.FindExisting();  // only if it is this very folder (another install is not ours)
            var oldVersion = existing != null && string.Equals(Path.GetFullPath(existing.Dir).TrimEnd('\\'),
                Path.GetFullPath(root).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase) ? existing.Version : null;
            var migrated = InstallState.MigrateFlat(root, oldVersion);  // Tiro 2.0.x was installed flat
            var state = InstallState.Load(root) ?? new InstallState();
            var runningBefore = state.Current ?? migrated;
            if (Directory.Exists(AppDir)) Directory.Delete(AppDir, true);  // reinstalling this version: fresh copy
            try { Directory.Move(staging, AppDir); }
            catch (IOException)  // another drive than the temp folder: copy instead
            {
                Directory.CreateDirectory(AppDir);
                CopyTree(staging, AppDir);
                try { Directory.Delete(staging, true); } catch { }
            }
            if (opt.UseGpu)
            {
                Report("Installing the GPU runtime…", doneDownloadBase);
                Extract(gpuZip, AppDir);
            }
            InstallState.WriteLauncher(root);
            if (runningBefore != null && runningBefore != manifest.Version) state.Previous = runningBefore;
            state.Current = manifest.Version;
            state.Pending = null;
            state.Trial = null;
            state.Unconfirmed = 0;
            state.Bad.Remove(manifest.Version);
            state.Save(root);
            state.Cleanup(root);
            var exe = TiroExe;
            var self = Assembly.GetExecutingAssembly().Location;
            var setupCopy = Path.Combine(opt.InstallDir, "TiroSetup.exe");
            if (!string.Equals(Path.GetFullPath(self), Path.GetFullPath(setupCopy), StringComparison.OrdinalIgnoreCase))
                File.Copy(self, setupCopy, true);
            Set(iInstall, StepState.Done);

            // ---- Windows integration + settings
            Set(iSetup, StepState.Active);
            Report("Setting up Windows…", doneDownloadBase);
            if (!opt.NoShell)
            {
                Shell.CreateShortcut(Shell.StartMenuShortcut, exe, "Tiro: speak anywhere, it types for you");
                if (opt.DesktopShortcut) Shell.CreateShortcut(Shell.DesktopShortcut, exe, "Tiro");
                else if (File.Exists(Shell.DesktopShortcut)) File.Delete(Shell.DesktopShortcut);
                Shell.RegisterUninstall(opt.InstallDir, manifest.Version, Shell.DirectorySize(opt.InstallDir));
            }
            await Shell.RunTiroAsync(AppExe,
                $"--set device={(opt.UseGpu ? "auto" : "cpu")} --set model={opt.ModelKey}" +
                (opt.NoShell ? "" : $" --set autostart={(opt.StartWithWindows ? "true" : "false")}"),
                TimeSpan.FromSeconds(30), ct);
            Set(iSetup, StepState.Done);

            // ---- prove it works
            Set(iTest, StepState.Active);
            Report("Testing speech recognition on a real recording…", doneDownloadBase);
            TestResult = await SelfTest(opt.UseGpu ? "cuda" : "cpu", ct);
            if (opt.UseGpu && (TestResult == null || TestResult.Device != "cuda"))
            {
                FellBackToCpu = true;  // e.g. a driver problem: Tiro still works on the CPU
                await Shell.RunTiroAsync(AppExe, "--set device=cpu", TimeSpan.FromSeconds(30), ct);
            }
            Set(iTest, TestResult != null && TestResult.Ok ? StepState.Done : StepState.Failed);
            ProgressChanged?.Invoke(new InstallProgress { Fraction = 1, Status = "Done" });
            try { Directory.Delete(staging, true); } catch { }
        }

        async Task<SelfTestResult> SelfTest(string device, CancellationToken ct)
        {
            var outFile = Path.Combine(temp, "selftest.json");
            if (File.Exists(outFile)) File.Delete(outFile);
            await Shell.RunTiroAsync(AppExe, $"--selftest \"{outFile}\" --device {device}", TimeSpan.FromMinutes(3), ct);
            if (!File.Exists(outFile)) return new SelfTestResult { Ok = false, Error = "The self-test didn't produce a result." };
            using (var f = File.OpenRead(outFile))
                return (SelfTestResult)new DataContractJsonSerializer(typeof(SelfTestResult)).ReadObject(f);
        }

        static void Extract(string zip, string destDir)
        {
            Directory.CreateDirectory(destDir);
            var root = Path.GetFullPath(destDir);
            using (var z = ZipFile.OpenRead(zip))
            {
                foreach (var e in z.Entries)
                {
                    var path = Path.GetFullPath(Path.Combine(destDir, e.FullName));
                    if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;  // zip-slip guard
                    if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(path); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    e.ExtractToFile(path, true);
                }
            }
        }

        static void CopyTree(string src, string dst)
        {
            foreach (var dir in Directory.EnumerateDirectories(src, "*", SearchOption.AllDirectories))
                Directory.CreateDirectory(Path.Combine(dst, dir.Substring(src.Length + 1)));
            foreach (var file in Directory.EnumerateFiles(src, "*", SearchOption.AllDirectories))
                File.Copy(file, Path.Combine(dst, file.Substring(src.Length + 1)), true);
        }

        /// <summary>Remove Tiro. Our own exe is deleted by a short-lived helper after we exit.</summary>
        public static async Task UninstallAsync(string dir, bool removeUserData, Action<string> status, CancellationToken ct)
        {
            status("Closing Tiro…");
            var appDir = InstallState.CurrentAppDir(dir);
            await Shell.StopTiroAsync(appDir != null ? Path.Combine(appDir, "Tiro.exe") : null, dir, ct);
            status("Removing shortcuts and settings entries…");
            Shell.Unregister();
            status("Deleting files…");
            var self = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
            foreach (var d in Directory.EnumerateDirectories(dir))
                try { Directory.Delete(d, true); } catch { }
            foreach (var f in Directory.EnumerateFiles(dir))
                if (!string.Equals(Path.GetFullPath(f), self, StringComparison.OrdinalIgnoreCase))
                    try { File.Delete(f); } catch { }
            if (removeUserData)
            {
                foreach (var d in new[]
                {
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tiro"),
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Tiro"),
                })
                    try { if (Directory.Exists(d)) Directory.Delete(d, true); } catch { }
            }
            if (self.StartsWith(Path.GetFullPath(dir), StringComparison.OrdinalIgnoreCase))
            {
                // we're running from inside the folder: remove it once this process has exited
                Process.Start(new ProcessStartInfo("cmd.exe", $"/c ping 127.0.0.1 -n 3 > nul & rmdir /s /q \"{dir}\"")
                { CreateNoWindow = true, UseShellExecute = false, WindowStyle = ProcessWindowStyle.Hidden });
            }
            else
            {
                try { Directory.Delete(dir, true); } catch { }
            }
        }
    }
}
