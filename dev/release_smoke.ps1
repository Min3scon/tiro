# Smoke test of a frozen Windows build before release: health check (and the right update keys), speech self-test
# on the GPU and the CPU, and real dictations into Notepad, a console program and Chrome through the built exe
# (isolated profile, F24 hotkey, test WAV as the microphone).
#   powershell -File dev\release_smoke.ps1 [-Exe build\dist\Tiro\Tiro.exe]
param([string]$Exe = "build\dist\Tiro\Tiro.exe")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$out = Join-Path $root "work\results\smoke"
New-Item -ItemType Directory -Force $out | Out-Null
$fail = 0
$others = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "*\work\update-e2e\*" -or $_.ExecutablePath -like "*\work\installer-e2e\*" }
if ($others) { Write-Host "another test's copy of Tiro is running; not starting (they would share the hotkey)"; exit 2 }
function Check($name, $ok, $detail) {
    if ($ok) { Write-Host "PASS $name $detail" } else { Write-Host "FAIL $name $detail"; $script:fail++ }
}
$profile = Join-Path $out "profile"
New-Item -ItemType Directory -Force $profile | Out-Null
'{"hotkey": "f24", "setup_done": true, "welcome_shown": true, "auto_update_check": false}' | Out-File (Join-Path $profile "settings.json") -Encoding ascii
$env:TIRO_CONFIG_DIR = $profile

$p = Start-Process -FilePath $Exe -ArgumentList "--health", "$out\health.json" -Wait -PassThru
$h = Get-Content "$out\health.json" | ConvertFrom-Json
Check "health" ($p.ExitCode -eq 0 -and $h.ok) "version $($h.version) keys $($h.keys -join ',')"
Check "release keys only" (($h.keys -join ",") -eq "ci-2026a,offline-2026a") ""

foreach ($dev in "cuda", "cpu") {
    $p = Start-Process -FilePath $Exe -ArgumentList "--selftest", "$out\selftest-$dev.json", "--device", $dev -Wait -PassThru
    $s = Get-Content "$out\selftest-$dev.json" | ConvertFrom-Json
    Check "self-test $dev" ($p.ExitCode -eq 0 -and $s.ok -and $s.device -eq $dev) "device $($s.device), $($s.decode_ms) ms, '$($s.text)'"
}
Remove-Item Env:TIRO_CONFIG_DIR

& .venv\Scripts\python.exe -u dev\latency_e2e.py --exe $Exe --targets notepad,term,browser --runs 3 --tag smoke 2>&1 |
    Tee-Object -FilePath "$out\latency.log"
$r = Get-Content "work\results\latency\smoke.json" | ConvertFrom-Json
foreach ($t in "notepad", "term", "browser") {
    $m = $r.targets.$t.median_ms
    Check "dictation into $t" ($m -ne $null -and $m -lt 300) "median $m ms"
}
if ($fail) { Write-Host "SMOKE TEST FAILED ($fail)"; exit 1 } else { Write-Host "SMOKE TEST PASSED" }
