# OMEGA daily paper-trading job (PAPER ONLY - there is no live path).
# Register once (runs Mon-Fri 18:30 local time, after NSE close and bhavcopy publication):
#   schtasks /Create /TN "OMEGA paper" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:30 `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\trading\scripts\paper_daily.ps1"
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [string]$Data = "data_store\nse",
    [string]$Strategy = "M1_xs_momentum",
    [string]$State = "runs\paper_m1"
)
$ErrorActionPreference = "Stop"
Set-Location $Repo
$py = Join-Path $Repo ".venv\Scripts\python.exe"
$log = Join-Path $Repo "runs\paper_daily.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
"=== $(Get-Date -Format o) ===" | Add-Content $log
& $py -m omega validate --data $Data 2>&1 | Add-Content $log
if ($LASTEXITCODE -ne 0) { "DATA VALIDATION FAILED - session not run" | Add-Content $log; exit 2 }
& $py -m omega paper --data $Data --strategy $Strategy --state $State --backup "runs\backups" 2>&1 | Add-Content $log
if ($LASTEXITCODE -eq 3) { "HALTED - reconciliation break; human review required" | Add-Content $log }
exit $LASTEXITCODE
