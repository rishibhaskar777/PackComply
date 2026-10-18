<#
.SYNOPSIS
  One-command PackComply demo start for Windows PowerShell (no Docker).

.DESCRIPTION
  Checks/starts PostgreSQL (pg_ctl), applies database migrations, starts the API (uvicorn) and the HTTPS Vite dev
  server in their own windows, waits until the API and the OCR models are ready, then prints the laptop URL, the
  phone URL and a QR code for the phone.

  Run from the repository root (any folder works):
      powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1
  Stop everything with scripts\stop-demo.ps1.

.PARAMETER NoBrowser   Do not open the laptop URL in the default browser.
.PARAMETER Yes         Answer "yes" to prompts (for example installing node_modules).
.PARAMETER SkipOcrWait Do not wait for the OCR warm-up (the first photo then loads the models, which is slow).
#>
param(
    [switch]$NoBrowser,
    [switch]$Yes,
    [switch]$SkipOcrWait
)

# ------------------------------------------------------------------------------------------------------------
# Configuration: change these two paths if PostgreSQL lives somewhere else.
$PgBin   = "D:\Development\PGSQL\pgsql\bin"
$PgData  = "D:\Development\PGSQL\data"
$PgPort  = 5432
$ApiPort = 8000
$WebPort = 5173
$OcrWarmupTimeoutSeconds = 420
# ------------------------------------------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$Root    = Split-Path -Parent $PSScriptRoot
$Python  = Join-Path $Root ".venv\Scripts\python.exe"
$Alembic = Join-Path $Root ".venv\Scripts\alembic.exe"
$Frontend = Join-Path $Root "frontend"
$StateDir = Join-Path $Root ".demo"
$PidFile  = Join-Path $StateDir "pids.json"
$FirewallRule = "PackComply Demo ($WebPort)"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

function Step($text)  { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)    { Write-Host "    OK   $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "    WARN $text" -ForegroundColor Yellow }
function Fail($text, $fix) {
    Write-Host "`n    FAIL $text" -ForegroundColor Red
    if ($fix) { Write-Host "         $fix" -ForegroundColor Yellow }
    exit 1
}

function Get-PortOwner([int]$port) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $null }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Pid = $conn.OwningProcess; Name = $(if ($proc) { $proc.ProcessName } else { "unknown" }) }
}

function Test-ApiReady {
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/health/ready" -UseBasicParsing -TimeoutSec 3; return $r.StatusCode -eq 200 } catch { return $false }
}
function Get-SystemStatus {
    try { return (Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/api/system/status" -TimeoutSec 3) } catch { return $null }
}
function Test-TcpOpen([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { $client.Connect("127.0.0.1", $port); return $true } catch { return $false } finally { $client.Close() }
}

# Wi-Fi / LAN IPv4 of the interface that has a default gateway, skipping virtual adapters (Hyper-V, WSL, VPN, Docker...).
function Get-LanIPv4 {
    $virtual = "Hyper-V|vEthernet|VMware|VirtualBox|Virtual|WSL|Docker|TAP-|TUN|WireGuard|VPN|Tailscale|ZeroTier|Npcap|Loopback|Bluetooth|Pseudo|Teredo|ISATAP"
    $candidates = @()
    foreach ($cfg in (Get-NetIPConfiguration -ErrorAction SilentlyContinue)) {
        if (-not $cfg.NetAdapter -or $cfg.NetAdapter.Status -ne "Up") { continue }
        if ($cfg.NetAdapter.InterfaceDescription -match $virtual -or $cfg.InterfaceAlias -match $virtual) { continue }
        foreach ($address in $cfg.IPv4Address) {
            $ip = $address.IPAddress
            if ($ip -match "^169\.254\." -or $ip -match "^127\.") { continue }
            $wifi = ($cfg.NetAdapter.PhysicalMediaType -match "802\.11|Native 802\.11") -or ($cfg.InterfaceAlias -match "Wi-?Fi|Wireless|WLAN")
            $candidates += [pscustomobject]@{ Ip = $ip; Wifi = $wifi; Gateway = [bool]$cfg.IPv4DefaultGateway; Alias = $cfg.InterfaceAlias }
        }
    }
    if (-not $candidates) { return $null }
    return ($candidates | Sort-Object @{ Expression = { -not $_.Gateway } }, @{ Expression = { -not $_.Wifi } } | Select-Object -First 1)
}

Write-Host "PackComply demo start" -ForegroundColor White
Write-Host "Repository: $Root"

# ---------------------------------------------------------------------------------------------------- prerequisites
Step "Checking prerequisites"
if (-not (Test-Path $Python)) {
    Fail "Python virtual environment not found at $Python" "Create it once:  python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -e `".[dev]`""
}
if (-not (Test-Path $Alembic)) {
    Fail "alembic is not installed in the virtual environment." "Install the requirements:  .\.venv\Scripts\Activate.ps1 ; pip install -e `".[dev]`""
}
Ok "Python venv: $Python"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "npm (Node.js 20+) was not found on PATH." "Install Node.js from https://nodejs.org and open a new terminal." }
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Warn "frontend\node_modules is missing."
    $answer = if ($Yes) { "y" } else { Read-Host "    Run 'npm install' in frontend now? (Y/n)" }
    if ($answer -match "^(|y|yes)$") {
        Push-Location $Frontend
        try { npm install; if ($LASTEXITCODE -ne 0) { Fail "npm install failed." "Fix the error above (network is needed once for npm install) and run this script again." } } finally { Pop-Location }
    } else { Fail "node_modules is required." "Run:  cd frontend ; npm install" }
}
Ok "Frontend dependencies installed"

# Ports: a leftover demo from an earlier run is the usual cause.
foreach ($pair in @(@($ApiPort, "API"), @($WebPort, "web app"))) {
    $owner = Get-PortOwner $pair[0]
    if ($owner) {
        Fail "Port $($pair[0]) (needed for the $($pair[1])) is already in use by '$($owner.Name)' (PID $($owner.Pid))." "If it is an earlier demo run:  scripts\stop-demo.ps1   -   otherwise stop that program or free the port, then run this script again."
    }
}
Ok "Ports $ApiPort and $WebPort are free"

# ---------------------------------------------------------------------------------------------------- PostgreSQL
Step "PostgreSQL"
if (Test-TcpOpen $PgPort) {
    Ok "PostgreSQL is already running on port $PgPort (leaving it as it is)"
} else {
    $pgCtl = Join-Path $PgBin "pg_ctl.exe"
    if (-not (Test-Path $pgCtl)) { Fail "pg_ctl.exe not found at $pgCtl" "Edit `$PgBin / `$PgData at the top of scripts\start-demo.ps1 to point at your PostgreSQL folders." }
    if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) { Fail "$PgData is not a PostgreSQL data directory." "Edit `$PgData at the top of scripts\start-demo.ps1." }
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $pgLog = Join-Path $StateDir "postgres.log"
    Write-Host "    starting PostgreSQL ($PgData) ..."
    & $pgCtl start -D $PgData -l $pgLog -w -t 60 | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-TcpOpen $PgPort)) {
        $tail = if (Test-Path $pgLog) { (Get-Content $pgLog -Tail 8) -join "`n         " } else { "(no log)" }
        Fail "PostgreSQL did not start. Last log lines:`n         $tail" "If Windows was shut down uncleanly, delete $PgData\postmaster.pid and run this script again. Full log: $pgLog"
    }
    Ok "PostgreSQL started"
}

# ---------------------------------------------------------------------------------------------------- migrations
Step "Database migrations"
Push-Location $Root
try {
    # alembic logs progress on stderr; Windows PowerShell 5.1 would treat that as an error under "Stop".
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Alembic -c backend/alembic.ini upgrade head 2>&1 | ForEach-Object { "    $_" }
    $alembicExit = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($alembicExit -ne 0) { Fail "alembic upgrade head failed." "Check DATABASE_URL in .env (user, password, database name) and that the database exists." }
} finally { Pop-Location }
Ok "Schema is up to date"

# ---------------------------------------------------------------------------------------------------- network
Step "Network address for the phone"
$lan = Get-LanIPv4
if ($lan) {
    Ok "$($lan.Ip) on '$($lan.Alias)'"
    $env:LAN_IP = $lan.Ip
    $env:PHONE_URL = "https://$($lan.Ip):$WebPort/#/capture"
} else {
    Warn "No Wi-Fi/LAN address found. The laptop URL works; connect to Wi-Fi (or a phone hotspot) to use the phone."
    Remove-Item Env:LAN_IP -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_URL -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------------------------------- servers
Step "Starting the API and the web app (separate windows)"
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$api = Start-Process -FilePath "powershell.exe" -PassThru -WorkingDirectory $Root -ArgumentList @(
    "-NoExit", "-NoProfile", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'PackComply API'; & '$Python' -m uvicorn backend.app.main:app --host 127.0.0.1 --port $ApiPort"
)
$web = Start-Process -FilePath "powershell.exe" -PassThru -WorkingDirectory $Frontend -ArgumentList @(
    "-NoExit", "-NoProfile", "-Command",
    "`$Host.UI.RawUI.WindowTitle = 'PackComply web (HTTPS)'; npm run dev:https -- --port $WebPort --strictPort"
)
@{ api = $api.Id; web = $web.Id; started = (Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
Ok "API window PID $($api.Id), web window PID $($web.Id)"

Step "Waiting for the API"
$deadline = (Get-Date).AddSeconds(90)
while (-not (Test-ApiReady)) {
    if ($api.HasExited) { Fail "The API window closed early." "Look at the error in a new run:  .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app" }
    if ((Get-Date) -gt $deadline) { Fail "The API did not become ready within 90 s (/api/health/ready)." "Check the 'PackComply API' window for the error. A 503 there usually means PostgreSQL rejected the .env credentials." }
    Start-Sleep -Milliseconds 800
}
Ok "API ready: /api/health/ready answered 200"

Step "Waiting for the web app"
$deadline = (Get-Date).AddSeconds(60)
while (-not (Test-TcpOpen $WebPort)) {
    if ($web.HasExited) { Fail "The web app window closed early." "Run  cd frontend ; npm run dev:https  to see the error." }
    if ((Get-Date) -gt $deadline) { Fail "The web app did not open port $WebPort within 60 s." "Check the 'PackComply web (HTTPS)' window." }
    Start-Sleep -Milliseconds 500
}
Ok "Web app is listening on port $WebPort"

if (-not $SkipOcrWait) {
    Step "Waiting for OCR warm-up (English + Hindi models; the first start is the slowest)"
    $started = Get-Date
    $deadline = $started.AddSeconds($OcrWarmupTimeoutSeconds)
    $state = ""
    while ($true) {
        $status = Get-SystemStatus
        $state = if ($status) { $status.ocr } else { "unknown" }
        if ($state -eq "ready" -or $state -eq "unavailable") { break }
        if ((Get-Date) -gt $deadline) { break }
        Write-Host ("`r    warming up ... {0:N0} s" -f ((Get-Date) - $started).TotalSeconds) -NoNewline
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    if ($state -eq "ready") { Ok ("OCR ready after {0:N0} s" -f ((Get-Date) - $started).TotalSeconds) }
    elseif ($state -eq "unavailable") { Warn "OCR failed to load (see the 'PackComply API' window). Photos cannot be read until this is fixed; run: python -m backend.scripts.warm_ocr" }
    else { Warn "OCR was still warming up after $OcrWarmupTimeoutSeconds s. The first photo will wait for the models." }
}

# ---------------------------------------------------------------------------------------------------- firewall
$laptopUrl = "https://localhost:$WebPort/#/overview"
$phoneUrl = if ($lan) { "https://$($lan.Ip):$WebPort/#/capture" } else { $null }
$firewallMissing = $false
if ($lan) {
    $rule = Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue
    if (-not $rule) { $firewallMissing = $true }
}

# ---------------------------------------------------------------------------------------------------- summary
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " PackComply is running" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Laptop : $laptopUrl"
if ($phoneUrl) {
    Write-Host " Phone  : $phoneUrl   (same Wi-Fi as this laptop)"
    Write-Host ""
    & $Python -m backend.scripts.print_qr $phoneUrl
} else {
    Write-Host " Phone  : no Wi-Fi/LAN address found"
}
Write-Host ""
Write-Host " The browser warns about the certificate once (it is generated on this laptop): Advanced -> Continue."
if ($firewallMissing) {
    Write-Host ""
    Write-Host " Windows Firewall has no rule for port $WebPort, so the phone may not connect. Run this ONCE in an" -ForegroundColor Yellow
    Write-Host " Administrator PowerShell:" -ForegroundColor Yellow
    Write-Host "   New-NetFirewallRule -DisplayName `"$FirewallRule`" -Direction Inbound -Protocol TCP -LocalPort $WebPort -Action Allow -Profile Any" -ForegroundColor White
}
Write-Host ""
Write-Host " Seed demo data (optional, once):  .\.venv\Scripts\python.exe -m backend.scripts.seed_demo"
Write-Host " Stop everything:                  scripts\stop-demo.ps1"
Write-Host ""

if (-not $NoBrowser) { Start-Process $laptopUrl }
