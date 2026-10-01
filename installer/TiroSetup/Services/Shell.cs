using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace TiroSetup.Services
{
    /// <summary>Windows integration: shortcuts, the Apps &amp; features entry, running/stopping Tiro.</summary>
    public static class Shell
    {
        public const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Tiro";
        const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";

        public static string DefaultInstallDir =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Tiro");

        public static string StartMenuShortcut =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Tiro.lnk");

        public static string DesktopShortcut =>
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Tiro.lnk");

        public sealed class Existing
        {
            public string Dir;
            public string Version;
            public bool HasGpuRuntime;
            public string ModelKey;
        }

        /// <summary>The current installation, if Tiro was installed with this Setup before.</summary>
        public static Existing FindExisting()
        {
            using (var k = Registry.CurrentUser.OpenSubKey(UninstallKey))
            {
                var dir = k?.GetValue("InstallLocation") as string;
                if (string.IsNullOrEmpty(dir) || !File.Exists(Path.Combine(dir, "Tiro.exe"))) return null;
                var models = Path.Combine(dir, "models");
                var appDir = InstallState.CurrentAppDir(dir) ?? dir;  // versioned layout, or the old flat one
                return new Existing
                {
                    Dir = dir,
                    Version = k.GetValue("DisplayVersion") as string ?? "",
                    HasGpuRuntime = File.Exists(Path.Combine(appDir, "_internal", "cuda", "cudnn64_9.dll")),
                    ModelKey = Directory.Exists(Path.Combine(models, "parakeet-tdt-0.6b-v3")) &&
                               !Directory.Exists(Path.Combine(models, "parakeet-tdt-0.6b-v2"))
                        ? "parakeet-tdt-0.6b-v3" : "parakeet-tdt-0.6b-v2",
                };
            }
        }

        public static void CreateShortcut(string lnkPath, string target, string description)
        {
            var shellType = Type.GetTypeFromProgID("WScript.Shell");
            dynamic shell = Activator.CreateInstance(shellType);
            try
            {
                dynamic lnk = shell.CreateShortcut(lnkPath);
                lnk.TargetPath = target;
                lnk.WorkingDirectory = Path.GetDirectoryName(target);
                lnk.IconLocation = target + ",0";
                lnk.Description = description;
                lnk.Save();
            }
            finally
            {
                System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
            }
        }

        public static void RegisterUninstall(string dir, string version, long installedBytes)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                var setup = Path.Combine(dir, "TiroSetup.exe");
                k.SetValue("DisplayName", "Tiro");
                k.SetValue("DisplayVersion", version);
                k.SetValue("Publisher", "Tiro");
                k.SetValue("DisplayIcon", Path.Combine(dir, "Tiro.exe") + ",0");
                k.SetValue("InstallLocation", dir);
                k.SetValue("UninstallString", $"\"{setup}\" --uninstall");
                k.SetValue("ModifyPath", $"\"{setup}\"");
                k.SetValue("URLInfoAbout", "https://github.com/Min3scon/tiro");
                k.SetValue("HelpLink", "https://github.com/Min3scon/tiro#readme");
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                k.SetValue("EstimatedSize", (int)Math.Min(int.MaxValue, installedBytes / 1024), RegistryValueKind.DWord);
                k.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
            }
        }

        public static void Unregister()
        {
            try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch { }
            try
            {
                using (var k = Registry.CurrentUser.OpenSubKey(RunKey, true))
                {
                    if (k?.GetValue("Tiro") != null) k.DeleteValue("Tiro", false);
                }
            }
            catch { }
            foreach (var lnk in new[] { StartMenuShortcut, DesktopShortcut })
                try { if (File.Exists(lnk)) File.Delete(lnk); } catch { }
        }

        /// <summary>Run Tiro.exe with arguments and wait (Tiro is a windowed app, so we wait on the process).</summary>
        public static async Task<int> RunTiroAsync(string exe, string args, TimeSpan timeout, CancellationToken ct)
        {
            var psi = new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Path.GetDirectoryName(exe) };
            using (var p = Process.Start(psi))
            {
                var done = await Task.Run(() => p.WaitForExit((int)timeout.TotalMilliseconds), ct).ConfigureAwait(false);
                if (!done)
                {
                    try { p.Kill(); } catch { }
                    return -1;
                }
                return p.ExitCode;
            }
        }

        /// <summary>
        /// Ask the running Tiro to quit (its single-instance channel), then make sure no copy of Tiro from this
        /// install folder is left running. Copies of Tiro elsewhere (another folder, a development build) are
        /// asked to quit but never killed.
        /// </summary>
        public static async Task StopTiroAsync(string anyTiroExe, string installDir, CancellationToken ct)
        {
            if (!Process.GetProcessesByName("Tiro").Any()) return;
            if (anyTiroExe != null && File.Exists(anyTiroExe))
                await RunTiroAsync(anyTiroExe, "--quit", TimeSpan.FromSeconds(15), ct).ConfigureAwait(false);
            for (var i = 0; i < 40 && Running(installDir).Any(); i++)
                await Task.Delay(250, ct).ConfigureAwait(false);
            foreach (var p in Running(installDir))
            {
                try { p.Kill(); p.WaitForExit(5000); } catch { }
            }
        }

        static System.Collections.Generic.IEnumerable<Process> Running(string installDir)
        {
            var prefix = Path.GetFullPath(installDir).TrimEnd('\\') + "\\";
            foreach (var p in Process.GetProcessesByName("Tiro"))
            {
                string path = null;
                try { path = p.MainModule?.FileName; } catch { }  // other user / elevated: not ours to stop
                if (path != null && path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) yield return p;
            }
        }

        public static void Launch(string exe)
        {
            Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true, WorkingDirectory = Path.GetDirectoryName(exe) });
        }

        public static void OpenUrl(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }

        public static long DirectorySize(string dir)
        {
            try { return Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories).Sum(f => new FileInfo(f).Length); }
            catch { return 0; }
        }
    }
}
