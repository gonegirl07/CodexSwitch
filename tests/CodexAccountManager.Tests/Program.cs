using CodexAccountManager.Core;

if (args.Length == 1 && args[0] == "--echo-stdin")
{
    Console.InputEncoding = new System.Text.UTF8Encoding(false); Console.OutputEncoding = new System.Text.UTF8Encoding(false);
    Console.Write(Console.In.ReadToEnd()); return 0;
}

if (args.Length == 2 && args[0] == "--refresh-accounts")
{
    var repo = new AccountRepository(args[1]); using var appLock = repo.AcquireLock();
    var doc = repo.LoadAccounts(); var settings = repo.LoadSettings(); var deps = await DependencyDetector.DetectAsync();
    foreach (var account in doc.Accounts)
    {
        var home = new CodexProfileService(repo.Root, new JunctionService()).Validate(account, settings);
        CredentialConfig.EnsureFile(home);
        account.LoginStatus = await new CodexStatusService().CheckAsync(deps, home);
        account.Quota = await new CodexQuotaService().ReadAsync(deps, home); account.QuotaError = null;
        account.LastCheckedAt = DateTimeOffset.UtcNow;
        Console.WriteLine(account.DisplayName + ": " + account.LoginStatus);
        foreach (var line in account.Quota.Lines) Console.WriteLine(line.Display());
    }
    repo.SaveAccounts(doc); return 0;
}

if (args.Length == 2 && args[0] == "--catalog")
{
    var result = new SessionProjectCatalog().Read(args[1]);
    Console.WriteLine($"Projects: {result.Projects.Count}; skipped entries: {result.SkippedFiles}");
    foreach (var project in result.Projects.Take(8)) Console.WriteLine($"{project.Directory} (sessions={project.SessionCount}, exists={project.Exists})");
    return 0;
}

var root = Path.Combine(Path.GetTempPath(), "CodexManagerTests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
var tests = new List<(string Name, Func<Task> Run)>();
void Test(string name, Action run) => tests.Add((name, () => { run(); return Task.CompletedTask; }));
void Assert(bool value, string message) { if (!value) throw new Exception(message); }
void Reject(Action action) { try { action(); } catch (IOException) { return; } throw new Exception("Expected fail-safe rejection."); }
Fixture Fixture() => new(Path.Combine(root, Guid.NewGuid().ToString("N")));

Test("create independent profiles, real NTFS junction and safe delete", () =>
{
    var f = Fixture();
    var a = f.Create("A"); var b = f.Create("B");
    Assert(a.Id != b.Id && a.ProfilePath != b.ProfilePath, "Profiles not independent");
    Assert(Directory.Exists(f.Path(a)), "Missing profile");
    Assert(PathSafety.Same(f.Junctions.GetTarget(f.Link(a)), f.Shared), "Wrong junction target");
    File.WriteAllText(Path.Combine(f.Path(a), "auth.json"), "test-only credential placeholder");
    Directory.CreateDirectory(Path.Combine(f.Path(a), "nested"));
    File.WriteAllText(Path.Combine(f.Path(a), "nested", "data"), "private");
    f.Service.Delete(a, f.Settings);
    Assert(!PathSafety.Exists(f.Path(a)) && !PathSafety.Exists(f.Link(a)), "Profile remains");
    Assert(File.ReadAllText(f.Sentinel) == "shared-do-not-delete", "Shared content lost");
    Assert(Directory.Exists(f.Path(b)), "Other account lost");
    f.Service.Delete(b, f.Settings);
});
Test("real sessions directory aborts before any deletion", () =>
{
    var f = Fixture(); var a = f.Create("A");
    f.Junctions.Remove(f.Link(a), f.Shared); Directory.CreateDirectory(f.Link(a));
    File.WriteAllText(Path.Combine(f.Link(a), "keep"), "keep");
    Reject(() => f.Service.Delete(a, f.Settings));
    Assert(File.Exists(Path.Combine(f.Link(a), "keep")) && File.Exists(Path.Combine(f.Path(a), "config.toml")), "Files removed on abort");
});
Test("wrong junction target aborts before deletion", () =>
{
    var f = Fixture(); var a = f.Create("A");
    f.Junctions.Remove(f.Link(a), f.Shared);
    var wrong = Path.Combine(f.Root, "wrong"); Directory.CreateDirectory(wrong);
    f.Junctions.Create(f.Link(a), wrong);
    Reject(() => f.Service.Delete(a, f.Settings));
    Assert(PathSafety.Exists(f.Link(a)) && File.Exists(f.Sentinel), "Changed wrong link or target");
});
Test("missing junction aborts", () =>
{
    var f = Fixture(); var a = f.Create("A"); f.Junctions.Remove(f.Link(a), f.Shared);
    Reject(() => f.Service.Delete(a, f.Settings)); Assert(Directory.Exists(f.Path(a)), "Deleted profile with missing link");
});
Test("broken target aborts", () =>
{
    var f = Fixture(); var a = f.Create("A");
    File.Delete(f.Sentinel); Directory.Delete(f.Shared, false);
    Reject(() => f.Service.Delete(a, f.Settings)); Assert(PathSafety.Exists(f.Link(a)), "Deleted broken junction");
});
Test("nested unexpected junction aborts before detaching sessions", () =>
{
    var f = Fixture(); var a = f.Create("A");
    f.Junctions.Create(Path.Combine(f.Path(a), "unexpected"), f.Shared);
    Reject(() => f.Service.Delete(a, f.Settings));
    Assert(PathSafety.Exists(f.Link(a)) && File.Exists(f.Sentinel), "Partial delete on failed preflight");
});
Test("junction creation never replaces existing data", () =>
{
    var f = Fixture(); var destination = Path.Combine(f.App, "existing"); Directory.CreateDirectory(destination);
    File.WriteAllText(Path.Combine(destination, "keep"), "keep");
    Reject(() => f.Junctions.Create(destination, f.Shared));
    Assert(File.Exists(Path.Combine(destination, "keep")), "Existing data removed");
});
Test("profile traversal and malicious identifiers rejected", () =>
{
    var f = Fixture(); var a = f.Create("A");
    foreach (var value in new[] { "../outside", "profiles/../outside", "D:/outside", "profiles/" + a.Id + "/.." })
    { var bad = new Account { Id = a.Id, ProfilePath = value }; Reject(() => f.Service.Delete(bad, f.Settings)); }
    foreach (var id in new[] { "..", "a/b", "a\\b", "CON", "", "x:stream" })
        Reject(() => PathSafety.Profile(f.App, new Account { Id = id, ProfilePath = "profiles/" + id }));
    Assert(File.Exists(f.Sentinel), "Traversal damaged shared files");
});
Test("reparse profile ancestor rejected", () =>
{
    var f = Fixture(); var outside = Path.Combine(f.Root, "outside"); Directory.CreateDirectory(outside);
    f.Junctions.Create(Path.Combine(f.App, "profiles"), outside);
    Reject(() => f.Create("A")); Assert(!Directory.EnumerateFileSystemEntries(outside).Any(), "Wrote through ancestor junction");
});
Test("shared target cannot overlap portable app", () =>
{
    var f = Fixture(); var inside = Path.Combine(f.App, "default"); Directory.CreateDirectory(Path.Combine(inside, "sessions"));
    Reject(() => f.Service.Create("A", "", new AppSettings { DefaultCodexHome = inside }));
});
Test("atomic JSON roundtrip and exclusive manager lock", () =>
{
    var f = Fixture(); var repo = new AccountRepository(f.App); var doc = new AccountDocument();
    doc.Accounts.Add(f.Create("Tài khoản 日本語")); repo.SaveAccounts(doc); repo.SaveSettings(f.Settings);
    Assert(repo.LoadAccounts().Accounts[0].DisplayName == "Tài khoản 日本語", "Unicode failed");
    Assert(repo.LoadSettings().DefaultCodexHome == f.Settings.DefaultCodexHome, "Settings failed");
    using var appLock = repo.AcquireLock(); Reject(() => repo.AcquireLock());
    Assert(!Directory.EnumerateFiles(f.App, "*.tmp").Any(), "Temporary JSON leaked");
});
Test("unsupported JSON versions are preserved", () =>
{
    var f = Fixture(); var file = Path.Combine(f.App, "accounts.json"); File.WriteAllText(file, "{\"version\":99,\"accounts\":[]}");
    Reject(() => new AccountRepository(f.App).LoadAccounts());
    Assert(File.ReadAllText(file).Contains("99"), "Invalid JSON overwritten");
});
Test("status classification never exposes CLI output", () =>
{
    var secret = "sensitive-placeholder";
    Assert(CodexStatusService.Classify(new(0, "Logged in using API key: " + secret)) == "Logged in (local credentials)", "Incorrect status");
    Assert(!CodexStatusService.Classify(new(2, secret)).Contains(secret), "Output leaked");
    Assert(CodexStatusService.Classify(new(1, "Not logged in")) == "Not logged in", "Logged-out classification failed");
    Assert(CodexStatusService.Classify(new(1, "invalid config")) == "Check failed (exit 1)", "Config error called logged-out");
});
Test("quota weekly-only does not invent a five-hour window", () =>
{
    using var json = System.Text.Json.JsonDocument.Parse("""
    {"rateLimitsByLimitId":{"codex":{"limitId":"codex","primary":null,"secondary":{"usedPercent":21,"windowDurationMins":10080,"resetsAt":1900000000}}},
     "rateLimits":{"limitId":"codex","primary":null,"secondary":{"usedPercent":21,"windowDurationMins":10080,"resetsAt":1900000000}}}
    """);
    var quota = CodexQuotaService.Parse(json.RootElement);
    Assert(quota.Lines.Count == 1 && quota.Lines[0].Title.Contains("Weekly") && quota.Lines[0].RemainingPercent == 79, "Weekly quota missing/duplicated");
    Assert(!quota.Lines.Any(l => l.Title.Contains("5 hours")), "Invented short quota");
});
Test("quota includes all buckets, credits, spend limits and reset count", () =>
{
    using var json = System.Text.Json.JsonDocument.Parse("""
    {"rateLimitsByLimitId":{
      "codex":{"limitId":"codex","primary":{"usedPercent":10,"windowDurationMins":300,"resetsAt":1900000000},"secondary":{"usedPercent":35,"windowDurationMins":10080,"resetsAt":1901000000},"credits":{"unlimited":false,"hasCredits":true,"balance":"12.50"}},
      "review":{"limitId":"review","limitName":"Review","primary":{"usedPercent":100,"windowDurationMins":1440,"resetsAt":null},"individualLimit":{"remainingPercent":40,"used":"6","limit":"10","resetsAt":1900000000}}
    },"rateLimitResetCredits":{"availableCount":2}}
    """);
    var quota = CodexQuotaService.Parse(json.RootElement);
    Assert(quota.Lines.Count == 7, "Quota fields were dropped");
    Assert(quota.Lines.Any(l => l.Title.Contains("Review") && l.RemainingPercent == 0), "Other bucket missing");
    Assert(quota.Lines.Any(l => l.Detail == "12.50") && quota.Lines.Any(l => l.Title == "Reset count" && l.Detail == "2"), "Credits missing");
});
Test("quota period is based on duration, missing metrics stay unavailable", () =>
{
    using var json = System.Text.Json.JsonDocument.Parse("""
    {"rateLimits":{"limitId":"codex","primary":{"usedPercent":null,"windowDurationMins":10080,"resetsAt":null},"secondary":null}}
    """);
    var quota = CodexQuotaService.Parse(json.RootElement);
    Assert(quota.Lines.Count == 1 && quota.Lines[0].Title.Contains("Weekly") && quota.Lines[0].RemainingPercent is null, "Null usage treated as zero or wrong period");
});
tests.Add(("Windows PowerShell fallback preserves Unicode native stdin and stdout", async () =>
{
    var legacy = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
    var deps = await DependencyDetector.DetectAsync([legacy]);
    Assert(deps.PowerShellMajor == 5 && deps.PowerShell == legacy, $"Legacy shell not selected: major={deps.PowerShellMajor}, path={deps.PowerShell}");
    var f = Fixture();
    var script = "'Tiếng Việt 日本語 ☀' | & " + ShellRunner.Quote(Environment.ProcessPath!) + " --echo-stdin";
    var result = await ShellRunner.RunAsync(legacy, script, f.App);
    Assert(result.ExitCode == 0 && result.Output.Contains("Tiếng Việt 日本語 ☀"), "Native UTF-8 pipeline corrupted");
    var preferred = await DependencyDetector.DetectAsync();
    Assert(preferred.PowerShellMajor >= 7, "Installed PowerShell 7 not preferred");
}));
Test("new login copies settings while enforcing file credentials without copying auth", () =>
{
    var f = Fixture(); var original = Path.Combine(f.Settings.DefaultCodexHome, "config.toml");
    var toml = "# Unicode thử nghiệm\r\nmodel = \"example-model\"\r\ncli_auth_credentials_store = \"keyring\"\r\n[mcp_servers.test]\r\ncommand = \"test\"\r\n";
    File.WriteAllText(original, toml); File.WriteAllText(Path.Combine(f.Settings.DefaultCodexHome, "auth.json"), "test-only");
    var a = f.Service.Create("New login", "", f.Settings, false);
    Assert(File.ReadAllText(Path.Combine(f.Path(a), "config.toml")) == toml.Replace("\"keyring\"", "\"file\""), "Unrelated TOML changed or file credentials not enforced");
    Assert(!File.Exists(Path.Combine(f.Path(a), "auth.json")), "New login copied credentials without opt-in");
    Assert(File.ReadAllText(original) == toml, "Original config changed");
});
Test("credential config ignores keys in multiline prompts and preserves comments", () =>
{
    var input = "prompt = \"\"\"\ncli_auth_credentials_store = 'keyring'\n\"\"\"\n'cli_auth_credentials_store' = 'auto' # keep\n[mcp_servers.x]\ncommand = 'unchanged'\n";
    var result = CredentialConfig.UseFile(input);
    Assert(result.Contains("cli_auth_credentials_store = 'keyring'\n\"\"\"") && result.Contains("'cli_auth_credentials_store' = \"file\" # keep"), "Edited prompt or lost comment");
    Assert(result.Contains("command = 'unchanged'"), "Unrelated settings changed");
    var table = "[mcp_servers.x]\ncommand = 'tool'\n";
    Assert(CredentialConfig.UseFile(table).StartsWith("cli_auth_credentials_store = \"file\"\n["), "Inserted root key inside table");
});
Test("explicit account copy creates independent auth and preserves original on delete", () =>
{
    var f = Fixture(); var source = Path.Combine(f.Settings.DefaultCodexHome, "auth.json");
    const string fake = "{\"testOnly\":\"not-a-real-credential\"}"; File.WriteAllText(source, fake);
    var a = f.Service.Create("Copy", "", f.Settings, true);
    Assert(File.ReadAllText(Path.Combine(f.Path(a), "auth.json")) == fake, "Auth not copied");
    File.WriteAllText(Path.Combine(f.Path(a), "auth.json"), "changed-private-copy");
    Assert(File.ReadAllText(source) == fake, "Auth shares file identity with original");
    f.Service.Delete(a, f.Settings);
    Assert(File.ReadAllText(source) == fake && File.Exists(f.Sentinel), "Original damaged by delete");
});
Test("missing source auth stops explicit copy before creating profile", () =>
{
    var f = Fixture(); Reject(() => f.Service.Create("Copy", "", f.Settings, true));
    Assert(!Directory.Exists(Path.Combine(f.App, "profiles")), "Created partial profile without source credentials");
});
Test("source auth cannot be written while copied", () =>
{
    var f = Fixture(); var source = Path.Combine(f.Settings.DefaultCodexHome, "auth.json"); File.WriteAllText(source, "test-only");
    using var writer = new FileStream(source, FileMode.Open, FileAccess.Write, FileShare.None);
    Reject(() => f.Service.Create("Copy", "", f.Settings, true));
    Assert(!Directory.Exists(Path.Combine(f.App, "profiles")), "Created partial profile during source write");
});
Test("Apply replaces only default auth and preserves both session trees and configs", () =>
{
    var f = Fixture(); var a = f.Create("Apply");
    var source = Path.Combine(f.Path(a), "auth.json"); var target = Path.Combine(f.Settings.DefaultCodexHome, "auth.json");
    File.WriteAllText(source, "{\"testOnly\":\"selected\"}"); File.WriteAllText(target, "{\"testOnly\":\"original\"}");
    var config = Path.Combine(f.Settings.DefaultCodexHome, "config.toml"); File.WriteAllText(config, "model = 'unchanged'");
    f.Service.ApplyAuthentication(a, f.Settings);
    Assert(File.ReadAllText(target) == File.ReadAllText(source), "Apply did not replace auth");
    Assert(File.ReadAllText(config) == "model = 'unchanged'" && File.ReadAllText(f.Sentinel) == "shared-do-not-delete", "Apply touched config/sessions");
    Assert(!Directory.EnumerateFiles(f.Settings.DefaultCodexHome, "*.tmp").Any(), "Apply temporary file leaked");
});
Test("Apply rejects invalid source auth without altering the default login", () =>
{
    var f = Fixture(); var a = f.Create("Apply"); var target = Path.Combine(f.Settings.DefaultCodexHome, "auth.json");
    File.WriteAllText(target, "{\"testOnly\":\"keep\"}"); File.WriteAllText(Path.Combine(f.Path(a), "auth.json"), "not JSON");
    Reject(() => f.Service.ApplyAuthentication(a, f.Settings));
    Assert(File.ReadAllText(target).Contains("keep") && !Directory.EnumerateFiles(f.Settings.DefaultCodexHome, "*.tmp").Any(), "Failed Apply damaged default auth");
});
Test("Apply leaves locked default auth untouched", () =>
{
    var f = Fixture(); var a = f.Create("Apply"); var target = Path.Combine(f.Settings.DefaultCodexHome, "auth.json");
    File.WriteAllText(target, "{\"testOnly\":\"keep\"}"); File.WriteAllText(Path.Combine(f.Path(a), "auth.json"), "{\"testOnly\":\"new\"}");
    using (var locked = new FileStream(target, FileMode.Open, FileAccess.Read, FileShare.None))
        Reject(() => f.Service.ApplyAuthentication(a, f.Settings));
    Assert(File.ReadAllText(target).Contains("keep"), "Locked auth altered");
});
Test("session projects use metadata cwd, deduplicate and mark missing directories", () =>
{
    var f = Fixture(); var project = Path.Combine(f.Root, "Dự án có dấu"); Directory.CreateDirectory(project);
    var missing = Path.Combine(f.Root, "missing-project");
    string Header(string path) => System.Text.Json.JsonSerializer.Serialize(new { type = "session_meta", payload = new { cwd = path } });
    File.WriteAllText(Path.Combine(f.Shared, "one.jsonl"), Header(project) + "\n{broken body ignored");
    File.WriteAllText(Path.Combine(f.Shared, "two.jsonl"), Header(project.ToUpperInvariant()));
    File.WriteAllText(Path.Combine(f.Shared, "missing.jsonl"), Header(missing));
    File.WriteAllText(Path.Combine(f.Shared, "unrelated.jsonl"), System.Text.Json.JsonSerializer.Serialize(new { type = "event_msg", payload = new { cwd = f.App } }));
    var result = new SessionProjectCatalog().Read(f.Shared);
    Assert(result.Projects.Count == 2, "Project catalog did not deduplicate/filter metadata");
    Assert(result.Projects[0].Exists && result.Projects[0].SessionCount == 2, "Existing project not first or incorrect session count");
    Assert(!result.Projects[1].Exists, "Missing directory not marked");
});
Test("session projects sort by name then path with missing directories last", () =>
{
    var f = Fixture();
    var paths = new[] { Path.Combine(f.Root, "z", "Alpha"), Path.Combine(f.Root, "a", "beta"), Path.Combine(f.Root, "a", "Alpha"), Path.Combine(f.Root, "missing") };
    for (var i = 0; i < paths.Length; i++)
    {
        if (i < 3) Directory.CreateDirectory(paths[i]);
        File.WriteAllText(Path.Combine(f.Shared, i + ".jsonl"), System.Text.Json.JsonSerializer.Serialize(new { type = "session_meta", payload = new { cwd = paths[i] } }));
    }
    var result = new SessionProjectCatalog().Read(f.Shared);
    Assert(result.Projects.Select(p => p.Directory).SequenceEqual(new[] { paths[2], paths[0], paths[1], paths[3] }), "Incorrect alphabetical ordering");
});
Test("session catalog skips nested links and supports large metadata headers", () =>
{
    var f = Fixture(); var outside = Path.Combine(f.Root, "external"); Directory.CreateDirectory(outside);
    f.Junctions.Create(Path.Combine(f.Shared, "link"), outside);
    var header = System.Text.Json.JsonSerializer.Serialize(new { type = "session_meta", payload = new { cwd = f.App, base_instructions = new string('x', 160000) } });
    File.WriteAllText(Path.Combine(f.Shared, "large.jsonl"), header);
    File.WriteAllText(Path.Combine(outside, "hidden.jsonl"), System.Text.Json.JsonSerializer.Serialize(new { type = "session_meta", payload = new { cwd = outside } }));
    var result = new SessionProjectCatalog().Read(f.Shared);
    Assert(result.Projects.Count == 1 && PathSafety.Same(result.Projects[0].Directory, f.App), "Catalog followed junction or failed large metadata");
    using var canceled = new CancellationTokenSource(); canceled.Cancel();
    try { new SessionProjectCatalog().Read(f.Shared, canceled.Token); throw new Exception("Cancellation ignored"); }
    catch (OperationCanceledException) { }
});
Test("pinned directory cannot be renamed during deletion checks", () =>
{
    var f = Fixture(); var a = f.Create("A");
    using var pin = JunctionService.PinDirectory(f.Path(a));
    Reject(() => Directory.Move(f.Path(a), f.Path(a) + "-moved"));
    Assert(PathSafety.Exists(f.Link(a)), "Pinned directory changed");
});
Test("malformed JSON is not overwritten", () =>
{
    var f = Fixture(); var file = Path.Combine(f.App, "accounts.json"); File.WriteAllText(file, "{broken");
    try { new AccountRepository(f.App).LoadAccounts(); throw new Exception("Malformed JSON accepted"); }
    catch (System.Text.Json.JsonException) { }
    Assert(File.ReadAllText(file) == "{broken", "Malformed JSON replaced");
});
Test("WinForms renders populated and empty account lists", () =>
{
    var f = Fixture(); var a = f.Create("Work account — thử nghiệm"); var b = f.Create("Personal account");
    a.LoginStatus = "Logged in (local credentials)"; a.LastCheckedAt = DateTimeOffset.Now;
    a.Quota = new QuotaSnapshot { Lines = [new("codex · Weekly", 72, 1900000000)] };
    b.Quota = new QuotaSnapshot { Lines = [new("codex · 5 hours", 95, 1900000000), new("codex · Weekly", 81, 1901000000), new("Review · Weekly", 44, 1901000000)] };
    a.Note = "UI fixture — no real credentials";
    var repo = new AccountRepository(f.App); repo.SaveSettings(f.Settings);
    repo.SaveAccounts(new AccountDocument { Accounts = [a, b] });
    Exception? error = null;
    var thread = new Thread(() =>
    {
        try
        {
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.EnableVisualStyles();
            using var form = new CodexAccountManager.MainForm(repo);
            void Prepare(CodexAccountManager.MainForm window)
            {
                window.Opacity = 0; window.ShowInTaskbar = false; window.Show();
                var deadline = DateTime.UtcNow.AddSeconds(30);
                while (!window.Ready.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(10); }
                Assert(window.Ready.IsCompleted, "UI startup did not complete");
                window.PerformLayout(); Application.DoEvents();
            }
            Prepare(form);
            var output = System.IO.Path.Combine(Environment.CurrentDirectory, "artifacts");
            Directory.CreateDirectory(output);
            using (var bitmap = new System.Drawing.Bitmap(form.Width, form.Height))
            { form.DrawToBitmap(bitmap, new System.Drawing.Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(System.IO.Path.Combine(output, "ui-populated.png")); }
            repo.SaveAccounts(new AccountDocument());
            using var empty = new CodexAccountManager.MainForm(repo);
            Prepare(empty);
            using var image = new System.Drawing.Bitmap(empty.Width, empty.Height);
            empty.DrawToBitmap(image, new System.Drawing.Rectangle(0, 0, empty.Width, empty.Height));
            image.Save(System.IO.Path.Combine(output, "ui-empty.png"));
            void CaptureDialog(Form dialog, string file)
            {
                using (dialog)
                {
                    dialog.Opacity = 0; dialog.Show(); Application.DoEvents();
                    if (dialog is CodexAccountManager.ProjectPickerForm picker)
                    {
                        var deadline = DateTime.UtcNow.AddSeconds(10);
                        while (!picker.Ready.IsCompleted && DateTime.UtcNow < deadline) { Application.DoEvents(); Thread.Sleep(10); }
                        Assert(picker.Ready.IsCompleted, "Project picker did not finish loading");
                        Application.DoEvents();
                    }
                    using var capture = new System.Drawing.Bitmap(dialog.Width, dialog.Height);
                    dialog.DrawToBitmap(capture, new System.Drawing.Rectangle(0, 0, dialog.Width, dialog.Height));
                    capture.Save(System.IO.Path.Combine(output, file)); dialog.Close();
                }
            }
            CaptureDialog(new CodexAccountManager.AddAccountForm(), "ui-add-account.png");
            CaptureDialog(new CodexAccountManager.AccountDetailsForm(a, f.Path(a), f.Shared), "ui-account-details.png");
            CaptureDialog(new CodexAccountManager.AdvancedSettingsForm(f.Settings, new(null, null, false)), "ui-settings.png");
            File.WriteAllText(Path.Combine(f.Shared, "project.jsonl"), System.Text.Json.JsonSerializer.Serialize(new { type = "session_meta", payload = new { cwd = f.App } }));
            CaptureDialog(new CodexAccountManager.ProjectPickerForm(f.Shared, "Work account"), "ui-project-picker.png");
        }
        catch (Exception ex) { error = ex; }
    });
    thread.SetApartmentState(ApartmentState.STA); thread.Start(); thread.Join();
    if (error is not null) throw error;
});
tests.Add(("PowerShell argument quoting and process environment isolation", async () =>
{
    var f = Fixture(); var deps = await DependencyDetector.DetectAsync(); deps.Require();
    var special = Path.Combine(f.Root, "Tài khoản ' ; $() & space"); Directory.CreateDirectory(special);
    var fake = Path.Combine(special, "fake codex.ps1");
    File.WriteAllText(fake, "[Console]::Out.WriteLine($env:CODEX_HOME); [Console]::Out.WriteLine(($args -join '|')); if ($env:OPENAI_API_KEY) { exit 23 }");
    var original = Environment.GetEnvironmentVariable("CODEX_HOME");
    var originalKey = Environment.GetEnvironmentVariable("OPENAI_API_KEY");
    try
    {
        Environment.SetEnvironmentVariable("OPENAI_API_KEY", "test-only");
        var script = CodexProcessLauncher.BuildScript(fake, special, special, CodexAction.Resume);
        var result = await ShellRunner.RunAsync(deps.PowerShell!, script, special);
        Assert(result.ExitCode == 0 && result.Output.Contains(special) && result.Output.Contains("resume|--all"), "Quoting or isolation failed");
        Assert(Environment.GetEnvironmentVariable("CODEX_HOME") == original, "Parent environment changed");
    }
    finally { Environment.SetEnvironmentVariable("OPENAI_API_KEY", originalKey); }
}));
tests.Add(("login exits only on success in PowerShell 7 and 5.1", async () =>
{
    var f = Fixture(); var deps = await DependencyDetector.DetectAsync(); deps.Require();
    var legacy = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
    foreach (var shell in new[] { deps.PowerShell!, legacy }.Distinct(StringComparer.OrdinalIgnoreCase))
    {
        foreach (var code in new[] { 0, 1 })
        {
            var fake = Path.Combine(f.Root, "fake-login.ps1"); File.WriteAllText(fake, "exit " + code);
            var script = CodexProcessLauncher.BuildScript(fake, f.App, f.App, CodexAction.Login) + "; [Console]::Out.WriteLine('terminal-kept'); exit 37";
            var result = await ShellRunner.RunAsync(shell, script, f.App);
            Assert(code == 0 ? result.ExitCode == 0 && !result.Output.Contains("terminal-kept") : result.ExitCode == 37 && result.Output.Contains("terminal-kept"), "Login exit behavior incorrect");
        }
    }
}));
tests.Add(("official CLI status against a fresh isolated profile", async () =>
{
    var f = Fixture(); var a = f.Create("Empty"); var deps = await DependencyDetector.DetectAsync(); deps.Require();
    Assert(deps.LoginStatusSupported, "Installed CLI login status not detected");
    var state = await new CodexStatusService().CheckAsync(deps, f.Path(a));
    Assert(state == "Not logged in", "Unexpected empty profile status: " + state);
    Assert(!File.Exists(Path.Combine(f.Path(a), "auth.json")), "Check created credentials");
    f.Service.Delete(a, f.Settings);
    Assert(File.ReadAllText(f.Sentinel) == "shared-do-not-delete", "Shared fixture changed");
}));

Test("cached quota labels render in English without mutating stored values", () =>
{
    var line = new QuotaLine("Tên bucket · Tuần", 72, null);
    Assert(line.Display() == "Tên bucket · Weekly: 72% left" && line.Title == "Tên bucket · Tuần", "Cached quota or server bucket changed");
    Assert(new QuotaLine("codex · 5 giờ", null, null, "Không có dữ liệu").Display() == "codex · 5 hours: No data", "Cached duration not translated");
    Assert(new QuotaLine("Lượt reset", null, null, "2").Display() == "Reset count: 2", "Cached reset label not translated");
});
var failures = 0;
try
{
    foreach (var test in tests)
    {
        try { await test.Run(); Console.WriteLine("PASS " + test.Name); }
        catch (Exception ex) { failures++; Console.WriteLine("FAIL " + test.Name + ": " + ex.Message); }
    }
}
finally
{
    // Test-owned tree only; do not follow any junction, including deliberately invalid test cases.
    void Cleanup(string directory)
    {
        if (!PathSafety.Within(directory, root) && !PathSafety.Same(directory, root)) throw new Exception("Cleanup outside fixture root");
        foreach (var entry in Directory.EnumerateFileSystemEntries(directory))
        {
            var attrs = File.GetAttributes(entry);
            if ((attrs & FileAttributes.Directory) != 0)
            { if ((attrs & FileAttributes.ReparsePoint) == 0) Cleanup(entry); else Directory.Delete(entry, false); }
            else File.Delete(entry);
        }
        Directory.Delete(directory, false);
    }
    Cleanup(root);
}
Console.WriteLine($"{tests.Count - failures}/{tests.Count} passed");
return failures == 0 ? 0 : 1;

sealed class Fixture
{
    public string Root { get; }
    public string App { get; }
    public string Shared { get; }
    public string Sentinel => System.IO.Path.Combine(Shared, "sentinel.txt");
    public JunctionService Junctions { get; } = new();
    public CodexProfileService Service { get; }
    public AppSettings Settings { get; }
    public Fixture(string root)
    {
        Root = root; App = System.IO.Path.Combine(root, "app"); Directory.CreateDirectory(App);
        var home = System.IO.Path.Combine(root, "default-home"); Shared = System.IO.Path.Combine(home, "sessions");
        Directory.CreateDirectory(Shared); File.WriteAllText(Sentinel, "shared-do-not-delete");
        Settings = new() { DefaultCodexHome = home, WorkingDirectory = App }; Service = new(App, Junctions);
    }
    public Account Create(string name) => Service.Create(name, "", Settings);
    public string Path(Account a) => PathSafety.Profile(App, a);
    public string Link(Account a) => System.IO.Path.Combine(Path(a), "sessions");
}
