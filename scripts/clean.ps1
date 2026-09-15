#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$relativeTargets = @('artifacts','src/CodexAccountManager/bin','src/CodexAccountManager/obj',
    'src/CodexAccountManager.Core/bin','src/CodexAccountManager.Core/obj',
    'tests/CodexAccountManager.Tests/bin','tests/CodexAccountManager.Tests/obj')
$freed = [long]0
foreach ($relative in $relativeTargets) {
    $target = [IO.Path]::GetFullPath((Join-Path $repoRoot $relative))
    if (-not $target.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw "Outside repo: $target" }
    if (-not (Test-Path -LiteralPath $target)) { continue }
    for ($ancestor = $target; $ancestor; $ancestor = [IO.Path]::GetDirectoryName($ancestor)) {
        if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Cleanup aborted at reparse point: $ancestor" }
    }
    $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($target)
    $directories = [Collections.Generic.List[string]]::new(); $files = [Collections.Generic.List[IO.FileInfo]]::new()
    while ($pending.Count) {
        $directory = $pending.Pop(); $directories.Add($directory)
        foreach ($entry in Get-ChildItem -LiteralPath $directory -Force) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Cleanup aborted at reparse point: $($entry.FullName)" }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) } else { $files.Add($entry) }
        }
    }
    if ($PSCmdlet.ShouldProcess($target, 'Remove verified generated build files (no junction traversal)')) {
        foreach ($file in $files) { $freed += $file.Length; Remove-Item -LiteralPath $file.FullName }
        for ($i=$directories.Count-1; $i -ge 0; $i--) { Remove-Item -LiteralPath $directories[$i] }
    }
}
Write-Output "Removed generated files: $freed bytes. Portable app and account data preserved."
