<#
.SYNOPSIS
  Stops what scripts\start-demo.ps1 started (the API and web windows).

.PARAMETER StopPostgres  Also stop PostgreSQL (only if it was started with the data directory below).
.PARAMETER Force         Also stop whatever else is listening on the API / web ports.
#>
param(
    [switch]$StopPostgres,
    [switch]$Force
)

# Keep in sync with scripts\start-demo.ps1.
$PgBin   = "D:\Development\PGSQL\pgsql\bin"
$PgData  = "D:\Development\PGSQL\data"
$ApiPort = 8000
$WebPort = 5173

$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root ".demo\pids.json"

function Stop-Tree([int]$processId, [string]$label) {
    $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $proc) { Write-Host "    $label (PID $processId) was not running." ; return }
    & taskkill.exe /PID $processId /T /F | Out-Null
    Write-Host "    stopped $label (PID $processId)" -ForegroundColor Green
}

Write-Host "Stopping the PackComply demo"
if (Test-Path $PidFile) {
    $state = Get-Content $PidFile -Raw | ConvertFrom-Json
    if ($state.api) { Stop-Tree ([int]$state.api) "API window" }
    if ($state.web) { Stop-Tree ([int]$state.web) "web window" }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
} else {
    Write-Host "    no .demo\pids.json: nothing recorded by start-demo.ps1."
}

foreach ($port in @($ApiPort, $WebPort)) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { continue }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    $name = if ($proc) { $proc.ProcessName } else { "unknown" }
    if ($Force) { Stop-Tree ([int]$conn.OwningProcess) "$name on port $port" }
    else { Write-Host "    port $port is still used by '$name' (PID $($conn.OwningProcess)). Re-run with -Force to stop it." -ForegroundColor Yellow }
}

if ($StopPostgres) {
    $pgCtl = Join-Path $PgBin "pg_ctl.exe"
    if (Test-Path $pgCtl) { & $pgCtl stop -D $PgData -m fast; Write-Host "    PostgreSQL stopped." -ForegroundColor Green }
    else { Write-Host "    pg_ctl.exe not found at $pgCtl" -ForegroundColor Yellow }
} else {
    Write-Host "    PostgreSQL left running (use -StopPostgres to stop it)."
}
