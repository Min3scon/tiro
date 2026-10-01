# Build TiroSetup.exe (the Windows installer) into release\. Run tools\package_release.py first: it writes the
# manifest the installer embeds (download URLs, sizes and SHA-256 of everything it fetches). The launcher
# (installer\TiroLauncher, the small Tiro.exe that switches versions) is built first and embedded in Setup.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $PSScriptRoot "out"
dotnet build (Join-Path $PSScriptRoot "TiroLauncher\TiroLauncher.csproj") -c Release -o (Join-Path $out "launcher") --nologo -v minimal
if ($LASTEXITCODE -ne 0) { throw "launcher build failed" }
dotnet build (Join-Path $PSScriptRoot "TiroSetup\TiroSetup.csproj") -c Release -o $out --nologo -v minimal
if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
$release = Join-Path $root "release"
New-Item -ItemType Directory -Force $release | Out-Null
Copy-Item (Join-Path $out "TiroSetup.exe") (Join-Path $release "TiroSetup.exe") -Force
Write-Host "TiroSetup.exe -> $release"
