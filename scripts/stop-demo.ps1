<#
.SYNOPSIS
  Stops what scripts\start-demo.ps1 started: the API and web windows, then PostgreSQL.

.PARAMETER KeepPostgres  Leave PostgreSQL running (by default it is stopped cleanly with pg_ctl stop).
.PARAMETER StopPostgres  Kept for older instructions; stopping PostgreSQL is now the default.
.PARAMETER Force         Also stop whatever else is listening on the API / web ports.
#>
param(
    [switch]$KeepPostgres,
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

# PostgreSQL last, after the API that uses it is gone. A fast, clean shutdown removes postmaster.pid itself.
if ($KeepPostgres) {
    Write-Host "    PostgreSQL left running (-KeepPostgres)."
} else {
    $pgCtl = Join-Path $PgBin "pg_ctl.exe"
    if (-not (Test-Path $pgCtl)) {
        Write-Host "    pg_ctl.exe not found at $pgCtl" -ForegroundColor Yellow
    } else {
        & $pgCtl status -D $PgData | Out-Null
        if ($LASTEXITCODE -eq 0) {
            # Hidden, output to files and a bounded wait: never block on handles inherited by postgres processes.
            $stopDir = Join-Path $Root ".demo"
            New-Item -ItemType Directory -Force -Path $stopDir | Out-Null
            $stop = Start-Process -FilePath $pgCtl -ArgumentList @("stop", "-D", "`"$PgData`"", "-m", "fast", "-w", "-t", "60") -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $stopDir "pg_ctl.stop.out.log") -RedirectStandardError (Join-Path $stopDir "pg_ctl.stop.err.log")
            [void]$stop.WaitForExit(70000)
            & $pgCtl status -D $PgData | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Host "    PostgreSQL stopped cleanly." -ForegroundColor Green }
            else { Write-Host "    pg_ctl stop failed (is PostgreSQL running as a Windows service? stop it there)." -ForegroundColor Yellow }
        } else {
            Write-Host "    PostgreSQL was not running."
        }
        # Only after confirming no server runs: a pid file left behind would block the next start.
        & $pgCtl status -D $PgData | Out-Null
        $postmasterPid = Join-Path $PgData "postmaster.pid"
        if ($LASTEXITCODE -ne 0 -and (Test-Path $postmasterPid)) {
            Remove-Item $postmasterPid -Force
            Write-Host "    removed stale $postmasterPid" -ForegroundColor Yellow
        }
    }
}
