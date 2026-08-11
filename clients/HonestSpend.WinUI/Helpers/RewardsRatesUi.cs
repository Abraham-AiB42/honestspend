using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace HonestSpend_WinUI.Helpers;

/// <summary>
/// Optional rewards-rate chips + number boxes for credit cards.
/// Categories match API: gas, groceries, restaurants, amazon, general, home_improvement.
/// </summary>
public sealed class RewardsRatesUi
{
    public const string HeaderText = "Rewards (optional)";

    private static readonly (string Key, string Label)[] Categories =
    {
        ("gas", "Gas %"),
        ("groceries", "Groceries %"),
        ("restaurants", "Dining %"),
        ("amazon", "Amazon %"),
        ("home_improvement", "Home improvement %"),
        ("general", "Everything else %"),
    };

    private static readonly (string Label, Dictionary<string, decimal> Rates)[] Presets =
    {
        ("None / skip", new Dictionary<string, decimal>()),
        ("Gas 5% / everything 1%", new Dictionary<string, decimal> { ["gas"] = 5, ["general"] = 1 }),
        ("Amazon 5%", new Dictionary<string, decimal> { ["amazon"] = 5, ["general"] = 1 }),
        ("Home Depot 5%", new Dictionary<string, decimal> { ["home_improvement"] = 5, ["general"] = 1 }),
        ("Groceries 3% / dining 3%", new Dictionary<string, decimal> { ["groceries"] = 3, ["restaurants"] = 3, ["general"] = 1 }),
        ("Custom…", new Dictionary<string, decimal>()),
    };

    private static readonly (string Label, string Key, decimal Rate)[] Chips =
    {
        ("Gas 5%", "gas", 5),
        ("Groceries 3%", "groceries", 3),
        ("Dining 3%", "restaurants", 3),
        ("Amazon 5%", "amazon", 5),
        ("Everything 1%", "general", 1),
        ("Home improvement 5%", "home_improvement", 5),
    };

    private readonly Dictionary<string, NumberBox> _boxes = new(StringComparer.OrdinalIgnoreCase);
    private ComboBox? _presetBox;
    private bool _suppressPreset;

    public FrameworkElement Root { get; private set; } = null!;

    public static RewardsRatesUi Build(bool compact = false)
    {
        var ui = new RewardsRatesUi();
        ui.Root = ui.BuildCore(compact);
        return ui;
    }

    private FrameworkElement BuildCore(bool compact)
    {
        var panel = new StackPanel { Spacing = compact ? 8 : 10 };

        panel.Children.Add(new TextBlock
        {
            Text = HeaderText,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            Margin = new Thickness(0, compact ? 4 : 8, 0, 0),
        });
        panel.Children.Add(new TextBlock
        {
            Text = "Percent cash-back / points by category — used when picking which card to charge.",
            Opacity = 0.7,
            TextWrapping = TextWrapping.Wrap,
            FontSize = 12,
        });

        _presetBox = new ComboBox
        {
            Header = "Preset",
            HorizontalAlignment = HorizontalAlignment.Stretch,
            MinWidth = 220,
        };
        foreach (var (label, _) in Presets)
            _presetBox.Items.Add(new ComboBoxItem { Content = label });
        _presetBox.SelectedIndex = 0;
        _presetBox.SelectionChanged += Preset_SelectionChanged;
        panel.Children.Add(_presetBox);

        // Two-row chip flow (no WrapPanel required)
        var chipPanel = new StackPanel { Spacing = 6 };
        var row1 = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        var row2 = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        for (var i = 0; i < Chips.Length; i++)
        {
            var (label, key, rate) = Chips[i];
            var btn = new Button
            {
                Content = label,
                Tag = $"{key}:{rate}",
                Padding = new Thickness(10, 4, 10, 4),
                MinWidth = 0,
            };
            btn.Click += Chip_Click;
            (i < 3 ? row1 : row2).Children.Add(btn);
        }
        chipPanel.Children.Add(row1);
        chipPanel.Children.Add(row2);
        panel.Children.Add(chipPanel);

        var grid = new Grid { ColumnSpacing = 10, RowSpacing = 6 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        for (var i = 0; i < (Categories.Length + 1) / 2; i++)
            grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        for (var i = 0; i < Categories.Length; i++)
        {
            var (key, label) = Categories[i];
            var box = new NumberBox
            {
                Header = label,
                Minimum = 0,
                Maximum = 100,
                SmallChange = 0.5,
                LargeChange = 1,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact,
                PlaceholderText = "—",
            };
            // Leave empty (NaN) so skip works
            box.Value = double.NaN;
            box.ValueChanged += (_, _) =>
            {
                if (_suppressPreset) return;
                MarkCustom();
            };
            _boxes[key] = box;
            Grid.SetRow(box, i / 2);
            Grid.SetColumn(box, i % 2);
            grid.Children.Add(box);
        }
        panel.Children.Add(grid);

        return panel;
    }

    private void Preset_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressPreset || _presetBox is null) return;
        var idx = _presetBox.SelectedIndex;
        if (idx < 0 || idx >= Presets.Length) return;
        var rates = Presets[idx].Rates;
        if (idx == Presets.Length - 1)
            return; // Custom — keep current numbers
        ApplyRates(rates, markPreset: false);
    }

    private void Chip_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { Tag: string tag }) return;
        var parts = tag.Split(':', 2);
        if (parts.Length != 2) return;
        var key = parts[0];
        if (!decimal.TryParse(parts[1], System.Globalization.NumberStyles.Any,
                System.Globalization.CultureInfo.InvariantCulture, out var rate))
            return;
        if (!_boxes.TryGetValue(key, out var box)) return;
        _suppressPreset = true;
        box.Value = (double)rate;
        MarkCustom();
        _suppressPreset = false;
    }

    private void MarkCustom()
    {
        if (_presetBox is null) return;
        _suppressPreset = true;
        _presetBox.SelectedIndex = Presets.Length - 1; // Custom…
        _suppressPreset = false;
    }

    public void ApplyRates(IReadOnlyDictionary<string, decimal> rates, bool markPreset = true)
    {
        _suppressPreset = true;
        foreach (var (key, box) in _boxes)
        {
            if (rates.TryGetValue(key, out var v))
                box.Value = (double)v;
            else
                box.Value = double.NaN;
        }

        if (markPreset && _presetBox is not null)
        {
            var match = -1;
            for (var i = 0; i < Presets.Length - 1; i++)
            {
                if (RatesEqual(Presets[i].Rates, rates))
                {
                    match = i;
                    break;
                }
            }
            _presetBox.SelectedIndex = match >= 0 ? match : (rates.Count == 0 ? 0 : Presets.Length - 1);
        }
        _suppressPreset = false;
    }

    public void ApplyFromStrings(IReadOnlyDictionary<string, string> rates)
    {
        var map = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
        foreach (var (k, v) in rates)
        {
            if (decimal.TryParse(v, System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out var d))
                map[k] = d;
        }
        ApplyRates(map);
    }

    /// <summary>Non-empty category → percent map. Empty if user skipped or all blank.</summary>
    public Dictionary<string, decimal> CollectRates()
    {
        var map = new Dictionary<string, decimal>(StringComparer.OrdinalIgnoreCase);
        if (_presetBox?.SelectedIndex == 0)
            return map; // explicit None / skip

        foreach (var (key, box) in _boxes)
        {
            if (double.IsNaN(box.Value) || box.Value < 0) continue;
            map[key] = (decimal)box.Value;
        }
        return map;
    }

    public bool HasRates() => CollectRates().Count > 0;

    private static bool RatesEqual(IReadOnlyDictionary<string, decimal> a, IReadOnlyDictionary<string, decimal> b)
    {
        if (a.Count != b.Count) return false;
        foreach (var (k, v) in a)
        {
            if (!b.TryGetValue(k, out var bv) || bv != v) return false;
        }
        return true;
    }
}
