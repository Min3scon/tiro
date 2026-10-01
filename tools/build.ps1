# Build Tiro.exe (one-folder PyInstaller app) into build\dist\Tiro, ready to run locally, then package the
# release downloads (tools\package_release.py) and the installer (installer\build.ps1).
# Usage:  powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-SkipInstaller]
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$dist = Join-Path $root "build\dist"

& $py (Join-Path $root "tools\make_assets.py")
if ($LASTEXITCODE -ne 0) { throw "asset generation failed" }

& $py -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $root "build\work") (Join-Path $root "tiro.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Local copy of the models next to the exe (hard links: no extra disk space). Release zips leave them out;
# the installer downloads them from Hugging Face.
$app = Join-Path $dist "Tiro"
foreach ($m in "parakeet-tdt-0.6b-v2", "silero-vad", "qwen2.5-1.5b-instruct", "qwen2.5-0.5b-instruct") {
    $src = Join-Path $root "models\$m"
    $dst = Join-Path $app "models\$m"
    New-Item -ItemType Directory -Force $dst | Out-Null
    if (-not (Test-Path $src)) { continue }
    Get-ChildItem $src -File -Recurse | Where-Object { $_.Name -notlike "*.part" -and $_.FullName -notmatch "\.cache\\" } | ForEach-Object {
        $target = Join-Path $dst $_.FullName.Substring($src.Length + 1)
        New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
        if (-not (Test-Path $target)) { New-Item -ItemType HardLink -Path $target -Target $_.FullName | Out-Null }
    }
}

& $py (Join-Path $root "tools\package_release.py")
if ($LASTEXITCODE -ne 0) { throw "packaging failed" }

if (-not $SkipInstaller) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "installer\build.ps1")
    if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
}
Write-Host "Done: $app\Tiro.exe and release\"
