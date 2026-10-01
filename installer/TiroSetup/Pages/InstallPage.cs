using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;
using TiroSetup.Services;

namespace TiroSetup.Pages
{
    public sealed class InstallPage : UserControl
    {
        readonly MainWindow w;
        readonly InstallEngine engine;
        readonly Dictionary<InstallStep, (Ellipse dot, TextBlock title)> rows = new Dictionary<InstallStep, (Ellipse, TextBlock)>();
        readonly ProgressBar bar = new ProgressBar { Style = UI.S("Progress"), Minimum = 0, Maximum = 1 };
        readonly TextBlock status = UI.Text("", "Body");
        readonly TextBlock speed = UI.Text("", "Caption");
        readonly StackPanel errorBox = new StackPanel { Visibility = Visibility.Collapsed, Margin = new Thickness(0, 14, 0, 0) };
        readonly Button cancel;
        CancellationTokenSource cts;

        public InstallPage(MainWindow w, bool preview = false)
        {
            this.w = w;
            engine = w.Context.Engine;
            var root = new StackPanel { MaxWidth = 760, HorizontalAlignment = HorizontalAlignment.Left };
            root.Children.Add(UI.Text("Installing Tiro", "H1"));
            root.Children.Add(new Border { Height = 8 });
            root.Children.Add(UI.Text("Downloads resume where they stopped if your connection drops, and every file is " +
                                      "checked before it's used.", "Muted", 14.5));
            root.Children.Add(new Border { Height = 20 });
            var list = new StackPanel();
            foreach (var step in engine.Steps)
            {
                var g = new Grid { Margin = new Thickness(0, 4, 0, 4) };
                g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(26) });
                g.ColumnDefinitions.Add(new ColumnDefinition());
                var dot = new Ellipse { Width = 9, Height = 9, Fill = UI.B("Ink700Brush"), VerticalAlignment = VerticalAlignment.Top,
                                        Margin = new Thickness(0, 6, 0, 0) };
                g.Children.Add(dot);
                var text = new StackPanel();
                var title = UI.Text(step.Title, "Body");
                text.Children.Add(title);
                text.Children.Add(UI.Text(step.Detail, "Caption"));
                Grid.SetColumn(text, 1);
                g.Children.Add(text);
                list.Children.Add(g);
                rows[step] = (dot, title);
            }
            root.Children.Add(UI.Card(list));
            root.Children.Add(new Border { Height = 18 });
            root.Children.Add(bar);
            root.Children.Add(new Border { Height = 10 });
            root.Children.Add(status);
            root.Children.Add(speed);
            root.Children.Add(errorBox);
            cancel = UI.Secondary("Cancel", (s, e) => w.Close());
            Content = UI.Layout(new ScrollViewer { Content = root, VerticalScrollBarVisibility = ScrollBarVisibility.Hidden },
                                UI.Footer(cancel));
            engine.StepChanged += s => Dispatcher.Invoke(() => Paint(s));
            engine.ProgressChanged += p => Dispatcher.Invoke(() =>
            {
                bar.Value = p.Fraction;
                status.Text = p.Status;
                speed.Text = p.Speed;
            });
            if (preview)
            {
                Loaded += (s, e) =>
                {
                    engine.Steps[0].State = StepState.Done;
                    Paint(engine.Steps[0]);
                    if (engine.Steps.Count > 1)
                    {
                        engine.Steps[1].State = StepState.Active;
                        Paint(engine.Steps[1]);
                    }
                    bar.Value = 0.34;
                    status.Text = engine.Steps.Count > 1 ? engine.Steps[1].Title + " · 412 MB of 1.1 GB" : "";
                    speed.Text = "41.8 MB/s · about 1 min left";
                };
                return;
            }
            Loaded += async (s, e) => await Run();
        }

        void Paint(InstallStep s)
        {
            var (dot, title) = rows[s];
            switch (s.State)
            {
                case StepState.Active:
                    dot.Fill = UI.B("BrandGradient");
                    title.FontWeight = FontWeights.SemiBold;
                    break;
                case StepState.Done:
                    dot.Fill = UI.B("OkBrush");
                    title.FontWeight = FontWeights.Normal;
                    break;
                case StepState.Failed:
                    dot.Fill = UI.B("DangerBrush");
                    break;
                case StepState.Skipped:
                    dot.Fill = UI.B("Ink700Brush");
                    break;
            }
        }

        async Task Run()
        {
            cts = new CancellationTokenSource();
            errorBox.Visibility = Visibility.Collapsed;
            w.Busy = true;
            try
            {
                await Task.Run(() => engine.RunAsync(cts.Token));
                w.Busy = false;
                w.Navigate(new DonePage(w));
            }
            catch (Exception ex)
            {
                w.Busy = false;
                errorBox.Children.Clear();
                errorBox.Children.Add(UI.Text("Something went wrong", "H3", 15, UI.B("DangerBrush")));
                errorBox.Children.Add(UI.Text(Friendly(ex), "Muted"));
                var retry = UI.Primary("Try again", async (s, e) => await Run());
                retry.HorizontalAlignment = HorizontalAlignment.Left;
                retry.Margin = new Thickness(0, 12, 0, 0);
                errorBox.Children.Add(retry);
                errorBox.Visibility = Visibility.Visible;
            }
        }

        static string Friendly(Exception ex)
        {
            var e = ex is AggregateException a && a.InnerException != null ? a.InnerException : ex;
            if (e is System.Net.Http.HttpRequestException || e is System.Net.WebException || e is System.IO.IOException && e.Message.Contains("connection"))
                return "The download was interrupted. Check your internet connection and try again: finished parts are kept, " +
                       "so it picks up where it stopped.";
            if (e is UnauthorizedAccessException)
                return "Setup couldn't write to the install folder. Choose a folder you own (the default is fine) and try again.";
            return e.Message;
        }
    }
}
