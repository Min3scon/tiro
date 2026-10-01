using System;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using TiroSetup.Pages;
using TiroSetup.Services;

namespace TiroSetup
{
    /// <summary>TiroSetup.exe --screenshots FOLDER: renders each page to PNG (for the README), without installing.</summary>
    static class Screenshots
    {
        public static async Task Render(SetupContext ctx, string folder)
        {
            Directory.CreateDirectory(folder);
            ctx.System = await SystemInfo.CollectAsync();
            ctx.System.Online = true;
            ctx.Recommendation = Recommendation.For(ctx.System);
            ctx.Options.UseGpu = ctx.Recommendation.RecommendGpu;
            var w = new MainWindow(ctx, false) { Left = -20000, Top = -20000, ShowActivated = false };
            w.Show();
            await Shot(w, new WelcomePage(w), Path.Combine(folder, "setup-1-welcome.png"));
            await Shot(w, new ChoosePage(w), Path.Combine(folder, "setup-2-choose.png"));
            ctx.Engine = new InstallEngine(ctx.Manifest, ctx.Options);
            await Shot(w, new InstallPage(w, preview: true), Path.Combine(folder, "setup-3-install.png"));
            ctx.Engine.TestResult = new SelfTestResult
            {
                Ok = true, Device = "cuda", DeviceLabel = ctx.Recommendation.GpuUsable ? ctx.Recommendation.GpuTitle + " (CUDA)" : "CPU",
                DecodeMs = 52, AudioSeconds = 5.9,
                Text = "Mister Quilter is the apostle of the middle classes, and we are glad to welcome his gospel.",
            };
            await Shot(w, new DonePage(w), Path.Combine(folder, "setup-4-done.png"));
            w.Close();
        }

        static async Task Shot(MainWindow w, System.Windows.Controls.UserControl page, string path)
        {
            w.Navigate(page);
            await Task.Delay(900);  // let the entrance animation finish
            var root = (FrameworkElement)w.Content;
            var dpi = VisualTreeHelper.GetDpi(w);
            var bmp = new RenderTargetBitmap((int)(root.ActualWidth * dpi.DpiScaleX), (int)(root.ActualHeight * dpi.DpiScaleY),
                                             96 * dpi.DpiScaleX, 96 * dpi.DpiScaleY, PixelFormats.Pbgra32);
            var dv = new DrawingVisual();  // draw at the origin (the window's shadow margin would shift it)
            using (var dc = dv.RenderOpen())
                dc.DrawRectangle(new VisualBrush(root), null, new Rect(0, 0, root.ActualWidth, root.ActualHeight));
            bmp.Render(dv);
            var enc = new PngBitmapEncoder();
            enc.Frames.Add(BitmapFrame.Create(bmp));
            using (var f = File.Create(path)) enc.Save(f);
        }
    }
}
