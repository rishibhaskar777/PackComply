"""Prepare a fresh UNSEEN evaluation set: ground-truth template + hold-out split for a folder of new photos.

    python -m backend.scripts.make_ground_truth_template PHOTO_DIR [--fixture-dir DIR] [--group NAME]

Writes two files into ``--fixture-dir`` (default ``backend/tests/fixtures/labels/unseen``, kept apart from the
development photos):

- ``expected_real.json``: one entry per photo with every scored field set to ``null`` and ``"_meta": {"filled": false}``.
  Read each photo yourself and fill in only what is legible (a list means any one of several spellings is accepted; dates
  are YYYY-MM[-DD] or P<n>M; leave ``null``/delete the key when the photo does not show the field), then set
  ``"filled": true``. Photos still marked ``"filled": false`` are not scored, so a half-filled template cannot produce numbers.
- ``split.json``: every photo is HOLD-OUT, so the run is reported as an unseen score.

Do this BEFORE running the pipeline on the photos, and run ``batch_evaluate`` once, without changing any extraction or
rule code in response to the result. Existing entries are never overwritten, so re-running only adds new photos.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any

from backend.scripts.batch_evaluate import IMAGE_SUFFIXES, REPO_ROOT, fixture_name
from backend.scripts.eval_real import FIELDS

DEFAULT_FIXTURE_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "labels" / "unseen"
COMMENT = [
    "Ground truth for UNSEEN photos, written by a person from each photo BEFORE the pipeline is run on it.",
    "Fill only what is legible; null / missing = the photo does not show it. A list accepts any one of several spellings.",
    "Dates: YYYY-MM[-DD] or P<n>M (shelf life). Phones compare digits only. consumer_care_website compares the host name.",
    "mrp_declaration: 'present_without_value' when the label says MRP but the value is unreadable. tax_inclusive: 'true' when 'inclusive of all taxes' is printed.",
    "product_category: 'food' or omit. Set _meta.filled to true when the photo is done; unfilled photos are not scored.",
]


def template_entry(group: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"_meta": {"filled": False, "real_photo": True, "group": group, "difficulty": "", "category": "", "source": "phone photo"}}
    entry.update({field: None for field in FIELDS})
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("photo_dir", type=Path)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--group", default=f"unseen_{date.today():%Y_%m_%d}", help="name of this batch in the report")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    photos = sorted(path for path in args.photo_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    if not photos:
        print(f"no images in {args.photo_dir}")
        return 1
    fixture_dir = args.fixture_dir.resolve()
    fixture_dir.mkdir(parents=True, exist_ok=True)

    truth_path = fixture_dir / "expected_real.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else {"_comment": COMMENT}
    added = 0
    for photo in photos:
        if fixture_name(photo) not in truth:
            truth[fixture_name(photo)] = template_entry(args.group)
            added += 1
    truth_path.write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    split_path = fixture_dir / "split.json"
    split = json.loads(split_path.read_text(encoding="utf-8")) if split_path.exists() else {
        "_comment": "Every photo in this folder is UNSEEN (HOLD-OUT): measure once, never tune on it.",
        "seed": "unseen", "new_photos": "holdout", "photos": {},
    }
    for photo in photos:
        split["photos"].setdefault(fixture_name(photo), {"file": photo.name, "split": "holdout", "group": args.group})
    split_path.write_text(json.dumps(split, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{len(photos)} photo(s): {added} new template entr{'y' if added == 1 else 'ies'} in {truth_path}")
    print("Next: fill the expected values by hand, set \"filled\": true for each finished photo, then run once:")
    print(f"  python -m backend.scripts.batch_evaluate {args.photo_dir} --fixture-dir {fixture_dir} "
          f"--report-json backend/app/assets/evaluation_report.json --run-label {args.group} --run-note \"<what these photos are>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
