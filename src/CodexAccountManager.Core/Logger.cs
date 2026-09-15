namespace CodexAccountManager.Core;

public sealed class Logger(string root)
{
    // Callers pass only operation identifiers, paths, exception types and numerical codes.
    public void Write(string operation, string? path = null, int? exitCode = null)
    {
        var directory = Path.Combine(root, "logs");
        PathSafety.NoReparseAncestors(directory);
        Directory.CreateDirectory(directory);
        var file = Path.Combine(directory, "app.log");
        PathSafety.NoReparseAncestors(file);
        if (File.Exists(file) && new FileInfo(file).Length > 1024 * 1024)
            File.WriteAllText(file, "");
        File.AppendAllText(file, $"{DateTimeOffset.UtcNow:O} {operation} {path?.Replace('\r', ' ').Replace('\n', ' ')} exit={exitCode}\n");
    }
}
