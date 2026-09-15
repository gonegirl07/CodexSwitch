#Requires -Version 7.0
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$exe = Join-Path $repoRoot 'dist/CodexAccountManager/CodexAccountManager.exe'
$version = ([xml](Get-Content -LiteralPath (Join-Path $repoRoot 'src/CodexAccountManager/CodexAccountManager.csproj') -Raw)).Project.PropertyGroup.Version
$output = Join-Path $repoRoot 'dist/releases'
$zip = Join-Path $output "CodexAccountManager-$version-win-x64.zip"
foreach ($path in @($exe, $zip)) {
    for ($ancestor = $path; $ancestor; $ancestor = [IO.Path]::GetDirectoryName($ancestor)) {
        if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Reparse point in packaging path: $ancestor"
        }
    }
}
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Build the portable executable first.' }
if ((Get-Item -LiteralPath $exe).VersionInfo.ProductVersion.Split('+')[0] -ne $version) { throw 'Executable version does not match source.' }
New-Item -ItemType Directory -Path $output -Force | Out-Null
# Explicit single-file input: never enumerate the used portable folder or its profiles.
Compress-Archive -LiteralPath $exe -DestinationPath $zip -Force
$archive = [IO.Compression.ZipFile]::OpenRead($zip)
try {
    if ($archive.Entries.Count -ne 1 -or $archive.Entries[0].FullName -cne 'CodexAccountManager.exe') { throw 'Unexpected ZIP contents.' }
    $stream = $archive.Entries[0].Open()
    try { $archivedHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)) }
    finally { $stream.Dispose() }
    if ($archivedHash -ne (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash) { throw 'ZIP executable hash mismatch.' }
}
finally { $archive.Dispose() }
Get-FileHash -LiteralPath $zip -Algorithm SHA256
Write-Output "Release ZIP: $zip"
