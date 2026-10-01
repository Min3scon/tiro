# Back up this user's Tiro settings, history and learned data before an upgrade. Run through an unvirtualized
# process (the real %APPDATA% / %LOCALAPPDATA%), e.g. via Invoke-CimMethod Win32_Process Create.
# Copies into work\backups\<timestamp>\ (never committed). Models and logs are skipped (re-downloadable / not data).
#   powershell -File dev\backup_user_tiro.ps1 [-Stamp 20261001-2000]
param([string]$Stamp = (Get-Date -Format "yyyyMMdd-HHmm"))
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "work\backups\$Stamp"
New-Item -ItemType Directory -Force $dest | Out-Null
$roaming = Join-Path $env:APPDATA "Tiro"
$local = Join-Path $env:LOCALAPPDATA "Tiro"
if (Test-Path $roaming) { Copy-Item $roaming (Join-Path $dest "AppData-Roaming-Tiro") -Recurse -Force }
if (Test-Path $local) {
    $d = Join-Path $dest "AppData-Local-Tiro"
    New-Item -ItemType Directory -Force $d | Out-Null
    Get-ChildItem $local -Force | Where-Object { $_.Name -notin @("models", "logs", "updates", "coreml-cache") } |
        ForEach-Object { Copy-Item $_.FullName (Join-Path $d $_.Name) -Recurse -Force }
}
$run = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue).Tiro
$uninstall = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Tiro" -ErrorAction SilentlyContinue
@{ run_key = $run; uninstall = if ($uninstall) { @{ location = $uninstall.InstallLocation; version = $uninstall.DisplayVersion } } else { $null };
   stamp = $Stamp; appdata = $env:APPDATA; localappdata = $env:LOCALAPPDATA } |
    ConvertTo-Json -Depth 4 | Out-File -FilePath (Join-Path $dest "registry.json") -Encoding utf8
Get-ChildItem $dest -Recurse -File | Measure-Object -Property Length -Sum |
    ForEach-Object { "backed up {0} files, {1:N1} MB to {2}" -f $_.Count, ($_.Sum / 1MB), $dest } |
    Out-File -FilePath (Join-Path $dest "summary.txt") -Encoding utf8
