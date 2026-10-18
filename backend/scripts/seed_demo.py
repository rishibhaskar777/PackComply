"""Fill a fresh database with realistic demo data, through the real pipeline.

    python -m backend.scripts.seed_demo [PHOTO_DIR ...] [--inspector-password X] [--maker-password Y]
                                        [--no-ocr] [--reset-demo]

- Creates the demo accounts ``demo_inspector`` (OFFICIAL) and ``demo_maker`` (MANUFACTURER). Their passwords are
  printed once, when the account is created (or when you set one with the flags); an existing account's password is
  never changed unless you pass the flag.
- Imports every JPG/PNG/WebP in the given folders (default: ``PackComply-samples`` and ``PackComply-samples-phone``
  next to this repository, when present) as one inspection each and runs the real workflow (extraction, rules, risk,
  repeat patterns). Photos that already have stored OCR in ``backend/tests/fixtures/labels/real`` reuse it, so seeding
  takes seconds; others are read with PaddleOCR unless ``--no-ocr`` is given.
- Inspection dates are spread over the last four weeks so trends and the dashboard look lived-in, and a few of the
  lowest-risk records are verified. The highest-risk record is left unreviewed for the live demo.
- Idempotent: an inspection is identified by its photo's file name, so running it again adds nothing.
- ``--reset-demo`` deletes ONLY what this script created (rows tagged ``demo_seed``, their photo files and the demo
  accounts). Real records and accounts are never touched.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from io import BytesIO
import json
from pathlib import Path
import secrets
import sys
from typing import Callable

from PIL import Image
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.security import hash_password
from backend.app.models import (
    AuthSession, ComplianceResult, ExtractedField, FieldCorrection, Inspection, InspectionEvent, InspectionImage, InspectionIssue,
    ManufacturerHistory, OcrResult, PasswordResetCode, SyncRecord, User,
)
from backend.app.services.audit import record_event
from backend.app.services.compliance.rule_engine import ComplianceEngine
from backend.app.services.corrections import mark_verified
from backend.app.services.extraction import ExtractionService
from backend.app.services.risk import RiskService
from backend.app.services.uploads import store_image, validate_image
from backend.app.services.workflow import InspectionWorkflow
from backend.scripts.batch_evaluate import IMAGE_SUFFIXES, REAL_FIXTURE_DIR, REPO_ROOT, fixture_name

INSPECTOR = {"username": "demo_inspector", "role": "OFFICIAL", "full_name": "Demo Inspector", "email": "demo.inspector@packcomply.local", "organization": "Legal Metrology Department (demo)"}
MAKER = {"username": "demo_maker", "role": "MANUFACTURER", "full_name": "Demo Manufacturer", "email": "demo.maker@packcomply.local", "organization": "Demo Foods Pvt Ltd"}
DEFAULT_FOLDERS = (REPO_ROOT.parent / "PackComply-samples", REPO_ROOT.parent / "PackComply-samples-phone")
NUMBER_PREFIX = "DEMO-"
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
SPREAD_DAYS = 27
VERIFIED_COUNT = 3
# Every sixth photo belongs to the manufacturer account, so that login shows a small, believable history too.
MAKER_EVERY = 6
_DATED = (InspectionImage, OcrResult, ExtractedField, ComplianceResult, InspectionEvent, InspectionIssue, FieldCorrection)


@dataclass
class SeedReport:
    created_users: dict[str, str] = field(default_factory=dict)  # username -> password, only for accounts set this run
    existing_users: list[str] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_failed: list[tuple[str, str]] = field(default_factory=list)
    verified: int = 0


class NoOcr:
    """Stands in for the OCR engine when every photo already has stored OCR; reading a photo here is a bug."""
    languages = ("en", "hi")

    def scan(self, *_args, **_kwargs):
        raise RuntimeError("OCR is not available for this photo (no stored OCR and --no-ocr was given).")


def demo_number(photo: Path) -> str:
    return NUMBER_PREFIX + sha1(photo.name.encode("utf-8")).hexdigest()[:10].upper()


def _password() -> str:
    return f"demo-{secrets.randbelow(10 ** 6):06d}"


def ensure_user(db: Session, spec: dict, password: str | None, report: SeedReport) -> User:
    user = db.scalar(select(User).where(User.username == spec["username"]))
    if user is None:
        chosen = password or _password()
        user = User(username=spec["username"], email=spec["email"], full_name=spec["full_name"], organization=spec["organization"], role=spec["role"], password_hash=hash_password(chosen), is_active=True, demo_seed=True)
        db.add(user)
        db.commit()
        report.created_users[spec["username"]] = chosen
        return user
    if password:
        user.password_hash = hash_password(password)
        db.commit()
        report.created_users[spec["username"]] = password
    else:
        report.existing_users.append(spec["username"])
    return user


def _prepare_upload(photo: Path, max_bytes: int) -> tuple[bytes, str, str, float]:
    """(content, file name, content type, scale from original pixels): WebP becomes PNG, an oversized photo is shrunk."""
    raw = photo.read_bytes()
    suffix = photo.suffix.lower()
    if suffix in CONTENT_TYPES and len(raw) <= max_bytes:
        return raw, photo.name, CONTENT_TYPES[suffix], 1.0
    with Image.open(BytesIO(raw)) as image:
        image.load()
        scale = 1.0
        if suffix in CONTENT_TYPES or max(image.size) > 2400:
            scale = min(1.0, 2400 / max(image.size))
        image = image.convert("RGB")
        if scale < 1.0:
            image = image.resize((round(image.width * scale), round(image.height * scale)))
        output = BytesIO()
        if suffix == ".webp":
            image.save(output, "PNG")
            return output.getvalue(), photo.with_suffix(".png").name, "image/png", scale
        image.save(output, "JPEG", quality=90)
        return output.getvalue(), photo.with_suffix(".jpg").name, "image/jpeg", scale


def _scaled(detections: list[dict], scale: float) -> list[dict]:
    if scale == 1.0:
        return detections
    return [{**item, "bbox": [[x * scale, y * scale] for x, y in item["bbox"]]} if item.get("bbox") else item for item in detections]


def _cached_ocr(photo: Path, fixture_dir: Path) -> dict | None:
    path = fixture_dir / f"{fixture_name(photo)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if data.get("raw_text") is not None and data.get("detections") is not None else None


def _backdate(db: Session, inspection: Inspection, when: datetime) -> None:
    """Shift everything stored for this inspection so it looks as if it were run at ``when``."""
    anchor = inspection.created_at if inspection.created_at.tzinfo else inspection.created_at.replace(tzinfo=timezone.utc)
    delta = anchor - when
    for model in _DATED:
        for row in db.scalars(select(model).where(model.inspection_id == inspection.id)).all():
            row.created_at, row.updated_at = row.created_at - delta, row.updated_at - delta
    for name in ("created_at", "updated_at", "inspected_at", "completed_at"):
        value = getattr(inspection, name)
        if value is not None:
            setattr(inspection, name, value - delta)
    db.commit()


def seed(db: Session, folders: list[Path], upload_dir: Path, *, fixture_dir: Path = REAL_FIXTURE_DIR, inspector_password: str | None = None, maker_password: str | None = None,
         allow_ocr: bool = True, ocr_factory: Callable[[], object] | None = None, now: datetime | None = None, log: Callable[[str], None] = print) -> SeedReport:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    report = SeedReport()
    inspector = ensure_user(db, INSPECTOR, inspector_password, report)
    maker = ensure_user(db, MAKER, maker_password, report)

    photos = sorted({path.resolve(): path for folder in folders if folder.is_dir() for path in folder.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES}.values(), key=lambda path: (str(path.parent), path.name))
    if not photos:
        log("No photos found; the accounts were created but there is nothing to import.")
        return report

    live_ocr: list[object] = []

    def ocr_engine() -> object:
        if not allow_ocr:
            return NoOcr()
        if not live_ocr:
            live_ocr.append((ocr_factory or _real_ocr)())
        return live_ocr[0]

    created: list[Inspection] = []
    total = len(photos)
    for index, photo in enumerate(photos):
        number = demo_number(photo)
        if db.scalar(select(Inspection.id).where(Inspection.inspection_number == number)) is not None:
            report.skipped_existing.append(photo.name)
            continue
        cached = _cached_ocr(photo, fixture_dir)
        if cached is None and not allow_ocr:
            report.skipped_failed.append((photo.name, "no stored OCR and --no-ocr given"))
            continue
        owner = maker if index % MAKER_EVERY == MAKER_EVERY - 1 else inspector
        inspection = None
        try:
            content, filename, content_type, scale = _prepare_upload(photo, settings.max_upload_bytes)
            validate_image(content, content_type, settings.max_upload_bytes)
            stored = store_image(content, filename, content_type, upload_dir)
            inspection = Inspection(inspection_number=number, user_id=owner.id, status="IMAGE_UPLOADED", demo_seed=True)
            db.add(inspection)
            db.flush()
            image = InspectionImage(inspection_id=inspection.id, panel="BACK", **stored.__dict__)
            db.add(image)
            db.commit()
            if cached is not None:
                db.add(OcrResult(inspection_id=inspection.id, image_id=image.id, engine=cached.get("engine") or "PaddleOCR", languages=cached.get("languages") or ["en"], raw_text=cached["raw_text"],
                                 detections=_scaled(cached["detections"], scale), duration_ms=round(float(cached["ocr_seconds"]) * 1000) if cached.get("ocr_seconds") else None))
                db.commit()
            workflow = InspectionWorkflow(db, ocr_engine(), ExtractionService(), ComplianceEngine(), upload_dir, settings.max_upload_bytes, settings.pattern_min_occurrences)
            workflow.process(inspection.id)
        except Exception as error:  # noqa: BLE001 - one bad photo must not stop the import
            db.rollback()
            if inspection is not None and inspection.id is not None:
                _delete_inspections(db, [inspection.id], upload_dir)
            report.skipped_failed.append((photo.name, str(error) or type(error).__name__))
            log(f"  skipped {photo.name}: {error}")
            continue
        if cached is not None:
            # The photo was really read in `ocr_seconds` when its OCR was stored, so the record shows that processing time
            # (a cached run finishes in under a second, which would make the dashboard's median time look absurd).
            ocr = db.scalar(select(OcrResult).where(OcrResult.inspection_id == inspection.id))
            if ocr is not None and ocr.duration_ms:
                inspection.completed_at = image.created_at + timedelta(milliseconds=ocr.duration_ms + 900)
                db.commit()
        days_ago = 1 + round(index * SPREAD_DAYS / max(total - 1, 1))
        _backdate(db, inspection, now - timedelta(days=days_ago, hours=(index * 7) % 9, minutes=(index * 13) % 50))
        created.append(inspection)
        report.imported.append(photo.name)
        log(f"  [{index + 1}/{total}] {photo.name}  ->  {number}{'  (cached OCR)' if cached is not None else ''}")

    report.verified = _verify_lowest_risk(db, created, inspector)
    return report


def _verify_lowest_risk(db: Session, created: list[Inspection], inspector: User) -> int:
    """Verify a few completed, low-risk records of the inspector, so the queue is not all unreviewed."""
    scored = []
    service = RiskService(db, get_settings().pattern_min_occurrences)
    for inspection in created:
        if inspection.user_id == inspector.id and inspection.status == "COMPLETED":
            scored.append((service.for_inspection(inspection).risk_score, inspection))
    scored.sort(key=lambda pair: (pair[0], pair[1].inspection_number))
    count = 0
    for _, inspection in scored[:VERIFIED_COUNT]:
        mark_verified(inspection, inspector.id, "Checked against the pack (demo data).")
        inspection.reviewed_at = (inspection.completed_at or inspection.created_at) + timedelta(hours=3)
        db.commit()
        record_event(db, inspection.id, "VERIFIED", inspector.id, {"remarks": inspection.remarks, "verdict": None})
        count += 1
    return count


def _delete_inspections(db: Session, ids: list, upload_dir: Path) -> int:
    if not ids:
        return 0
    paths = []
    for storage_key in db.scalars(select(InspectionImage.storage_key).where(InspectionImage.inspection_id.in_(ids))).all():
        paths.append((upload_dir.resolve() / storage_key).resolve())
    db.execute(update(ManufacturerHistory).where(ManufacturerHistory.inspection_id.in_(ids)).values(inspection_id=None))
    db.execute(update(SyncRecord).where(SyncRecord.inspection_id.in_(ids)).values(inspection_id=None))
    for model in (ComplianceResult, ExtractedField, FieldCorrection, InspectionEvent, InspectionIssue, OcrResult, InspectionImage):
        db.execute(delete(model).where(model.inspection_id.in_(ids)))
    db.execute(delete(Inspection).where(Inspection.id.in_(ids)))
    db.commit()
    for path in paths:
        if path.is_relative_to(upload_dir.resolve()):
            path.unlink(missing_ok=True)
    return len(ids)


def reset_demo(db: Session, upload_dir: Path) -> dict[str, int]:
    """Delete exactly the rows tagged ``demo_seed`` (inspections with everything under them, then the demo accounts)."""
    inspections = _delete_inspections(db, list(db.scalars(select(Inspection.id).where(Inspection.demo_seed.is_(True))).all()), upload_dir)
    user_ids = list(db.scalars(select(User.id).where(User.demo_seed.is_(True))).all())
    if user_ids:
        # Anything a demo account still owns that is NOT tagged is somebody's real data: detach it rather than lose it.
        db.execute(update(Inspection).where(Inspection.user_id.in_(user_ids)).values(user_id=None))
        db.execute(update(Inspection).where(Inspection.inspector_id.in_(user_ids)).values(inspector_id=None))
        db.execute(update(Inspection).where(Inspection.reviewed_by.in_(user_ids)).values(reviewed_by=None))
        db.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
        db.execute(delete(PasswordResetCode).where(PasswordResetCode.user_id.in_(user_ids)))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()
    return {"inspections": inspections, "users": len(user_ids)}


def _real_ocr():
    from backend.scripts.batch_evaluate import ocr_service

    return _ReadingService(ocr_service())


class _ReadingService:
    """Gives the batch OCR service the scan(path, progress, languages) shape the workflow calls."""

    def __init__(self, service) -> None:
        self._service = service
        self.languages = tuple(getattr(service, "languages", ("en", "hi")))

    def scan(self, path, progress=None, languages=None):
        kwargs = {key: value for key, value in (("progress", progress), ("languages", languages)) if value is not None}
        return self._service.scan(path, **kwargs)

    def close(self) -> None:
        self._service.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folders", nargs="*", type=Path, help="photo folders (default: ../PackComply-samples and ../PackComply-samples-phone if present)")
    parser.add_argument("--inspector-password", help="set demo_inspector's password (otherwise a random one is printed when the account is created)")
    parser.add_argument("--maker-password", help="set demo_maker's password")
    parser.add_argument("--no-ocr", action="store_true", help="never run OCR; photos without stored OCR are skipped")
    parser.add_argument("--reset-demo", action="store_true", help="delete only the data this script created (tagged demo_seed), then exit")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from backend.app.core.database import SessionLocal

    upload_dir = get_settings().upload_dir
    with SessionLocal() as db:
        if args.reset_demo:
            removed = reset_demo(db, upload_dir)
            print(f"Removed {removed['inspections']} demo inspection(s) and {removed['users']} demo account(s). Nothing else was touched.")
            return 0
        folders = args.folders or [folder for folder in DEFAULT_FOLDERS if folder.is_dir()]
        if not folders:
            print("No photo folder given and the PackComply-samples folders were not found next to the repository.")
        print("Seeding demo data" + (f" from {', '.join(str(folder) for folder in folders)}" if folders else "") + " ...")
        report = seed(db, list(folders), upload_dir, inspector_password=args.inspector_password, maker_password=args.maker_password, allow_ocr=not args.no_ocr)
    print(f"\nImported {len(report.imported)} new inspection(s); {len(report.skipped_existing)} already present; {len(report.skipped_failed)} skipped; {report.verified} verified.")
    if report.created_users:
        print("\nDemo accounts (shown once — note them down):")
        for username, password in report.created_users.items():
            print(f"  {username:16} password: {password}")
    for username in report.existing_users:
        print(f"  {username:16} already existed; password unchanged (use --{'inspector' if username == INSPECTOR['username'] else 'maker'}-password to set one)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
