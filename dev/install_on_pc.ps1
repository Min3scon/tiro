# Install a published Tiro release on THIS PC for its user, replacing the old copy, with a backup and an automatic
# way back. Must run as the real user, outside any app container (e.g. via Invoke-CimMethod Win32_Process Create),
# so that %APPDATA%, %LOCALAPPDATA% and the registry are the real ones.
#   powershell -File dev\install_on_pc.ps1 -Version 2.0.3 [-OldExe D:\dictation\dist\Tiro\Tiro.exe]
# Log: work\backups\install-<version>.log
param([Parameter(Mandatory = $true)][string]$Version, [string]$OldExe = "D:\dictation\dist\Tiro\Tiro.exe")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$work = Join-Path $root "work\backups"
New-Item -ItemType Directory -Force $work | Out-Null
$log = Join-Path $work "install-$Version.log"
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$oldRun = (Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue).Tiro
$installDir = Join-Path $env:LOCALAPPDATA "Programs\Tiro"
Log "installing Tiro $Version; old autostart: $oldRun"

function Restore-Old {
    Log "ROLLING BACK to the old copy"
    if ($oldRun) { Set-ItemProperty -Path $runKey -Name "Tiro" -Value $oldRun }
    if (Test-Path $OldExe) { Start-Process -FilePath $OldExe -ArgumentList "--autostart" -WorkingDirectory (Split-Path $OldExe) }
}

try {
    # 1. backup (settings, history, learned data, registry)
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "backup_user_tiro.ps1") -Stamp "before-$Version"
    Log "backup: $(Get-Content (Join-Path $work "before-$Version\summary.txt"))"

    # 2. the installer, straight from the release (checked against the release's SHA-256 list below)
    $setup = Join-Path $work "TiroSetup-$Version.exe"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/Min3scon/tiro/releases/download/v$Version/TiroSetup.exe" -OutFile $setup
    Log "downloaded TiroSetup $((Get-Item $setup).Length) bytes, sha256 $((Get-FileHash $setup -Algorithm SHA256).Hash)"

    # 3. models already on this PC: copy them where the installed app looks, so nothing big is downloaded again
    $models = Join-Path $installDir "models"
    New-Item -ItemType Directory -Force $models | Out-Null
    foreach ($m in "parakeet-tdt-0.6b-v2", "qwen2.5-1.5b-instruct", "qwen2.5-0.5b-instruct") {
        $src = Join-Path $root "models\$m"
        if ((Test-Path $src) -and -not (Test-Path (Join-Path $models $m))) {
            Copy-Item $src (Join-Path $models $m) -Recurse
            Log "copied model $m"
        }
    }

    # 4. stop the old copy (it isn't in the install folder, so Setup would only ask it to quit)
    if (Test-Path $OldExe) {
        Start-Process -FilePath $OldExe -ArgumentList "--quit" -Wait -WindowStyle Hidden
        Start-Sleep -Seconds 3
        Get-Process Tiro -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $OldExe } | Stop-Process -Force
        Log "old copy stopped"
    }

    # 5. install (GPU edition when the card is usable: Setup decides), self-test included
    $p = Start-Process -FilePath $setup -ArgumentList "--silent", "--log", (Join-Path $work "setup-$Version.log") -Wait -PassThru
    Log "setup exit code $($p.ExitCode)"
    if ($p.ExitCode -ne 0) { throw "Setup failed (exit $($p.ExitCode)); see setup-$Version.log" }
    $state = Get-Content (Join-Path $installDir "state.json") -Raw | ConvertFrom-Json
    if ($state.current -ne $Version) { throw "state.json says $($state.current), expected $Version" }

    # 6. start it (through the launcher, as Windows will at login) and check it comes up
    Start-Process -FilePath (Join-Path $installDir "Tiro.exe") -WorkingDirectory $installDir
    $tiroLog = Join-Path $env:LOCALAPPDATA "Tiro\logs\tiro.log"
    $deadline = (Get-Date).AddSeconds(120)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if ((Test-Path $tiroLog) -and (Select-String -Path $tiroLog -Pattern "Tiro $Version starting" -Quiet) -and
            (Select-String -Path $tiroLog -Pattern "ASR ready" -Quiet)) {
            $lines = Get-Content $tiroLog -Tail 200
            $start = ($lines | Select-String -Pattern "Tiro $Version starting" | Select-Object -Last 1).LineNumber
            if ($start -and ($lines[($start - 1)..($lines.Count - 1)] | Select-String -Pattern "ASR ready" -Quiet)) { $ok = $true; break }
        }
    }
    if (-not $ok) { throw "Tiro $Version did not report ready within 2 minutes" }
    $newRun = (Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue).Tiro
    Log "running; autostart now: $newRun"
    Log "DONE"
} catch {
    Log "FAILED: $_"
    Restore-Old
    exit 1
}
