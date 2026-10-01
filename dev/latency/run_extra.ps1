# Extra A1 measurements: Chrome baseline with the paint-based measurement, and CPU-only before/after.
#   powershell -File dev\latency\run_extra.ps1 [-Only tag1,tag2]
param([string[]]$Only = @())
$ErrorActionPreference = "Continue"
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root ".venv\Scripts\python.exe"
$harness = Join-Path $root "dev\latency_e2e.py"
$out = Join-Path $root "work\results\latency"
$long = Join-Path $out "long.wav"
$cpu = "device=cpu" 

$matrix = @(
    @{ tag = "baseline2-browser";      tail = "0"; reuse = "0"; wav = "";    runs = 6; targets = "browser"; extra = "" },
    @{ tag = "baseline2-browser-long"; tail = "0"; reuse = "0"; wav = $long; runs = 3; targets = "browser"; extra = "" },
    @{ tag = "baseline2-cpu";          tail = "0"; reuse = "0"; wav = "";    runs = 6; targets = "notepad"; extra = $cpu },
    @{ tag = "baseline2-cpu-long";     tail = "0"; reuse = "0"; wav = $long; runs = 3; targets = "notepad"; extra = $cpu },
    @{ tag = "after-cpu";              tail = "1"; reuse = "1"; wav = "";    runs = 6; targets = "notepad"; extra = $cpu },
    @{ tag = "after-cpu-long";         tail = "1"; reuse = "1"; wav = $long; runs = 3; targets = "notepad"; extra = $cpu }
)

foreach ($m in $matrix) {
    if ($Only.Count -gt 0 -and -not ($Only -contains $m.tag)) { continue }
    $env:TIRO_ADAPTIVE_TAIL = $m.tail
    $env:TIRO_REUSE_FINAL = $m.reuse
    $cmdArgs = @($harness, "--targets", $m.targets, "--runs", $m.runs, "--insertion", "type", "--tag", $m.tag)
    if ($m.extra) { $cmdArgs += @("--set", $m.extra.Trim()) }
    if ($m.wav) { $cmdArgs += @("--wav", $m.wav) }
    $log = Join-Path $out ($m.tag + ".log")
    "=== " + (Get-Date -Format "HH:mm:ss") + " " + $m.tag | Tee-Object -FilePath $log
    & $py -u @cmdArgs 2>&1 | Tee-Object -FilePath $log -Append
    Start-Sleep -Seconds 5
}
"=== all done " + (Get-Date -Format "HH:mm:ss")
