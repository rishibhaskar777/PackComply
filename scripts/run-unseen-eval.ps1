<#
.SYNOPSIS
  Score the UNSEEN photo set once, after a person has filled in the ground truth by hand.

.DESCRIPTION
  Runs backend.scripts.batch_evaluate over the unseen folder, writes the result into
  backend\app\assets\evaluation_report.json as the run "unseen-<date>" (the About & accuracy page shows it beside the
  latest development run) and prints the summary.

  The three steps (see README, "Evaluating on unseen photos"):
    1. python -m backend.scripts.make_ground_truth_template <folder>      -> writes the fill-in template
    2. fill backend\tests\fixtures\labels\unseen\expected_real.json BY HAND from the real packs ("filled": true)
    3. powershell -ExecutionPolicy Bypass -File scripts\run-unseen-eval.ps1   (once)

  It refuses to run while the template is untouched, and refuses a second run unless -Force is given: an unseen
  score is only honest if nobody tunes extraction or rules after seeing it.

.PARAMETER PhotoDir  Folder with the unseen photos.
.PARAMETER Note      One line describing the photos, shown beside the numbers.
.PARAMETER Force     Run again although an unseen run already exists in the report.
#>
param(
    [string]$PhotoDir = "D:\Development\VibeCoding\Projects\PackComply-samples-unseen",
    [string]$Note = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$FixtureDir = Join-Path $Root "backend\tests\fixtures\labels\unseen"
$Truth = Join-Path $FixtureDir "expected_real.json"
$Report = Join-Path $Root "backend\app\assets\evaluation_report.json"
$Label = "unseen-" + (Get-Date -Format "yyyy-MM-dd")
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = "utf-8"

function Fail($text, $fix) {
    Write-Host "`nFAIL $text" -ForegroundColor Red
    if ($fix) { Write-Host "     $fix" -ForegroundColor Yellow }
    exit 1
}

if (-not (Test-Path $Python)) { Fail "Python virtual environment not found at $Python" "Create it once: python -m venv .venv ; pip install -e `".[dev]`"" }
if (-not (Test-Path $PhotoDir)) { Fail "The unseen photo folder does not exist: $PhotoDir" "Put the new, never-seen photos there (or pass -PhotoDir), then run: python -m backend.scripts.make_ground_truth_template `"$PhotoDir`"" }
if (-not (Test-Path $Truth)) { Fail "No ground-truth template at $Truth" "Step 1: python -m backend.scripts.make_ground_truth_template `"$PhotoDir`"   then fill it in by hand (step 2)." }

# Count the photos a person has finished; a template still all "filled": false produces no numbers.
$truthJson = Get-Content $Truth -Raw -Encoding UTF8 | ConvertFrom-Json
$entries = @($truthJson.PSObject.Properties | Where-Object { $_.Name -notlike "_*" })
$filled = @($entries | Where-Object { $_.Value._meta -and $_.Value._meta.filled -eq $true })
if ($filled.Count -eq 0) { Fail "Every photo in the template is still marked `"filled`": false ($($entries.Count) photos)." "Step 2: read each real pack yourself, fill the values, and set `"filled`": true in $Truth" }
Write-Host "Ground truth: $($filled.Count) of $($entries.Count) photos filled by hand" -ForegroundColor Cyan

if ((Test-Path $Report) -and -not $Force) {
    $existing = (Get-Content $Report -Raw -Encoding UTF8 | ConvertFrom-Json).runs | Where-Object { $_.kind -eq "unseen" }
    if ($existing) { Fail "The report already holds an unseen run ('$(@($existing)[0].label)'). Measuring again after seeing it is not an unseen score." "If this is a genuinely new, untouched batch, re-run with -Force." }
}

Push-Location $Root
try {
    $arguments = @("-m", "backend.scripts.batch_evaluate", $PhotoDir, "--fixture-dir", $FixtureDir, "--report-json", $Report, "--run-label", $Label)
    if ($Note) { $arguments += @("--run-note", $Note) }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { Fail "batch_evaluate failed (exit $LASTEXITCODE)." "Read the output above." }
} finally { Pop-Location }

$run = (Get-Content $Report -Raw -Encoding UTF8 | ConvertFrom-Json).runs | Where-Object { $_.label -eq $Label } | Select-Object -First 1
if (-not $run) { Fail "The report does not contain the run '$Label'." "batch_evaluate writes a report only when ground truth exists for the photos." }
$group = $run.groups | Where-Object { $_.name -eq "ALL" } | Select-Object -First 1
if (-not $group) { $group = $run.groups | Select-Object -Last 1 }
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Unseen evaluation: $Label ($($run.kind))" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host (" Photos scored        : {0}" -f $group.photos)
Write-Host (" Precision / recall   : {0:P1} / {1:P1}" -f $group.precision, $group.recall)
Write-Host (" Rule agreement       : {0}/{1}" -f $group.rule_agreement.agree, $group.rule_agreement.total)
Write-Host (" Verdicts correct     : {0}/{1}" -f $group.verdicts_correct, $group.photos)
Write-Host (" False NON_COMPLIANT  : {0}" -f $group.false_non_compliant)
Write-Host (" False PASS (mandatory): {0}" -f $group.false_pass_mandatory)
Write-Host ""
Write-Host " Written to $Report. Restart the API (or reload About & accuracy) to see it beside the development run."
Write-Host " Do not change extraction or rules in response to this result."
