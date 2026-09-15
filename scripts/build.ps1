#Requires -Version 7.0
[CmdletBinding()]
param([string]$DotNet = 'dotnet', [switch]$SkipPublish)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$publishLock = $null
Push-Location $projectRoot
try {
    & $DotNet build src/CodexAccountManager/CodexAccountManager.csproj -c Release --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    & $DotNet run --project tests/CodexAccountManager.Tests -c Release
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    if (-not $SkipPublish) {
        $publishPath = Join-Path $projectRoot 'dist/CodexAccountManager'
        $stagePath = Join-Path $projectRoot ('artifacts/publish-' + [guid]::NewGuid().ToString('N'))
        & $DotNet publish src/CodexAccountManager/CodexAccountManager.csproj -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -p:DebugType=None -p:DebugSymbols=false -o $stagePath --nologo
        if ($LASTEXITCODE -ne 0) { throw 'Publish failed.' }
        New-Item -ItemType Directory -Path $publishPath -Force | Out-Null
        function Assert-PublishPath([string]$Path) {
            $full = [IO.Path]::GetFullPath($Path)
            $allowed = [IO.Path]::GetFullPath($publishPath) + [IO.Path]::DirectorySeparatorChar
            if (-not $full.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw "Outside publish folder: $full" }
            for ($current = $full; $current; $current = [IO.Path]::GetDirectoryName($current)) {
                if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse point in publish path: $current" }
            }
        }
        # Remove only runtime assets named by the previous self-contained dependency manifest.
        # Never enumerate profiles or recursively delete a portable directory.
        $oldDeps = Join-Path $publishPath 'CodexAccountManager.deps.json'
        Assert-PublishPath $oldDeps
        $lockPath = Join-Path $publishPath 'manager.lock'
        Assert-PublishPath $lockPath
        $publishLock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $runtimeFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        if (Test-Path -LiteralPath $oldDeps) {
            $manifest = Get-Content -LiteralPath $oldDeps -Raw | ConvertFrom-Json -AsHashtable
            foreach ($target in $manifest.targets.Values) {
                foreach ($entry in $target.GetEnumerator()) {
                    if ($entry.Key -notlike 'runtimepack.*') { continue }
                    foreach ($kind in @('runtime','native','resources')) {
                        if ($entry.Value.ContainsKey($kind)) {
                            foreach ($relative in $entry.Value[$kind].Keys) { [void]$runtimeFiles.Add((Join-Path $publishPath $relative)) }
                        }
                    }
                }
            }
        }
        # WindowsDesktop satellite assemblies are not listed in the app dependency manifest.
        $satellites = @('Microsoft.VisualBasic.Forms','PresentationCore','PresentationFramework','PresentationUI','ReachFramework',
            'System.Windows.Controls.Ribbon','System.Windows.Forms.Design','System.Windows.Forms.Primitives','System.Windows.Forms',
            'System.Windows.Input.Manipulations','System.Xaml','UIAutomationClient','UIAutomationClientSideProviders',
            'UIAutomationProvider','UIAutomationTypes','WindowsBase','WindowsFormsIntegration')
        foreach ($culture in @('cs','de','es','fr','it','ja','ko','pl','pt-BR','ru','tr','zh-Hans','zh-Hant')) {
            foreach ($assembly in $satellites) { [void]$runtimeFiles.Add((Join-Path $publishPath "$culture/$assembly.resources.dll")) }
        }
        foreach ($file in $runtimeFiles) { Assert-PublishPath $file }
        foreach ($file in $runtimeFiles) { if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file } }
        foreach ($directory in ($runtimeFiles | ForEach-Object { Split-Path $_ -Parent } | Sort-Object -Unique)) {
            if ($directory -eq $publishPath) { continue }
            Assert-PublishPath $directory
            if ((Test-Path -LiteralPath $directory -PathType Container) -and -not (Get-ChildItem -LiteralPath $directory -Force | Select-Object -First 1)) { Remove-Item -LiteralPath $directory }
        }
        foreach ($name in @('CodexAccountManager.dll','CodexAccountManager.Core.dll','CodexAccountManager.deps.json','CodexAccountManager.runtimeconfig.json')) {
            $file = Join-Path $publishPath $name
            Assert-PublishPath $file
            if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
        }
        $exe = Join-Path $publishPath 'CodexAccountManager.exe'
        Assert-PublishPath $exe
        Copy-Item -LiteralPath (Join-Path $stagePath 'CodexAccountManager.exe') -Destination $exe
        Remove-Item -LiteralPath (Join-Path $stagePath 'CodexAccountManager.exe')
        Remove-Item -LiteralPath $stagePath
        Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination $publishPath
        New-Item -ItemType Directory -Path (Join-Path $publishPath 'docs') -Force | Out-Null
        foreach ($name in @('ARCHITECTURE.md','VALIDATION.md')) {
            Copy-Item -LiteralPath (Join-Path $projectRoot "docs/$name") -Destination (Join-Path $publishPath 'docs')
        }
        Get-FileHash -LiteralPath $exe -Algorithm SHA256
        Write-Host "Portable folder: $publishPath"
    }
}
finally { if ($publishLock) { $publishLock.Dispose() }; Pop-Location }
