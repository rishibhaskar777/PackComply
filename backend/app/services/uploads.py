from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
PDF_CONTENT_TYPE = "application/pdf"
MAX_PDF_PAGES = 20
# EXIF orientations that rotate the image by 90/270 degrees on display.
_TRANSPOSED_ORIENTATIONS = {5, 6, 7, 8}


class UploadValidationError(ValueError):
    """Raised when an uploaded file is not a permitted, valid package image."""


@dataclass(frozen=True)
class StoredUpload:
    storage_key: str
    original_filename: str
    content_type: str
    byte_size: int
    checksum: str
    width: int | None = None
    height: int | None = None


def image_dimensions(source: bytes | Path) -> tuple[int, int] | None:
    """Return the displayed (EXIF-oriented) pixel size, or None if unreadable.

    OpenCV and browsers both apply EXIF orientation, so stored dimensions and
    OCR bounding boxes must use the same oriented coordinate space.
    """
    try:
        with Image.open(BytesIO(source) if isinstance(source, bytes) else source) as image:
            width, height = image.size
            orientation = image.getexif().get(0x0112)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    return (height, width) if orientation in _TRANSPOSED_ORIENTATIONS else (width, height)


def validate_image(content: bytes, declared_content_type: str, max_bytes: int) -> str:
    if not content:
        raise UploadValidationError("The uploaded image is empty.")
    if len(content) > max_bytes:
        raise UploadValidationError(f"Image exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
    if declared_content_type not in ALLOWED_IMAGE_TYPES:
        raise UploadValidationError("Only JPG, JPEG, and PNG images are supported.")

    try:
        image = Image.open(BytesIO(content))
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise UploadValidationError("The file content is not a valid image.") from error

    detected_type = Image.open(BytesIO(content)).get_format_mimetype()
    if detected_type not in ALLOWED_IMAGE_TYPES or detected_type != declared_content_type:
        raise UploadValidationError("The file's declared type does not match its image content.")
    return ALLOWED_IMAGE_TYPES[detected_type]


@dataclass(frozen=True)
class RenderedPdf:
    """The first page of an artwork PDF as PNG bytes, plus what the audit trail records about the source."""
    png: bytes
    page_count: int
    source_sha256: str
    source_bytes: int


def is_pdf(content: bytes, declared_content_type: str) -> bool:
    return declared_content_type == PDF_CONTENT_TYPE or content[:5] == b"%PDF-"


def render_pdf_first_page(content: bytes, max_bytes: int, max_dimension: int) -> RenderedPdf:
    """Render page 1 of an artwork PDF to a PNG whose long side is ``max_dimension`` (what OCR reads).

    Rejects empty, oversized, malformed, encrypted and long (> ``MAX_PDF_PAGES`` pages) PDFs. Only the first
    page is ever rendered. pypdfium2 (Apache-2.0 / BSD-3) runs locally; nothing leaves the machine.
    """
    if not content:
        raise UploadValidationError("The uploaded PDF is empty.")
    if len(content) > max_bytes:
        raise UploadValidationError(f"PDF exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
    if content[:5] != b"%PDF-":
        raise UploadValidationError("The file content is not a valid PDF.")
    # /Encrypt in the trailer also covers PDFs that open without a password but restrict copying or printing.
    if b"/Encrypt" in content:
        raise UploadValidationError("Encrypted or password-protected PDFs are not supported. Export an unprotected copy of the artwork.")

    import pypdfium2 as pdfium  # Imported here so image-only deployments never load it.

    try:
        document = pdfium.PdfDocument(content)
    except (pdfium.PdfiumError, ValueError, OSError) as error:
        message = str(error).lower()
        if "password" in message:
            raise UploadValidationError("Encrypted or password-protected PDFs are not supported. Export an unprotected copy of the artwork.") from error
        raise UploadValidationError("The file content is not a valid PDF.") from error
    try:
        page_count = len(document)
        if page_count < 1:
            raise UploadValidationError("The PDF has no pages.")
        if page_count > MAX_PDF_PAGES:
            raise UploadValidationError(f"The PDF has {page_count} pages; at most {MAX_PDF_PAGES} are accepted. Upload only the label artwork.")
        page = document[0]
        try:
            width, height = page.get_size()  # PDF points
            if width <= 0 or height <= 0:
                raise UploadValidationError("The first page of the PDF has no printable area.")
            scale = max_dimension / max(width, height)
            bitmap = page.render(scale=scale)  # White background, no annotations/forms by default.
            image = bitmap.to_pil().convert("RGB")
        finally:
            page.close()
    except UploadValidationError:
        raise
    except (pdfium.PdfiumError, ValueError, OSError, MemoryError) as error:
        raise UploadValidationError("The first page of the PDF could not be rendered.") from error
    finally:
        document.close()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    if buffer.tell() > max_bytes:
        raise UploadValidationError(f"The rendered first page exceeds the {max_bytes // (1024 * 1024)} MB limit. Export a simpler artwork PDF.")
    return RenderedPdf(buffer.getvalue(), page_count, sha256(content).hexdigest(), len(content))


def store_image(content: bytes, original_filename: str, content_type: str, upload_dir: Path) -> StoredUpload:
    """Store bytes below the configured directory without trusting the client path."""
    extension = ALLOWED_IMAGE_TYPES[content_type]
    safe_name = f"{uuid4().hex}{extension}"
    root = upload_dir.resolve()
    destination = (root / safe_name).resolve()
    if not destination.is_relative_to(root):  # Defensive guard for future storage changes.
        raise RuntimeError("Unsafe upload storage path.")

    root.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    width, height = image_dimensions(content) or (None, None)
    return StoredUpload(
        storage_key=safe_name,
        original_filename=Path(original_filename or "package-image").name,
        content_type=content_type,
        byte_size=len(content),
        checksum=sha256(content).hexdigest(),
        width=width,
        height=height,
    )
