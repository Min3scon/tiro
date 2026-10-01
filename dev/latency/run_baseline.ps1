# Phase A1 latency matrix (runs one after another; each writes work/results/latency/<tag>.json and <tag>.log).
#   powershell -File dev\latency\run_baseline.ps1 [-Only tag1,tag2]
param([string[]]$Only = @())
$ErrorActionPreference = "Continue"
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })  # "-File" passes "a,b" as one string
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root ".venv\Scripts\python.exe"
$harness = Join-Path $root "dev\latency_e2e.py"
$out = Join-Path $root "work\results\latency"
$long = Join-Path $out "long.wav"
New-Item -ItemType Directory -Force $out | Out-Null

$matrix = @(
    @{ tag = "baseline-type";      tail = "0"; ins = "type";  wav = "";    runs = 6; targets = "notepad,term,claude,browser" },
    @{ tag = "baseline-type-long"; tail = "0"; ins = "type";  wav = $long; runs = 3; targets = "notepad,term,claude,browser" },
    @{ tag = "tail-type";          tail = "1"; ins = "type";  wav = "";    runs = 6; targets = "notepad,term,claude,browser" },
    @{ tag = "tail-type-long";     tail = "1"; ins = "type";  wav = $long; runs = 3; targets = "notepad,term,claude,browser" },
    @{ tag = "tail-paste";         tail = "1"; ins = "paste"; wav = "";    runs = 6; targets = "notepad,term,claude,browser" },
    @{ tag = "tail-paste-long";    tail = "1"; ins = "paste"; wav = $long; runs = 3; targets = "notepad,term,claude,browser" },
    @{ tag = "idle-type";          tail = "1"; ins = "type";  wav = "";    runs = 3; targets = "notepad"; idle = 180 }
)

foreach ($m in $matrix) {
    if ($Only.Count -gt 0 -and -not ($Only -contains $m.tag)) { continue }
    $env:TIRO_ADAPTIVE_TAIL = $m.tail
    $cmdArgs = @($harness, "--targets", $m.targets, "--runs", $m.runs, "--insertion", $m.ins, "--tag", $m.tag)
    if ($m.wav) { $cmdArgs += @("--wav", $m.wav) }
    if ($m.idle) { $cmdArgs += @("--idle-first", $m.idle) }
    $log = Join-Path $out ($m.tag + ".log")
    "=== " + (Get-Date -Format "HH:mm:ss") + " " + $m.tag | Tee-Object -FilePath $log
    & $py -u @cmdArgs 2>&1 | Tee-Object -FilePath $log -Append
    Start-Sleep -Seconds 5
}
"=== all done " + (Get-Date -Format "HH:mm:ss")

