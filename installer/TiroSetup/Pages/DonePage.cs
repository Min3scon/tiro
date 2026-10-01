using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using TiroSetup.Services;

namespace TiroSetup.Pages
{
    /// <summary>Installed, with proof (the self-test's real numbers), then hands over to Tiro's own setup:
    /// AI helper, microphone, shortcut, learning, your words, and a live test.</summary>
    public sealed class DonePage : UserControl
    {
        public DonePage(MainWindow w)
        {
            var e = w.Context.Engine;
            var t = e?.TestResult;
            var root = new StackPanel { MaxWidth = 700, HorizontalAlignment = HorizontalAlignment.Left,
                                        VerticalAlignment = VerticalAlignment.Center };
            root.Children.Add(UI.Logo(56));
            root.Children.Add(new Border { Height = 16 });
            var ok = t != null && t.Ok;
            root.Children.Add(UI.Text(ok ? "Tiro is installed" : "Tiro is installed, with a problem", "H1"));
            root.Children.Add(new Border { Height = 10 });
            var proof = new StackPanel();
            if (ok)
            {
                proof.Children.Add(UI.Row("Speech test", $"A {t.AudioSeconds:0}-second recording understood in " +
                                                           $"{t.DecodeMs / 1000.0:0.00} s on {t.DeviceLabel}", UI.B("OkBrush")));
                proof.Children.Add(UI.Row("It heard", "“" + t.Text + "”", UI.B("OkBrush"), "Muted"));
                if (e.FellBackToCpu)
                    proof.Children.Add(UI.Row("Note", "The graphics card didn't start, so Tiro uses the processor for now. " +
                                                      (t.FallbackReason ?? ""), UI.B("EmberBrush")));
            }
            else
            {
                proof.Children.Add(UI.Row("Speech test", t?.Error ?? "The self-test didn't run.", UI.B("DangerBrush")));
            }
            root.Children.Add(UI.Card(proof));
            root.Children.Add(new Border { Height = 18 });
            root.Children.Add(UI.Text("Next, Tiro opens its own short setup: it fetches and tests its AI helper, then you pick " +
                                      "your microphone and shortcut, choose whether it learns from what you say, add any " +
                                      "special words, and try it out.", "Muted", 14.5));
            var open = UI.Primary("Open Tiro", (s, a) =>
            {
                Process.Start(new ProcessStartInfo(e.TiroExe, "--setup --installed") { UseShellExecute = true });
                Application.Current.Shutdown();
            });
            Content = UI.Layout(root, UI.Footer(open));
        }
    }
}
