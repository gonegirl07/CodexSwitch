using System.Text.Json;

namespace CodexAccountManager.Core;

public sealed class AccountRepository
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true, PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    public string Root { get; }
    public AccountRepository(string root) { Root = PathSafety.Canonical(root); PathSafety.OrdinaryDirectory(Root); }

    public FileStream AcquireLock()
    {
        var path = Path.Combine(Root, "manager.lock");
        PathSafety.NoReparseAncestors(path);
        return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
    }

    public AccountDocument LoadAccounts()
    {
        var doc = Read<AccountDocument>("accounts.json") ?? new();
        Validate(doc);
        return doc;
    }

    public AppSettings LoadSettings()
    {
        var settings = Read<AppSettings>("settings.json") ?? new();
        if (settings.Version != 1) throw new IOException("Unsupported settings.json version.");
        _ = PathSafety.Canonical(settings.DefaultCodexHome);
        _ = PathSafety.Canonical(settings.WorkingDirectory);
        return settings;
    }

    private void Validate(AccountDocument doc)
    {
        if (doc.Version != 1 || doc.Accounts is null) throw new IOException("Unsupported or invalid accounts.json.");
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var account in doc.Accounts)
        {
            if (account is null || !ids.Add(account.Id) || string.IsNullOrWhiteSpace(account.DisplayName)
                || !account.SharedSessions) throw new IOException("Invalid or duplicate account record.");
            _ = PathSafety.Profile(Root, account);
        }
    }

    public void SaveAccounts(AccountDocument doc) { Validate(doc); Write("accounts.json", doc); }
    public void SaveSettings(AppSettings settings)
    {
        if (settings.Version != 1) throw new IOException("Unsupported settings version.");
        Write("settings.json", settings);
    }

    private T? Read<T>(string name) where T : class
    {
        var path = Path.Combine(Root, name);
        PathSafety.NoReparseAncestors(path);
        if (!File.Exists(path)) return null;
        return JsonSerializer.Deserialize<T>(File.ReadAllText(path), Json)
            ?? throw new IOException($"Invalid {name}. File preserved.");
    }

    private void Write<T>(string name, T value)
    {
        var path = Path.Combine(Root, name);
        PathSafety.NoReparseAncestors(path);
        var temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { JsonSerializer.Serialize(stream, value, Json); stream.Flush(true); }
            File.Move(temp, path, true);
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
}
