<#
.SYNOPSIS
  Back up the PackComply database (pg_dump) and the uploads / evidence folder (zip) before a demo.

.DESCRIPTION
  Writes backups\packcomply-<timestamp>.dump (PostgreSQL custom format, restorable with scripts\restore-db.ps1) and
  backups\packcomply-uploads-<timestamp>.zip (the stored photos, which are the evidence behind every record).
  The database connection is read from DATABASE_URL in .env; the PostgreSQL bin folder is the same setting as in
  start-demo.ps1. PostgreSQL must be running (scripts\start-demo.ps1 starts it).

  powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1
#>
param(
    [switch]$SkipUploads
)

# ------------------------------------------------------------------------------------------------------------
# Configuration: the same PostgreSQL location as scripts\start-demo.ps1.
$PgBin  = "D:\Development\PGSQL\pgsql\bin"
$PgData = "D:\Development\PGSQL\data"
# ------------------------------------------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path $Root "backups"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"

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

$envValues = Read-DotEnv (Join-Path $Root ".env")
$url = $envValues["DATABASE_URL"]
if (-not $url) { Fail "DATABASE_URL is not set in .env." "Copy .env.example to .env and set the PostgreSQL credentials." }
if ($url -notmatch '^[a-z+]+://(?<user>[^:@/]+)(:(?<password>[^@]*))?@(?<host>[^:/]+)(:(?<port>\d+))?/(?<db>[^?]+)') { Fail "DATABASE_URL could not be read." "Expected postgresql+psycopg://user:password@host:port/database" }
$dbUser = [uri]::UnescapeDataString($Matches.user); $dbPassword = [uri]::UnescapeDataString("$($Matches.password)")
$dbHost = $Matches.host; $dbPort = if ($Matches.port) { $Matches.port } else { "5432" }; $dbName = $Matches.db

$pgDump = Join-Path $PgBin "pg_dump.exe"
if (-not (Test-Path $pgDump)) { Fail "pg_dump.exe not found at $pgDump" "Edit `$PgBin at the top of scripts\backup-db.ps1 (the same folder as in start-demo.ps1)." }
$client = New-Object System.Net.Sockets.TcpClient
try { $client.Connect($dbHost, [int]$dbPort) } catch { Fail "PostgreSQL is not answering on ${dbHost}:${dbPort}." "Start it with scripts\start-demo.ps1 (or pg_ctl start -D `"$PgData`")." } finally { $client.Close() }

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$dump = Join-Path $BackupDir "packcomply-$Stamp.dump"

Write-Host "Database '$dbName' -> $dump" -ForegroundColor Cyan
$env:PGPASSWORD = $dbPassword
try {
    & $pgDump --host $dbHost --port $dbPort --username $dbUser --format custom --no-owner --file $dump $dbName
    if ($LASTEXITCODE -ne 0) { Remove-Item $dump -ErrorAction SilentlyContinue; Fail "pg_dump failed (exit $LASTEXITCODE)." "Check the credentials in DATABASE_URL." }
} finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
Write-Host ("    OK   {0:N1} MB" -f ((Get-Item $dump).Length / 1MB)) -ForegroundColor Green

if (-not $SkipUploads) {
    $uploadSetting = if ($envValues["UPLOAD_DIR"]) { $envValues["UPLOAD_DIR"] } else { "uploads" }
    $uploads = if ([System.IO.Path]::IsPathRooted($uploadSetting)) { $uploadSetting } else { Join-Path $Root $uploadSetting }
    $zip = Join-Path $BackupDir "packcomply-uploads-$Stamp.zip"
    if ((Test-Path $uploads) -and (Get-ChildItem $uploads -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        Write-Host "Uploads '$uploads' -> $zip" -ForegroundColor Cyan
        Compress-Archive -Path (Join-Path $uploads "*") -DestinationPath $zip -CompressionLevel Optimal
        Write-Host ("    OK   {0:N1} MB" -f ((Get-Item $zip).Length / 1MB)) -ForegroundColor Green
    } else {
        Write-Host "No uploaded files in '$uploads' yet; nothing to zip." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Backup finished. Restore with:  powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\packcomply-$Stamp.dump" -ForegroundColor Green
