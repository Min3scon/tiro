using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Effects;

namespace TiroSetup
{
    /// <summary>A small modal question in Setup's style.</summary>
    public static class Confirm
    {
        public static bool Ask(Window owner, string title, string message, string yes, string no)
        {
            var result = false;
            var dlg = new Window
            {
                Owner = owner,
                WindowStyle = WindowStyle.None,
                AllowsTransparency = true,
                Background = Brushes.Transparent,
                ResizeMode = ResizeMode.NoResize,
                SizeToContent = SizeToContent.WidthAndHeight,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ShowInTaskbar = false,
            };
            var panel = new StackPanel { Width = 380 };
            panel.Children.Add(new TextBlock { Text = title, Style = (Style)Application.Current.FindResource("H2"), FontSize = 18 });
            panel.Children.Add(new TextBlock
            {
                Text = message,
                Style = (Style)Application.Current.FindResource("Muted"),
                Margin = new Thickness(0, 8, 0, 20),
            });
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
            var noBtn = new Button { Content = no, Style = (Style)Application.Current.FindResource("SecondaryButton"), IsCancel = true };
            var yesBtn = new Button
            {
                Content = yes,
                Style = (Style)Application.Current.FindResource("PrimaryButton"),
                Margin = new Thickness(10, 0, 0, 0),
                MinWidth = 100,
            };
            noBtn.Click += (s, e) => dlg.Close();
            yesBtn.Click += (s, e) => { result = true; dlg.Close(); };
            buttons.Children.Add(noBtn);
            buttons.Children.Add(yesBtn);
            panel.Children.Add(buttons);
            dlg.Content = new Border
            {
                Margin = new Thickness(16),
                Padding = new Thickness(24, 22, 24, 20),
                CornerRadius = new CornerRadius(14),
                Background = (Brush)Application.Current.FindResource("Ink850Brush"),
                BorderBrush = (Brush)Application.Current.FindResource("LineStrongBrush"),
                BorderThickness = new Thickness(1),
                Effect = new DropShadowEffect { BlurRadius = 24, ShadowDepth = 6, Direction = 270, Opacity = 0.6 },
                Child = panel,
            };
            dlg.ShowDialog();
            return result;
        }
    }
}
