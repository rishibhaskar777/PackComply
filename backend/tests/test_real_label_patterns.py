"""Label patterns met on real package photos, each covered with invented text.

Every case here is a general layout or wording pattern (not any brand's text):
combined role keys, maker keys after the product name, company names wrapped
onto the next line, "Use before N months", consumer-care wording, non-food
ingredient lists, the licence printed with the fssai logo, promotional lines,
rotated side text and photos taken sideways.
"""
import json
from pathlib import Path

from PIL import Image
import pytest

from backend.app.services.compliance.rule_engine import ComplianceEngine, FieldEvidence
from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.layout import Layout
from backend.app.services.ocr import OcrDetection, PaddleOcrService
from backend.app.services.ocr.ocr_service import is_sideways, unrotate_bbox


def box(text: str, x0: float, y0: float, x1: float, y1: float, confidence: float = 0.95, rotation: int = 0) -> dict:
    return {"text": text, "confidence": confidence, "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "language": "en", "rotation": rotation}


@pytest.fixture
def service() -> ExtractionService:
    return ExtractionService()


# ------------------------------------------------------------------ parties

def test_one_party_named_for_several_roles(service: ExtractionService) -> None:
    fields = service.extract("Manufactured, Packed & Marketed by:\nRiverbend Home Care Limited, 4 Lake Road, Pune - 411001")
    for role in ("manufacturer", "packer", "brand_owner"):
        assert fields[role].value == "Riverbend Home Care Limited"
        assert fields[f"{role}_address"].value == "4 Lake Road, Pune - 411001"


def test_combined_key_with_for_names_the_brand_owner_only(service: ExtractionService) -> None:
    fields = service.extract("Manufactured & Packed for: Orchid Retail Ltd, 9 Ring Road, Surat - 395002")
    assert fields["brand_owner"].value == "Orchid Retail Ltd"
    assert fields["manufacturer"].value is None and fields["packer"].value is None


def test_maker_key_after_the_product_name_with_a_wrapped_company_name(service: ExtractionService) -> None:
    fields = service.extract("Glow Fresh Neem Mfg by Sunpeak Consumer\nGoods Private Limited,\nPlot 7, MIDC, Nashik - 422010")
    assert fields["manufacturer"].value == "Sunpeak Consumer Goods Private Limited"
    assert fields["manufacturer_address"].value == "Plot 7, MIDC, Nashik - 422010"
    assert fields["product_name"].value == "Glow Fresh Neem"
    assert fields["product_name"].matched_pattern == "product_name:before_maker_key"


def test_inline_role_words_without_a_company_are_not_parties(service: ExtractionService) -> None:
    fields = service.extract("For details refer 'Packed By' address on the pack\nWrite to the Manufacturer or our helpdesk")
    assert fields["packer"].value is None and fields["manufacturer"].value is None


def test_company_suffix_wrapped_onto_the_next_line(service: ExtractionService) -> None:
    fields = service.extract("Manufactured by: Hillcrest Personal Care Pvt\nLtd, Survey No. 12, Baddi, 173205")
    assert fields["manufacturer"].value == "Hillcrest Personal Care Pvt Ltd"
    assert fields["manufacturer_address"].value == "Survey No. 12, Baddi, 173205"


def test_company_name_ends_at_its_suffix_before_a_full_stop(service: ExtractionService) -> None:
    fields = service.extract("Marketed by: Bluebay Labs Limited. 'Sagar House', Link Road, Mumbai - 400053")
    assert fields["brand_owner"].value == "Bluebay Labs Limited"
    assert fields["brand_owner_address"].value.startswith("'Sagar House'")


# -------------------------------------------------------------------- dates

def test_use_before_months_from_manufacture(service: ExtractionService) -> None:
    fields = service.extract("Mfg. Date: see pack\nUse before 24 months from the date of manufacture.")
    assert fields["best_before"].normalized_value == "P24M"
    assert fields["manufacturing_date"].value is None


# ----------------------------------------------------------- consumer care

@pytest.mark.parametrize("heading", ["For Consumer Comments/Complaints, call", "Call CONSUMER RELATIONS on", "Customer Support:"])
def test_consumer_care_heading_wordings(service: ExtractionService, heading: str) -> None:
    fields = service.extract(f"{heading}\n022-24440000")
    assert fields["consumer_care_phone"].normalized_value == "02224440000"


def test_toll_free_number_counts_without_a_heading(service: ExtractionService) -> None:
    fields = service.extract("Write to us or call\n1800-200-3000 or\nvisit our website")
    assert fields["consumer_care_phone"].normalized_value == "18002003000"
    assert fields["consumer_care_phone"].matched_pattern == "consumer_care_phone:toll_free_anywhere"


def test_website_with_a_contact_page_path_is_valid() -> None:
    findings = ComplianceEngine().evaluate({"consumer_care_website": FieldEvidence("https://in.example.com/contact-us/")})
    care = next(finding for finding in findings if finding.rule_id == "CONSUMER_CARE_FORMAT")
    assert care.status == "REVIEW" and care.message.startswith("Only a website")


# ------------------------------------------------------- product category

def test_ingredients_on_a_soap_are_not_food(service: ExtractionService) -> None:
    fields = service.extract("INGREDIENTS: Sodium Palmate, Aqua, Glycerin, Perfume\nTOILET SOAP Gr. 1 TFM 76%")
    assert fields["product_category"].value is None


def test_ingredients_alone_still_mean_food(service: ExtractionService) -> None:
    assert service.extract("INGREDIENTS: Rice flour, Salt, Spices")["product_category"].value == "food"


def test_nutrition_panel_means_food_even_with_cleaner_words(service: ExtractionService) -> None:
    fields = service.extract("INGREDIENTS: Wheat flour, Dough conditioner\nNUTRITION INFORMATION per 100 g\nKeep the cleaner sachet away")
    assert fields["product_category"].value == "food"


# ------------------------------------------------------------------ licence

def test_licence_printed_with_the_fssai_logo_wins_over_unit_licences(service: ExtractionService) -> None:
    fields = service.extract("", [
        box("Mfd. at: Unit A, Plot 3, Hosur - 635109", 10, 10, 300, 24),
        box("Lic. No. 10011011000011", 10, 30, 220, 44),
        box("Unit B, Sector 2, Pantnagar - 263153", 10, 50, 300, 64),
        box("Lic. No. 10011011000022", 10, 70, 220, 84),
        box("fssai", 600, 300, 680, 330),
        box("Lic. No. 10022022000033", 590, 335, 760, 350),
    ])
    assert fields["fssai_license"].value == "10022022000033"


def test_first_licence_is_kept_when_no_logo_is_read(service: ExtractionService) -> None:
    fields = service.extract("Lic. No. 10011011000011\nLic. No. 10022022000033")
    assert fields["fssai_license"].value == "10011011000011"


# ------------------------------------------------------------- product name

def test_promotional_and_qr_lines_are_not_the_product_name(service: ExtractionService) -> None:
    fields = service.extract("", [
        box("SCAN TO WIN", 10, 10, 200, 60),
        box("Golden Crunch Cookies", 10, 80, 220, 110),
        box("vouchers worth 500", 10, 120, 200, 140),
    ])
    assert fields["product_name"].value == "Golden Crunch Cookies"


def test_product_name_before_a_caution_sentence(service: ExtractionService) -> None:
    fields = service.extract("Liquid Dish Wash Gel. CAUTION: Keep away from children")
    assert fields["product_name"].value == "Liquid Dish Wash Gel"


def test_rotated_side_text_stays_out_of_lines_and_names() -> None:
    layout = Layout.build("", [
        box("Sunleaf", 300, 100, 600, 220),
        box("scan for more", 620, 60, 640, 260),   # printed up the panel edge
        box("Herbal Toothpowder", 300, 230, 600, 260),
    ])
    assert sorted(line.text for line in layout.lines) == ["Herbal Toothpowder", "Sunleaf", "scan for more"]
    assert next(line for line in layout.lines if line.text == "scan for more").vertical
    assert ExtractionService().extract("", [box("Sunleaf", 300, 100, 600, 220), box("scan for more", 620, 60, 640, 260)])["product_name"].value == "Sunleaf"


# ------------------------------------------------------------- sideways photo

def test_sideways_detection_needs_mostly_tall_boxes() -> None:
    tall = [OcrDetection("abcd", 0.9, [[0, 0], [10, 0], [10, 80], [0, 80]]) for _ in range(6)]
    wide = [OcrDetection("abcd", 0.9, [[0, 0], [80, 0], [80, 10], [0, 10]]) for _ in range(6)]
    assert is_sideways(tall)
    assert not is_sideways(tall + wide + wide)
    assert not is_sideways(tall[:3])


@pytest.mark.parametrize("rotation", [90, 270])
def test_boxes_read_on_a_turned_photo_map_back_to_original_pixels(tmp_path: Path, rotation: int) -> None:
    import cv2
    import numpy

    width, height = 40, 30
    image = numpy.zeros((height, width), dtype=numpy.uint8)
    image[5, 30] = 255  # one marked pixel at x=30, y=5
    turned = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE if rotation == 90 else cv2.ROTATE_90_COUNTERCLOCKWISE)
    y, x = (int(value) for value in numpy.argwhere(turned == 255)[0])
    (ox, oy), = unrotate_bbox([[x + 0.5, y + 0.5]], rotation, (width, height))
    assert abs(ox - 30.5) <= 1 and abs(oy - 5.5) <= 1


def test_preprocessing_turns_the_photo(tmp_path: Path) -> None:
    image_path = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), color="white").save(image_path)
    processed = PaddleOcrService._preprocess(image_path, max_image_dimension=400, rotation=90)
    try:
        assert Image.open(processed).size == (100, 400)
    finally:
        processed.unlink(missing_ok=True)


def test_sideways_photo_is_read_in_the_orientation_that_reads_best(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "label.png"
    Image.new("RGB", (200, 100), color="white").save(image)
    sideways = [OcrDetection("x1y", 0.6, [[i * 20, 0], [i * 20 + 10, 0], [i * 20 + 10, 80], [i * 20, 80]]) for i in range(6)]
    upright = [OcrDetection("Net Wt. 200 g", 0.97, [[10, 10], [90, 10], [90, 20], [10, 20]]), OcrDetection("Marketed by: Riverbend Ltd", 0.95, [[10, 30], [95, 30], [95, 40], [10, 40]])]

    def fake_preprocess(path: Path, max_image_dimension: int = 640, rotation: int = 0) -> Path:
        output = path.parent / f"processed-{rotation}.png"
        output.write_bytes(b"x")
        return output

    reads = {"processed-0.png": sideways, "processed-90.png": [], "processed-270.png": upright}
    monkeypatch.setattr(PaddleOcrService, "_preprocess", staticmethod(fake_preprocess))
    service = PaddleOcrService(languages=("en",), max_image_dimension=200)
    monkeypatch.setattr(service, "_predict", lambda language, path: reads[path.name])

    document = service.scan(image)

    assert [item.text for item in document.detections] == ["Net Wt. 200 g", "Marketed by: Riverbend Ltd"]
    assert {item.rotation for item in document.detections} == {270}
    # Read at (10..90, 10..20) on the photo turned 270 degrees = original x 180..190, y 10..90.
    assert document.detections[0].bbox == [[190, 10], [190, 90], [180, 90], [180, 10]]
    # The layout reads the stored boxes upright again, so key and value share a line.
    layout = Layout.build(document.raw_text, document.as_database_detections())
    assert [line.text for line in layout.lines] == ["Net Wt. 200 g", "Marketed by: Riverbend Ltd"]


def test_auto_rotation_can_be_turned_off(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "label.png"
    Image.new("RGB", (200, 100), color="white").save(image)
    sideways = [OcrDetection("x1y", 0.6, [[i * 20, 0], [i * 20 + 10, 0], [i * 20 + 10, 80], [i * 20, 80]]) for i in range(6)]
    monkeypatch.setattr(PaddleOcrService, "_preprocess", staticmethod(lambda path, max_image_dimension=640, rotation=0: (path.parent / "p.png", (path.parent / "p.png").write_bytes(b"x"))[0]))
    service = PaddleOcrService(languages=("en",), max_image_dimension=200, auto_rotate=False)
    calls = []
    monkeypatch.setattr(service, "_predict", lambda language, path: calls.append(path) or sideways)
    assert all(item.rotation == 0 for item in service.scan(image).detections)
    assert len(calls) == 1


# ----------------------------------------- patterns from the retired hold-out

@pytest.mark.parametrize("key", ["Imported In India By :", "Imported into India by", "Imported & Marketed by:"])
def test_importer_key_wordings(service: ExtractionService, key: str) -> None:
    fields = service.extract(f"{key}\nNorthwind Trading Co. (Exports) Pvt. Ltd.\nB-4, Okhla Phase II, New Delhi - 110020")
    assert fields["importer"].value == "Northwind Trading Co. (Exports) Pvt. Ltd."
    assert fields["importer_address"].value == "B-4, Okhla Phase II, New Delhi - 110020"
    assert fields["import_declaration"].value == "present"


@pytest.mark.parametrize("text, expected", [
    ("INGREDIENTS: Aqua, Glycerin Net Wt. When Packed 75 g", "75 g"),
    ("Net Quantity (when packed): 500 ml", "500 ml"),
    ("Net Weight at the time of packing\n1 kg", "1000 g"),
])
def test_net_quantity_when_packed_wordings(service: ExtractionService, text: str, expected: str) -> None:
    assert service.extract(text)["net_quantity"].normalized_value == expected


@pytest.mark.parametrize("line, name, address", [
    ("MANUFACTURED BY:\nSHIVAM GRAM UDYOG 12/40 KM STONE, HISAR ROAD, HISAR - 125001", "SHIVAM GRAM UDYOG", "12/40 KM STONE, HISAR ROAD, HISAR - 125001"),
    ("Packed by: Lotus Agro Traders Plot 9, Phase 2, Jalna - 431203", "Lotus Agro Traders", "Plot 9, Phase 2, Jalna - 431203"),
])
def test_maker_without_a_legal_suffix_ends_at_the_address(service: ExtractionService, line: str, name: str, address: str) -> None:
    role = "manufacturer" if line.startswith("MANUFACTURED") else "packer"
    fields = service.extract(line)
    assert fields[role].value == name
    assert fields[f"{role}_address"].value == address


def test_company_suffix_followed_by_its_abbreviation(service: ExtractionService) -> None:
    fields = service.extract("TFM 70%. MARKETED BY EXAMPLE CONSUMER PRODUCTS LIMITED (ECPL). © ECPL 2020. A REGISTERED MARK")
    assert fields["brand_owner"].value == "EXAMPLE CONSUMER PRODUCTS LIMITED"


def test_coded_units_written_as_a_paragraph(service: ExtractionService) -> None:
    fields = service.extract(
        "MFG. BY, SEE THE LETTER AFTER THE BATCH CODE. A) RIVER SOAPS PVT. LTD., PLOT 4, GIDC,\n"
        "VAPI - 396195. B) HILLTOP CHEMICALS LTD., SECTOR 5, BADDI - 173205. K) COASTAL\n"
        "CARE INDUSTRIES, MIDC, TALOJA - 410208."
    )
    assert fields["manufacturer"].value == "RIVER SOAPS PVT. LTD."
    assert fields["manufacturer"].matched_pattern == "multi_unit_label" and fields["manufacturer"].needs_review
    units = json.loads(fields["manufacturing_units"].value)
    assert [(unit["code"], unit["name"]) for unit in units] == [("A", "RIVER SOAPS PVT. LTD."), ("B", "HILLTOP CHEMICALS LTD."), ("K", "COASTAL CARE INDUSTRIES")]
    assert units[1]["address"].startswith("SECTOR 5, BADDI - 173205")


def test_codes_after_a_named_maker_are_its_unit_addresses(service: ExtractionService) -> None:
    fields = service.extract("Mfg. By: Example Foods Limited,\nA) Ring Road, Indore - 452001\nB) MIDC Area, Nagpur - 440016")
    assert fields["manufacturer"].value == "Example Foods Limited"
    assert fields["manufacturing_units"].value is None
