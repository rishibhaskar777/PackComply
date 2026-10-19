"""python -m backend.scripts.seed_demo: idempotent, cached-OCR fast path, and --reset-demo touches only its own rows."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import get_settings
from backend.app.core.security import verify_password
from backend.app.models import Base, ComplianceResult, Inspection, InspectionImage, OcrResult, User
from backend.scripts import seed_demo

# Photos whose OCR is already stored in backend/tests/fixtures/labels/real, so no OCR engine is needed.
CACHED = ("611tFAX73zL._SL1500_.jpg", "Parle-G-Gluco-Biscuits-70g-2.jpeg", "WhatsApp Image 2026-10-15 at 10.51.15 PM.jpeg")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in CACHED:
        Image.new("RGB", (64, 64), (200, 200, 200)).save(photos / name, "JPEG" if name.lower().endswith((".jpg", ".jpeg")) else "PNG")
    (photos / "unknown-label.png").write_bytes(b"not an image")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions, photos, tmp_path / "uploads"
    get_settings.cache_clear()
    engine.dispose()


def run(sessions, photos, uploads, **options):
    # The inspector tests below look at the inspector's data alone; the manufacturer's products have their own tests.
    options.setdefault("maker_products", ())
    with sessions() as db:
        return seed_demo.seed(db, [photos], uploads, allow_ocr=False, log=lambda _line: None, **options)


def counts(sessions) -> tuple[int, int, int]:
    with sessions() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Inspection, User, OcrResult))


def test_seed_runs_the_real_pipeline_from_cached_ocr(env) -> None:
    sessions, photos, uploads = env
    report = run(sessions, photos, uploads, inspector_password="Inspector-pass-1", maker_password="Maker-pass-1")
    assert len(report.imported) == 3 and [name for name, _ in report.skipped_failed] == ["unknown-label.png"]
    assert set(report.created_users) == {"demo_inspector", "demo_maker"}
    with sessions() as db:
        inspector = db.scalar(select(User).where(User.username == "demo_inspector"))
        assert inspector.role == "OFFICIAL" and inspector.demo_seed and verify_password("Inspector-pass-1", inspector.password_hash)
        inspections = db.scalars(select(Inspection)).all()
        assert all(item.demo_seed and item.status == "COMPLETED" and item.inspection_number.startswith("DEMO-") for item in inspections)
        # Findings come from the real rule engine, with every enabled rule evaluated.
        assert db.scalar(select(func.count()).select_from(ComplianceResult)) == 3 * 14
        assert len({item.created_at.date() for item in inspections}) >= 2  # spread over the last weeks, not all "today"
        assert all(item.created_at <= datetime.now(timezone.utc).replace(tzinfo=item.created_at.tzinfo) for item in inspections)
    assert report.verified == 2 or report.verified == 3  # lowest-risk records of the inspector, never all of them
    assert len(list(uploads.iterdir())) == 3


def test_seed_is_idempotent_and_keeps_existing_passwords(env) -> None:
    sessions, photos, uploads = env
    run(sessions, photos, uploads, inspector_password="Inspector-pass-1")
    before = counts(sessions)
    again = run(sessions, photos, uploads)
    assert counts(sessions) == before
    assert again.imported == [] and len(again.skipped_existing) == 3 and again.created_users == {}
    assert sorted(again.existing_users) == ["demo_inspector", "demo_maker"]
    with sessions() as db:
        assert verify_password("Inspector-pass-1", db.scalar(select(User).where(User.username == "demo_inspector")).password_hash)
    # A password flag on a later run sets it explicitly.
    run(sessions, photos, uploads, maker_password="New-maker-pass-2")
    with sessions() as db:
        assert verify_password("New-maker-pass-2", db.scalar(select(User).where(User.username == "demo_maker")).password_hash)


def test_reset_demo_removes_only_tagged_data(env) -> None:
    sessions, photos, uploads = env
    uploads.mkdir(parents=True, exist_ok=True)
    real_file = uploads / "real-photo.jpg"
    real_file.write_bytes(b"real")
    with sessions() as db:
        real_user = User(username="real_official", email="real@example.invalid", full_name="Real Official", password_hash="x", role="OFFICIAL", is_active=True)
        db.add(real_user)
        db.commit()
        real = Inspection(inspection_number="PC-REAL-1", user_id=real_user.id, status="IMAGE_UPLOADED")
        db.add(real)
        db.flush()
        db.add(InspectionImage(inspection_id=real.id, storage_key="real-photo.jpg", original_filename="real.jpg", content_type="image/jpeg", byte_size=4))
        db.commit()
    run(sessions, photos, uploads)
    assert counts(sessions)[0] == 4
    with sessions() as db:
        removed = seed_demo.reset_demo(db, uploads)
    assert removed == {"inspections": 3, "users": 2}
    with sessions() as db:
        assert [item.inspection_number for item in db.scalars(select(Inspection)).all()] == ["PC-REAL-1"]
        assert [item.username for item in db.scalars(select(User)).all()] == ["real_official"]
        assert db.scalar(select(func.count()).select_from(InspectionImage)) == 1
    assert [path.name for path in uploads.iterdir()] == ["real-photo.jpg"]
    # Resetting twice is harmless, and the demo can be seeded again afterwards.
    with sessions() as db:
        assert seed_demo.reset_demo(db, uploads) == {"inspections": 0, "users": 0}
    assert len(run(sessions, photos, uploads).imported) == 3


# ----------------------------------------------------------------------------- the manufacturer's data

SAMPLE_NAMES = ("1000088947.jpg", "71t28Cnhd2L._SL1000_.jpg", "WhatsApp Image 2026-10-15 at 10.51.16 PM (3).jpeg", "713ThnhUQPL._SL1500_.jpg")


@pytest.fixture
def maker_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in SAMPLE_NAMES:
        Image.new("RGB", (64, 64), (200, 200, 200)).save(photos / name, "PNG" if name.endswith(".png") else "JPEG")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    yield sessions, photos, tmp_path / "uploads"
    get_settings.cache_clear()
    engine.dispose()


def run_with_maker(sessions, photos, uploads, **options):
    with sessions() as db:
        return seed_demo.seed(db, [photos], uploads, allow_ocr=False, log=lambda _line: None, **options)


def maker_products(sessions):
    from backend.app.services.products import product_summary

    with sessions() as db:
        maker = db.scalar(select(User).where(User.username == "demo_maker"))
        return product_summary(db, maker.id)


def test_demo_maker_has_six_products_with_a_mix_and_an_improving_gluco_biscuits(maker_env) -> None:
    sessions, photos, uploads = maker_env
    report = run_with_maker(sessions, photos, uploads)
    assert len(report.maker_imported) == 7  # six products, one of them checked twice
    summary = maker_products(sessions)
    assert (summary.total, summary.ready, summary.fix, summary.check) == (6, 3, 2, 1)
    assert summary.readiness_percent == 50 and summary.labels_to_fix == 2
    gluco = next(card for card in summary.products if card.product_name == "Gluco Biscuits")
    assert (gluco.checks, gluco.previous_fixes, gluco.fixes, gluco.status, gluco.improved) == (2, 3, 0, "READY", True)
    assert [check.fixes for check in gluco.history] == [0, 3]
    assert all(card.net_quantity or card.product_name for card in summary.products)
    with sessions() as db:
        rows = db.scalars(select(Inspection).where(Inspection.inspection_number.like("DEMO-M-%"))).all()
        assert len(rows) == 7 and all(item.demo_seed and item.status == "COMPLETED" for item in rows)
        second = next(item for item in rows if item.previous_inspection_id is not None)
        assert second.product_key == "gluco biscuits"


def test_maker_seeding_is_idempotent_and_leaves_the_inspector_alone(maker_env) -> None:
    sessions, photos, uploads = maker_env
    run_with_maker(sessions, photos, uploads)
    before = counts(sessions)
    with sessions() as db:
        inspector = db.scalar(select(User).where(User.username == "demo_inspector"))
        inspector_rows = db.scalar(select(func.count()).select_from(Inspection).where(Inspection.user_id == inspector.id))
    again = run_with_maker(sessions, photos, uploads)
    assert counts(sessions) == before and again.maker_imported == []
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Inspection).where(Inspection.user_id == inspector.id)) == inspector_rows


def test_reset_removes_the_makers_linked_checks_too(maker_env) -> None:
    sessions, photos, uploads = maker_env
    run_with_maker(sessions, photos, uploads)
    with sessions() as db:
        removed = seed_demo.reset_demo(db, uploads)
    assert removed["users"] == 2 and removed["inspections"] >= 7
    assert counts(sessions)[:2] == (0, 0)
    assert not uploads.exists() or not list(uploads.iterdir())


def test_older_manufacturer_demo_rows_are_replaced(maker_env) -> None:
    sessions, photos, uploads = maker_env
    run(sessions, photos, uploads)
    with sessions() as db:
        maker = db.scalar(select(User).where(User.username == "demo_maker"))
        db.add(Inspection(inspection_number="DEMO-LEGACY0001", user_id=maker.id, status="IMAGE_UPLOADED", demo_seed=True))
        db.commit()
    report = run_with_maker(sessions, photos, uploads)
    assert report.maker_removed == 1
    with sessions() as db:
        assert db.scalar(select(Inspection.id).where(Inspection.inspection_number == "DEMO-LEGACY0001")) is None
