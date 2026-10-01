using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using TiroSetup.Services;

namespace TiroSetup.Pages
{
    public sealed class UninstallPage : UserControl
    {
        readonly MainWindow w;
        readonly CheckBox wipe;
        readonly TextBlock status = UI.Text("", "Muted");
        readonly Button go;
        bool finished;

        public UninstallPage(MainWindow w)
        {
            this.w = w;
            var root = new StackPanel { MaxWidth = 640, HorizontalAlignment = HorizontalAlignment.Left,
                                        VerticalAlignment = VerticalAlignment.Center };
            root.Children.Add(UI.Logo(56));
            root.Children.Add(new Border { Height = 16 });
            root.Children.Add(UI.Text("Uninstall Tiro?", "H1"));
            root.Children.Add(new Border { Height = 10 });
            root.Children.Add(UI.Text($"This removes Tiro from {w.Context.Existing.Dir}, its Start menu entry and its " +
                                      "startup entry.", "Muted", 14.5));
            root.Children.Add(new Border { Height = 18 });
            wipe = new CheckBox { Style = UI.S("Switch"), IsChecked = false,
                                  Content = "Also delete my settings, dictation history, dictionary and learned fixes" };
            root.Children.Add(UI.Card(wipe));
            root.Children.Add(new Border { Height = 12 });
            root.Children.Add(status);
            go = UI.Primary("Uninstall", async (s, e) => await Run());
            Content = UI.Layout(root, UI.Footer(UI.Secondary("Keep Tiro", (s, e) => w.Close()), go));
        }

        async Task Run()
        {
            if (finished)
            {
                Application.Current.Shutdown();
                return;
            }
            go.IsEnabled = false;
            w.Busy = true;
            var removeData = wipe.IsChecked == true;
            try
            {
                await Task.Run(() => InstallEngine.UninstallAsync(w.Context.Existing.Dir, removeData,
                    m => Dispatcher.Invoke(() => status.Text = m), CancellationToken.None));
                status.Text = removeData ? "Tiro and everything it stored have been removed." : "Tiro has been removed. Your settings are kept in case you come back.";
            }
            catch (Exception ex)
            {
                status.Text = "Something went wrong: " + ex.Message;
            }
            w.Busy = false;
            finished = true;
            go.Content = "Close";
            go.IsEnabled = true;
        }
    }
}
