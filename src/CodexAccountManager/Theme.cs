namespace CodexAccountManager;

internal static class Theme
{
    public static readonly Color Ink = Color.FromArgb(20, 44, 62);
    public static readonly Color Muted = Color.FromArgb(100, 117, 128);
    public static readonly Color Background = Color.FromArgb(243, 247, 249);
    public static readonly Color Accent = Color.FromArgb(19, 118, 105);
    public static Icon Icon => new(typeof(Theme).Assembly.GetManifestResourceStream("CodexAccountManager.app.ico")!);
    public static Image Logo => Image.FromStream(typeof(Theme).Assembly.GetManifestResourceStream("CodexAccountManager.logo.png")!);
    public static Button Button(string text, bool primary = false, int width = 100)
    {
        var button = new Button { Text = text, Width = width, Height = 34, FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Ink : Color.White, ForeColor = primary ? Color.White : Ink,
            Cursor = Cursors.Hand, Font = new Font("Segoe UI", 9.5f), Margin = new Padding(0, 0, 8, 0) };
        button.FlatAppearance.BorderColor = primary ? Ink : Color.FromArgb(219, 228, 232);
        return button;
    }
    public static void SetupDialog(Form form, string title, Size size)
    {
        form.Text = title; form.ClientSize = size; form.Font = new Font("Segoe UI", 10);
        form.BackColor = Background; form.ForeColor = Ink; form.Icon = Icon;
        form.FormBorderStyle = FormBorderStyle.FixedDialog; form.MaximizeBox = false; form.MinimizeBox = false;
        form.StartPosition = FormStartPosition.CenterParent; form.ShowInTaskbar = false;
        form.AutoScaleMode = AutoScaleMode.Dpi;
    }
    public static Label Label(string text, bool bold = false) => new()
    {
        Text = text, AutoEllipsis = true, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleLeft,
        ForeColor = bold ? Ink : Muted, Font = new Font("Segoe UI", bold ? 11 : 9.5f, bold ? FontStyle.Bold : FontStyle.Regular), Margin = Padding.Empty
    };
}
internal sealed class AccountCard : Panel
{
    public AccountCard() { DoubleBuffered = true; BackColor = Color.White; Padding = new Padding(16, 12, 16, 10); Margin = new Padding(0, 0, 14, 14); }
    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        using var pen = new Pen(Color.FromArgb(220, 229, 233));
        e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
    }
}
