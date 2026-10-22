<#
.SYNOPSIS
  One-command PackComply demo start for Windows PowerShell (no Docker).

.DESCRIPTION
  Starts the HTTPS Vite dev server, checks/starts PostgreSQL (pg_ctl), applies database migrations only when needed,
  starts the API (uvicorn), runs the pre-flight checks while the API boots, waits until the API and the web app answer
  (and the OCR models are ready), then prints the laptop URL, the phone URLs, a QR code and a per-step timing table.

  Run from the repository root (any folder works):
      powershell -ExecutionPolicy Bypass -File scripts\start-demo.ps1
  Stop everything with scripts\stop-demo.ps1.

.PARAMETER NoBrowser   Do not open the laptop URL in the default browser.
.PARAMETER KeepSessions Do not sign everyone out: existing browser sessions stay valid (default: the API start deletes all sessions).
.PARAMETER Yes         Answer "yes" to prompts (for example installing node_modules).
.PARAMETER Fast        Skip the disk, firewall and OCR-model pre-flight checks (a warning says so).
.PARAMETER SkipOcrWait Do not wait for the OCR warm-up (the first photo then loads the models, which is slow).
#>
param(
    [switch]$NoBrowser,
    [switch]$KeepSessions,
    [switch]$Yes,
    [switch]$SkipOcrWait,
    [switch]$Fast
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
$FirewallRule = "PackComply dev $WebPort"
$PgReadyTimeoutSeconds = 60
$PollMs = 500
$PgLog  = Join-Path $StateDir "postgres.log"
$ApiLog = Join-Path $StateDir "api.log"
$WebLog = Join-Path $StateDir "web.log"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

# Per-step timer: Step closes the previous step's clock and opens the next; Show-Timings prints the table.
$script:Timings = New-Object System.Collections.Generic.List[object]
$script:CurrentStep = $null
$script:StepClock = $null
$script:TotalClock = [System.Diagnostics.Stopwatch]::StartNew()
$script:Started = New-Object System.Collections.Generic.List[int]
function Stop-StepTimer {
    if ($script:CurrentStep) { $script:Timings.Add([pscustomobject]@{ Step = $script:CurrentStep; Seconds = $script:StepClock.Elapsed.TotalSeconds }); $script:CurrentStep = $null }
}
function Show-Timings {
    Stop-StepTimer
    Write-Host ""
    Write-Host " Where the time went" -ForegroundColor White
    foreach ($t in $script:Timings) { Write-Host ("   {0,-52} {1,7:N1} s" -f $t.Step, $t.Seconds) }
    Write-Host ("   {0,-52} {1,7:N1} s" -f "TOTAL", $script:TotalClock.Elapsed.TotalSeconds) -ForegroundColor White
}
function Step($text)  { Stop-StepTimer; Write-Host "`n==> $text" -ForegroundColor Cyan; $script:CurrentStep = $text; $script:StepClock = [System.Diagnostics.Stopwatch]::StartNew() }
function Ok($text)    { Write-Host "    OK   $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "    WARN $text" -ForegroundColor Yellow }
function Fail($text, $fix) {
    Write-Host "`n    FAIL $text" -ForegroundColor Red
    if ($fix) { Write-Host "         $fix" -ForegroundColor Yellow }
    foreach ($id in $script:Started) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { & taskkill.exe /PID $id /T /F | Out-Null } }
    Show-Timings
    exit 1
}

# Opens a visible window that runs a command and writes its output to a UTF-8 log (Tee-Object would write UTF-16).
function Start-DemoWindow([string]$title, [string]$workDir, [string]$command, [string]$logFile) {
    $line = "`$Host.UI.RawUI.WindowTitle = '$title'; Set-Content -Path '$logFile' -Value '' -Encoding UTF8; $command 2>&1 | ForEach-Object { Add-Content -Path '$logFile' -Value ([string]`$_) -Encoding UTF8; `$_ }"
    $proc = Start-Process -FilePath "powershell.exe" -PassThru -WorkingDirectory $workDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $line)
    $script:Started.Add($proc.Id)
    return $proc
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

# LAN_IP from the environment or .env wins as the primary address.
function Get-ConfiguredLanIp {
    if ($env:LAN_IP) { return $env:LAN_IP.Trim() }
    $envFile = Join-Path $Root ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*LAN_IP\s*=\s*\S' } | Select-Object -First 1
        if ($line) { return (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'") }
    }
    return $null
}

# Every non-loopback IPv4 of the active adapters, best first (LAN_IP, then adapters with a gateway, then Wi-Fi). Virtual
# adapters (Hyper-V, WSL, VPN, Docker...) are skipped, but the Windows Mobile Hotspot adapter ("Wi-Fi Direct Virtual
# Adapter", usually 192.168.137.x) is kept because phones connect through it.
function Get-LanIPv4All {
    $virtual = "Hyper-V|vEthernet|VMware|VirtualBox|Virtual|WSL|Docker|TAP-|TUN|WireGuard|VPN|Tailscale|ZeroTier|Npcap|Loopback|Bluetooth|Pseudo|Teredo|ISATAP"
    $candidates = @()
    foreach ($cfg in (Get-NetIPConfiguration -ErrorAction SilentlyContinue)) {
        if (-not $cfg.NetAdapter -or $cfg.NetAdapter.Status -ne "Up") { continue }
        $hotspot = $cfg.NetAdapter.InterfaceDescription -match "Wi-?Fi Direct"
        if (-not $hotspot -and ($cfg.NetAdapter.InterfaceDescription -match $virtual -or $cfg.InterfaceAlias -match $virtual)) { continue }
        foreach ($address in $cfg.IPv4Address) {
            $ip = $address.IPAddress
            if ($ip -match "^169\.254\." -or $ip -match "^127\.") { continue }
            $wifi = $hotspot -or ($cfg.NetAdapter.PhysicalMediaType -match "802\.11|Native 802\.11") -or ($cfg.InterfaceAlias -match "Wi-?Fi|Wireless|WLAN")
            $candidates += [pscustomobject]@{ Ip = $ip; Wifi = $wifi; Gateway = [bool]$cfg.IPv4DefaultGateway; Alias = $cfg.InterfaceAlias }
        }
    }
    $sorted = @($candidates | Sort-Object @{ Expression = { -not $_.Gateway } }, @{ Expression = { -not $_.Wifi } })
    $forced = Get-ConfiguredLanIp
    if ($forced -and $forced -match '^\d{1,3}(\.\d{1,3}){3}$') {
        $match = $sorted | Where-Object { $_.Ip -eq $forced } | Select-Object -First 1
        $first = if ($match) { $match } else { [pscustomobject]@{ Ip = $forced; Wifi = $false; Gateway = $false; Alias = "LAN_IP setting" } }
        $sorted = @($first) + @($sorted | Where-Object { $_.Ip -ne $forced })
    }
    return $sorted
}

# The rule must allow inbound TCP on the web port for every profile: a phone hotspot network is usually "Public".
function Test-FirewallRuleOk {
    $rules = @(Get-NetFirewallRule -DisplayName $FirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq "True" -and $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" })
    foreach ($rule in $rules) {
        $port = $rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        $profiles = [string]$rule.Profile
        $allProfiles = ($profiles -match "Any") -or (($profiles -match "Domain") -and ($profiles -match "Private") -and ($profiles -match "Public"))
        if ($allProfiles -and $port -and ("$($port.LocalPort)" -eq "$WebPort")) { return $true }
    }
    return $false
}

# curl.exe ships with Windows 10+; -k accepts the dev server's self-signed certificate for this local probe. (A PowerShell
# certificate callback fails on the network thread, so Invoke-WebRequest cannot be used here.)
function Test-WebReady {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { return (Test-TcpOpen $WebPort) }
    $code = & curl.exe -k -s -o NUL -w "%{http_code}" --max-time 3 "https://127.0.0.1:$WebPort/"
    return ("$code" -match '^[23]\d\d$')
}

Write-Host "PackComply demo start" -ForegroundColor White
Write-Host "Repository: $Root"
if ($Fast) { Warn "-Fast: the disk, firewall and OCR-model pre-flight checks are skipped." }

# ---------------------------------------------------------------------------------------------------- prerequisites
Step "Prerequisites"
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
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

# ---------------------------------------------------------------------------------------------------- web app (launch)
# Vite does not need the database, so it starts now and loads while PostgreSQL, the migrations and the API come up.
Step "Web app: launch"
$web = Start-DemoWindow "PackComply web (HTTPS)" $Frontend "npm run dev:https -- --port $WebPort --strictPort" $WebLog
$webLaunched = Get-Date
@{ web = $web.Id; started = (Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
Ok "web window PID $($web.Id); log: $WebLog"

# ---------------------------------------------------------------------------------------------------- PostgreSQL
Step "PostgreSQL"
$pgCtl = Join-Path $PgBin "pg_ctl.exe"
$pgIsReady = Join-Path $PgBin "pg_isready.exe"
function Test-PgAccepting {
    if (Test-Path $pgIsReady) { & $pgIsReady -h 127.0.0.1 -p $PgPort -q | Out-Null; return $LASTEXITCODE -eq 0 }
    return (Test-TcpOpen $PgPort)
}
if (Test-PgAccepting) {
    Ok "PostgreSQL already running on port $PgPort (leaving it as it is)"
} else {
    if (-not (Test-Path $pgCtl)) { Fail "pg_ctl.exe not found at $pgCtl" "Edit `$PgBin / `$PgData at the top of scripts\start-demo.ps1 to point at your PostgreSQL folders." }
    if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) { Fail "$PgData is not a PostgreSQL data directory." "Edit `$PgData at the top of scripts\start-demo.ps1." }
    # pg_ctl status exits 0 when a server runs from this data directory and 3 when none does. A postmaster.pid left
    # behind with no server (unclean shutdown) blocks the start, so it is removed here - never while a server runs.
    & $pgCtl status -D $PgData | Out-Null
    $pgRunning = ($LASTEXITCODE -eq 0)
    $postmasterPid = Join-Path $PgData "postmaster.pid"
    if (-not $pgRunning -and (Test-Path $postmasterPid)) {
        Remove-Item $postmasterPid -Force
        Warn "No PostgreSQL server is running, so the stale lock file was deleted: $postmasterPid"
    }
    if ($pgRunning) { Write-Host "    PostgreSQL is running but not accepting connections yet; waiting ..." }
    else {
        Write-Host "    starting PostgreSQL ($PgData), log: $PgLog"
        # Never wait on pg_ctl: the postgres child it leaves behind inherits its stdout/stderr handles, so a plain
        # call, a pipe or Start-Process -Wait never returns on Windows. Start it hidden, with its output in files, and poll.
        $pgOut = Join-Path $StateDir "pg_ctl.out.log"
        $pgErr = Join-Path $StateDir "pg_ctl.err.log"
        Start-Process -FilePath $pgCtl -ArgumentList @("start", "-D", "`"$PgData`"", "-l", "`"$PgLog`"") -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $pgOut -RedirectStandardError $pgErr | Out-Null
    }
    $pgClock = [System.Diagnostics.Stopwatch]::StartNew()
    $lastShown = 0
    while (-not (Test-PgAccepting)) {
        $waited = [int][math]::Floor($pgClock.Elapsed.TotalSeconds)
        if ($waited -ge $PgReadyTimeoutSeconds) {
            $tail = if (Test-Path $PgLog) { (Get-Content $PgLog -Tail 8) -join "`n         " } else { "(no log)" }
            Fail "PostgreSQL did not accept connections on port $PgPort within $PgReadyTimeoutSeconds s. Last log lines:`n         $tail" "Full log: $PgLog (pg_ctl output: $StateDir\pg_ctl.err.log)"
        }
        if ($waited -gt $lastShown) { $lastShown = $waited; Write-Host "    waiting for PostgreSQL ($waited/$PgReadyTimeoutSeconds s)" }
        Start-Sleep -Milliseconds $PollMs
    }
    Ok ("PostgreSQL is accepting connections ({0:N1} s)" -f $pgClock.Elapsed.TotalSeconds)
}

# ---------------------------------------------------------------------------------------------------- migrations
# The newest revision is read from the migration files (no process needed); one "alembic current" tells where the
# database is. Only when they differ is "upgrade head" run. The pre-flight reuses this result.
Step "Database migrations"
$headIds = @()
$versionsDir = Join-Path $Root "backend\alembic\versions"
if (Test-Path $versionsDir) {
    $revs = @{}; $downs = @{}
    foreach ($file in Get-ChildItem $versionsDir -Filter "*.py") {
        $text = Get-Content $file.FullName -Raw
        $rev = [regex]::Match($text, '(?m)^revision\s*(?::\s*\w+\s*)?=\s*["'']([^"'']+)["'']')
        if (-not $rev.Success) { continue }
        $revs[$rev.Groups[1].Value] = $true
        $downLine = [regex]::Match($text, '(?m)^down_revision[^\r\n]*').Value
        foreach ($d in [regex]::Matches($downLine, '["'']([^"'']+)["'']')) { $downs[$d.Groups[1].Value] = $true }
    }
    $headIds = @($revs.Keys | Where-Object { -not $downs.ContainsKey($_) })
}
function Invoke-Alembic([string[]]$alembicArgs) {
    Push-Location $Root
    try {
        # alembic logs progress on stderr; Windows PowerShell 5.1 would treat that as an error under "Stop".
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $out = (& $Alembic -c backend/alembic.ini @alembicArgs 2>&1 | Out-String)
        $script:AlembicExit = $LASTEXITCODE
        $ErrorActionPreference = $previous
        return $out
    } finally { Pop-Location }
}
$currentText = Invoke-Alembic @("current")
$atHead = ($headIds.Count -gt 0) -and ($script:AlembicExit -eq 0) -and (@($headIds | Where-Object { $currentText -notmatch [regex]::Escape($_) }).Count -eq 0)
if ($atHead) {
    Ok "migrations up to date (head: $($headIds -join ', '))"
} else {
    Write-Host "    database is not at the newest revision; running alembic upgrade head ..."
    (Invoke-Alembic @("upgrade", "head")) -split "`r?`n" | Where-Object { $_ } | ForEach-Object { Write-Host "    $_" }
    if ($script:AlembicExit -ne 0) { Fail "alembic upgrade head failed." "Check DATABASE_URL in .env (user, password, database name) and that the database exists." }
    $atHead = $true
    Ok "Schema is up to date"
}

# ---------------------------------------------------------------------------------------------------- network
Step "Network address for the phone"
$lanAll = @(Get-LanIPv4All)
$lan = $lanAll | Select-Object -First 1
if ($lan) {
    foreach ($entry in $lanAll) { Ok "$($entry.Ip) on '$($entry.Alias)'" }
    $env:LAN_IP = $lan.Ip
    $env:PHONE_URL = "https://$($lan.Ip):$WebPort/#/capture"  # signed out, the phone shows the sign-in page, then continues to capture
} else {
    Warn "No Wi-Fi/LAN address found. The laptop URL works; connect to Wi-Fi (or a phone hotspot) to use the phone."
    Remove-Item Env:LAN_IP -ErrorAction SilentlyContinue
    Remove-Item Env:PHONE_URL -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------------------------------- API (launch)
# Started before the pre-flight so the API boots (imports, OCR warm-up) while the checks run. A critical failure below
# closes both windows again.
Step "API: launch"
# The demo always starts at the sign-in page: the API deletes every stored session on start (sessions only, no other data).
if ($KeepSessions) { Remove-Item Env:DEMO_FORCE_RELOGIN -ErrorAction SilentlyContinue; Ok "Existing sessions are kept (-KeepSessions)" }
else { $env:DEMO_FORCE_RELOGIN = "1" }
$api = Start-DemoWindow "PackComply API" $Root "& '$Python' -m uvicorn backend.app.main:app --host 127.0.0.1 --port $ApiPort" $ApiLog
$apiLaunched = Get-Date
@{ api = $api.Id; web = $web.Id; started = (Get-Date).ToString("o") } | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
Ok "API window PID $($api.Id); log: $ApiLog"

# ---------------------------------------------------------------------------------------------------- pre-flight
# One summary of everything the demo depends on. A critical check that failed closes the windows and stops here, with
# the fix; warnings (no Wi-Fi, no firewall rule) are shown but do not stop the laptop-only demo.
Step "Pre-flight checks"
$checks = New-Object System.Collections.Generic.List[object]
function Add-Check([string]$name, [string]$state, [string]$detail, [string]$fix = "") {
    $checks.Add([pscustomobject]@{ Name = $name; State = $state; Detail = $detail; Fix = $fix })
}

if (Test-PgAccepting) { Add-Check "Database" "OK" "PostgreSQL answers on port $PgPort" }
else { Add-Check "Database" "FAIL" "PostgreSQL is not answering on port $PgPort" "Start it with pg_ctl or re-run this script; check the log in $PgLog" }

if ($atHead) { Add-Check "Migrations" "OK" "database is at head ($($headIds -join ', '))" }
else { Add-Check "Migrations" "FAIL" "database is not at the newest migration" "Run:  .\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head" }

if ($Fast) {
    Add-Check "Skipped" "WARN" "OCR models, disk space and firewall checks (-Fast)"
} else {
    # OCR models are on disk (no download is attempted while the demo runs).
    $modelDir = Join-Path $Root ".paddlex\official_models"
    $modelFolders = if (Test-Path $modelDir) { @(Get-ChildItem $modelDir -Directory -ErrorAction SilentlyContinue | Where-Object { (Get-ChildItem $_.FullName -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1) }) } else { @() }
    $detModels = @($modelFolders | Where-Object { $_.Name -match "det" }).Count
    $recModels = @($modelFolders | Where-Object { $_.Name -match "rec" }).Count
    if ($detModels -ge 1 -and $recModels -ge 1) { Add-Check "OCR models" "OK" "$($modelFolders.Count) model folders in .paddlex\official_models" }
    else { Add-Check "OCR models" "FAIL" "no detection/recognition models in $modelDir" "Download them once (needs internet):  .\.venv\Scripts\python.exe -m backend.scripts.warm_ocr" }

    # Free disk space where the uploads and the database live.
    try {
        $drive = Get-PSDrive -Name ($Root.Substring(0, 1)) -ErrorAction Stop
        $freeGb = [math]::Round($drive.Free / 1GB, 1)
        if ($freeGb -lt 0.5) { Add-Check "Disk space" "FAIL" "only $freeGb GB free on $($drive.Name):" "Free some space; uploads and PDF reports need room." }
        elseif ($freeGb -lt 2) { Add-Check "Disk space" "WARN" "$freeGb GB free on $($drive.Name):" }
        else { Add-Check "Disk space" "OK" "$freeGb GB free on $($drive.Name):" }
    } catch { Add-Check "Disk space" "WARN" "could not read the free space" }
}

if ($lan) { Add-Check "LAN address" "OK" "$($lan.Ip) on '$($lan.Alias)'" }
else { Add-Check "LAN address" "WARN" "no Wi-Fi/LAN address (the laptop URL still works; the phone will not)" }
$firewallProblem = $false
if ($lan -and -not $Fast) {
    if (Test-FirewallRuleOk) { Add-Check "Firewall rule" "OK" "'$FirewallRule' allows TCP $WebPort on all profiles" }
    else { $firewallProblem = $true; Add-Check "Firewall rule" "WARN" "no rule (or Private-only) for port $WebPort; the phone may not connect (command is printed at the end)" }
}

foreach ($check in $checks) {
    $line = "    {0,-4} {1,-14} {2}" -f $check.State, $check.Name, $check.Detail
    $colour = switch ($check.State) { "OK" { "Green" } "WARN" { "Yellow" } default { "Red" } }
    Write-Host $line -ForegroundColor $colour
}
$critical = @($checks | Where-Object { $_.State -eq "FAIL" })
if ($critical.Count -gt 0) {
    Fail "$($critical.Count) critical check(s) failed: $(($critical | ForEach-Object { $_.Name }) -join ', '). Closing the servers that were started." $critical[0].Fix
}

# ---------------------------------------------------------------------------------------------------- wait for both
# The API and the web app have been booting side by side; wait for both in one loop.
Step "Waiting for the API and the web app"
$apiOk = $false; $webOk = $false
$apiDeadline = $apiLaunched.AddSeconds(90)
$webDeadline = $webLaunched.AddSeconds(90)
while (-not ($apiOk -and $webOk)) {
    if (-not $apiOk) {
        if (Test-ApiReady) { $apiOk = $true; Ok ("API ready: /api/health/ready answered 200 ({0:N1} s after launch)" -f ((Get-Date) - $apiLaunched).TotalSeconds) }
        elseif ($api.HasExited) { Fail "API FAILED: the API window closed early." "Log file: $ApiLog   (or run:  .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app)" }
        elseif ((Get-Date) -gt $apiDeadline) { Fail "API FAILED: /api/health/ready did not answer 200 within 90 s." "Log file: $ApiLog. A 503 usually means PostgreSQL rejected the .env credentials." }
    }
    if (-not $webOk) {
        if (Test-WebReady) { $webOk = $true; Ok ("Web app answers on port $WebPort ({0:N1} s after launch)" -f ((Get-Date) - $webLaunched).TotalSeconds) }
        elseif ($web.HasExited) { Fail "WEB APP FAILED: the window closed early." "Log file: $WebLog   (or run:  cd frontend ; npm run dev:https)" }
        elseif ((Get-Date) -gt $webDeadline) { Fail "WEB APP FAILED: port $WebPort did not answer over HTTPS within 90 s." "Log file: $WebLog" }
    }
    if (-not ($apiOk -and $webOk)) { Start-Sleep -Milliseconds $PollMs }
}

if (-not $SkipOcrWait) {
    Step "OCR warm-up (English + Hindi models; the first start is the slowest)"
    $started = Get-Date
    $deadline = $started.AddSeconds($OcrWarmupTimeoutSeconds)
    $state = ""
    while ($true) {
        $status = Get-SystemStatus
        $state = if ($status) { $status.ocr } else { "unknown" }
        if ($state -eq "ready" -or $state -eq "unavailable") { break }
        if ((Get-Date) -gt $deadline) { break }
        Write-Host ("`r    warming up ... {0:N0} s" -f ((Get-Date) - $started).TotalSeconds) -NoNewline
        Start-Sleep -Milliseconds $PollMs
    }
    Write-Host ""
    if ($state -eq "ready") { Ok ("OCR ready ({0:N1} s in this step, {1:N1} s after the API launched)" -f ((Get-Date) - $started).TotalSeconds, ((Get-Date) - $apiLaunched).TotalSeconds) }
    elseif ($state -eq "unavailable") { Warn "OCR failed to load (see $ApiLog). Photos cannot be read until this is fixed; run: python -m backend.scripts.warm_ocr" }
    else { Warn "OCR was still warming up after $OcrWarmupTimeoutSeconds s. The first photo will wait for the models." }
}

# ---------------------------------------------------------------------------------------------------- summary
$laptopUrl = "https://localhost:$WebPort/#/login"
Step "Summary"
# Reached only after the API health check and the web app both answered (every failure above exits with its log file).
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " READY - PackComply is running (API healthy, web app answering)" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Laptop : $laptopUrl"
if ($lan) {
    foreach ($entry in $lanAll) {
        $mark = if ($entry.Ip -eq $lan.Ip) { "  <- primary, QR below" } else { "" }
        Write-Host (" Phone  : https://{0}:{1}/#/capture   [{2}]{3}" -f $entry.Ip, $WebPort, $entry.Alias, $mark)
    }
    Write-Host ""
    & $Python -m backend.scripts.print_qr "https://$($lan.Ip):$WebPort/#/capture"
} else {
    Write-Host " Phone  : no Wi-Fi/LAN address found"
}
Write-Host ""
Write-Host " The browser warns about the certificate once (it is generated on this laptop): Advanced -> Continue."
if ($firewallProblem) {
    Write-Host ""
    Write-Host " Windows Firewall has no rule allowing port $WebPort on every network profile (a hotspot is often 'Public')," -ForegroundColor Yellow
    Write-Host " so the phone may not connect. Run this ONCE in an Administrator PowerShell (it is not run for you):" -ForegroundColor Yellow
    Write-Host "   Remove-NetFirewallRule -DisplayName `"$FirewallRule`" -ErrorAction SilentlyContinue; New-NetFirewallRule -DisplayName `"$FirewallRule`" -Direction Inbound -Protocol TCP -LocalPort $WebPort -Action Allow -Profile Any" -ForegroundColor White
}
Write-Host ""
Write-Host " Seed demo data (optional, once):  .\.venv\Scripts\python.exe -m backend.scripts.seed_demo"
Write-Host " Stop everything:                  scripts\stop-demo.ps1"
Show-Timings
Write-Host ""

if (-not $NoBrowser) { Start-Process $laptopUrl }
