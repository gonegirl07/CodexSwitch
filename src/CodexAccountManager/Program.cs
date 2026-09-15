using CodexAccountManager.Core;

namespace CodexAccountManager;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        try
        {
            var repository = new AccountRepository(AppContext.BaseDirectory);
            using var appLock = repository.AcquireLock();
            using var form = new MainForm(repository);
            if (args.Contains("--smoke-test"))
            {
                form.Shown += async (_, _) =>
                {
                    await form.Ready;
                    using var bitmap = new Bitmap(form.Width, form.Height);
                    form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height));
                    bitmap.Save(Path.Combine(AppContext.BaseDirectory, "smoke-test.png"));
                    form.Close();
                };
            }
            Application.Run(form);
        }
        catch (Exception ex)
        {
            MessageBox.Show($"Cannot open Codex Account Manager.\n\n{ex.Message}\n\nCheck folder permissions and whether another manager is open. Existing data was not reset.",
                "Codex Account Manager", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Environment.ExitCode = 1;
        }
    }
}
