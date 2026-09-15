using CodexAccountManager.Core;
using System.Diagnostics;

namespace CodexAccountManager;

public sealed class MainForm : Form
{
    private readonly AccountRepository repository;
    private readonly CodexProfileService profiles;
    private readonly CodexProcessLauncher launcher = new();
    private readonly CodexStatusService statusService = new();
    private readonly CodexQuotaService quotaService = new();
    private readonly Logger logger;
    private readonly AccountDocument accounts;
    private AppSettings settings;
    private Dependencies dependencies = new(null, null, false);
    private readonly FlowLayoutPanel cards = new() { Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(22, 4, 8, 8) };
    private readonly Label status = Theme.Label("Checking…");
    private readonly Label count = Theme.Label("");
    private readonly Panel header = new() { Dock = DockStyle.Fill };
    private readonly ToolTip tips = new();
    private bool busy;
    private readonly TaskCompletionSource ready = new();
    public Task Ready => ready.Task;

    public MainForm(AccountRepository repository)
    {
        this.repository = repository; logger = new(repository.Root); profiles = new(repository.Root, new JunctionService());
        accounts = repository.LoadAccounts(); settings = repository.LoadSettings();
        if (!File.Exists(Path.Combine(repository.Root, "accounts.json"))) repository.SaveAccounts(accounts);
        if (!File.Exists(Path.Combine(repository.Root, "settings.json"))) repository.SaveSettings(settings);
        logger.Write("startup", repository.Root);
        Text = "Codex Account Manager"; Icon = Theme.Icon; Font = new Font("Segoe UI", 9.5f);
        BackColor = Theme.Background; ForeColor = Theme.Ink; ClientSize = new Size(940, 565); MinimumSize = new Size(620, 420);
        StartPosition = FormStartPosition.CenterScreen; AutoScaleMode = AutoScaleMode.Dpi; DoubleBuffered = true;
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 88)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 34));
        var logo = new PictureBox { Image = Theme.Logo, SizeMode = PictureBoxSizeMode.Zoom, Location = new Point(24, 22), Size = new Size(44, 44) };
        var title = Theme.Label("Codex Accounts", true); title.Dock = DockStyle.None; title.Location = new Point(80, 21); title.Size = new Size(235, 27); title.Font = new Font("Segoe UI", 16, FontStyle.Bold);
        count.Dock = DockStyle.None; count.Location = new Point(82, 50); count.Size = new Size(235, 24);
        var topActions = new FlowLayoutPanel { Anchor = AnchorStyles.Top | AnchorStyles.Right, Size = new Size(294, 38), Location = new Point(ClientSize.Width - 316, 29), WrapContents = false };
        topActions.Controls.Add(ActionButton("+ Add account", AddAccount, true, 155));
        topActions.Controls.Add(ActionButton("Settings", SettingsDialog, false, 100));
        header.Width = ClientSize.Width;
        header.Controls.AddRange([logo, title, count, topActions]);
        status.Padding = new Padding(24, 0, 0, 0);
        layout.Controls.Add(header, 0, 0); layout.Controls.Add(cards, 0, 1); layout.Controls.Add(status, 0, 2);
        Controls.Add(layout);
        cards.Resize += (_, _) => ResizeCards(); RenderAccounts();
        Shown += async (_, _) =>
        {
            try
            {
                await Run(async () =>
                {
                    await RefreshDependencies();
                    foreach (var account in accounts.Accounts) CredentialConfig.EnsureFile(profiles.Validate(account, settings));
                });
            }
            finally { ready.TrySetResult(); }
        };
        FormClosing += (_, e) => { if (busy) { e.Cancel = true; status.Text = "Please wait for the current operation."; } };
        FormClosed += (_, _) => { tips.Dispose(); logo.Image?.Dispose(); };
    }

    private Button ActionButton(string text, Func<Task> action, bool primary = false, int width = 100)
    {
        var button = Theme.Button(text, primary, width);
        button.Click += async (_, _) => await Run(action); return button;
    }
    private async Task Run(Func<Task> action)
    {
        if (busy) return;
        busy = true; header.Enabled = cards.Enabled = false;
        try { await action(); }
        catch (Exception ex)
        {
            status.Text = "Operation incomplete.";
            try { logger.Write("error:" + ex.GetType().Name, exitCode: ex.HResult); }
            catch (Exception) { status.Text = "Operation incomplete; could not write the log."; }
            MessageBox.Show(this, ex.Message, "Codex Accounts", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally { busy = false; header.Enabled = cards.Enabled = true; UseWaitCursor = false; RenderAccounts(); }
    }
    private void ResizeCards()
    {
        var available = cards.ClientSize.Width - cards.Padding.Horizontal - SystemInformation.VerticalScrollBarWidth;
        var columns = available >= 790 ? 2 : 1;
        foreach (Control card in cards.Controls) card.Width = Math.Max(320, available / columns - 14);
    }
    private void RenderAccounts()
    {
        var scroll = cards.AutoScrollPosition; cards.SuspendLayout();
        foreach (Control control in cards.Controls.Cast<Control>().ToArray()) control.Dispose();
        count.Text = accounts.Accounts.Count == 1 ? "1 account" : $"{accounts.Accounts.Count} accounts";
        if (accounts.Accounts.Count == 0)
        {
            var empty = new AccountCard { Height = 145 };
            var label = Theme.Label("Add your first account", true); label.TextAlign = ContentAlignment.MiddleCenter;
            empty.Controls.Add(label); cards.Controls.Add(empty);
        }
        foreach (var account in accounts.Accounts) cards.Controls.Add(CreateCard(account));
        ResizeCards(); cards.ResumeLayout(); cards.AutoScrollPosition = new Point(-scroll.X, -scroll.Y);
    }
    private Control CreateCard(Account account)
    {
        var quotaLines = account.Quota?.Lines ?? [];
        var quotaRows = Math.Max(1, quotaLines.Count) + (account.QuotaError is not null ? 1 : 0);
        var card = new AccountCard { Height = 208 + quotaRows * 25 };
        var body = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 7, Margin = Padding.Empty };
        foreach (var height in new[] { 30, 28, 27, quotaRows * 25, 25, 29, 38 }) body.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
        var heading = Theme.Label(account.DisplayName, true); heading.Font = new Font("Segoe UI", 12, FontStyle.Bold);
        body.Controls.Add(heading, 0, 0); tips.SetToolTip(heading, account.DisplayName);
        var state = account.LoginStatus switch
        {
            "Logged in (local credentials)" => "●  Logged in", "Not logged in" => "○  Not logged in",
            "Not checked" => "○  Not checked", _ => account.LoginStatus
        };
        var login = Theme.Label(state); login.ForeColor = account.LoginStatus.StartsWith("Logged in", StringComparison.Ordinal) ? Theme.Accent : Theme.Muted;
        body.Controls.Add(login, 0, 1);
        string shared;
        try { profiles.Validate(account, settings); shared = "Shared sessions ✓"; }
        catch (Exception ex) { shared = "Check shared sessions"; tips.SetToolTip(card, ex.Message); }
        body.Controls.Add(Theme.Label(shared), 0, 2);
        var quotaPanel = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = quotaRows, Margin = Padding.Empty };
        var row = 0;
        void AddQuota(string text, Color color)
        {
            quotaPanel.RowStyles.Add(new RowStyle(SizeType.Absolute, 25));
            var label = Theme.Label(text); label.ForeColor = color; tips.SetToolTip(label, text);
            quotaPanel.Controls.Add(label, 0, row++);
        }
        if (account.QuotaError is not null)
            AddQuota(account.Quota is null ? "Quota: refresh failed" : "Cached quota · " + account.Quota.FetchedAt.ToLocalTime().ToString("dd/MM HH:mm"), Color.DarkOrange);
        if (quotaLines.Count == 0) AddQuota(account.Quota is null ? "Quota: not checked" : "Quota: no limits returned", Theme.Muted);
        foreach (var line in quotaLines) AddQuota(line.Display(), line.RemainingPercent is <= 10 ? Color.Firebrick : Theme.Accent);
        body.Controls.Add(quotaPanel, 0, 3);
        var note = Theme.Label(string.IsNullOrEmpty(account.Note) ? "—" : account.Note); tips.SetToolTip(note, account.Note); body.Controls.Add(note, 0, 4);
        body.Controls.Add(Theme.Label("Updated: " + (account.LastCheckedAt?.ToLocalTime().ToString("dd/MM HH:mm") ?? "—") + "    ·    " + account.Id[..8]), 0, 5);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Margin = Padding.Empty };
        actions.Controls.Add(ActionButton("Open", () => Launch(account, CodexAction.Open), true, 102));
        actions.Controls.Add(ActionButton("Refresh", () => Check(account), false, 102));
        actions.Controls.Add(ActionButton("Apply", () => Apply(account), false, 70));
        var more = Theme.Button("•••", width: 42); var menu = new ContextMenuStrip();
        void Item(string text, Func<Task> action) { var item = menu.Items.Add(text); item.Click += async (_, _) => await Run(action); }
        Item("Log in again", () => Launch(account, CodexAction.Login)); Item("Resume", () => Launch(account, CodexAction.Resume));
        Item("Open folder", () => OpenFolder(account));
        Item("Details", () =>
        {
            using var dialog = new AccountDetailsForm(account, PathSafety.Profile(repository.Root, account), Path.Combine(settings.DefaultCodexHome, "sessions"));
            if (dialog.ShowDialog(this) != DialogResult.OK) return Task.CompletedTask;
            var oldName = account.DisplayName; var oldNote = account.Note;
            account.DisplayName = dialog.AccountName; account.Note = dialog.AccountNote;
            try { repository.SaveAccounts(accounts); }
            catch { account.DisplayName = oldName; account.Note = oldNote; throw; }
            status.Text = "Account updated.";
            return Task.CompletedTask;
        });
        menu.Items.Add(new ToolStripSeparator()); Item("Delete account", () => Delete(account));
        more.Click += (_, _) => menu.Show(more, new Point(0, more.Height)); more.Disposed += (_, _) => menu.Dispose();
        actions.Controls.Add(more); body.Controls.Add(actions, 0, 6); card.Controls.Add(body); return card;
    }
    private async Task RefreshDependencies()
    {
        dependencies = await DependencyDetector.DetectAsync();
        status.Text = $"Codex {(dependencies.Codex is null ? "not installed" : "ready")}   ·   PowerShell {(dependencies.PowerShell is null ? "not installed" : dependencies.PowerShellMajor + (dependencies.PowerShellMajor < 7 ? " · UTF-8" : ""))}";
    }
    private async Task AddAccount()
    {
        using var dialog = new AddAccountForm(); if (dialog.ShowDialog(this) != DialogResult.OK) return;
        if (!dialog.CopyDefaultAccount) dependencies.Require();
        var account = profiles.Create(dialog.AccountName, dialog.AccountNote, settings, dialog.CopyDefaultAccount);
        accounts.Accounts.Add(account);
        try { repository.SaveAccounts(accounts); } catch { accounts.Accounts.Remove(account); throw; }
        logger.Write(dialog.CopyDefaultAccount ? "create:copy-default" : "create:login", PathSafety.Profile(repository.Root, account));
        if (dialog.CopyDefaultAccount)
        {
            status.Text = "Added account from default Codex.";
            if (dependencies.Codex is not null && dependencies.PowerShell is not null) await Check(account);
        }
        else await Launch(account, CodexAction.Login);
    }
    private Task Launch(Account account, CodexAction action)
    {
        var path = profiles.Validate(account, settings);
        CredentialConfig.EnsureFile(path);
        var workingDirectory = settings.WorkingDirectory;
        if (action == CodexAction.Open)
        {
            using var picker = new ProjectPickerForm(profiles.SharedTarget(settings), account.DisplayName);
            if (picker.ShowDialog(this) != DialogResult.OK || picker.SelectedDirectory is null) return Task.CompletedTask;
            workingDirectory = picker.SelectedDirectory;
        }
        launcher.Launch(dependencies, account.Id, path, workingDirectory, action);
        logger.Write("launch:" + action, path); status.Text = "Opened " + account.DisplayName; return Task.CompletedTask;
    }
    private async Task Check(Account account)
    {
        await RefreshDependencies(); var path = profiles.Validate(account, settings); CredentialConfig.EnsureFile(path); status.Text = "Refreshing " + account.DisplayName + "…";
        account.LoginStatus = await statusService.CheckAsync(dependencies, path); account.LastCheckedAt = DateTimeOffset.UtcNow;
        try { account.Quota = await quotaService.ReadAsync(dependencies, path); account.QuotaError = null; }
        catch (Exception ex) when (ex is IOException or TimeoutException or System.Text.Json.JsonException)
        {
            account.QuotaError = ex is TimeoutException ? "Quota request timed out" : "Could not read quota; check login and connection";
        }
        repository.SaveAccounts(accounts); logger.Write("check", path); status.Text = "Updated " + account.DisplayName;
        if (account.QuotaError is not null) status.Text = account.QuotaError;
    }
    private Task Delete(Account account)
    {
        var path = profiles.Validate(account, settings);
        if (launcher.IsRunning(account.Id)) throw new IOException("Close this account's terminals before deleting it.");
        if (MessageBox.Show(this, $"Delete {account.DisplayName}?\n\n{path}\n\nAuthentication, config and private data will be deleted.\nDefault Codex shared sessions will NOT be deleted.\n\nClose all terminals for this account before continuing.",
            "Delete account", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return Task.CompletedTask;
        profiles.Delete(account, settings); accounts.Accounts.Remove(account);
        try { repository.SaveAccounts(accounts); } catch { accounts.Accounts.Add(account); throw; }
        logger.Write("delete", path); status.Text = "Account deleted; shared sessions preserved."; return Task.CompletedTask;
    }
    private Task OpenFolder(Account account)
    {
        var path = PathSafety.Profile(repository.Root, account); PathSafety.OrdinaryDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe") { UseShellExecute = true, ArgumentList = { path } }); return Task.CompletedTask;
    }
    private Task Apply(Account account)
    {
        if (launcher.IsRunning(account.Id)) throw new IOException("Close this account's terminals before Apply to prevent concurrent authentication updates.");
        if (MessageBox.Show(this, $"Apply {account.DisplayName}'s login to default Codex?\n\nClose Codex App and terminals using the default login before continuing. Reopen Codex App after Apply.\n\nTarget: {settings.DefaultCodexHome}\nOnly auth.json is replaced; config and sessions are preserved.",
            "Apply login", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) != DialogResult.Yes) return Task.CompletedTask;
        profiles.ApplyAuthentication(account, settings);
        logger.Write("apply-auth", settings.DefaultCodexHome);
        status.Text = "Applied " + account.DisplayName + ". Reopen Codex App to use this login.";
        return Task.CompletedTask;
    }
    private async Task SettingsDialog()
    {
        await RefreshDependencies(); using var dialog = new AdvancedSettingsForm(settings, dependencies);
        if (dialog.ShowDialog(this) != DialogResult.OK) return;
        var updated = dialog.Settings;
        if (accounts.Accounts.Count > 0 && !PathSafety.Same(updated.DefaultCodexHome, settings.DefaultCodexHome))
            throw new IOException("Safely delete managed accounts before changing the default Codex Home.");
        _ = profiles.SharedTarget(updated); PathSafety.OrdinaryDirectory(updated.WorkingDirectory);
        repository.SaveSettings(updated); settings = updated; status.Text = "Settings saved.";
    }
}
