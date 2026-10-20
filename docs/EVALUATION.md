# Accuracy evaluation

How the numbers in the README's "Accuracy" section are produced. The source of truth is `backend/app/assets/evaluation_report.json`, served by `GET /api/system/evaluation` and shown on the in-app "About & accuracy" page.

`#/about` shows what the 14 rules check (loaded from the ruleset API, each with its legal reference), a diagram of the pipeline, the privacy statement, and the **measured** accuracy: precision and recall per field, rule agreement, the verdict confusion matrix, the count of false NON_COMPLIANT verdicts and of false PASS findings on mandatory rules, dataset sizes (development / phone hold-out retired to development / unseen) and the median OCR time. All numbers come from the JSON written by the evaluation script (`backend/app/assets/evaluation_report.json`, served by `GET /api/system/evaluation`); nothing is typed into the page. Each run is labelled **Development score** (extraction was tuned while looking at those photos, so the numbers are optimistic) or **Unseen score**, with its dataset name and date.

Refresh the development report from the stored OCR fixtures (no photos needed):

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.eval_real --report-json backend/app/assets/evaluation_report.json --run-label development-2026-10 --run-note "23 labelled photos, development"
```

#### Adding a fresh UNSEEN set

Three steps: **(1)** generate the template (`make_ground_truth_template`), **(2)** fill it in by hand from the real packs, **(3)** run `scripts\run-unseen-eval.ps1` once. Nothing has been evaluated on unseen photos in this repository yet: the About page shows only the development run until a person has done step 3. The detail:


1. Put the new phone photos in one folder, for example `D:\unseen-photos` (only photos nobody has run through the pipeline or tuned on).
2. Generate the ground-truth template, a separate fixture folder keeps this set apart from the development photos: `python -m backend.scripts.make_ground_truth_template D:\unseen-photos` (writes `backend/tests/fixtures/labels/unseen/expected_real.json` and `split.json`; add `--fixture-dir` and `--group` to choose another folder or batch name). Every field is `null` and every photo is marked `"filled": false`; every photo is HOLD-OUT.
3. **Before running the pipeline on them**, read each photo yourself and fill the expected values by hand (the format is described at the top of `backend/tests/fixtures/labels/real/expected_real.json`; omit or leave `null` what the photo does not show), then set `"filled": true` for each finished photo. Unfilled photos are not scored.
4. Evaluate **once**, without tuning, with the script (it runs the same `batch_evaluate`, labels the run `unseen-<date>`, and prints the summary):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run-unseen-eval.ps1                     # default folder D:\Development\VibeCoding\Projects\PackComply-samples-unseen
   powershell -ExecutionPolicy Bypass -File scripts\run-unseen-eval.ps1 -PhotoDir D:\unseen-photos -Note "N new phone photos, labelled by hand before the run"
   ```

   The script refuses to run while no photo in the template is marked `"filled": true`, and refuses a second run once an unseen run is in the report (`-Force` overrides, for a genuinely new untouched batch). The equivalent manual command is:

   ```powershell
   .\.venv\Scripts\python.exe -m backend.scripts.batch_evaluate D:\unseen-photos --fixture-dir backend\tests\fixtures\labels\unseen --report-json backend\app\assets\evaluation_report.json --run-label unseen-2026-10-29 --run-note "N new phone photos, labelled by hand before the run"
   ```

   This runs the real OCR, extraction and rules, prints the scores and **adds** the run to the report file (runs with other labels are kept, so development and unseen results sit side by side on the page). Do not change extraction or rule code in response to the numbers and then re-run: that turns the unseen set into a development set. Commit the updated `evaluation_report.json` if you want it in the repository.


## Running tests

```powershell
pytest backend/tests
python -m backend.scripts.eval_extraction   # field-level precision/recall on the labelled fixtures
python -m backend.scripts.eval_compliance   # rule-outcome and verdict accuracy on the same fixtures
cd frontend; npm test
```

`eval_compliance` extracts every fixture, stores it the way an inspection does, runs the current ruleset and compares each rule outcome and the verdict with the `rules` / `verdict` entries in `expected.json`.

`eval_extraction` runs extraction over every label in `backend/tests/fixtures/labels` (one real Parle-G back label exported from OCR, plus synthetic layouts) and compares the result with `expected.json`. Add a fixture and its ground truth whenever a new label layout is supported; `--failures` prints only the rows that differ.

### Evaluating on real label photos

One command runs the full pipeline (real PaddleOCR with the `.env` settings, extraction, compliance; no database writes) over a folder of photos and prints the scores:

```powershell
python -m backend.scripts.batch_evaluate D:\path\to\label-photos
```

- OCR output (never the photos) is saved to `backend/tests/fixtures/labels/real/<name>.json`, so `--reuse-ocr` re-runs extraction and compliance in seconds after a code change. `python -m backend.scripts.eval_real` scores the stored fixtures without the photo folder.
- `reports/batch/report.html` (git-ignored, photos embedded) shows each photo with its OCR evidence boxes, extracted fields and rule findings for quick review.
- `split.json` fixes a seeded DEV / HOLD-OUT split by file name the first time a photo is seen; existing assignments never change. Look at DEV failures (`--failures`) to improve extraction; HOLD-OUT is for measuring only — its failing rows are hidden unless `eval_real --show-holdout` is given.
- The first hold-out was retired on 2026-10-15 after its failures were fixed: all 9 original photos are now DEV, and `"new_photos": "holdout"` in `split.json` sends every photo added later to a fresh HOLD-OUT.
- The second hold-out, 14 phone photos (`phone_holdout_2026_10`), was retired on 2026-10-16 after its failures were inspected to change the extraction approach. All 23 photos are now DEV: every score printed is a development score, and there is no unseen-label score until new photos are added. Photos added together keep a group (`_meta.group` in `expected_real.json`) that is still scored as its own column.
- Besides precision / recall and rule agreement, the scores count verdict-safety errors: **false NON_COMPLIANT** verdicts (the ground truth is not NON_COMPLIANT) and **false PASS** findings on mandatory rules (a PASS the ground truth does not give, or a PASS on a wrongly read value). The ground-truth rules see the same photo: its OCR coverage comes from extraction, and a declaration the person did not list counts as not detected unless they recorded its key without a value.
- Scores are printed for DEV, HOLD-OUT, the real phone photos (`"_meta": {"real_photo": true}`) and all photos, once ground truth for a photo is in `real/expected_real.json` (format described at the top of that file). Photos without ground truth are listed but not scored.
- Use `--fixture-dir` to keep a different photo set's fixtures, split and ground truth apart.

OCR re-reads a photo taken sideways: when most text boxes are tall and narrow, the photo is read turned 90° and 270° and the orientation with the most confidently read text is kept (`OCR_AUTO_ROTATE=false` turns this off). Boxes are always stored in original photo pixels; each detection records the `rotation` it was read at so extraction can rebuild the reading order. This costs two extra OCR passes, only on sideways photos. Upside-down (180°) photos are not detected.
