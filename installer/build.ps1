# Build TiroSetup.exe (the Windows installer) into release\. Run tools\package_release.py first: it writes the
# manifest the installer embeds (download URLs, sizes and SHA-256 of everything it fetches).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$proj = Join-Path $PSScriptRoot "TiroSetup\TiroSetup.csproj"
$out = Join-Path $PSScriptRoot "out"
dotnet build $proj -c Release -o $out --nologo -v minimal
if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
$release = Join-Path $root "release"
New-Item -ItemType Directory -Force $release | Out-Null
Copy-Item (Join-Path $out "TiroSetup.exe") (Join-Path $release "TiroSetup.exe") -Force
Write-Host "TiroSetup.exe -> $release"
