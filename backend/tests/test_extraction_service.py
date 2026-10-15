import json

import pytest

from backend.app.services.extraction import ExtractionService
from backend.app.services.extraction.extraction_service import FIELD_NAMES


@pytest.fixture
def service() -> ExtractionService:
    return ExtractionService()


def test_extracts_complete_english_label(service: ExtractionService) -> None:
    fields = service.extract("""Sunshine Biscuits
Manufactured by: ABC Foods Pvt Ltd
123 Industrial Area, New Delhi - 110001
Packed by: XYZ Packaging Ltd
Imported by: Example Imports Pvt Ltd
MRP: Rs. 100
Net Quantity: 500g
Mfg: 08/2026
Packed On: 15/08/2026
Best Before: 6 Months
Use By: 08/2027
Expiry: 12/2027
Customer Care: 1800-123-4567
care@example.com
www.example.com""")
    assert fields["manufacturer"].value == "ABC Foods Pvt Ltd"
    assert fields["manufacturer_address"].value == "123 Industrial Area, New Delhi - 110001"
    assert fields["packer"].value == "XYZ Packaging Ltd"
    assert fields["importer"].value == "Example Imports Pvt Ltd"
    assert fields["mrp"].value == "₹100"
    assert fields["net_quantity"].value == "500 g"
    assert fields["manufacturing_date"].value == "08/2026"
    assert fields["packing_date"].value == "15/08/2026"
    assert fields["best_before"].value == "6 months"
    assert fields["use_by"].value == "08/2027"
    assert fields["expiry_date"].value == "12/2027"
    assert fields["consumer_care_phone"].value == "1800-123-4567"
    assert fields["consumer_care_email"].value == "care@example.com"
    assert fields["consumer_care_website"].value == "www.example.com"

@pytest.mark.parametrize(("text", "field", "expected"), [
    ("एमआरपी ₹100\nशुद्ध मात्रा: 500 ग्राम", "mrp", "₹100"),
    ("निर्माता: ABC Foods\nनिर्माण तिथि: 08/2026", "manufacturer", "ABC Foods"),
    ("Net Qty 1kg", "net_quantity", "1 kg"),
    ("Contents: 250 ml", "net_quantity", "250 ml"),
    ("M F G: 08/2026\nMRP Rs.l00", "mrp", None),
])
def test_supported_english_hindi_and_noise_patterns(service: ExtractionService, text: str, field: str, expected: str | None) -> None:
    assert service.extract(text)[field].value == expected


def test_multiple_candidates_require_review(service: ExtractionService) -> None:
    fields = service.extract("MRP ₹100\nMRP ₹120\nNet Qty: 500g\nNet Qty: 1kg\nManufacturer: ABC Foods\nManufacturer: XYZ Foods")
    assert fields["mrp"].value == "₹100" and fields["mrp"].needs_review
    assert fields["net_quantity"].value == "500 g" and fields["net_quantity"].needs_review
    assert fields["manufacturer"].value == "ABC Foods" and fields["manufacturer"].needs_review


def test_no_fabrication_and_month_year_precision(service: ExtractionService) -> None:
    fields = service.extract("ABC Foods\n08/2026\n₹100\nBest Before 18 months from manufacture")
    assert fields["manufacturer"].value is None
    assert fields["mrp"].value is None
    assert fields["expiry_date"].value is None
    assert fields["best_before"].value == "18 months from manufacture"
    assert fields["best_before"].value != "01/08/2026"


@pytest.mark.parametrize("text", ["", "Random artwork\nBatch 42\nBlue packaging"])
def test_empty_and_unrelated_ocr_produces_only_conservative_results(service: ExtractionService, text: str) -> None:
    fields = service.extract(text)
    assert fields["manufacturer"].value is None
    assert fields["mrp"].value is None
    for field in fields.values():
        assert 0.0 <= field.confidence <= 1.0
        assert isinstance(field.needs_review, bool)


# Layout-aware extraction -----------------------------------------------------

def row(text: str, x: float, y: float, height: float = 16, confidence: float = 0.98) -> dict:
    width = 0.55 * height * len(text)
    return {"text": text, "confidence": confidence, "bbox": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}


def extract_rows(service: ExtractionService, *rows: dict):
    return service.extract("\n".join(item["text"] for item in rows), list(rows))


def test_value_to_the_right_of_a_key_uses_exact_boxes_and_layout_confidence(service: ExtractionService) -> None:
    key, value = row("NET WEIGHT:", 400, 100), row("52g", 560, 100)
    fields = extract_rows(service, key, value)
    quantity = fields["net_quantity"]
    assert (quantity.value, quantity.normalized_value, quantity.matched_pattern) == ("52 g", "52 g", "net_quantity:right_of_key")
    assert quantity.source_text == "NET WEIGHT: | 52g" and quantity.source_bboxes == [key["bbox"], value["bbox"]]
    assert quantity.confidence < extract_rows(service, row("NET WEIGHT: 52g", 400, 100))["net_quantity"].confidence


def test_value_below_a_key_and_text_only_next_line_fallback(service: ExtractionService) -> None:
    below = extract_rows(service, row("NET WT.", 60, 200), row("200 g", 60, 220))["net_quantity"]
    text_only = service.extract("NET WT.\n200 g")["net_quantity"]
    assert below.value == text_only.value == "200 g"
    assert below.matched_pattern == "net_quantity:below_key" and text_only.matched_pattern == "net_quantity:next_line"
    assert text_only.source_bboxes is None and text_only.confidence < below.confidence


@pytest.mark.parametrize(("text", "expected"), [
    ("Net Wt. 200 g", "200 g"), ("Net Qty: 1 L", "1000 ml"), ("NET CONTENT 500ml", "500 ml"), ("Net Quantity: 12 N", "12 count"),
    ("Net Vol: 330 ml", "330 ml"), ("शुद्ध मात्रा: 500 ग्राम", "500 g"), ("शुद्ध वजन 1 किग्रा", "1000 g"), ("कुल मात्रा 250 मि.ली.", "250 ml"),
])
def test_net_quantity_keys_and_units(service: ExtractionService, text: str, expected: str) -> None:
    assert service.extract(text)["net_quantity"].normalized_value == expected


def test_nutrition_rows_are_never_net_quantity(service: ExtractionService) -> None:
    fields = service.extract("NUTRITION FACTS\nAMOUNT PER 100g (approx.)\nPROTEIN 6.5g\nCARBOHYDRATE 78.2g")
    assert fields["net_quantity"].value is None


@pytest.mark.parametrize(("text", "value", "normalized"), [
    ("M.R.P. ₹ 50/-", "₹50", "₹50"), ("MRP Rs.45.00 (Incl. of all taxes)", "₹45.00", "₹45"), ("MRP: ₹ 1,250.00", "₹1250.00", "₹1250"),
    ("MRP INR 99", "₹99", "₹99"), ("अधिकतम खुदरा मूल्य ₹ 60", "₹60", "₹60"), ("एमआरपी रु 35", "₹35", "₹35"),
])
def test_mrp_formats(service: ExtractionService, text: str, value: str, normalized: str) -> None:
    fields = service.extract(text)
    assert (fields["mrp"].value, fields["mrp"].normalized_value, fields["mrp_declaration"].value) == (value, normalized, "present_with_value")


def test_mrp_key_without_a_readable_price_is_never_invented(service: ExtractionService) -> None:
    fields = extract_rows(service, row("MRP RS.", 574, 260, 25), row("LIC. No.:10013022002245", 692, 272, 15), row("INCL. OF ALL TAXES", 574, 290, 18))
    assert fields["mrp"].value is None
    assert fields["mrp_declaration"].value == "present_without_value" and fields["mrp_declaration"].needs_review
    assert fields["tax_inclusive"].value == "true" and fields["tax_inclusive"].source_text == "INCL. OF ALL TAXES"
    absent = service.extract("Biscuits\nNet Wt. 50 g")
    assert absent["mrp_declaration"].value == "absent" and absent["tax_inclusive"].value is None


@pytest.mark.parametrize("statement", ["Inclusive of all taxes", "(Incl. of all taxes)", "INCL OF ALL TAXES", "(सभी करों सहित)"])
def test_tax_inclusive_statements(service: ExtractionService, statement: str) -> None:
    assert service.extract(f"MRP ₹10\n{statement}")["tax_inclusive"].value == "true"


def test_party_roles_are_distinguished(service: ExtractionService) -> None:
    fields = service.extract(
        "Marketed by:\nRoyal Grains Pvt Ltd\n12 MG Road, Karnal, Haryana - 132001\n"
        "Manufactured by:\nKarnal Agro Industries\nVillage Taraori, Karnal, HR - 132116\n"
        "Packed by: Haryana Packers & Co.\nSector 3, HSIIDC, Karnal - 132001\nLic. No. 10817005000222\n"
        "Imported by: Global Foods Trading LLP, Unit 4, Andheri East, Mumbai - 400069"
    )
    assert fields["brand_owner"].value == "Royal Grains Pvt Ltd" and fields["brand_owner_address"].value == "12 MG Road, Karnal, Haryana - 132001"
    assert fields["manufacturer"].value == "Karnal Agro Industries" and fields["manufacturer_address"].value == "Village Taraori, Karnal, HR - 132116"
    assert fields["packer"].value == "Haryana Packers & Co." and fields["packer_address"].value == "Sector 3, HSIIDC, Karnal - 132001"
    assert fields["importer"].value == "Global Foods Trading LLP" and fields["importer_address"].value == "Unit 4, Andheri East, Mumbai - 400069"


@pytest.mark.parametrize("key", ["MANUFACTURED FOR:", "Mfd. for", "Mktd. by", "Distributed by", "विपणनकर्ता:"])
def test_brand_owner_keys_never_produce_a_manufacturer(service: ExtractionService, key: str) -> None:
    fields = service.extract(f"{key}\nParle Products Pvt Ltd\nVile Parle East, Mumbai - 400057")
    assert fields["brand_owner"].value == "Parle Products Pvt Ltd"
    assert fields["manufacturer"].value is None


@pytest.mark.parametrize("value", ["FOR", "BY", "LTD", "12, MIDC Road, Pune - 411001", "MRP Rs 10"])
def test_party_values_must_be_company_lines(service: ExtractionService, value: str) -> None:
    assert service.extract(f"Manufactured by:\n{value}")["manufacturer"].value is None


def test_unit_code_is_stripped_and_suffix_misread_corrected(service: ExtractionService) -> None:
    fields = service.extract("Manufactured by:\n(BE) - SHASHI SIDNAL FOODS PVT LTO\nSANIKOPPA, BAILHONGAL, KA - 591125\nLIC. No.: 10012043000075")
    assert fields["manufacturer"].value == "SHASHI SIDNAL FOODS PVT LTD"
    assert fields["manufacturer_address"].value == "SANIKOPPA, BAILHONGAL, KA - 591125"
    assert fields["manufacturing_units"].value is None


def test_multi_unit_label_lists_every_unit(service: ExtractionService) -> None:
    fields = service.extract(
        "MANUFACTURED BY:\n(HP)-CF FOODS PVT LTD,15 A, SANKALAPUR INDL AREA\nBALLARI ROAD, KARIGNOOR, KA-583231\nLIC. No.:10014043000891\n"
        "(MY)-KOIEL FOOD PROCE'S, 33 /B, HOOTAGALLI\nK.R.S. ROAD, BELAVADI, KA - 570018\nLIC. No.:10012043000064\nCONSUMER CARE CELL"
    )
    units = json.loads(fields["manufacturing_units"].value)
    assert units == [
        {"code": "HP", "name": "CF FOODS PVT LTD", "address": "15 A, SANKALAPUR INDL AREA, BALLARI ROAD, KARIGNOOR, KA-583231", "licence": "10014043000891"},
        {"code": "MY", "name": "KOIEL FOOD PROCE'S", "address": "33 /B, HOOTAGALLI, K.R.S. ROAD, BELAVADI, KA - 570018", "licence": "10012043000064"},
    ]
    assert fields["manufacturer"].value == "CF FOODS PVT LTD"
    assert fields["manufacturer"].needs_review and fields["manufacturer"].matched_pattern == "multi_unit_label"


def test_product_name_prefers_prominent_line_and_brand_extension(service: ExtractionService) -> None:
    fields = extract_rows(service, row("WARNING: KEEP DRY", 40, 10), row("Parle-G", 480, 160, 72), row("Parle-G Gluco Biscuits", 45, 190, 17), row("INGREDIENTS: WHEAT FLOUR", 45, 210))
    name = fields["product_name"]
    assert name.value == "Parle-G Gluco Biscuits" and name.matched_pattern == "product_name:brand_extension"
    assert name.confidence <= 0.8


def test_product_name_with_a_small_margin_needs_review(service: ExtractionService) -> None:
    fields = extract_rows(service, row("Golden Crunch", 40, 10), row("Classic Salted", 40, 30))
    assert fields["product_name"].value == "Golden Crunch" and fields["product_name"].needs_review


@pytest.mark.parametrize(("text", "field", "value", "normalized"), [
    ("MFD: MAR 2026", "manufacturing_date", "MAR 2026", "2026-03"),
    ("PKD: MAR/26", "packing_date", "MAR/26", "2026-03"),
    ("Mfg. Date 03.2026", "manufacturing_date", "03.2026", "2026-03"),
    ("PKD 12-03-2026", "packing_date", "12-03-2026", "2026-03-12"),
    ("EXP 08/27", "expiry_date", "08/27", "2027-08"),
    ("Use by: 15/03/26", "use_by", "15/03/26", "2026-03-15"),
    ("Best Before: 9 months from manufacture", "best_before", "9 months from manufacture", "P9M"),
    ("Date of Packing: 01/2026", "packing_date", "01/2026", "2026-01"),
    ("निर्माण तिथि: 02/2026", "manufacturing_date", "02/2026", "2026-02"),
    ("उपयोग की अंतिम तिथि: 01/2027", "use_by", "01/2027", "2027-01"),
    ("समाप्ति: 12/2027", "expiry_date", "12/2027", "2027-12"),
])
def test_date_formats_and_iso_normalisation(service: ExtractionService, text: str, field: str, value: str, normalized: str) -> None:
    extracted = service.extract(text)[field]
    assert (extracted.value, extracted.normalized_value) == (value, normalized)


def test_party_keys_and_exports_are_not_dates(service: ExtractionService) -> None:
    fields = service.extract("Mfd. by:\n08/2026\nCOMMERCIAL EXPORT OF THIS PACK IS PROHIBITED 12/2027")
    assert fields["manufacturing_date"].value is None and fields["expiry_date"].value is None


def test_impossible_dates_are_rejected(service: ExtractionService) -> None:
    assert service.extract("MFG: 13/2026")["manufacturing_date"].value is None


def test_consumer_care_block_and_website_validation(service: ExtractionService) -> None:
    fields = service.extract(
        "TOTAL SATURATED FAT NOT MORE THAN S.8 PERCENT\nLIC.No.10014043000999\nM.R.P. Rs. 40/-\n"
        "CONSUMER CARE:\nCall Toll Free 1800 425 7788\nEmail: care@healthybites.co.in\nVisit: www.healthybites.co.in"
    )
    assert fields["consumer_care_phone"].value == "1800 425 7788" and fields["consumer_care_phone"].normalized_value == "18004257788"
    assert fields["consumer_care_email"].value == "care@healthybites.co.in"
    assert fields["consumer_care_website"].value == "www.healthybites.co.in"


@pytest.mark.parametrize("text", ["S.8 PERCENT", "LIC.No.10014043000999", "M.R.P Rs 50.00", "PVT.LTD MUMBAI", "K.R.S. ROAD", "care@example.com"])
def test_non_websites_are_rejected(service: ExtractionService, text: str) -> None:
    assert service.extract(f"Consumer care:\n{text}")["consumer_care_website"].value is None


def test_phone_numbers_outside_a_care_block_are_ignored(service: ExtractionService) -> None:
    assert service.extract("8901719255144\nBatch 1800 425 7788")["consumer_care_phone"].value is None


def test_fssai_licence_is_the_first_on_the_label(service: ExtractionService) -> None:
    fields = service.extract("MRP RS.\nLIC. No.:10013022002245\nMANUFACTURED BY:\n(HP)-CF FOODS PVT LTD, KA-583231\nLIC. No.:10014043000891\nFSSAI Lic. No.\n1001 2043 0000 64")
    assert fields["fssai_license"].value == "10013022002245"
    assert fields["fssai_license"].matched_pattern == "fssai_license:same_line:first_of_3"
    assert service.extract("FSSAI Lic. No.\n1001 2043 0000 64")["fssai_license"].value == "10012043000064"


def test_origin_unit_price_and_batch(service: ExtractionService) -> None:
    fields = service.extract("Country of Origin: Thailand\nUnit Sale Price: ₹0.25 per g\nB.No. KW2602\nMRP ₹ 50/-")
    assert fields["country_of_origin"].value == "Thailand"
    assert (fields["unit_sale_price"].value, fields["unit_sale_price"].normalized_value) == ("₹0.25 per g", "₹0.25/g")
    assert fields["batch_number"].value == "KW2602"
    assert service.extract("₹62.50/kg")["unit_sale_price"].value == "₹62.50 per kg"
    assert service.extract("Made in India")["country_of_origin"].value == "India"
    assert service.extract("Batch: see crimp")["batch_number"].value is None


def test_every_field_is_always_present(service: ExtractionService) -> None:
    assert set(service.extract("Net Wt. 50 g")) == set(FIELD_NAMES)
