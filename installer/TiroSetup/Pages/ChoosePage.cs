using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using TiroSetup.Services;

namespace TiroSetup.Pages
{
    /// <summary>"Your graphics card is the fastest way to run Tiro": the recommendation, both options with honest
    /// numbers, and advanced choices tucked away.</summary>
    public sealed class ChoosePage : UserControl
    {
        readonly MainWindow w;
        readonly RadioButton gpuCard, cpuCard;
        readonly TextBlock sizeText;
        readonly TextBox dirBox;
        readonly CheckBox autostart, desktop;
        readonly RadioButton english, multi;

        public ChoosePage(MainWindow w)
        {
            this.w = w;
            var ctx = w.Context;
            var r = ctx.Recommendation;
            var root = new StackPanel { MaxWidth = 760, HorizontalAlignment = HorizontalAlignment.Left };
            root.Children.Add(UI.Text(r.Headline, "H1"));
            root.Children.Add(new Border { Height = 8 });
            root.Children.Add(UI.Text(r.Why, "Muted", 14.5));
            root.Children.Add(new Border { Height = 20 });

            var cards = new Grid();
            cards.ColumnDefinitions.Add(new ColumnDefinition());
            cards.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(14) });
            cards.ColumnDefinitions.Add(new ColumnDefinition());
            gpuCard = Option("Graphics card", r.GpuTitle, r.GpuUsable
                    ? $"Words appear {Recommendation.Seconds(r.GpuSeconds)} after you stop talking. Downloads NVIDIA's GPU libraries once (about 1.2 GB)."
                    : r.GpuProblem, r.RecommendGpu);
            gpuCard.IsEnabled = r.GpuUsable;
            cpuCard = Option("Processor", r.CpuTitle,
                $"Words appear {Recommendation.Seconds(r.CpuSeconds)} after you stop talking. Same accuracy, smaller download.",
                !r.RecommendGpu);
            gpuCard.IsChecked = r.RecommendGpu;
            cpuCard.IsChecked = !r.RecommendGpu;
            gpuCard.Checked += (s, e) => UpdateSize();
            cpuCard.Checked += (s, e) => UpdateSize();
            cards.Children.Add(gpuCard);
            Grid.SetColumn(cpuCard, 2);
            cards.Children.Add(cpuCard);
            root.Children.Add(cards);
            if (!r.GpuUsable && !string.IsNullOrEmpty(r.GpuFixUrl))
                root.Children.Add(UI.Link("How to update the NVIDIA driver", (s, e) => Shell.OpenUrl(r.GpuFixUrl)));

            sizeText = UI.Text("", "Caption");
            sizeText.Margin = new Thickness(2, 14, 0, 0);
            root.Children.Add(sizeText);

            // ---- advanced
            var adv = new StackPanel { Visibility = Visibility.Collapsed, Margin = new Thickness(0, 10, 0, 0) };
            var toggle = UI.Link("Advanced options", (s, e) =>
                adv.Visibility = adv.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible);
            toggle.HorizontalAlignment = HorizontalAlignment.Left;
            toggle.Margin = new Thickness(0, 10, 0, 0);
            root.Children.Add(toggle);
            var advCard = new StackPanel();
            advCard.Children.Add(UI.Text("Install folder", "Caption"));
            var dirRow = new DockPanel { Margin = new Thickness(0, 6, 0, 14) };
            var browse = UI.Secondary("Browse…", (s, e) => Browse());
            DockPanel.SetDock(browse, Dock.Right);
            dirRow.Children.Add(browse);
            dirBox = new TextBox { Text = ctx.Options.InstallDir, Margin = new Thickness(0, 0, 10, 0), Padding = new Thickness(8, 7, 8, 7),
                                   Background = UI.B("Ink800Brush"), Foreground = UI.B("TextBrush"), BorderBrush = UI.B("LineStrongBrush"),
                                   FontSize = 13, VerticalContentAlignment = VerticalAlignment.Center };
            dirRow.Children.Add(dirBox);
            advCard.Children.Add(dirRow);
            advCard.Children.Add(UI.Text("Language", "Caption"));
            var seg = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 6, 0, 14) };
            english = new RadioButton { Content = "English (most accurate)", Style = UI.S("Segment"), GroupName = "lang" };
            multi = new RadioButton { Content = "25 European languages", Style = UI.S("Segment"), GroupName = "lang" };
            english.IsChecked = ctx.Options.ModelKey != "parakeet-tdt-0.6b-v3";
            multi.IsChecked = !english.IsChecked;
            english.Checked += (s, e) => UpdateSize();
            multi.Checked += (s, e) => UpdateSize();
            seg.Children.Add(english);
            seg.Children.Add(multi);
            advCard.Children.Add(seg);
            autostart = new CheckBox { Content = "Start Tiro when I sign in to Windows", Style = UI.S("Switch"), IsChecked = true,
                                       Margin = new Thickness(0, 0, 0, 10) };
            desktop = new CheckBox { Content = "Add a desktop shortcut", Style = UI.S("Switch"), IsChecked = false };
            advCard.Children.Add(autostart);
            advCard.Children.Add(desktop);
            adv.Children.Add(UI.Card(advCard));
            root.Children.Add(adv);

            var scroll = new ScrollViewer { Content = root, VerticalScrollBarVisibility = ScrollBarVisibility.Hidden };
            Content = UI.Layout(scroll, UI.Footer(UI.Secondary("Back", (s, e) => w.Navigate(new WelcomePage(w))),
                                                  UI.Primary("Install", (s, e) => Install())));
            UpdateSize();
        }

        static RadioButton Option(string kind, string title, string detail, bool recommended)
        {
            var p = new StackPanel();
            var head = new DockPanel { LastChildFill = false };
            head.Children.Add(UI.Text(kind.ToUpperInvariant(), "Eyebrow"));
            if (recommended)
            {
                var badge = new Border { Style = UI.S("Badge"), Child = UI.Text("RECOMMENDED", "Eyebrow", 10, Brushes.White) };
                DockPanel.SetDock(badge, Dock.Right);
                head.Children.Add(badge);
            }
            p.Children.Add(head);
            p.Children.Add(new Border { Height = 8 });
            p.Children.Add(UI.Text(title, "H3", 16));
            p.Children.Add(new Border { Height = 6 });
            p.Children.Add(UI.Text(detail, "Muted", 13));
            return new RadioButton { Style = UI.S("SetupCard"), Content = p, GroupName = "device" };
        }

        void UpdateSize()
        {
            var ctx = w.Context;
            ctx.Options.ModelKey = multi != null && multi.IsChecked == true ? "parakeet-tdt-0.6b-v3" : "parakeet-tdt-0.6b-v2";
            var gpu = gpuCard.IsChecked == true;
            sizeText.Text = $"Download: {InstallEngine.Human(ctx.DownloadBytes(gpu))} · Tiro then fetches its small AI " +
                            "helper (about 0.5–1.2 GB) when it first starts.";
        }

        void Browse()
        {
            using (var dlg = new System.Windows.Forms.FolderBrowserDialog { SelectedPath = dirBox.Text, ShowNewFolderButton = true })
            {
                if (dlg.ShowDialog() == System.Windows.Forms.DialogResult.OK)
                    dirBox.Text = dlg.SelectedPath.EndsWith("Tiro", StringComparison.OrdinalIgnoreCase)
                        ? dlg.SelectedPath : Path.Combine(dlg.SelectedPath, "Tiro");
            }
        }

        void Install()
        {
            var o = w.Context.Options;
            o.UseGpu = gpuCard.IsChecked == true;
            o.InstallDir = dirBox.Text.Trim();
            o.StartWithWindows = autostart.IsChecked == true;
            o.DesktopShortcut = desktop.IsChecked == true;
            var engine = new InstallEngine(w.Context.Manifest, o);
            var needGb = engine.RequiredDiskBytes() / 1073741824.0;
            var freeGb = SystemInfo.FreeSpaceGb(o.InstallDir);
            if (freeGb > 0 && freeGb < needGb &&
                !Confirm.Ask(w, "Not much disk space",
                    $"Tiro needs about {needGb:0.0} GB on this drive and there's {freeGb:0.0} GB free. Try anyway?",
                    "Try anyway", "Back"))
                return;
            w.Context.Engine = engine;
            w.Navigate(new InstallPage(w));
        }
    }
}
