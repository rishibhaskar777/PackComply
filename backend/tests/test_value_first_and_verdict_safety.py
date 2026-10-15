"""Value-first linking, fuzzy keys and verdict safety, each rule covered with invented text.

Every case is a general pattern met on phone photos (no brand's wording): keys
misread by OCR, values on the next row of a curved pack, coding areas a key
points to ("see below"), letter and symbol codes, key/value tables, glued and
dual-unit quantities, garbled currency marks, and the three declaration states
that keep an unreadable value from being reported as a missing declaration.
"""
import json

import pytest

from backend.app.services.compliance.rule_engine import NOT_IN_VIEW_MESSAGE, UNREAD_MESSAGE, ComplianceEngine, FieldEvidence
from backend.app.services.compliance.ruleset import RulesetError, parse_ruleset
from backend.app.services.compliance.rule_engine import CHECKS, CONDITIONS
from backend.app.services.extraction import ExtractionService, fuzzy
from backend.app.services.extraction import values as value_first
from backend.app.services.extraction.fuzzy import phrases
from backend.app.services.extraction.layout import Layout


def row(text: str, x: float, y: float, height: float = 16, confidence: float = 0.98) -> dict:
    width = 0.55 * height * len(text)
    return {"text": text, "confidence": confidence, "bbox": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}


def extract_rows(*rows: dict) -> dict:
    return ExtractionService().extract("\n".join(item["text"] for item in rows), list(rows))


def status(fields: dict, group: str) -> str:
    return json.loads(fields["declaration_status"].value)[group]["state"]


# ------------------------------------------------------------------ fuzzy keys

@pytest.mark.parametrize(("text", "key"), [
    ("VIRP: 45.00", "mrp"),                      # M read as VI
    ("Ingredlents: water", "ingredients"),       # i read as l
    ("Net Contemt: 200 ml", "net content"),      # one edit in a 10-letter key
    ("Manufactued by: X", "manufactured by"),    # a dropped letter
    ("Mfg. Date 03/26", "mfg date"),
])
def test_keys_misread_by_ocr_still_match(text: str, key: str) -> None:
    hits = fuzzy.find(text, phrases("k", key))
    assert hits and hits[0].start == 0


def test_short_keys_and_small_words_must_be_exact() -> None:
    assert not fuzzy.find("MAP 45", phrases("mrp", "mrp"))                   # 3-letter keys need context (short_edits)
    assert fuzzy.find("MAP (incl. of all taxes)", phrases("mrp", "mrp"), short_edits=1)
    assert not fuzzy.find("500 mg", phrases("mfg", "mfg"), short_edits=1)     # never shortened: "mg" is not "mfg"
    assert not fuzzy.find("No. 12", phrases("nw", "n w"), short_edits=1)      # 2-letter keys are always exact
    assert not fuzzy.find("MANUFACTURED IN A FACILITY", phrases("d", "manufactured on"))  # "on" must be read
    assert fuzzy.find("Manufacturdby: Acme", phrases("m", "manufactured by"))  # a glued, nearly complete key


def test_a_key_cut_off_at_the_end_of_the_line_matches_its_start() -> None:
    assert fuzzy.find("Manufacture D", phrases("d", "manufacture date"), prefix=True)
    assert not fuzzy.find("Manufacture D 12/25 batch", phrases("d", "manufacture date"), prefix=True)


# ------------------------------------------------------------ value candidates

@pytest.mark.parametrize(("text", "kind", "normalized"), [
    ("T499.00", "price", "₹499"),             # ₹ read as T, price-shaped amount
    ("२58/-", "price", "₹58"),                # ₹ read as the Devanagari digit two
    ("R≤.355.00", "price", "₹355"),
    ("र 120 50", "price", "₹120.5"),          # decimal point lost
    ("USP: ₹0.23/mn", "usp", "₹0.23/ml"),     # ml read as mn
    ("0.36/9", "usp", "₹0.36/g"),             # g read as 9
    ("250mlnet", "quantity", "250 ml"),       # key glued after the value
    ("100 ml / 3.38 fl. oz.", "quantity", "100 ml"),
])
def test_value_shapes_including_ocr_garbled_ones(text: str, kind: str, normalized: str) -> None:
    found = [value for value in value_first.candidates(Layout.from_text(text)) if value.kind == kind]
    assert found and found[0].normalized == normalized


@pytest.mark.parametrize("text", [
    "Protein 6.5 g", "Each 5 ml contains 10 mg/ml", "Amount per 100 g", "1000 g (125g x 8 units)",
    "1 tablespoon in 500ml water",
])
def test_nutrition_dosage_and_part_quantities_are_never_net_quantity_candidates(text: str) -> None:
    quantities = [value.value for value in value_first.candidates(Layout.from_text(text)) if value.kind == "quantity"]
    assert quantities in ([], ["1000 g"])


@pytest.mark.parametrize("text", ["Acrylates/C12-15 Alkyl Benzoate", "M.L. No. AB/COS/06/94", "Fat 6.90 4.83 16%", "Call 1-800-425-4449"])
def test_codes_decimals_and_phone_numbers_are_not_dates(text: str) -> None:
    assert not [value for value in value_first.candidates(Layout.from_text(text)) if value.kind == "date"]


def test_offer_prices_are_marked() -> None:
    values = value_first.candidates(Layout.from_text("Also available with bottle @ ₹40/-"))
    assert values and all(value.offer for value in values if value.kind == "price")


# -------------------------------------------------------------- value-first

def test_value_on_the_tilted_next_row_links_to_its_key() -> None:
    fields = extract_rows(row("Net Content", 400, 800), row("(when packed):", 400, 816), row("200ml", 470, 832))
    assert fields["net_quantity"].value == "200 ml"
    assert fields["net_quantity"].matched_pattern.startswith("net_quantity:")


def test_redirect_below_links_the_stamped_values_and_skips_offers() -> None:
    fields = extract_rows(
        row("Also available with refill @ ₹40/-", 100, 100),
        row("MRP (inclusive of all taxes)", 100, 200),
        row("Mfg. Date", 100, 216),
        row("see below.", 100, 232),
        row("₹45.00", 100, 400),
        row("06/2026", 100, 416),
    )
    assert fields["mrp"].value == "₹45.00" and fields["mrp"].matched_pattern == "mrp:redirect_below"
    assert fields["manufacturing_date"].normalized_value == "2026-06"
    assert fields["mrp"].needs_review  # far from its key: confirm on the pack


def test_combined_key_block_with_symbol_codes_and_a_coding_area() -> None:
    fields = extract_rows(
        row("B. No.(#), MRP (Incl. of all taxes)", 100, 100),
        row("Mfg. Date ($) & Expiry Date [¥]", 100, 116),
        row("₹ 210 50", 300, 400),
        row("#QX17 $03/26 ¥02/28", 100, 416),
    )
    assert fields["expiry_date"].normalized_value == "2028-02" and fields["expiry_date"].matched_pattern == "expiry_date:code_¥"
    assert fields["manufacturing_date"].normalized_value == "2026-03"
    assert fields["mrp"].value == "₹210.50" and fields["mrp"].matched_pattern == "mrp:coding_area"


def test_conventional_letter_codes_bind_without_a_printed_key() -> None:
    fields = extract_rows(row("Body Butter", 100, 50, 40), row("M 11/25 14 02", 100, 400), row("U 10/28", 100, 416))
    assert fields["manufacturing_date"].normalized_value == "2025-11"
    assert fields["manufacturing_date"].matched_pattern == "manufacturing_date:letter_code_M"
    assert fields["best_before"].normalized_value == "2028-10"


def test_key_column_and_tilted_value_column_pair_row_by_row() -> None:
    # Each value sits lower than its key (a curved pack): nearest-row matching would shift them by one.
    fields = extract_rows(
        row("B.No", 100, 300, 20), row("Mfd.Date", 100, 330, 20), row("Exp.Date", 100, 360, 20),
        row("QZ4471", 260, 318, 20), row("07/2026", 260, 348, 20), row("06/2028", 260, 378, 20),
    )
    assert fields["manufacturing_date"].normalized_value == "2026-07"
    assert fields["manufacturing_date"].matched_pattern == "manufacturing_date:table_row"
    assert fields["expiry_date"].normalized_value == "2028-06"


def test_two_date_keys_and_a_date_range_pair_in_order() -> None:
    fields = extract_rows(row("For Batch No., Mfg. Date-Use before Date,", 100, 100), row("A1234", 100, 120), row("02/2026-01/2029", 100, 140))
    assert (fields["manufacturing_date"].normalized_value, fields["best_before"].normalized_value) == ("2026-02", "2029-01")


def test_unkeyed_price_confirmed_by_unit_sale_price_and_net_quantity_is_the_mrp() -> None:
    fields = ExtractionService().extract("Net Wt. 200 g\nRs.120.00\nUSP: Rs.0.60/g")
    assert fields["mrp"].value == "₹120.00" and fields["mrp"].matched_pattern == "mrp:usp_consistency"
    assert ExtractionService().extract("Net Wt. 200 g\nRs.99.00\nUSP: Rs.0.60/g")["mrp"].value is None


# ------------------------------------------------------------------ layout

def test_sideways_photo_without_recorded_rotation_reads_in_order() -> None:
    # Tall boxes whose tops line up: text running down the photo, read with it turned 270 degrees.
    boxes = [
        {"text": text, "confidence": 0.98, "bbox": [[x, 100], [x + 20, 100], [x + 20, 100 + length], [x, 100 + length]]}
        for text, x, length in (("Net weight:", 300, 110), ("Product Information", 250, 190), ("Marketed by", 200, 120), ("Acme Wellness Pvt. Ltd.", 180, 230), ("Plot 4, Jaipur 302022", 160, 210))
    ]
    layout = Layout.build("", boxes)
    assert [line.text for line in layout.lines] == ["Plot 4, Jaipur 302022", "Acme Wellness Pvt. Ltd.", "Marketed by", "Product Information", "Net weight:"][::-1]


# ----------------------------------------------------------------- parties

def test_numbered_role_key_with_space_separated_roles() -> None:
    fields = ExtractionService().extract("(1) Imported Marketed by Kestrel Trading Pvt Ltd.\n12 Ring Road, Noida 201301")
    assert fields["importer"].value == "Kestrel Trading Pvt Ltd." and fields["brand_owner"].value == "Kestrel Trading Pvt Ltd."


def test_name_started_on_the_key_line_with_a_glued_suffix() -> None:
    fields = ExtractionService().extract("MADE IN INDIA. MKTD. BY: RIVERSIDE\nHOMECARELTD.(RHC), PUNE 411001.")
    assert fields["brand_owner"].value == "RIVERSIDE HOMECARE LTD."


def test_misread_role_key_needs_a_company_with_a_legal_suffix() -> None:
    assert ExtractionService().extract("Manutactured by:\nLotus Polymers Limited,\nPlot 3, Baddi 173205")["manufacturer"].value == "Lotus Polymers Limited"
    assert ExtractionService().extract("Manutactured by:\nVill. Kot Tl Sanand.")["manufacturer"].value is None


def test_company_under_a_multi_line_key_and_a_wrapped_suffix() -> None:
    fields = ExtractionService().extract(
        "Marketed by/Registered\nAddress/ Consumer Feedback:\nManager (Consumer Relations)\n#Orchid Home Products\nLimited, 5 Park Street, Kolkata 700016"
    )
    assert fields["brand_owner"].value == "Orchid Home Products Limited"


def test_ms_prefix_is_not_part_of_the_name() -> None:
    assert ExtractionService().extract("Manufactured by: M/s. Saffron Foods Pvt Ltd, 4 Mill Road, Agra 282001")["manufacturer"].value == "Saffron Foods Pvt Ltd"


def test_maker_reference_to_coded_units() -> None:
    fields = ExtractionService().extract(
        "FOR MFG. UNIT ADDRESS, READ CHARACTER AFTER B IN CODING AREA\nAND REFER HERE: AX) DELTA FOODS LTD., 4 MIDC, NAGPUR 440016.\nC) EAST COAST FOODS LTD., 9 SIPCOT, HOSUR 635126."
    )
    assert fields["manufacturer"].value == "DELTA FOODS LTD." and fields["manufacturer"].matched_pattern == "multi_unit_label"


def test_unreadable_line_after_a_maker_key_is_not_a_company() -> None:
    fields = extract_rows(row("Manufactured By:", 100, 100), row("aksarRoadHnrar-4dnatarakhand", 100, 120, confidence=0.6))
    assert fields["manufacturer"].value is None
    assert status(fields, "manufacturer") == "KEY_FOUND_VALUE_UNREAD"


# ------------------------------------------------------------ consumer care

def test_misread_at_sign_is_restored_in_a_contact_context_only() -> None:
    fields = ExtractionService().extract("Consumer Care:\nEmail: helpdesk0greenleaf.co.in")
    assert fields["consumer_care_email"].value == "helpdesk@greenleaf.co.in"
    assert fields["consumer_care_website"].value is None  # never a website
    assert ExtractionService()._repaired_email("contactldtealeaf.in") == "contact@tealeaf.in"
    assert ExtractionService()._repaired_email("a0b0c.com") is None  # two readings: ambiguous


def test_phone_repairs_and_a_contact_cluster_without_a_heading() -> None:
    assert ExtractionService().extract("Customer care: C800 123 4567")["consumer_care_phone"].normalized_value == "18001234567"
    assert ExtractionService().extract("Consumer care: (011)41234567")["consumer_care_phone"].normalized_value == "01141234567"
    fields = ExtractionService().extract("Plot 3, Delhi 110020\ninfo@greenleaf.in\n011 41234567 | www.greenleaf.in")
    assert fields["consumer_care_phone"].matched_pattern == "consumer_care_phone:contact_cluster"


# ----------------------------------------------------------------- category

@pytest.mark.parametrize(("text", "category"), [
    ("Each tablet contains Paracetamol IP 500 mg\nSchedule H drug", "drug"),
    ("Sunscreen gel SPF 50\nIngredients: Aqua, Glycerin, Sugar", "personal_care"),
    ("Liquid vaporiser refill\nHousehold insecticide", "household"),
    ("FSSAI Lic. No. 10020022000456\nIngredients: Water", "food"),
    ("Protective phone case\nTPU, Matte Black", "other"),
    ("Manufacturing licence M.L. No. AB/COS/12/18\nShampoo base", "personal_care"),
])
def test_product_category(text: str, category: str) -> None:
    assert ExtractionService().extract(text)["product_category"].value == category


def test_fssai_rule_does_not_apply_to_non_food() -> None:
    findings = ComplianceEngine().evaluate({"product_category": FieldEvidence("personal_care")})
    assert next(item for item in findings if item.rule_id == "FSSAI_LICENSE_PRESENT").status == "NOT_APPLICABLE"


# ------------------------------------------------------------- product name

def test_product_name_skips_misread_headings_foreign_copy_and_fragments() -> None:
    fields = extract_rows(
        row("Ingredlents", 100, 40, 40),
        row("Schlanke Passform / Maßgeschneidertes Design", 100, 100, 38),
        row("Calm Bloom Face Serum", 100, 200, 30),
        row("BUTTER) FLAVOUR, SALT, MILK SOLIDS", 100, 260, 30),
        row("Net content: 30 ml", 100, 320),
    )
    assert fields["product_name"].value == "Calm Bloom Face Serum"


def test_stacked_title_lines_join() -> None:
    fields = extract_rows(row("FOAM", 100, 100, 60), row("WASH", 100, 165, 60), row("Directions: wet hands", 100, 300), row("Net content: 9 g", 100, 330))
    assert fields["product_name"].value == "FOAM WASH"


# ----------------------------------------------------- tax-inclusive statement

def test_tax_statement_read_with_ocr_errors_and_cut_off() -> None:
    assert ExtractionService().extract("MRP Rs.99.00 (lnclusive of all laxes)")["tax_inclusive"].value == "true"
    cut = ExtractionService().extract("Net Wt. 50 g\nMRP Rs. 99 (Inct. of af")
    assert cut["tax_inclusive"].value is None and status(cut, "tax_inclusive") == "KEY_FOUND_VALUE_UNREAD"


# --------------------------------------------------------- declaration states

def test_declaration_states_from_extraction() -> None:
    fields = ExtractionService().extract("Net Contemt: see bottom of pack\nMRP: see below\nMfd. Date: see below\nManufactured by: Acme Foods Ltd, Pune 411001")
    assert status(fields, "net_quantity") == "KEY_FOUND_VALUE_UNREAD"
    assert status(fields, "mrp") == "KEY_FOUND_VALUE_UNREAD"
    assert status(fields, "manufacturing_date") == "KEY_FOUND_VALUE_UNREAD"
    assert status(fields, "manufacturer") == "DECLARED_WITH_VALUE"
    assert status(fields, "consumer_care") == "NOT_DETECTED"


def test_other_details_elsewhere_marks_the_rest_as_unread() -> None:
    fields = ExtractionService().extract("Liquid vaporiser refill\nFor MRP and other details, see bottom of the pack.")
    assert status(fields, "net_quantity") == "KEY_FOUND_VALUE_UNREAD" and status(fields, "unit_sale_price") == "KEY_FOUND_VALUE_UNREAD"


ADEQUATE = json.dumps({"text_lines": 30, "mean_confidence": 0.93, "low_confidence_line_share": 0.1})


def evaluate(states: dict[str, str], coverage: str | None = ADEQUATE, **values: str) -> dict[str, tuple[str, str]]:
    declarations = {group: {"state": state, "evidence": "Net Contemt: see below" if state == "KEY_FOUND_VALUE_UNREAD" else None} for group, state in states.items()}
    fields = {"declaration_status": FieldEvidence(json.dumps(declarations)), **{name: FieldEvidence(value) for name, value in values.items()}}
    if coverage:
        fields["ocr_coverage"] = FieldEvidence(coverage)
    return {item.rule_id: (item.status, item.message) for item in ComplianceEngine().evaluate(fields)}


@pytest.mark.parametrize(("rule", "group", "values"), [
    ("MANUFACTURER_INFORMATION", "manufacturer", {}),
    ("NET_QUANTITY_FORMAT", "net_quantity", {}),
    ("MRP_FORMAT", "mrp", {}),
    ("MRP_TAX_INCLUSIVE", "tax_inclusive", {"mrp": "₹50"}),
    ("UNIT_SALE_PRICE", "unit_sale_price", {"mrp": "₹50", "net_quantity": "200 g"}),
    ("CONSUMER_CARE_FORMAT", "consumer_care", {}),
    ("MANUFACTURE_DATE", "manufacturing_date", {}),
])
def test_key_without_readable_value_is_review_not_fail(rule: str, group: str, values: dict) -> None:
    status_, message = evaluate({group: "KEY_FOUND_VALUE_UNREAD"}, **values)[rule]
    assert status_ == "REVIEW" and message.startswith(UNREAD_MESSAGE) and "Net Contemt" in message


@pytest.mark.parametrize(("rule", "group", "values"), [
    ("MANUFACTURER_INFORMATION", "manufacturer", {}),
    ("NET_QUANTITY_FORMAT", "net_quantity", {}),
    ("MRP_FORMAT", "mrp", {}),
    ("MRP_TAX_INCLUSIVE", "tax_inclusive", {"mrp": "₹50"}),
    ("UNIT_SALE_PRICE", "unit_sale_price", {"mrp": "₹50", "net_quantity": "200 g"}),
    ("CONSUMER_CARE_FORMAT", "consumer_care", {}),
])
def test_not_detected_fails_only_on_a_well_read_photo(rule: str, group: str, values: dict) -> None:
    assert evaluate({group: "NOT_DETECTED"}, **values)[rule][0] == "FAIL"
    for poor in ({"text_lines": 4, "mean_confidence": 0.95, "low_confidence_line_share": 0.0},
                 {"text_lines": 30, "mean_confidence": 0.62, "low_confidence_line_share": 0.1},
                 {"text_lines": 30, "mean_confidence": 0.9, "low_confidence_line_share": 0.5}):
        status_, message = evaluate({group: "NOT_DETECTED"}, json.dumps(poor), **values)[rule]
        assert status_ == "REVIEW" and message.startswith(NOT_IN_VIEW_MESSAGE)


def test_manufacture_date_is_never_fail() -> None:
    assert evaluate({"manufacturing_date": "NOT_DETECTED"})["MANUFACTURE_DATE"][0] == "REVIEW"


def test_results_without_declaration_status_keep_their_old_outcomes() -> None:
    findings = {item.rule_id: item.status for item in ComplianceEngine().evaluate({"mrp_declaration": FieldEvidence("present_without_value")})}
    assert findings["MRP_FORMAT"] == "REVIEW" and findings["NET_QUANTITY_FORMAT"] == "FAIL"


def test_ruleset_evidence_thresholds_are_validated() -> None:
    rule = {"code": "MRP_FORMAT", "title": "MRP", "description": "d", "legal_reference": "Rule 6(1)(e)", "severity": "HIGH"}
    known = {"known_conditions": frozenset(CONDITIONS), "known_codes": frozenset(CHECKS)}
    parsed = parse_ruleset({"ruleset_id": "X", "version": 2, "rules": [rule], "evidence": {"min_text_lines": "10"}}, **known)
    assert parsed.evidence == {"min_text_lines": 10}
    with pytest.raises(RulesetError, match="unknown setting"):
        parse_ruleset({"ruleset_id": "X", "version": 2, "rules": [rule], "evidence": {"min_lines": 3}}, **known)
    with pytest.raises(RulesetError, match="must be a number"):
        parse_ruleset({"ruleset_id": "X", "version": 2, "rules": [rule], "evidence": {"min_mean_confidence": "high"}}, **known)


def test_bharat_is_a_domestic_country_of_origin() -> None:
    findings = {item.rule_id: item.status for item in ComplianceEngine().evaluate({"country_of_origin": FieldEvidence("Bharat")})}
    assert findings["IMPORTER_INFORMATION"] == "NOT_APPLICABLE"


def test_unreadable_phone_photo_needs_review_instead_of_non_compliant() -> None:
    # Keys printed, values stamped in a coding area that is out of focus: nothing may be called missing.
    fields = extract_rows(
        row("Calm Bloom Face Serum", 100, 40, 40),
        row("Marketed by: Calm Bloom Labs Pvt. Ltd., 4 Park Road, Jaipur 302022", 100, 120),
        row("For Consumer Complaints: help@calmbloom.in", 100, 140),
        row("Net content: 30 ml", 100, 160),
        row("MRP (incl. of all taxes)", 100, 180),
        row("Unit Sale Price", 100, 196),
        row("Mfg. Date", 100, 212),
        row("See at bottom", 100, 228),
        *(row(f"Aqua, Glycerin, Niacinamide {index}%", 100, 260 + 16 * index) for index in range(5)),
    )
    findings = {item.rule_id: item.status for item in ComplianceEngine().evaluate(
        {name: FieldEvidence(value.value) for name, value in fields.items() if value.value is not None})}
    assert findings["MRP_FORMAT"] == "REVIEW" and findings["MANUFACTURE_DATE"] == "REVIEW"
    assert "FAIL" not in findings.values()
