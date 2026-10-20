"""Artwork PDF upload: first page rendered to a PNG for manufacturers, rejected for inspectors."""
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api.auth import get_current_user
from backend.app.core.config import get_settings
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.models import Base, InspectionEvent, InspectionImage, User
from backend.app.services.uploads import UploadValidationError, render_pdf_first_page

MAKER = User(id=uuid4(), username="maker", email="maker@test.invalid", full_name="Maker", password_hash="x", role="MANUFACTURER", is_active=True)
OFFICIAL = User(id=uuid4(), username="official", email="official@test.invalid", full_name="Official", password_hash="x", role="OFFICIAL", is_active=True)


def make_pdf(pages: int = 1, encrypt: str | None = None) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, encrypt=StandardEncryption(encrypt, canPrint=0) if encrypt else None)
    for number in range(pages):
        pdf.setFont("Helvetica-Bold", 28)
        pdf.drawString(60, 700, f"MRP Rs. 99.00 page {number + 1}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db

    def as_user(user: User) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        with factory() as db:
            if db.get(User, user.id) is None:
                db.add(User(id=user.id, username=user.username, email=user.email, full_name=user.full_name, password_hash="x", role=user.role, is_active=True))
                db.commit()
        return TestClient(app)

    yield as_user, factory, tmp_path
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


def upload(client: TestClient, content: bytes, name: str = "artwork.pdf", content_type: str = "application/pdf"):
    created = client.post("/api/inspections", json={})
    assert created.status_code == 201, created.text
    inspection_id = created.json()["id"]
    return inspection_id, client.post(f"/api/inspections/{inspection_id}/image", files={"image": (name, content, content_type)})


def test_first_page_is_rendered_at_ocr_resolution() -> None:
    rendered = render_pdf_first_page(make_pdf(3), 10 * 1024 * 1024, 1280)
    image = Image.open(BytesIO(rendered.png))
    assert image.format == "PNG" and max(image.size) == 1280
    assert rendered.page_count == 3


def test_encrypted_pdf_is_rejected() -> None:
    with pytest.raises(UploadValidationError, match="Encrypted"):
        render_pdf_first_page(make_pdf(1, encrypt="secret"), 10 * 1024 * 1024, 1280)


def test_more_than_twenty_pages_is_rejected_but_twenty_is_fine() -> None:
    with pytest.raises(UploadValidationError, match="21 pages"):
        render_pdf_first_page(make_pdf(21), 10 * 1024 * 1024, 640)
    assert render_pdf_first_page(make_pdf(20), 10 * 1024 * 1024, 640).page_count == 20


def test_oversized_and_corrupt_pdfs_are_rejected() -> None:
    pdf = make_pdf(1)
    with pytest.raises(UploadValidationError, match="exceeds"):
        render_pdf_first_page(pdf, len(pdf) - 1, 640)
    with pytest.raises(UploadValidationError, match="not a valid PDF"):
        render_pdf_first_page(b"%PDF-1.4 this is not really a pdf", 10 * 1024 * 1024, 640)
    with pytest.raises(UploadValidationError, match="empty"):
        render_pdf_first_page(b"", 10 * 1024 * 1024, 640)


def test_manufacturer_pdf_is_stored_as_png_with_source_checksum_in_audit(app_client) -> None:
    as_user, factory, upload_dir = app_client
    pdf = make_pdf(2)
    inspection_id, response = upload(as_user(MAKER), pdf)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == "image/png" and body["original_filename"] == "artwork-page1.png"
    assert max(body["width"], body["height"]) == get_settings().ocr_max_image_dimension
    with factory() as db:
        storage_key = db.scalars(select(InspectionImage)).one().storage_key
    stored = (upload_dir / storage_key).read_bytes()
    assert stored[:8] == b"\x89PNG\r\n\x1a\n" and body["checksum"] == sha256(stored).hexdigest()

    with factory() as db:
        event = db.scalars(select(InspectionEvent).where(InspectionEvent.event_type == "PDF_RENDERED")).one()
        assert event.details["source_sha256"] == sha256(pdf).hexdigest() and event.details["pages"] == 2
        assert event.details["image_id"] == body["id"]
        assert db.scalars(select(InspectionImage)).one().content_type == "image/png"
    audit = as_user(MAKER).get(f"/api/inspections/{inspection_id}/audit").json()
    assert any(item["type"] == "PDF_RENDERED" and item["details"]["source_sha256"] == sha256(pdf).hexdigest() for item in audit)


def test_manufacturer_pdf_with_wrong_declared_type_is_still_a_pdf(app_client) -> None:
    as_user, _, _ = app_client
    _, response = upload(as_user(MAKER), make_pdf(1), "artwork.png", "image/png")
    assert response.status_code == 201


def test_manufacturer_gets_clear_422_for_encrypted_and_long_pdfs(app_client) -> None:
    as_user, factory, upload_dir = app_client
    client = as_user(MAKER)
    _, encrypted = upload(client, make_pdf(1, encrypt="secret"))
    _, long = upload(client, make_pdf(21))
    assert encrypted.status_code == 422 and "Encrypted" in encrypted.json()["detail"]
    assert long.status_code == 422 and "21 pages" in long.json()["detail"]
    assert list(upload_dir.iterdir()) == []
    with factory() as db:
        assert db.scalars(select(InspectionImage)).first() is None


def test_oversized_pdf_is_rejected_with_the_upload_limit(app_client, monkeypatch) -> None:
    as_user, _, _ = app_client
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1024")
    get_settings.cache_clear()
    _, response = upload(as_user(MAKER), make_pdf(1))
    assert response.status_code == 422 and "upload limit" in response.json()["detail"]


def test_inspector_pdf_is_rejected_and_nothing_is_stored(app_client) -> None:
    as_user, factory, upload_dir = app_client
    _, response = upload(as_user(OFFICIAL), make_pdf(1))
    assert response.status_code == 422 and "not accepted for inspections" in response.json()["detail"]
    _, disguised = upload(as_user(OFFICIAL), make_pdf(1), "label.png", "image/png")
    assert disguised.status_code == 422
    assert list(upload_dir.iterdir()) == []
    with factory() as db:
        assert db.scalars(select(InspectionImage)).first() is None
