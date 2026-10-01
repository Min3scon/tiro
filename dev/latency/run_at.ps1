# Wait until a clock time (optional), then run a latency matrix script; everything goes to work/results/latency/<log>.
#   powershell -File dev\latency\run_at.ps1 [-At 18:00:10] [-Script run_baseline.ps1] [-Log matrix.log] [-Only tag1,tag2]
param([string]$At = "", [string]$Script = "run_baseline.ps1", [string]$Log = "matrix.log", [string[]]$Only = @())
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$logPath = Join-Path $root ("work\results\latency\" + $Log)
if ($At) {
    $t = [datetime]::ParseExact($At, "HH:mm:ss", $null)
    $wait = ($t - (Get-Date)).TotalSeconds
    if ($wait -gt 0) { Start-Sleep -Seconds ([int][math]::Ceiling($wait)) }
}
"start " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") | Out-File -FilePath $logPath -Encoding utf8
$path = Join-Path $PSScriptRoot $Script
if ($Only.Count -gt 0) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $path -Only ($Only -join ",") *>&1 | Out-File -FilePath $logPath -Append -Encoding utf8
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $path *>&1 | Out-File -FilePath $logPath -Append -Encoding utf8
}
"finished " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") | Out-File -FilePath $logPath -Append -Encoding utf8
