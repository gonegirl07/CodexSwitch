using CodexAccountManager.Core;
using System.Diagnostics;

namespace CodexAccountManager;

public sealed class AdvancedSettingsForm : Form
{
    private readonly TextBox home = new() { Dock = DockStyle.Fill };
    private readonly TextBox workingDirectory = new() { Dock = DockStyle.Fill };
    public AppSettings Settings => new() { DefaultCodexHome = PathSafety.Canonical(home.Text), WorkingDirectory = PathSafety.Canonical(workingDirectory.Text) };
    public AdvancedSettingsForm(AppSettings settings, Dependencies dependencies)
    {
        Theme.SetupDialog(this, "Advanced settings", new Size(580, 340));
        home.Text = settings.DefaultCodexHome; workingDirectory.Text = settings.WorkingDirectory;
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(22, 14, 22, 14), ColumnCount = 1, RowCount = 9 };
        foreach (var height in new[] { 25, 36, 25, 40, 30, 30, 33, 18, 42 }) layout.RowStyles.Add(new RowStyle(SizeType.Absolute, height));
        layout.Controls.Add(Theme.Label("Default Codex Home"), 0, 0); layout.Controls.Add(home, 0, 1);
        layout.Controls.Add(Theme.Label("Working directory"), 0, 2); layout.Controls.Add(workingDirectory, 0, 3);
        layout.Controls.Add(new TextBox { Text = "Codex: " + (dependencies.Codex ?? "Not found"), ReadOnly = true, BorderStyle = BorderStyle.None, Dock = DockStyle.Fill, BackColor = Theme.Background }, 0, 4);
        layout.Controls.Add(new TextBox { Text = "PowerShell: " + (dependencies.PowerShell ?? "Not found"), ReadOnly = true, BorderStyle = BorderStyle.None, Dock = DockStyle.Fill, BackColor = Theme.Background }, 0, 5);
        var download = new LinkLabel { Text = "Download .NET 8 Desktop Runtime · Windows x64", AutoSize = true, LinkColor = Theme.Accent };
        download.LinkClicked += (_, _) =>
        {
            try { Process.Start(new ProcessStartInfo("https://dotnet.microsoft.com/en-us/download/dotnet/8.0") { UseShellExecute = true }); }
            catch (Exception) { MessageBox.Show(this, "Could not open the browser. Visit dotnet.microsoft.com/download/dotnet/8.0."); }
        };
        layout.Controls.Add(download, 0, 6);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft, WrapContents = false };
        var save = Theme.Button("Save", true); save.DialogResult = DialogResult.OK;
        var cancel = Theme.Button("Cancel"); cancel.DialogResult = DialogResult.Cancel;
        actions.Controls.AddRange([save, cancel]); layout.Controls.Add(actions, 0, 8);
        Controls.Add(layout); AcceptButton = save; CancelButton = cancel;
    }
}
