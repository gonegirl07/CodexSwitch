using System.Text;
using System.Text.RegularExpressions;

namespace CodexAccountManager.Core;

public static class CredentialConfig
{
    private static readonly Regex Assignment = new("\\G(?:cli_auth_credentials_store|\"cli_auth_credentials_store\"|'cli_auth_credentials_store')[ \\t]*=[ \\t]*", RegexOptions.CultureInvariant);

    // A targeted lexical edit preserves comments, whitespace and unrelated TOML verbatim.
    // Strings/arrays are skipped so a key in a multiline prompt cannot be mistaken for configuration.
    public static string UseFile(string text)
    {
        var lineStart = true; var depth = 0; var foundStart = -1; var foundEnd = -1;
        for (var i = 0; i < text.Length;)
        {
            var c = text[i];
            if (c == '\n') { lineStart = true; i++; continue; }
            if (c is ' ' or '\t' or '\r' or '\uFEFF') { i++; continue; }
            if (c == '#') { while (i < text.Length && text[i] != '\n') i++; continue; }
            if (lineStart && depth == 0)
            {
                if (c == '[') break; // Remaining assignments belong to tables, not the root config.
                var match = Assignment.Match(text, i);
                if (match.Success)
                {
                    if (foundStart >= 0) throw new IOException("config.toml has duplicate credential store keys; file was not changed.");
                    foundStart = i + match.Length;
                    if (foundStart >= text.Length || text[foundStart] is not ('\'' or '"'))
                        throw new IOException("Credential store value in config.toml is not a string; file was not changed.");
                    foundEnd = SkipString(text, foundStart);
                    i = foundEnd; lineStart = false; continue;
                }
            }
            lineStart = false;
            if (c is '\'' or '"') { i = SkipString(text, i); continue; }
            if (c is '[' or '{') depth++;
            if (c is ']' or '}') depth--;
            i++;
        }
        if (foundStart >= 0) return text[..foundStart] + "\"file\"" + text[foundEnd..];
        var ending = text.Contains("\r\n", StringComparison.Ordinal) ? "\r\n" : "\n";
        var prefix = text.StartsWith('\uFEFF') ? "\uFEFF" : "";
        return prefix + "cli_auth_credentials_store = \"file\"" + ending + text[prefix.Length..];
    }

    private static int SkipString(string text, int start)
    {
        var quote = text[start];
        var triple = start + 2 < text.Length && text[start + 1] == quote && text[start + 2] == quote;
        for (var i = start + (triple ? 3 : 1); i < text.Length; i++)
        {
            if (quote == '"' && text[i] == '\\') { i++; continue; }
            if (text[i] != quote) continue;
            if (!triple) return i + 1;
            if (i + 2 < text.Length && text[i + 1] == quote && text[i + 2] == quote)
            {
                var end = i + 3;
                while (end < text.Length && text[end] == quote && end < i + 5) end++;
                return end;
            }
        }
        throw new IOException("config.toml contains an unterminated string; file was not changed.");
    }

    public static void EnsureFile(string profile)
    {
        PathSafety.OrdinaryDirectory(profile);
        var path = Path.Combine(profile, "config.toml"); PathSafety.NoReparseAncestors(path);
        var exists = File.Exists(path);
        var original = exists ? File.ReadAllText(path) : "";
        var updated = UseFile(original);
        if (original == updated) return;
        using var pin = JunctionService.PinDirectory(profile, allowWrites: true);
        var temporary = Path.Combine(profile, ".credential-config-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                var bytes = new UTF8Encoding(false).GetBytes(updated); stream.Write(bytes); stream.Flush(true);
            }
            PathSafety.NoReparseAncestors(path);
            if (exists) File.Replace(temporary, path, null); else File.Move(temporary, path);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
