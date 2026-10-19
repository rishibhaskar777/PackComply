"""Render the demo label artwork for the manufacturer portal, and (with ``--ocr``) store its OCR.

    python -m backend.scripts.make_demo_artwork [--ocr]

A manufacturer checks *artwork* before it is printed, so the demo needs artwork: ``demo/artwork/gluco-biscuits-draft.png``
(a first draft that is missing three declarations), ``gluco-biscuits-corrected.png`` (the same label with them added) and
``cream-cracker.png`` (a label that is already complete). The text is drawn with the Noto Sans fonts the project already
ships (SIL OFL), so the images are fully reproducible and contain nothing copyrighted.

``--ocr`` reads each image once with the project's OCR and writes ``demo/artwork/ocr/<name>.json``, which
``backend.scripts.seed_demo`` reuses so seeding takes seconds. Nothing here touches the database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTWORK_DIR = REPO_ROOT / "demo" / "artwork"
FONT_DIR = REPO_ROOT / "backend" / "app" / "assets" / "fonts"
SIZE = (1000, 1400)

# Palette of the drawn packaging only (this is an image, not application UI).
PAPER, INK, MUTED = (255, 255, 255), (24, 24, 27), (82, 82, 91)
BAND = (180, 83, 9)
BAND_INK = (255, 255, 255)

MAKER = "Sample Foods Pvt Ltd"
ADDRESS = "Plot 12, MIDC Andheri East, Mumbai 400093, Maharashtra"
CARE = "Consumer Care, Sample Foods Pvt Ltd, Mumbai 400057 · 1800-123-4567 · care@samplefoods.in"


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf")), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}".strip()
        if line and draw.textlength(candidate, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + ([line] if line else [])


def render(path: Path, *, product: str, tagline: str, quantity: str, mrp: str, tax_note: bool, unit_price: str | None, mfd: str, best_before: str,
           ingredients: str, care: bool, fssai: str) -> None:
    image = Image.new("RGB", SIZE, PAPER)
    draw = ImageDraw.Draw(image)
    margin, width = 70, SIZE[0] - 140
    draw.rectangle([0, 0, SIZE[0], 210], fill=BAND)
    draw.text((margin, 55), "SAMPLE FOODS", font=_font(True, 40), fill=BAND_INK)
    draw.text((margin, 125), tagline, font=_font(False, 30), fill=BAND_INK)
    y = 270
    draw.text((margin, y), product, font=_font(True, 96), fill=INK)
    y += 150
    draw.text((margin, y), f"Net Quantity: {quantity}", font=_font(True, 46), fill=INK)
    y += 90
    draw.text((margin, y), f"MRP {mrp}" + (" (incl. of all taxes)" if tax_note else ""), font=_font(True, 46), fill=INK)
    y += 80
    if unit_price:
        draw.text((margin, y), f"Unit Sale Price: {unit_price}", font=_font(False, 38), fill=INK)
        y += 70
    draw.text((margin, y), f"Mfd: {mfd}", font=_font(False, 38), fill=INK)
    draw.text((margin + 380, y), f"Best Before: {best_before}", font=_font(False, 38), fill=INK)
    y += 100
    body = _font(False, 30)
    for line in _wrap(draw, f"Ingredients: {ingredients}", body, width):
        draw.text((margin, y), line, font=body, fill=MUTED)
        y += 44
    y += 30
    for line in _wrap(draw, f"Manufactured by: {MAKER}, {ADDRESS}", body, width):
        draw.text((margin, y), line, font=body, fill=INK)
        y += 44
    y += 24
    if care:
        for line in _wrap(draw, CARE, body, width):
            draw.text((margin, y), line, font=body, fill=INK)
            y += 44
        y += 24
    draw.text((margin, y), f"FSSAI Lic. No. {fssai}", font=body, fill=INK)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)


GLUCO = dict(product="Gluco Biscuits", tagline="Energy biscuits with glucose", quantity="70 g", mrp="₹10.00", mfd="10/2026", best_before="07/2027",
             ingredients="Wheat flour (maida), sugar, edible vegetable oil (palm), glucose, invert syrup, milk solids, raising agents, salt.", fssai="11224999000123")
ARTWORK = {
    # A first draft: no "incl. of all taxes", no unit sale price and no consumer care line.
    "gluco-biscuits-draft": dict(GLUCO, tax_note=False, unit_price=None, care=False),
    "gluco-biscuits-corrected": dict(GLUCO, tax_note=True, unit_price="₹0.14 per g", care=True),
    "cream-cracker": dict(product="Cream Cracker", tagline="Crisp, light and salty", quantity="120 g", mrp="₹30.00", tax_note=True, unit_price="₹0.25 per g", mfd="09/2026", best_before="06/2027",
                          ingredients="Refined wheat flour (maida), edible vegetable oil, sugar, invert syrup, salt, milk solids, raising agents.", care=True, fssai="11224999000456"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ocr", action="store_true", help="also read each image with the project's OCR and store demo/artwork/ocr/<name>.json")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for name, spec in ARTWORK.items():
        render(ARTWORK_DIR / f"{name}.png", **spec)
        print(f"drew {ARTWORK_DIR / f'{name}.png'}")
    if args.ocr:
        from backend.scripts.batch_evaluate import ocr_service, run_ocr

        service = ocr_service()
        try:
            for name in ARTWORK:
                result = run_ocr(service, ARTWORK_DIR / f"{name}.png")
                target = ARTWORK_DIR / "ocr" / f"{name}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print(f"read {name}: {len(result['detections'])} lines in {result['ocr_seconds']} s")
        finally:
            service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
