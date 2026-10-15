from backend.app.services.extraction.layout import Layout, LayoutBox, merge_lines, suppress_duplicates
from backend.app.services.ocr import OcrDetection


def box(text: str, x0: float, y0: float, x1: float, y1: float, confidence: float = 0.95, language: str | None = None) -> dict:
    return {"text": text, "confidence": confidence, "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], "language": language}


def test_side_by_side_boxes_on_one_row_form_one_line_in_reading_order() -> None:
    layout = Layout.build("", [box("70g", 682, 308, 732, 336), box("NET WEIGHT:", 599, 313, 689, 333), box("Biscuits", 10, 200, 90, 215)])
    assert [line.text for line in layout.lines] == ["Biscuits", "NET WEIGHT: 70g"]
    assert layout.lines[1].bboxes == [box("", 599, 313, 689, 333)["bbox"], box("", 682, 308, 732, 336)["bbox"]]


def test_distant_columns_on_the_same_row_stay_separate_lines() -> None:
    layout = Layout.build("", [box("PROTEIN", 66, 509, 115, 523), box("6.5g", 226, 508, 253, 523), box("(H1)-ELVEETY INDUSTRIES PVT LTD", 470, 505, 705, 524)])
    assert sorted(line.text for line in layout.lines) == ["(H1)-ELVEETY INDUSTRIES PVT LTD", "6.5g", "PROTEIN"]


def test_right_and_below_neighbours() -> None:
    layout = Layout.build("", [
        box("MRP", 400, 120, 426, 136), box("₹ 20.00", 470, 120, 530, 136),
        box("MANUFACTURED FOR:", 400, 170, 550, 186), box("b", 420, 186, 428, 196, 0.64), box("Masti Snacks Pvt Ltd", 400, 192, 560, 208),
        box("Elsewhere", 900, 192, 980, 208),
    ])
    lines = {line.text: line for line in layout.lines}
    assert layout.right_of(lines["MRP"]).text == "₹ 20.00"
    assert layout.right_of(lines["₹ 20.00"]) is None
    # The lone "b" speck is skipped; the far-right box does not overlap horizontally.
    assert layout.next_in_column(lines["MANUFACTURED FOR:"]).text == "Masti Snacks Pvt Ltd"


def test_below_is_limited_to_two_and_a_half_line_heights() -> None:
    layout = Layout.build("", [box("Packed by:", 10, 10, 90, 26), box("Far Away Foods Ltd", 10, 80, 150, 96)])
    assert layout.below(layout.lines[0]) == []


def test_legacy_cross_language_rereads_are_suppressed_but_new_script_is_kept() -> None:
    boxes = [
        LayoutBox("Parle-G Gluco BiscuITs", 0.88, box("", 45, 191, 288, 208)["bbox"]),
        LayoutBox("Parle-G", 0.95, box("", 44, 189, 141, 208)["bbox"]),            # second pass, contained
        LayoutBox("पारले-जी", 0.9, box("", 45, 191, 288, 208)["bbox"]),            # adds Devanagari
    ]
    assert [item.text for item in suppress_duplicates(boxes)] == ["Parle-G Gluco BiscuITs", "पारले-जी"]


def test_low_confidence_specks_are_ignored_and_ocr_detections_are_accepted() -> None:
    layout = Layout.build("", [OcrDetection("Net Qty: 1 L", 0.97, box("", 0, 0, 100, 16)["bbox"], "en"), OcrDetection("1h", 0.27, box("", 0, 40, 10, 50)["bbox"])])
    assert [line.text for line in layout.lines] == ["Net Qty: 1 L"]


def test_text_fallback_without_boxes() -> None:
    layout = Layout.build("Net Wt.\n200 g\n", [{"text": "Net Wt.", "confidence": 0.9, "bbox": None}])
    assert not layout.geometric
    assert layout.right_of(layout.lines[0]) is None
    assert layout.next_in_column(layout.lines[0]).text == "200 g"


def test_merge_lines_requires_vertical_overlap() -> None:
    first = LayoutBox("INCL. OF ALL TAXES", 0.9, box("", 574, 279, 689, 298)["bbox"])
    second = LayoutBox("LIC. No.:10013022002245", 0.9, box("", 692, 272, 815, 287)["bbox"])
    assert len(merge_lines([first, second])) == 2
