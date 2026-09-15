using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace CodexAccountManager.Core;

public sealed class CodexQuotaService
{
    public async Task<QuotaSnapshot> ReadAsync(Dependencies dependencies, string home, CancellationToken cancellationToken = default)
    {
        if (dependencies.NativeCodex is null) throw new IOException("Codex executable not found for quota queries.");
        PathSafety.OrdinaryDirectory(home);
        var info = new ProcessStartInfo(dependencies.NativeCodex)
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = home
        };
        info.ArgumentList.Add("-c"); info.ArgumentList.Add("cli_auth_credentials_store=\"file\"");
        info.ArgumentList.Add("app-server"); info.ArgumentList.Add("--listen"); info.ArgumentList.Add("stdio://");
        foreach (var key in ShellRunner.IsolatedEnvironmentVariables) info.Environment.Remove(key);
        info.Environment["CODEX_HOME"] = home;
        using var process = Process.Start(info) ?? throw new IOException("Could not start the quota reader.");
        // Drain diagnostics without retaining or logging raw content.
        var diagnostics = Drain(process.StandardError);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            await Send(new { id = 1, method = "initialize", @params = new { clientInfo = new { name = "codex_account_manager", version = "1.2.0" } } });
            _ = await Receive(1);
            await Send(new { method = "initialized", @params = new { } });
            await Send(new { id = 2, method = "account/rateLimits/read" });
            return Parse(await Receive(2));
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        { throw new TimeoutException("Quota request timed out after 30 seconds. Please Refresh."); }
        finally
        {
            process.StandardInput.Close();
            using var shutdown = new CancellationTokenSource(TimeSpan.FromSeconds(2));
            try { await process.WaitForExitAsync(shutdown.Token); }
            catch (OperationCanceledException) { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); }
            await diagnostics;
        }

        async Task Send<T>(T request)
        {
            await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(request).AsMemory(), timeout.Token);
            await process.StandardInput.FlushAsync(timeout.Token);
        }
        async Task<JsonElement> Receive(int id)
        {
            while (true)
            {
                var line = await process.StandardOutput.ReadLineAsync(timeout.Token);
                if (line is null) throw new IOException("Codex closed the quota connection before returning data.");
                using var message = JsonDocument.Parse(line);
                var value = message.RootElement;
                if (!value.TryGetProperty("id", out var responseId) || responseId.ValueKind != JsonValueKind.Number || !responseId.TryGetInt32(out var number) || number != id) continue;
                if (value.TryGetProperty("error", out var error))
                {
                    var code = error.TryGetProperty("code", out var codeValue) ? codeValue.ToString() : "unknown";
                    throw new IOException($"Could not read quota (Codex {code}). Check login and connection, then Refresh.");
                }
                if (!value.TryGetProperty("result", out var result)) throw new IOException("Invalid quota response.");
                return result.Clone();
            }
        }
    }
    private static async Task Drain(StreamReader reader)
    {
        var buffer = new char[2048];
        while (await reader.ReadAsync(buffer) != 0) { }
    }

    public static QuotaSnapshot Parse(JsonElement result)
    {
        var snapshot = new QuotaSnapshot(); var seen = new HashSet<string>(StringComparer.Ordinal);
        if (result.TryGetProperty("rateLimitsByLimitId", out var buckets) && buckets.ValueKind == JsonValueKind.Object)
            foreach (var bucket in buckets.EnumerateObject()) AddBucket(bucket.Value, bucket.Name);
        if (result.TryGetProperty("rateLimits", out var single) && single.ValueKind == JsonValueKind.Object)
            AddBucket(single, "codex");
        if (result.TryGetProperty("rateLimitResetCredits", out var resets) && resets.ValueKind == JsonValueKind.Object
            && resets.TryGetProperty("availableCount", out var count) && count.ValueKind == JsonValueKind.Number)
            snapshot.Lines.Add(new("Reset count", null, null, count.ToString()));
        return snapshot;

        void AddBucket(JsonElement bucket, string key)
        {
            if (bucket.ValueKind != JsonValueKind.Object) return;
            var id = Text(bucket, "limitId") ?? key;
            if (!seen.Add(id)) return;
            var label = Text(bucket, "limitName") ?? id;
            foreach (var field in bucket.EnumerateObject())
            {
                var window = field.Value;
                if (window.ValueKind != JsonValueKind.Object || !window.TryGetProperty("usedPercent", out var used)) continue;
                double? remaining = used.ValueKind == JsonValueKind.Number && used.TryGetDouble(out var percentage) && double.IsFinite(percentage)
                    ? Math.Clamp(100 - percentage, 0, 100) : null;
                var duration = Integer(window, "windowDurationMins");
                var period = duration switch
                {
                    10080 => "Weekly", 300 => "5 hours", > 0 when duration % 1440 == 0 => $"{duration / 1440} days",
                    > 0 when duration % 60 == 0 => $"{duration / 60} hours", > 0 => $"{duration} minutes", _ => field.Name
                };
                snapshot.Lines.Add(new(label + " · " + period, remaining, Integer(window, "resetsAt")));
            }
            if (bucket.TryGetProperty("credits", out var credits) && credits.ValueKind == JsonValueKind.Object)
            {
                var detail = credits.TryGetProperty("unlimited", out var unlimited) && unlimited.ValueKind == JsonValueKind.True
                    ? "Unlimited" : Text(credits, "balance") ?? (credits.TryGetProperty("hasCredits", out var has) && has.ValueKind == JsonValueKind.False ? "No credits" : "Balance unavailable");
                snapshot.Lines.Add(new(label + " · Credits", null, null, detail));
            }
            if (bucket.TryGetProperty("individualLimit", out var limit) && limit.ValueKind == JsonValueKind.Object)
            {
                var remaining = limit.TryGetProperty("remainingPercent", out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) ? (double?)number : null;
                snapshot.Lines.Add(new(label + " · Spending", remaining, Integer(limit, "resetsAt"), null));
                snapshot.Lines.Add(new(label + " · Used / limit", null, null, (Text(limit, "used") ?? "?") + " / " + (Text(limit, "limit") ?? "?")));
            }
            if (Text(bucket, "rateLimitReachedType") is string reached) snapshot.Lines.Add(new(label + " · Limit reached", null, null, reached));
            if (bucket.TryGetProperty("spendControlReached", out var spend) && spend.ValueKind == JsonValueKind.True)
                snapshot.Lines.Add(new(label + " · Spending limit", null, null, "Limit reached"));
        }
    }
    private static string? Text(JsonElement element, string key) => element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
    private static long? Integer(JsonElement element, string key) => element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number) ? number : null;
}
