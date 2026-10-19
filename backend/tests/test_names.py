"""Display normalisation of OCR-read product names."""
import pytest

from backend.app.services.names import normalise_display_name


@pytest.mark.parametrize("raw,expected", [
    ("Parle-G Gluco BiscuITs", "Parle-G Gluco Biscuits"),
    ("BABYSHAMPOO", "Baby Shampoo"),
    ("PARLE-G GLUCO BISCUITS", "Parle-G Gluco Biscuits"),
    ("DABUR AMLA HAIR OIL", "Dabur Amla Hair Oil"),
    ("gluco biscuits of india", "Gluco Biscuits of India"),
    ("NATURE'S GOLD", "Nature's Gold"),
    ("Himalaya Baby Shampoo", "Himalaya Baby Shampoo"),
    ("  Maggi   2-Minute  Noodles ", "Maggi 2-Minute Noodles"),
    ("TATA SALT 1KG", "Tata Salt 1KG"),
    ("SPF 50 SUNSCREEN", "SPF 50 Sunscreen"),
    ("iPhone Case", "iPhone Case"),
    ("T-SHIRT", "T-Shirt"),
])
def test_casing_is_tidied_but_known_patterns_survive(raw, expected) -> None:
    assert normalise_display_name(raw) == expected


def test_it_is_idempotent_and_leaves_other_scripts_alone() -> None:
    for raw in ("Parle-G Gluco BiscuITs", "BABYSHAMPOO", "NATURE'S GOLD", "tata salt"):
        once = normalise_display_name(raw)
        assert normalise_display_name(once) == once
    assert normalise_display_name("पारले जी बिस्कुट") == "पारले जी बिस्कुट"
    assert normalise_display_name(None) is None and normalise_display_name("") == ""


def test_unsplittable_run_together_words_are_left_alone() -> None:
    assert normalise_display_name("XYZQWERTYUIOP") == "Xyzqwertyuiop"  # recased, not guessed at
    assert normalise_display_name("BABYSHAMPOOZ") == "Babyshampooz"


def test_label_title_prefers_the_product_then_the_company_and_never_a_file_name() -> None:
    from backend.app.services.corrections import label_title

    assert label_title("Gluco Biscuits", "Parle Products", "Parle") == "Gluco Biscuits"
    assert label_title(None, "Britannia Industries Ltd.", "Contract Packer") == "Britannia Industries Ltd."
    assert label_title("  ", None, "Nestlé India Limited") == "Nestlé India Limited"
    assert label_title(None, None, None) == "Untitled label"
    assert label_title("", "", "") == "Untitled label"
