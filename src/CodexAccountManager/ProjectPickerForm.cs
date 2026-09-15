using CodexAccountManager.Core;

namespace CodexAccountManager;

public sealed class ProjectPickerForm : Form
{
    private readonly TextBox search = new() { Dock = DockStyle.Fill, PlaceholderText = "Search project folders…" };
    private readonly ListBox projects = new() { Dock = DockStyle.Fill, IntegralHeight = false, BorderStyle = BorderStyle.FixedSingle, HorizontalScrollbar = true };
    private readonly Label status = Theme.Label("Loading projects…");
    private readonly Button open = Theme.Button("Open", true);
    private readonly CancellationTokenSource cancellation = new();
    private readonly string sessionsDirectory;
    private bool resourcesDisposed;
    private IReadOnlyList<SessionProject> catalog = [];
    public string? SelectedDirectory { get; private set; }
    public Task Ready { get; private set; } = Task.CompletedTask;
    public ProjectPickerForm(string sessionsDirectory, string accountName)
    {
        this.sessionsDirectory = sessionsDirectory;
        Theme.SetupDialog(this, "Open project · " + accountName, new Size(660, 410));
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22, 16, 22, 16), ColumnCount = 1, RowCount = 4 };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38)); layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 32)); layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft, WrapContents = false };
        var cancel = Theme.Button("Cancel"); cancel.DialogResult = DialogResult.Cancel;
        var browse = Theme.Button("Browse…", width: 140);
        actions.Controls.AddRange([open, cancel, browse]);
        layout.Controls.Add(search, 0, 0); layout.Controls.Add(projects, 0, 1); layout.Controls.Add(status, 0, 2); layout.Controls.Add(actions, 0, 3);
        Controls.Add(layout); AcceptButton = open; CancelButton = cancel; open.Enabled = false;
        search.TextChanged += (_, _) => Filter(); projects.SelectedIndexChanged += (_, _) => open.Enabled = projects.SelectedItem is SessionProject { Exists: true };
        open.Click += (_, _) => SelectProject(); projects.DoubleClick += (_, _) => SelectProject();
        browse.Click += (_, _) =>
        {
            using var dialog = new FolderBrowserDialog { Description = "Select project folder", UseDescriptionForTitle = true };
            if (dialog.ShowDialog(this) == DialogResult.OK) Finish(dialog.SelectedPath);
        };
        Shown += (_, _) => Ready = LoadProjects();
        FormClosing += (_, _) => { if (!resourcesDisposed) cancellation.Cancel(); };
    }
    private async Task LoadProjects()
    {
        try
        {
            var token = cancellation.Token;
            var result = await Task.Run(() => new SessionProjectCatalog().Read(sessionsDirectory, token));
            if (IsDisposed || cancellation.IsCancellationRequested) return;
            catalog = result.Projects; Filter();
            status.Text = $"{catalog.Count} folders from default sessions" + (result.SkippedFiles > 0 ? $" · Skipped {result.SkippedFiles} unreadable items" : "");
        }
        catch (OperationCanceledException) { }
        catch (Exception) { if (!IsDisposed) status.Text = "Could not read default sessions. Browse for a folder instead."; }
    }
    private void Filter()
    {
        projects.BeginUpdate(); projects.Items.Clear();
        foreach (var project in catalog.Where(p => p.Directory.Contains(search.Text.Trim(), StringComparison.OrdinalIgnoreCase))) projects.Items.Add(project);
        if (projects.Items.Count > 0) projects.SelectedIndex = 0;
        else open.Enabled = false;
        projects.EndUpdate();
    }
    private void SelectProject()
    {
        if (projects.SelectedItem is SessionProject { Exists: true } project) Finish(project.Directory);
    }
    private void Finish(string path)
    {
        try { PathSafety.OrdinaryDirectory(path); SelectedDirectory = PathSafety.Canonical(path); DialogResult = DialogResult.OK; }
        catch (IOException ex) { MessageBox.Show(this, ex.Message, "Cannot open folder", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing && !resourcesDisposed) { resourcesDisposed = true; cancellation.Cancel(); cancellation.Dispose(); }
        base.Dispose(disposing);
    }
}
