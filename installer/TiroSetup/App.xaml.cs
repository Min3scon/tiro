using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using TiroSetup.Services;

namespace TiroSetup
{
    public partial class App : Application
    {
        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);
            var args = e.Args.Select(a => a.ToLowerInvariant()).ToList();
            if (args.Contains("--silent"))
            {
                ShutdownMode = ShutdownMode.OnExplicitShutdown;
                Task.Run(() => RunSilent(e.Args)).ContinueWith(t => Dispatcher.Invoke(() => Shutdown(t.Result)));
                return;
            }
            var ctx = new SetupContext();
            var shotsAt = args.IndexOf("--screenshots");
            if (shotsAt >= 0 && shotsAt + 1 < e.Args.Length)
            {
                ShutdownMode = ShutdownMode.OnExplicitShutdown;
                var folder = e.Args[shotsAt + 1];
                Screenshots.Render(ctx, folder).ContinueWith(t =>
                {
                    if (t.Exception != null)
                        File.WriteAllText(Path.Combine(folder, "error.txt"), t.Exception.ToString());
                    Shutdown(t.Exception != null ? 1 : 0);
                }, TaskScheduler.FromCurrentSynchronizationContext());
                return;
            }
            var window = new MainWindow(ctx, args.Contains("--uninstall"));
            MainWindow = window;
            window.Show();
        }

        /// <summary>TiroSetup.exe --silent [--cpu|--gpu] [--multilingual] [--dir PATH] [--no-autostart] [--log FILE]</summary>
        static int RunSilent(string[] raw)
        {
            var args = raw.Select(a => a.ToLowerInvariant()).ToList();
            string Arg(string name)
            {
                var i = args.IndexOf(name);
                return i >= 0 && i + 1 < raw.Length ? raw[i + 1] : null;
            }
            var logPath = Arg("--log");
            void Log(string line)
            {
                var stamped = $"{DateTime.Now:HH:mm:ss} {line}";
                if (logPath != null) File.AppendAllText(logPath, stamped + Environment.NewLine);
            }
            try
            {
                var ctx = new SetupContext();
                ctx.System = SystemInfo.CollectAsync().Result;
                ctx.Recommendation = Recommendation.For(ctx.System);
                var o = ctx.Options;
                o.UseGpu = args.Contains("--gpu") || (!args.Contains("--cpu") && ctx.Recommendation.RecommendGpu);
                o.ModelKey = args.Contains("--multilingual") ? "parakeet-tdt-0.6b-v3" : "parakeet-tdt-0.6b-v2";
                o.InstallDir = Arg("--dir") ?? o.InstallDir;
                o.StartWithWindows = !args.Contains("--no-autostart");
                Log($"system: {ctx.System.CpuName}; GPU: {ctx.Recommendation.GpuTitle} usable={ctx.Recommendation.GpuUsable}");
                Log($"installing to {o.InstallDir} gpu={o.UseGpu} model={o.ModelKey}");
                var engine = new InstallEngine(ctx.Manifest, o);
                string lastStatus = null;
                engine.StepChanged += s => Log($"step {s.Title}: {s.State}");
                engine.ProgressChanged += p =>
                {
                    var status = p.Status.Split('·')[0].Trim();
                    if (status != lastStatus) { lastStatus = status; Log($"{p.Fraction:P0} {p.Status}"); }
                };
                engine.RunAsync(CancellationToken.None).GetAwaiter().GetResult();
                var t = engine.TestResult;
                Log(t == null ? "self-test: no result"
                    : $"self-test ok={t.Ok} device={t.DeviceLabel} decode={t.DecodeMs} ms text=\"{t.Text}\" {t.Error}");
                return t != null && t.Ok ? 0 : 2;
            }
            catch (Exception ex)
            {
                Log("FAILED: " + ex);
                return 1;
            }
        }
    }
}
