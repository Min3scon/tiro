# Start a long-running training/eval job detached from the terminal, logging to work\logs\<Name>.out.
#   powershell -File training\tools\launch.ps1 -Name bench -Module training.eval.bench_all -Args "--sets dev-mini"
#   -Low runs it at below-normal CPU priority (training), so your apps stay responsive.
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Module,
    [string]$Args = "",
    [string]$Python = "",
    [switch]$Low
)
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Python) { $Python = Join-Path $repo "work\venv\Scripts\python.exe" }
$logs = Join-Path $repo "work\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$out = Join-Path $logs "$Name.out"
$err = Join-Path $logs "$Name.err"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$argList = @("-u", "-m", $Module) + ($Args -split ' (?=(?:[^"]*"[^"]*")*[^"]*$)' | Where-Object { $_ -ne "" })
$p = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
if ($Low) { $p.PriorityClass = "BelowNormal" }
Set-Content -Path (Join-Path $logs "$Name.pid") -Value $p.Id
"started $Name pid=$($p.Id) log=$out"
