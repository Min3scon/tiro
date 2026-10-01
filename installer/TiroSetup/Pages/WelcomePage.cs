using System.Windows;
using System.Windows.Controls;

namespace TiroSetup.Pages
{
    public sealed class WelcomePage : UserControl
    {
        public const string PrivacyTitle = "Everything stays on this computer";
        public const string PrivacyBody =
            "No audio, text or history is ever sent anywhere. Speech recognition, corrections and learning all run " +
            "on your PC. Tiro learns the names and words you use from what you dictate and keeps that here too: you " +
            "can switch learning off or clear it with one click, and it never learns from password fields. " +
            "The only downloads are Tiro itself and its models.";

        public WelcomePage(MainWindow w)
        {
            var stack = new StackPanel { VerticalAlignment = VerticalAlignment.Center, MaxWidth = 640,
                                         HorizontalAlignment = HorizontalAlignment.Left };
            stack.Children.Add(UI.Logo(64));
            stack.Children.Add(new Border { Height = 18 });
            var updating = w.Context.Existing != null;
            stack.Children.Add(UI.Text(updating ? $"Update Tiro to {w.Context.Manifest.Version}" : "Speak. Tiro types.", "H1"));
            stack.Children.Add(new Border { Height = 10 });
            stack.Children.Add(UI.Text(updating
                ? "Your settings, dictionary and everything Tiro has learned are kept."
                : "Hold a key, talk, let go: your words appear wherever your cursor is, in any app. Setup checks your " +
                  "PC, picks the fastest way to run Tiro on it, and takes a few minutes.", "Muted", 15));
            stack.Children.Add(new Border { Height = 26 });
            stack.Children.Add(UI.Promise(PrivacyTitle, PrivacyBody));
            Content = UI.Layout(stack, UI.Footer(UI.Primary(updating ? "Continue" : "Get started",
                (s, e) => w.Navigate(new CheckPage(w)))));
        }
    }
}
