namespace CodexAccountManager.Core;

public sealed class Account
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string DisplayName { get; set; } = "";
    public string ProfilePath { get; set; } = "";
    public bool SharedSessions { get; set; } = true;
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? LastCheckedAt { get; set; }
    public string LoginStatus { get; set; } = "Not checked";
    public string Note { get; set; } = "";
    public QuotaSnapshot? Quota { get; set; }
    public string? QuotaError { get; set; }
}

public sealed class AccountDocument
{
    public int Version { get; set; } = 1;
    public List<Account> Accounts { get; set; } = [];
}

public sealed class AppSettings
{
    public int Version { get; set; } = 1;
    public string DefaultCodexHome { get; set; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".codex");
    public string WorkingDirectory { get; set; } = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
}

public sealed class QuotaSnapshot
{
    public DateTimeOffset FetchedAt { get; set; } = DateTimeOffset.UtcNow;
    public List<QuotaLine> Lines { get; set; } = [];
}
public sealed record QuotaLine(string Title, double? RemainingPercent, long? ResetsAt, string? Detail = null)
{
    public string Display()
    {
        var value = RemainingPercent is double remaining ? $"{remaining:0.#}% left" : EnglishLabel(Detail ?? "No data");
        string reset = "";
        if (ResetsAt is long seconds)
        {
            try { reset = " · reset " + DateTimeOffset.FromUnixTimeSeconds(seconds).ToLocalTime().ToString("dd/MM HH:mm"); }
            catch (ArgumentOutOfRangeException) { reset = " · reset unknown"; }
        }
        var separator = Title.LastIndexOf(" · ", StringComparison.Ordinal);
        var title = separator < 0 ? EnglishLabel(Title) : Title[..(separator + 3)] + EnglishLabel(Title[(separator + 3)..]);
        return $"{title}: {value}{reset}";
    }

    // Translate app-generated cached labels without altering account data or server bucket names.
    private static string EnglishLabel(string text)
    {
        var translated = text switch
        {
            "Tuần" => "Weekly", "Lượt reset" => "Reset count", "Chi tiêu" => "Spending",
            "Đã dùng / hạn mức" => "Used / limit", "Đã chạm hạn mức" => "Limit reached",
            "Giới hạn chi tiêu" => "Spending limit", "Không giới hạn" => "Unlimited",
            "Không có credits" => "No credits", "Chưa có số dư" => "Balance unavailable",
            "Không có dữ liệu" => "No data", _ => text
        };
        foreach (var (suffix, unit) in new[] { (" ngày", "days"), (" giờ", "hours"), (" phút", "minutes") })
            if (translated.EndsWith(suffix, StringComparison.Ordinal) && long.TryParse(translated[..^suffix.Length], out var duration))
                return $"{duration} {unit}";
        return translated;
    }
}
