# Phase A1 "after" matrix: the same dictations as run_baseline.ps1 with the speed fixes in (adaptive tail, reuse
# of the pause decode, GPU priority for the final decode, native hotkey). Results: work/results/latency/after-*.
#   powershell -File dev\latency\run_after.ps1 [-Only tag1,tag2]
param([string[]]$Only = @())
$ErrorActionPreference = "Continue"
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root ".venv\Scripts\python.exe"
$harness = Join-Path $root "dev\latency_e2e.py"
$out = Join-Path $root "work\results\latency"
$long = Join-Path $out "long.wav"
New-Item -ItemType Directory -Force $out | Out-Null
$env:TIRO_ADAPTIVE_TAIL = "1"
$env:TIRO_REUSE_FINAL = "1"

$matrix = @(
    @{ tag = "after-first";      ins = "type";  wav = "";    runs = 3; targets = "notepad"; first = $true },
    @{ tag = "after-type";       ins = "type";  wav = "";    runs = 6; targets = "notepad,term,claude,browser" },
    @{ tag = "after-type-long";  ins = "type";  wav = $long; runs = 3; targets = "notepad,term,claude,browser" },
    @{ tag = "after-paste";      ins = "paste"; wav = "";    runs = 6; targets = "claude,browser" },
    @{ tag = "after-paste-long"; ins = "paste"; wav = $long; runs = 3; targets = "claude,browser" }
)

foreach ($m in $matrix) {
    if ($Only.Count -gt 0 -and -not ($Only -contains $m.tag)) { continue }
    $cmdArgs = @($harness, "--targets", $m.targets, "--runs", $m.runs, "--insertion", $m.ins, "--tag", $m.tag)
    if ($m.wav) { $cmdArgs += @("--wav", $m.wav) }
    if ($m.first) { $cmdArgs += @("--first-immediately") }
    $log = Join-Path $out ($m.tag + ".log")
    "=== " + (Get-Date -Format "HH:mm:ss") + " " + $m.tag | Tee-Object -FilePath $log
    & $py -u @cmdArgs 2>&1 | Tee-Object -FilePath $log -Append
    Start-Sleep -Seconds 5
}
"=== all done " + (Get-Date -Format "HH:mm:ss")
