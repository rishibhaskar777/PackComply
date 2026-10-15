from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png"}
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
