namespace CodexAccountManager.Core;

public static class PathSafety
{
    public static string Canonical(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathFullyQualified(path)
            || path.StartsWith(@"\\") || path.Length < 3 || path[1] != ':')
            throw new IOException("An absolute local drive path is required.");
        if (path[2..].Contains(':') || path.Split(['\\', '/']).Any(p => p is "." or ".." || p.EndsWith(' ') || p.EndsWith('.')))
            throw new IOException("Ambiguous path or traversal is not allowed.");
        return Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
    }

    public static bool Same(string a, string b) => string.Equals(Canonical(a), Canonical(b), StringComparison.OrdinalIgnoreCase);
    public static bool Within(string path, string root) => Canonical(path).StartsWith(
        Canonical(root).TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);

    public static bool Exists(string path)
    {
        try { _ = File.GetAttributes(path); return true; }
        catch (FileNotFoundException) { return false; }
        catch (DirectoryNotFoundException) { return false; }
    }

    public static void NoReparseAncestors(string path)
    {
        var current = Canonical(path);
        while (true)
        {
            if (Exists(current) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                throw new IOException($"Unexpected reparse point: {current}");
            var parent = Path.GetDirectoryName(current);
            if (parent is null || Same(parent, current)) break;
            current = parent;
        }
    }

    public static void OrdinaryDirectory(string path)
    {
        NoReparseAncestors(path);
        if (!Directory.Exists(path)) throw new IOException($"Directory not found: {path}");
    }

    public static string Profile(string appRoot, Account account)
    {
        if (!Guid.TryParseExact(account.Id, "N", out _) || account.Id.Length != 32)
            throw new IOException("Invalid account ID.");
        var expected = Path.Combine("profiles", account.Id);
        if (!string.Equals(account.ProfilePath.Replace('/', '\\'), expected, StringComparison.Ordinal))
            throw new IOException("Profile path does not match the account ID.");
        var root = Canonical(Path.Combine(appRoot, "profiles"));
        var profile = Canonical(Path.Combine(appRoot, expected));
        if (!Within(profile, root)) throw new IOException("Profile escapes profiles directory.");
        NoReparseAncestors(profile);
        return profile;
    }
}
