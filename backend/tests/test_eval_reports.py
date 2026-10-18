"""Evaluation report JSON and the unseen-set template generator."""
import json
from pathlib import Path

from PIL import Image

from backend.scripts import make_ground_truth_template as template
from backend.scripts.eval_real import REAL_FIXTURE_DIR, build_run, evaluate, write_report


def test_report_run_is_measured_labelled_and_mergeable(tmp_path) -> None:
    records = evaluate(REAL_FIXTURE_DIR)
    run = build_run(records, "dev-test", REAL_FIXTURE_DIR, "note")
    assert run["kind"] == "development" and run["dataset_sizes"]["unseen"] == 0 and run["dataset_sizes"]["total"] == len(records)
    everything = next(group for group in run["groups"] if group["name"] == "ALL")
    assert everything["photos"] == len(records) and 0 <= everything["precision"] <= 1 and 0 <= everything["recall"] <= 1
    assert sum(sum(row.values()) for row in everything["verdict_confusion"].values()) == len(records)
    path = tmp_path / "report.json"
    write_report(path, run)
    write_report(path, {**run, "label": "second"})
    write_report(path, {**run, "label": "dev-test", "note": "replaced"})
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    assert [item["label"] for item in runs] == ["dev-test", "second"] and runs[0]["note"] == "replaced"


def test_template_marks_photos_unfilled_and_unseen_and_is_not_scored(tmp_path, monkeypatch) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("a.jpg", "b.png"):
        Image.new("RGB", (8, 8)).save(photos / name)
    fixtures = tmp_path / "unseen"
    monkeypatch.setattr("sys.argv", ["x", str(photos), "--fixture-dir", str(fixtures), "--group", "unseen_test"])
    assert template.main() == 0
    truth = json.loads((fixtures / "expected_real.json").read_text(encoding="utf-8"))
    entry = truth["real_a"]
    assert entry["_meta"]["filled"] is False and entry["_meta"]["group"] == "unseen_test" and entry["mrp"] is None
    split = json.loads((fixtures / "split.json").read_text(encoding="utf-8"))
    assert {value["split"] for value in split["photos"].values()} == {"holdout"} and split["new_photos"] == "holdout"
    # An OCR fixture exists but the truth is unfilled: no score is produced.
    (fixtures / "real_a.json").write_text(json.dumps({"raw_text": "MRP Rs 10", "detections": [], "languages": ["en"]}), encoding="utf-8")
    assert evaluate(fixtures) == []
    # Re-running never overwrites hand-filled entries.
    truth["real_a"]["mrp"] = "Rs 10"
    (fixtures / "expected_real.json").write_text(json.dumps(truth), encoding="utf-8")
    assert template.main() == 0
    assert json.loads((fixtures / "expected_real.json").read_text(encoding="utf-8"))["real_a"]["mrp"] == "Rs 10"
    # Filled in -> scored, and reported as an unseen run.
    truth["real_a"]["_meta"]["filled"] = True
    (fixtures / "expected_real.json").write_text(json.dumps(truth), encoding="utf-8")
    records = evaluate(fixtures)
    assert len(records) == 1 and build_run(records, "u", fixtures)["kind"] == "unseen"
