using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;

namespace TiroSetup.Pages
{
    /// <summary>Small helpers so the pages read like layouts, using the styles in Theme.xaml.</summary>
    static class UI
    {
        public static T Res<T>(string key) where T : class => Application.Current.FindResource(key) as T;
        public static Style S(string key) => Res<Style>(key);
        public static Brush B(string key) => Res<Brush>(key);

        public static TextBlock Text(string text, string style = "Body", double? size = null, Brush color = null)
        {
            var t = new TextBlock { Text = text, Style = S(style), TextWrapping = TextWrapping.Wrap };
            if (size.HasValue) t.FontSize = size.Value;
            if (color != null) t.Foreground = color;
            return t;
        }

        public static Button Primary(string text, RoutedEventHandler click, double minWidth = 150)
        {
            var b = new Button { Content = text, Style = S("PrimaryButton"), MinWidth = minWidth };
            b.Click += click;
            return b;
        }

        public static Button Secondary(string text, RoutedEventHandler click)
        {
            var b = new Button { Content = text, Style = S("SecondaryButton") };
            b.Click += click;
            return b;
        }

        public static Button Link(string text, RoutedEventHandler click)
        {
            var b = new Button { Content = text, Style = S("LinkButton") };
            b.Click += click;
            return b;
        }

        public static Border Card(UIElement child, Thickness? padding = null)
        {
            return new Border { Style = S("Card"), Child = child, Padding = padding ?? new Thickness(20, 16, 20, 16) };
        }

        /// <summary>The green-edged privacy promise, shown on the first page.</summary>
        public static Border Promise(string title, string body)
        {
            var panel = new StackPanel();
            var head = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 6) };
            head.Children.Add(new Ellipse { Width = 8, Height = 8, Fill = B("OkBrush"), Margin = new Thickness(0, 1, 9, 0),
                                            VerticalAlignment = VerticalAlignment.Center });
            head.Children.Add(Text(title, "H3", 14.5));
            panel.Children.Add(head);
            panel.Children.Add(Text(body, "Muted", 13));
            return new Border
            {
                Child = panel, Padding = new Thickness(20, 16, 20, 16), CornerRadius = new CornerRadius(12),
                Background = new SolidColorBrush(Color.FromArgb(0x14, 0x5C, 0xD6, 0xA2)),
                BorderBrush = new SolidColorBrush(Color.FromArgb(0x59, 0x5C, 0xD6, 0xA2)), BorderThickness = new Thickness(1),
            };
        }

        public static Image Logo(double size)
        {
            var img = new Image { Width = size, Height = size,
                                  Source = new BitmapImage(new Uri("pack://application:,,,/Images/tiro-256.png")) };
            RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.HighQuality);
            return img;
        }

        /// <summary>A row: dot (or check) + label + value, for the "checking your PC" list.</summary>
        public static Grid Row(string label, string value, Brush dot, string valueStyle = "Body")
        {
            var g = new Grid { Margin = new Thickness(0, 5, 0, 5) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(26) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var e = new Ellipse { Width = 8, Height = 8, Fill = dot, VerticalAlignment = VerticalAlignment.Center };
            g.Children.Add(e);
            var l = Text(label, "Muted");
            Grid.SetColumn(l, 1);
            g.Children.Add(l);
            var v = Text(value, valueStyle);
            Grid.SetColumn(v, 2);
            g.Children.Add(v);
            return g;
        }

        public static DockPanel Footer(params UIElement[] right)
        {
            var d = new DockPanel { LastChildFill = false, Margin = new Thickness(0, 18, 0, 0) };
            for (var i = right.Length - 1; i >= 0; i--)
            {
                DockPanel.SetDock(right[i], Dock.Right);
                if (right[i] is FrameworkElement fe) fe.Margin = new Thickness(10, 0, 0, 0);
                d.Children.Add(right[i]);
            }
            return d;
        }

        /// <summary>Page layout: content area that stretches, footer pinned to the bottom.</summary>
        public static Grid Layout(UIElement content, UIElement footer)
        {
            var g = new Grid();
            g.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            g.Children.Add(content);
            if (footer != null)
            {
                Grid.SetRow(footer, 1);
                g.Children.Add(footer);
            }
            return g;
        }
    }
}
