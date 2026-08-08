using System.Text.Json;
using LedgerRing_WinUI.Helpers;
using LedgerRing_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Windows.Storage;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace LedgerRing_WinUI.Pages;

public sealed partial class DataPage : Page
{
    public DataPage()
    {
        InitializeComponent();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        await LoadAsync();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await LoadAsync();

    private async Task LoadAsync()
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();

            var info = await api.GetSystemInfoAsync();
            EngineText.Text =
                $"Engine v{JsonUi.Str(info, "version")} · Grok {(info.TryGetProperty("grok_enabled", out var g) && g.GetBoolean() ? "on" : "off")} · " +
                $"Plaid {(info.TryGetProperty("plaid_enabled", out var p) && p.GetBoolean() ? "on" : "off")}";

            var st = await api.GetBackupStatusAsync();
            DbPathText.Text = JsonUi.Str(st, "db_path");
            DbMetaText.Text =
                $"Size {JsonUi.Str(st, "db_size_mb")} MB ({JsonUi.Str(st, "db_size_bytes")} bytes) · " +
                $"backups dir {JsonUi.Str(st, "backups_dir")} · count {JsonUi.Str(st, "backup_count")}";

            if (st.TryGetProperty("schedule", out var sch) && sch.ValueKind == JsonValueKind.Object)
                ApplySchedule(sch);
            else
            {
                try
                {
                    ApplySchedule(await api.GetBackupScheduleAsync());
                }
                catch
                {
                    ScheduleMeta.Text = "Schedule unavailable (update engine).";
                }
            }

            var rows = new List<BackupRow>();
            if (st.TryGetProperty("backups", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                foreach (var b in arr.EnumerateArray())
                {
                    var name = JsonUi.Str(b, "name");
                    var size = JsonUi.Int(b, "size_bytes");
                    var created = JsonUi.Str(b, "created");
                    rows.Add(new BackupRow(name, $"{created} · {size / 1024.0:0.0} KB"));
                }
            }
            BackupList.ItemsSource = rows;
            MsgText.Text = rows.Count == 0 ? "No backups yet — create one." : $"{rows.Count} recent backups.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private void ApplySchedule(JsonElement sch)
    {
        AutoOnBox.IsChecked = !sch.TryGetProperty("enabled", out var en) || en.GetBoolean();
        if (sch.TryGetProperty("interval_hours", out var ih) && ih.TryGetInt32(out var hours))
            IntervalBox.Value = hours;
        if (sch.TryGetProperty("keep", out var k) && k.TryGetInt32(out var keep))
            KeepBox.Value = keep;
        var due = sch.TryGetProperty("due_now", out var d) && d.GetBoolean();
        ScheduleMeta.Text =
            $"Last: {JsonUi.Str(sch, "last_at", "never")} · next: {JsonUi.Str(sch, "next_due_at", "—")} · " +
            (due ? "DUE NOW" : "not due");
    }

    private async void SaveSchedule_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PutBackupScheduleAsync(new
            {
                enabled = AutoOnBox.IsChecked == true,
                interval_hours = double.IsNaN(IntervalBox.Value) ? 24 : (int)IntervalBox.Value,
                keep = double.IsNaN(KeepBox.Value) ? 14 : (int)KeepBox.Value,
                run_now = false,
            });
            if (res.TryGetProperty("schedule", out var sch))
                ApplySchedule(sch);
            MsgText.Text = "Auto-backup schedule saved.";
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void RunAuto_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.PutBackupScheduleAsync(new
            {
                enabled = AutoOnBox.IsChecked == true,
                interval_hours = double.IsNaN(IntervalBox.Value) ? 24 : (int)IntervalBox.Value,
                keep = double.IsNaN(KeepBox.Value) ? 14 : (int)KeepBox.Value,
                run_now = true,
            });
            if (res.TryGetProperty("schedule", out var sch))
                ApplySchedule(sch);
            var ran = res.TryGetProperty("ran", out var r) && r.ValueKind != JsonValueKind.Null
                ? r.GetRawText()
                : "null";
            MsgText.Text = "Auto-backup run: " + ran;
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void Create_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var note = string.IsNullOrWhiteSpace(NoteBox.Text) ? null : NoteBox.Text.Trim();
            var res = await api.CreateBackupAsync(true, note);
            MsgText.Text = $"Created {JsonUi.Str(res, "name")} ({JsonUi.Str(res, "size_bytes")} bytes)";
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void DownloadLive_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var bytes = await api.DownloadLiveBackupAsync();
            await SaveBytesAsync(bytes, $"ledger_live_{DateTime.Now:yyyyMMdd_HHmmss}.zip");
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void DownloadNamed_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not string name) return;
        ErrorBar.IsOpen = false;
        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var bytes = await api.DownloadBackupAsync(name);
            await SaveBytesAsync(bytes, name);
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void RestoreNamed_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn || btn.Tag is not string name) return;
        ErrorBar.IsOpen = false;
        var confirm = new ContentDialog
        {
            Title = "Restore backup?",
            Content = $"Replace the live database with:\n{name}\n\nA safety backup is created first. Restart the app after.",
            PrimaryButtonText = "Restore",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = XamlRoot,
        };
        if (await confirm.ShowAsync() != ContentDialogResult.Primary) return;

        try
        {
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RestoreBackupAsync(name);
            MsgText.Text =
                $"Staged restore from {JsonUi.Str(res, "restored_from")}. Safety: {JsonUi.Str(res, "safety_backup")}";
            WarnBar.Title = "Restart required";
            WarnBar.Message = JsonUi.Str(res, "hint") + " Restarting engine…";
            WarnBar.IsOpen = true;
            if (App.Backend is not null)
            {
                var ok = await App.Backend.RestartAsync();
                WarnBar.Message = ok
                    ? "Engine restarted — staged restore applied on startup."
                    : ("Restart failed: " + (App.Backend.LastError ?? "unknown") +
                       " — stop other processes on :7420 and restart app.");
            }
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async void RestoreUpload_Click(object sender, RoutedEventArgs e)
    {
        ErrorBar.IsOpen = false;
        try
        {
            var picker = new FileOpenPicker();
            picker.FileTypeFilter.Add(".zip");
            picker.FileTypeFilter.Add(".db");
            picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
            var window = App.MainWindowInstance ?? throw new InvalidOperationException("No window");
            InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(window));
            var file = await picker.PickSingleFileAsync();
            if (file is null) return;

            var confirm = new ContentDialog
            {
                Title = "Restore from file?",
                Content = $"Upload and restore:\n{file.Name}\n\nSafety backup of current DB is created first.",
                PrimaryButtonText = "Restore",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = XamlRoot,
            };
            if (await confirm.ShowAsync() != ContentDialogResult.Primary) return;

            using var stream = await file.OpenStreamForReadAsync();
            using var api = new LedgerApiClient();
            await api.EnsureBackendAsync();
            var res = await api.RestoreBackupUploadAsync(stream, file.Name);
            MsgText.Text = $"Staged restore from upload. Safety: {JsonUi.Str(res, "safety_backup")}";
            WarnBar.Title = "Restart required";
            WarnBar.IsOpen = true;
            if (App.Backend is not null)
            {
                var ok = await App.Backend.RestartAsync();
                WarnBar.Message = ok
                    ? "Engine restarted — staged restore applied."
                    : ("Restart failed: " + (App.Backend.LastError ?? "unknown"));
            }
            else
                WarnBar.Message = JsonUi.Str(res, "hint");
            await LoadAsync();
        }
        catch (Exception ex)
        {
            ErrorBar.Message = ex.Message;
            ErrorBar.IsOpen = true;
        }
    }

    private async Task SaveBytesAsync(byte[] bytes, string suggestedName)
    {
        var picker = new FileSavePicker();
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;
        if (suggestedName.EndsWith(".db", StringComparison.OrdinalIgnoreCase))
            picker.FileTypeChoices.Add("SQLite", new List<string> { ".db" });
        else
            picker.FileTypeChoices.Add("ZIP archive", new List<string> { ".zip" });
        picker.SuggestedFileName = Path.GetFileNameWithoutExtension(suggestedName);
        var window = App.MainWindowInstance ?? throw new InvalidOperationException("No window");
        InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(window));
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;
        await FileIO.WriteBytesAsync(file, bytes);
        MsgText.Text = $"Saved → {file.Path}";
    }

    private sealed record BackupRow(string Name, string Meta);
}
