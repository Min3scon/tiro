using System;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using TiroSetup.Services;

namespace TiroSetup.Pages
{
    /// <summary>Looks at the PC (Windows, processor, memory, graphics card, internet) and says what it found.</summary>
    public sealed class CheckPage : UserControl
    {
        readonly MainWindow w;
        readonly StackPanel list = new StackPanel();
        readonly Button next;

        public CheckPage(MainWindow w)
        {
            this.w = w;
            var stack = new StackPanel { MaxWidth = 700, HorizontalAlignment = HorizontalAlignment.Left };
            stack.Children.Add(UI.Text("Checking your PC", "H1"));
            stack.Children.Add(new Border { Height = 8 });
            stack.Children.Add(UI.Text("So you don't have to choose between graphics card and processor, Setup looks at " +
                                       "your hardware and picks the best option for it.", "Muted", 15));
            stack.Children.Add(new Border { Height = 22 });
            stack.Children.Add(UI.Card(list, new Thickness(20, 12, 20, 12)));
            next = UI.Primary("Next", (s, e) => w.Navigate(new ChoosePage(w)));
            next.IsEnabled = false;
            Content = UI.Layout(stack, UI.Footer(next));
            Loaded += async (s, e) => await Run();
        }

        void Add(string label, string value, bool ok = true) =>
            list.Children.Add(UI.Row(label, value, UI.B(ok ? "OkBrush" : "EmberBrush")));

        async Task Run()
        {
            var info = await SystemInfo.CollectAsync();
            w.Context.System = info;
            Add("Windows", $"{info.WindowsName} (build {info.WindowsBuild})", info.Is64Bit);
            await Task.Delay(140);
            Add("Processor", $"{info.CpuShortName} · {info.CpuCores} cores" + (info.Avx2 ? " · AVX2" : ""));
            await Task.Delay(140);
            Add("Memory", $"{info.RamGb:0} GB", info.RamGb >= 7.5);
            await Task.Delay(140);
            var r = Recommendation.For(info);
            w.Context.Recommendation = r;
            if (info.Nvidia != null) Add("Graphics", $"{r.GpuTitle} · {r.GpuDetail}", r.GpuUsable);
            else Add("Graphics", info.DisplayAdapters.Count > 0 ? string.Join(", ", info.DisplayAdapters) + " (not NVIDIA)"
                                                               : "No NVIDIA graphics card", false);
            await Task.Delay(140);
            await info.CheckOnlineAsync();
            Add("Internet", info.Online ? "Connected" : "Not connected: " + info.OnlineDetail, info.Online);
            var free = SystemInfo.FreeSpaceGb(w.Context.Options.InstallDir);
            Add("Disk space", $"{free:0} GB free", free > 6);
            await Task.Delay(220);
            next.IsEnabled = info.Online;
            if (!info.Online)
                list.Children.Add(UI.Text("Setup needs an internet connection to download Tiro and its speech model " +
                                          "(about 1–4 GB). Connect, then close Setup and open it again.", "Muted"));
            else
            {
                await Task.Delay(700);
                if (IsLoaded) w.Navigate(new ChoosePage(w));
            }
        }
    }
}
