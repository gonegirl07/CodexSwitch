using System.Text.Json;

namespace CodexAccountManager.Core;

public sealed record SessionProject(string Directory, DateTime LastUsedUtc, int SessionCount, bool Exists)
{
    public string Name => Path.GetFileName(Path.TrimEndingDirectorySeparator(Directory)) is { Length: > 0 } name ? name : Directory;
    public override string ToString() => Name + "  —  " + Directory + (Exists ? "" : "  — no longer exists");
}
public sealed record ProjectCatalog(IReadOnlyList<SessionProject> Projects, int SkippedFiles);

public sealed class SessionProjectCatalog
{
    public ProjectCatalog Read(string sessionsDirectory, CancellationToken cancellationToken = default)
    {
        PathSafety.OrdinaryDirectory(sessionsDirectory);
        var projects = new Dictionary<string, SessionProject>(StringComparer.OrdinalIgnoreCase);
        var pending = new Stack<string>(); pending.Push(sessionsDirectory);
        var skipped = 0;
        var metadataBuffer = new byte[128 * 1024];
        while (pending.TryPop(out var directory))
        {
            cancellationToken.ThrowIfCancellationRequested();
            try
            {
                PathSafety.OrdinaryDirectory(directory);
                foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    try
                    {
                        var attributes = File.GetAttributes(entry);
                        if ((attributes & FileAttributes.ReparsePoint) != 0) { skipped++; continue; }
                        if ((attributes & FileAttributes.Directory) != 0) { pending.Push(entry); continue; }
                        if (!entry.EndsWith(".jsonl", StringComparison.OrdinalIgnoreCase)) continue;
                        var cwd = ReadWorkingDirectory(entry, metadataBuffer);
                        if (cwd is null) { skipped++; continue; }
                        var path = PathSafety.Canonical(cwd);
                        var lastUsed = File.GetLastWriteTimeUtc(entry);
                        projects.TryGetValue(path, out var previous);
                        projects[path] = new(path, previous is not null && previous.LastUsedUtc > lastUsed ? previous.LastUsedUtc : lastUsed,
                            (previous?.SessionCount ?? 0) + 1, Directory.Exists(path));
                    }
                    catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or ArgumentException)
                    { skipped++; }
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { skipped++; }
        }
        return new(projects.Values.OrderByDescending(p => p.Exists)
            .ThenBy(p => p.Name, StringComparer.OrdinalIgnoreCase)
            .ThenBy(p => p.Directory, StringComparer.OrdinalIgnoreCase).ToArray(), skipped);
    }

    private static string? ReadWorkingDirectory(string file, byte[] buffer)
    {
        // Only inspect the beginning of the session metadata record, never conversation bodies.
        // cwd precedes large instructions/tool definitions in current Codex session headers.
        using var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var count = stream.ReadAtLeast(buffer, buffer.Length, throwOnEndOfStream: false);
        var bytes = buffer.AsSpan(0, count);
        if (bytes.StartsWith(new byte[] { 0xEF, 0xBB, 0xBF })) bytes = bytes[3..];
        var reader = new Utf8JsonReader(bytes, isFinalBlock: false, state: default);
        string? type = null, cwd = null;
        var inPayload = false;
        while (reader.Read())
        {
            if (reader.TokenType == JsonTokenType.EndObject && reader.CurrentDepth == 0) break;
            if (reader.TokenType == JsonTokenType.EndObject && reader.CurrentDepth == 1) inPayload = false;
            if (reader.TokenType != JsonTokenType.PropertyName) continue;
            if (reader.CurrentDepth == 1 && reader.ValueTextEquals("type"))
            {
                if (!reader.Read() || reader.TokenType != JsonTokenType.String) return null;
                type = reader.GetString();
            }
            else if (reader.CurrentDepth == 1 && reader.ValueTextEquals("payload")) inPayload = true;
            else if (inPayload && reader.CurrentDepth == 2 && reader.ValueTextEquals("cwd"))
            {
                if (!reader.Read() || reader.TokenType != JsonTokenType.String) return null;
                cwd = reader.GetString();
            }
            if (type is not null && type != "session_meta") return null;
            if (type == "session_meta" && cwd is not null) return cwd;
        }
        return null;
    }
}
