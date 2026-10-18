"""Run the real OCR -> extraction -> compliance pipeline over a folder of label photos.

    python -m backend.scripts.batch_evaluate PHOTO_DIR [--fixture-dir DIR] [--out reports/batch] [--reuse-ocr]

For every image (JPG/PNG/WebP) this runs PaddleOcrService with the .env OCR
settings, ExtractionService and the ComplianceEngine exactly as an inspection
does, but without touching the database.  It writes:

- ``<out>/<fixture>.json``: OCR detections, extracted fields with evidence,
  rule outcomes, verdict and timings;
- ``<out>/report.html``: one self-contained page with each photo beside its
  fields and findings (the photos are embedded as thumbnails; keep ``reports/``
  out of git);
- ``<fixture-dir>/<fixture>.json`` (default backend/tests/fixtures/labels/real):
  the OCR output only (no image), so extraction can be re-run without OCR;
- ``<fixture-dir>/split.json``: a seeded, filename-based DEV / HOLD-OUT split.
  The first run ranks all photos by sha256(seed:name) and makes the first 30%
  HOLD-OUT; photos added later are assigned one by one (HOLD-OUT when their
  hash falls in the lowest 30%), or all to HOLD-OUT when the manifest says
  ``"new_photos": "holdout"`` (after a hold-out was retired).  Existing
  assignments never change.

If ``<fixture-dir>/expected_real.json`` holds ground truth for some photos, the
DEV / HOLD-OUT / real-photo score tables of backend.scripts.eval_real are
printed at the end.  ``--reuse-ocr`` skips OCR and re-runs extraction and
compliance on the stored fixtures.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
from datetime import date
import hashlib
import html
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from time import perf_counter
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps

from backend.app.core.config import get_ocr_languages, get_settings
from backend.app.models import OcrResult
from backend.app.services.compliance.rule_engine import ComplianceEngine, verdict
from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.evidence import build_extracted_fields, field_evidence
from backend.app.services.uploads import image_dimensions

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "labels" / "real"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SPLIT_SEED = "packcomply-real-labels-v1"
HOLDOUT_SHARE = 0.3
THUMBNAIL_EDGE = 640


def fixture_name(path: Path) -> str:
    """Stable, filesystem-safe fixture name derived from the photo's file name."""
    stem = re.sub(r"[^0-9a-z]+", "_", path.stem.lower()).strip("_")
    return f"real_{stem}"


def make_split(photos: list[Path], pinned_dev: set[str]) -> dict[str, Any]:
    """Seeded split by file name: rank by sha256(seed + name), the first ~30% are hold-out.

    Photos named in ``pinned_dev`` (already used for tuning) are always DEV, but
    still count towards the size of the whole set.
    """
    names = sorted(path.name for path in photos)
    holdout_size = round(len(names) * HOLDOUT_SHARE)
    ranked = sorted((name for name in names if name not in pinned_dev), key=lambda name: hashlib.sha256(f"{SPLIT_SEED}:{name}".encode()).hexdigest())
    holdout = set(ranked[:holdout_size])
    return {
        "_comment": "Fixed DEV / HOLD-OUT split of the real label photos. Created once by batch_evaluate before any extraction change; never edit to improve scores.",
        "seed": SPLIT_SEED,
        "holdout_share": HOLDOUT_SHARE,
        "method": "sort photo file names by sha256(seed:name); the first round(n * holdout_share) not pinned to DEV are HOLD-OUT",
        "pinned_dev": sorted(pinned_dev),
        "photos": {fixture_name(Path(name)): {"file": name, "split": "holdout" if name in holdout else "dev"} for name in names},
    }


def _late_split(name: str) -> str:
    """Split of a photo added after the manifest was created: hold-out for the lowest 30% of hashes."""
    fraction = int(hashlib.sha256(f"{SPLIT_SEED}:{name}".encode()).hexdigest()[:8], 16) / 0x1_0000_0000
    return "holdout" if fraction < HOLDOUT_SHARE else "dev"


def load_split(photos: list[Path], pinned_dev: set[str], fixture_dir: Path) -> dict[str, Any]:
    path = fixture_dir / "split.json"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        split = make_split(photos, pinned_dev)
        print(f"created split manifest {path}")
    else:
        split = json.loads(path.read_text(encoding="utf-8"))
        new = [photo for photo in photos if fixture_name(photo) not in split["photos"]]
        if not new:
            return split
        # "new_photos": "holdout" (set when a hold-out is retired) sends every new photo to the fresh hold-out.
        policy = split.get("new_photos", "hash")
        for photo in new:
            assignment = "dev" if photo.name in pinned_dev else "holdout" if policy == "holdout" else _late_split(photo.name)
            split["photos"][fixture_name(photo)] = {"file": photo.name, "split": assignment, "added_later": True}
        print(f"added {len(new)} new photo(s) to {path}")
    path.write_text(json.dumps(split, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return split


def ocr_service() -> Any:
    from backend.app.services.ocr import PaddleOcrService

    settings = get_settings()
    return PaddleOcrService(
        languages=get_ocr_languages(settings),
        max_image_dimension=settings.ocr_max_image_dimension,
        secondary_language_max_image_dimension=settings.ocr_secondary_language_max_image_dimension,
        secondary_language_timeout_seconds=settings.ocr_secondary_language_timeout_seconds,
        recognition_batch_size=settings.ocr_recognition_batch_size,
        detection_model=settings.ocr_detection_model,
        recognition_model=settings.ocr_recognition_model,
        enable_mkldnn=settings.ocr_enable_mkldnn,
        cpu_threads=settings.ocr_cpu_threads,
        auto_rotate=settings.ocr_auto_rotate,
    )


def run_ocr(service: Any, photo: Path) -> dict[str, Any]:
    # OCR writes its preprocessed copy beside the input, so work on a temporary copy.
    workdir = Path(tempfile.mkdtemp(prefix="packcomply-batch-"))
    try:
        image = workdir / photo.name
        shutil.copyfile(photo, image)
        started = perf_counter()
        document = service.scan(image.resolve())
        seconds = perf_counter() - started
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    width, height = image_dimensions(photo) or (None, None)
    return {
        "source": f"batch_evaluate OCR of {photo.name}",
        "engine": document.engine,
        "languages": document.languages,
        "skipped_languages": document.skipped_languages,
        "image_width": width,
        "image_height": height,
        "ocr_seconds": round(seconds, 2),
        "raw_text": document.raw_text,
        "detections": document.as_database_detections(),
    }


def analyse(fixture: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Extraction and compliance on stored OCR output, as an inspection runs them."""
    started = perf_counter()
    values = ExtractionService().extract(fixture["raw_text"], detections=fixture.get("detections"))
    extraction_seconds = perf_counter() - started
    ocr = OcrResult(id=uuid4(), inspection_id=uuid4(), raw_text=fixture["raw_text"], detections=fixture.get("detections") or [], languages=fixture.get("languages") or ["en"])
    started = perf_counter()
    stored = build_extracted_fields(ocr.inspection_id, ocr, values)
    findings = ComplianceEngine().evaluate({field.field_name: field_evidence(field) for field in stored}, today=today)
    compliance_seconds = perf_counter() - started
    return {
        "fields": {name: asdict(value) for name, value in values.items() if value.value is not None},
        "findings": [asdict(finding) for finding in findings],
        "verdict": verdict([finding.status for finding in findings]),
        "extraction_seconds": round(extraction_seconds, 4),
        "compliance_seconds": round(compliance_seconds, 4),
    }


# ------------------------------------------------------------------ report

_STATUS_CLASS = {"PASS": "pass", "FAIL": "fail", "REVIEW": "review", "NOT_APPLICABLE": "na"}


def _thumbnail(photo: Path) -> tuple[str, float]:
    """Base64 JPEG thumbnail and the factor from original pixels to thumbnail pixels."""
    with Image.open(photo) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        scale = min(1.0, THUMBNAIL_EDGE / max(image.size))
        image = image.resize((round(image.width * scale), round(image.height * scale)))
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode(), scale


def _overlay(result: dict[str, Any], scale: float) -> str:
    shapes = []
    for name, field in result["fields"].items():
        for box in field.get("source_bboxes") or []:
            points = " ".join(f"{x * scale:.1f},{y * scale:.1f}" for x, y in box)
            shapes.append(f'<polygon points="{points}"><title>{html.escape(name)}</title></polygon>')
    return "".join(shapes)


def render_report(results: list[dict[str, Any]], photo_dir: Path, output: Path) -> None:
    cards = []
    for result in results:
        photo = photo_dir / result["file"]
        data, scale = _thumbnail(photo)
        width, height = result["ocr"].get("image_width") or 1, result["ocr"].get("image_height") or 1
        rows = "".join(
            f"<tr><th>{html.escape(name)}</th><td>{html.escape(str(field['value']))}"
            f"{' <span class=flag>review</span>' if field['needs_review'] else ''}"
            f"<div class=src>{html.escape(field.get('matched_pattern') or '')} · {html.escape((field.get('source_text') or '')[:160])}</div></td></tr>"
            for name, field in result["fields"].items()
        )
        findings = "".join(
            f"<li class={_STATUS_CLASS.get(item['status'], 'na')}><b>{html.escape(item['status'])}</b> {html.escape(item['rule_id'])} — {html.escape(item['message'])}</li>"
            for item in result["findings"]
        )
        cards.append(f"""
<section class=card id="{html.escape(result['fixture'])}">
  <header><h2>{html.escape(result['file'])}</h2>
    <span class="tag {result['split']}">{result['split'].upper()}</span>
    <span class="verdict v-{result['verdict'].lower()}">{result['verdict']}</span>
    <span class=time>OCR {result['timings']['ocr_seconds']}s · {len(result['ocr']['detections'])} boxes · {', '.join(result['ocr']['languages'])}</span></header>
  <div class=grid>
    <figure><div class=photo><img src="data:image/jpeg;base64,{data}" alt="">
      <svg viewBox="0 0 {width * scale:.0f} {height * scale:.0f}">{_overlay(result, scale)}</svg></div>
      <details><summary>OCR text</summary><pre>{html.escape(result['ocr']['raw_text'])}</pre></details></figure>
    <div><table>{rows or '<tr><td>No fields extracted</td></tr>'}</table><ul class=findings>{findings}</ul></div>
  </div>
</section>""")
    output.write_text(f"""<!doctype html>
<html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PackComply batch report</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --ink:#1b1f24; --muted:#5b6470; --line:#dde1e6; --pass:#1a7f37; --fail:#cf222e; --review:#9a6700; --na:#6e7781; --box:#0969da; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --muted:#9198a1; --line:#30363d; --pass:#3fb950; --fail:#f85149; --review:#d29922; --na:#9198a1; --box:#58a6ff; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui,sans-serif; }}
h1 {{ font-size:20px; margin:0 0 4px; }} .muted {{ color:var(--muted); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:16px 0; }}
.card header {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }} .card h2 {{ font-size:15px; margin:0; word-break:break-all; }}
.tag, .verdict {{ font-size:12px; padding:2px 8px; border-radius:99px; border:1px solid var(--line); }}
.tag.holdout {{ border-color:var(--review); color:var(--review); }}
.v-compliant {{ color:var(--pass); }} .v-non_compliant {{ color:var(--fail); }} .v-needs_review {{ color:var(--review); }}
.time {{ color:var(--muted); font-size:12px; }}
.grid {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.3fr); gap:16px; margin-top:12px; }}
@media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} }}
.photo {{ position:relative; }} .photo img {{ width:100%; display:block; border-radius:4px; }}
.photo svg {{ position:absolute; inset:0; width:100%; height:100%; }} .photo polygon {{ fill:none; stroke:var(--box); stroke-width:2; }}
table {{ width:100%; border-collapse:collapse; }} th, td {{ text-align:left; vertical-align:top; padding:4px 6px; border-bottom:1px solid var(--line); }}
th {{ white-space:nowrap; font-weight:600; width:1%; }} .src {{ color:var(--muted); font-size:12px; word-break:break-word; }}
.flag {{ font-size:11px; color:var(--review); border:1px solid var(--review); border-radius:4px; padding:0 4px; }}
.findings {{ list-style:none; padding:0; margin:12px 0 0; }} .findings li {{ padding:3px 0; font-size:13px; }}
li.pass b {{ color:var(--pass); }} li.fail b {{ color:var(--fail); }} li.review b {{ color:var(--review); }} li.na {{ color:var(--muted); }}
pre {{ white-space:pre-wrap; font-size:12px; max-height:320px; overflow:auto; }}
</style></head><body>
<h1>PackComply batch report</h1>
<p class=muted>{len(results)} photos from {html.escape(str(photo_dir))}. Blue boxes are the OCR evidence of extracted fields. Verdicts are an inspection aid, not a legal decision.</p>
{''.join(cards)}
</body></html>
""", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("photo_dir", type=Path)
    parser.add_argument("--fixture-dir", type=Path, default=REAL_FIXTURE_DIR, help="where OCR fixtures, split.json and expected_real.json live")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "batch")
    parser.add_argument("--reuse-ocr", action="store_true", help="re-use stored OCR fixtures instead of running OCR (photos without one are OCR'd)")
    parser.add_argument("--pin-dev", action="append", default=[], help="photo file name that must be in DEV (already used for tuning)")
    parser.add_argument("--only", help="only process photos whose file name contains this text")
    parser.add_argument("--failures", action="store_true", help="also list failing DEV rows (HOLD-OUT failures stay hidden)")
    parser.add_argument("--report-json", type=Path, help="write the measured results to this JSON file (the About & accuracy page reads backend/app/assets/evaluation_report.json); an existing file keeps its other runs")
    parser.add_argument("--run-label", default="development", help="name of this run in the report; a run with the same label is replaced")
    parser.add_argument("--run-note", default="", help="one line describing the photos, shown beside the numbers (e.g. '20 new phone photos, 2026-10-29')")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    photos = sorted(path for path in args.photo_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    if not photos:
        print(f"no images in {args.photo_dir}")
        return 1
    fixture_dir = args.fixture_dir.resolve()
    split = load_split(photos, set(args.pin_dev), fixture_dir)
    args.out.mkdir(parents=True, exist_ok=True)

    service = None
    results = []
    try:
        for photo in photos:
            if args.only and args.only not in photo.name:
                continue
            name = fixture_name(photo)
            fixture_path = fixture_dir / f"{name}.json"
            if args.reuse_ocr and fixture_path.exists():
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            else:
                service = service or ocr_service()
                fixture = run_ocr(service, photo)
                fixture_path.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            analysis = analyse(fixture)
            result = {
                "file": photo.name,
                "fixture": name,
                "split": split["photos"].get(name, {}).get("split", "unsplit"),
                "timings": {"ocr_seconds": fixture.get("ocr_seconds"), "extraction_seconds": analysis.pop("extraction_seconds"), "compliance_seconds": analysis.pop("compliance_seconds")},
                "ocr": {key: fixture.get(key) for key in ("engine", "languages", "skipped_languages", "image_width", "image_height", "raw_text", "detections")},
                **analysis,
            }
            (args.out / f"{name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            results.append(result)
            print(f"{photo.name:45} {result['split']:8} OCR {fixture.get('ocr_seconds')}s  boxes={len(fixture['detections']):4}  fields={len(result['fields']):2}  {result['verdict']}")
    finally:
        if service is not None:
            service.close()
    render_report(results, args.photo_dir, args.out / "report.html")
    print(f"\nreport: {args.out / 'report.html'}")
    if (fixture_dir / "expected_real.json").exists():
        from backend.scripts.eval_real import evaluate, print_report

        records = evaluate(fixture_dir, {result["fixture"]: result for result in results})
        print_report(records, failures=args.failures)
        if args.report_json:
            from backend.scripts.eval_real import build_run, write_report

            write_report(args.report_json, build_run(records, args.run_label, fixture_dir, args.run_note))
        unlabelled = [result["file"] for result in results if result["fixture"] not in {record["fixture"] for record in records}]
        if unlabelled:
            print(f"\n{len(unlabelled)} photo(s) have no ground truth in expected_real.json and are not scored: {', '.join(unlabelled)}")
    else:
        print(f"no {fixture_dir / 'expected_real.json'}: write ground truth there to get DEV / HOLD-OUT scores")
        if args.report_json:
            print("no report written: a report needs ground truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
