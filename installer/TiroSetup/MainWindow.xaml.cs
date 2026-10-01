using System;
using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using TiroSetup.Pages;

namespace TiroSetup
{
    public partial class MainWindow : Window
    {
        public readonly SetupContext Context;
        public bool Busy;  // an install/uninstall is running: closing asks first

        public MainWindow(SetupContext ctx, bool uninstall)
        {
            InitializeComponent();
            Context = ctx;
            VersionText.Text = "v" + ctx.Manifest.Version;
            Clipped.SizeChanged += (s, e) =>
                Clipped.Clip = new RectangleGeometry(new Rect(0, 0, Clipped.ActualWidth, Clipped.ActualHeight), 18, 18);
            if (uninstall && ctx.Existing != null) Navigate(new UninstallPage(this));
            else Navigate(new WelcomePage(this));
        }

        public void Navigate(UserControl page)
        {
            Host.Content = page;
            var ease = new CubicEase { EasingMode = EasingMode.EaseOut };
            Host.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(320)) { EasingFunction = ease });
            HostShift.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(14, 0, TimeSpan.FromMilliseconds(380)) { EasingFunction = ease });
        }

        void OnDrag(object sender, MouseButtonEventArgs e)
        {
            if (e.ButtonState == MouseButtonState.Pressed) DragMove();
        }

        void OnMinimize(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;

        void OnClose(object sender, RoutedEventArgs e) => Close();

        protected override void OnClosing(CancelEventArgs e)
        {
            if (Busy && !Confirm.Ask(this, "Stop installing?",
                    "Setup is still working. Downloads you've finished are kept, so running Setup again picks up where it left off.",
                    "Stop", "Keep going"))
            {
                e.Cancel = true;
                return;
            }
            base.OnClosing(e);
            if (Busy) Environment.Exit(0);
        }
    }
}
