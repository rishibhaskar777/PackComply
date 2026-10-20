<#
.SYNOPSIS
  Restore the PackComply database (and optionally the uploads / evidence folder) from a backup made by backup-db.ps1.

.DESCRIPTION
  DESTRUCTIVE: the current contents of the database are replaced by the backup. You must type the backup's file name to
  continue. Stop the API first (scripts\stop-demo.ps1) so no connection holds the tables; this script refuses to run while
  the API port is in use.

  powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\packcomply-20261021-090000.dump
  powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\packcomply-20261021-090000.dump -UploadsZip backups\packcomply-uploads-20261021-090000.zip

.PARAMETER File        The .dump file to restore (required).
.PARAMETER UploadsZip  The matching uploads zip to unpack into the uploads folder (optional; asks again before extracting).
#>
param(
    [Parameter(Mandatory = $true)][string]$File,
    [string]$UploadsZip = ""
)

# ------------------------------------------------------------------------------------------------------------
# Configuration: the same PostgreSQL location as scripts\start-demo.ps1.
$PgBin   = "D:\Development\PGSQL\pgsql\bin"
$PgData  = "D:\Development\PGSQL\data"
$ApiPort = 8000
# ------------------------------------------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Alembic = Join-Path $Root ".venv\Scripts\alembic.exe"

function Fail($text, $fix) {
    Write-Host "`nFAIL $text" -ForegroundColor Red
    if ($fix) { Write-Host "     $fix" -ForegroundColor Yellow }
    exit 1
}

function Read-DotEnv([string]$path) {
    $values = @{}
    if (Test-Path $path) {
        foreach ($line in Get-Content $path -Encoding UTF8) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$' -and $line -notmatch '^\s*#') { $values[$Matches[1]] = $Matches[2].Trim('"').Trim("'") }
        }
    }
    return $values
}

$dumpPath = if ([System.IO.Path]::IsPathRooted($File)) { $File } else { Join-Path (Get-Location) $File }
if (-not (Test-Path $dumpPath)) { Fail "Backup file not found: $dumpPath" "Backups are written to the backups\ folder by scripts\backup-db.ps1." }
$dumpPath = (Resolve-Path $dumpPath).Path

$envValues = Read-DotEnv (Join-Path $Root ".env")
$url = $envValues["DATABASE_URL"]
if (-not $url) { Fail "DATABASE_URL is not set in .env." "" }
if ($url -notmatch '^[a-z+]+://(?<user>[^:@/]+)(:(?<password>[^@]*))?@(?<host>[^:/]+)(:(?<port>\d+))?/(?<db>[^?]+)') { Fail "DATABASE_URL could not be read." "" }
$dbUser = [uri]::UnescapeDataString($Matches.user); $dbPassword = [uri]::UnescapeDataString("$($Matches.password)")
$dbHost = $Matches.host; $dbPort = if ($Matches.port) { $Matches.port } else { "5432" }; $dbName = $Matches.db

$pgRestore = Join-Path $PgBin "pg_restore.exe"
if (-not (Test-Path $pgRestore)) { Fail "pg_restore.exe not found at $pgRestore" "Edit `$PgBin at the top of scripts\restore-db.ps1 (the same folder as in start-demo.ps1)." }

$apiBusy = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($apiBusy) { Fail "Something is listening on port $ApiPort (the API?). Restoring under a running API can fail or be undone by it." "Stop it first:  scripts\stop-demo.ps1   (add -StopPostgres only if you also want the database down; keep it running for the restore)." }
$client = New-Object System.Net.Sockets.TcpClient
try { $client.Connect($dbHost, [int]$dbPort) } catch { Fail "PostgreSQL is not answering on ${dbHost}:${dbPort}." "Start it (pg_ctl start -D `"$PgData`") and run this script again." } finally { $client.Close() }

$name = Split-Path -Leaf $dumpPath
Write-Host ""
Write-Host "This will REPLACE everything in database '$dbName' on ${dbHost}:${dbPort} with the contents of:" -ForegroundColor Yellow
Write-Host "    $dumpPath  ($("{0:N1}" -f ((Get-Item $dumpPath).Length / 1MB)) MB, $((Get-Item $dumpPath).LastWriteTime))" -ForegroundColor Yellow
Write-Host "Records created after that backup will be lost." -ForegroundColor Yellow
$typed = Read-Host "Type the backup file name ($name) to continue, or anything else to cancel"
if ($typed -ne $name) { Write-Host "Cancelled. Nothing was changed."; exit 0 }

$env:PGPASSWORD = $dbPassword
try {
    & $pgRestore --host $dbHost --port $dbPort --username $dbUser --dbname $dbName --clean --if-exists --no-owner --exit-on-error $dumpPath
    if ($LASTEXITCODE -ne 0) { Fail "pg_restore failed (exit $LASTEXITCODE). The database may be partly restored." "Re-run after fixing the error above, or restore an earlier backup." }
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Host "    OK   database restored" -ForegroundColor Green

if (Test-Path $Alembic) {
    Push-Location $Root
    try {
        $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & $Alembic -c backend/alembic.ini upgrade head 2>&1 | ForEach-Object { "    $_" }
        $ErrorActionPreference = $previous
    } finally { Pop-Location }
    Write-Host "    OK   migrations are at head (an older backup is brought up to date)" -ForegroundColor Green
}

if ($UploadsZip) {
    $zipPath = if ([System.IO.Path]::IsPathRooted($UploadsZip)) { $UploadsZip } else { Join-Path (Get-Location) $UploadsZip }
    if (-not (Test-Path $zipPath)) { Fail "Uploads zip not found: $zipPath" "The database was restored; the evidence photos were not." }
    $uploadSetting = if ($envValues["UPLOAD_DIR"]) { $envValues["UPLOAD_DIR"] } else { "uploads" }
    $uploads = if ([System.IO.Path]::IsPathRooted($uploadSetting)) { $uploadSetting } else { Join-Path $Root $uploadSetting }
    $zipName = Split-Path -Leaf $zipPath
    $typedZip = Read-Host "Unpack $zipName into '$uploads' (existing files with the same name are overwritten)? Type the zip file name to continue"
    if ($typedZip -ne $zipName) { Write-Host "Skipped the uploads. The database was restored." }
    else {
        New-Item -ItemType Directory -Force -Path $uploads | Out-Null
        Expand-Archive -Path $zipPath -DestinationPath $uploads -Force
        Write-Host "    OK   uploads restored to $uploads" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Restore finished. Start the demo again with scripts\start-demo.ps1" -ForegroundColor Green
