"""The strength sets CSV template 1.0.0 (files spec 8.4; strength spec 5). Synthetic data."""

from __future__ import annotations

import pytest

from coach.integrations.files import strength_csv
from coach.integrations.files.strength_csv import SetRow, TemplateError, export, parse

HEADER = ",".join(strength_csv.HEADER)


def rows(*lines: str, header: str = HEADER) -> bytes:
    return ("\n".join((header, *lines)) + "\n").encode("utf-8")


@pytest.mark.req("INT-FIL-001", "FR-STR-005")
def test_a_template_file_gives_typed_rows() -> None:
    found = parse(
        b"\xef\xbb\xbf"  # a byte-order mark is ignored
        + rows(
            "ex.goblet_squat,1,10,20,7,,10,20,7", "Sentadilla goblet,2,,22.5,,3,,,", "", ",,,,,,,,"
        )
    )
    assert found == [
        SetRow(2, "ex.goblet_squat", 1, 10, 20.0, 7.0, None, 10, 20.0, 7.0),
        SetRow(3, "Sentadilla goblet", 2, None, 22.5, None, 3),
    ]


REFUSED = [
    (b"", "the file is empty"),
    (rows("x,1", header="exercise,set"), "line 1: the header"),
    (b"\x00" + rows("ex.plank,1,1,,,,,,"), "NUL"),
    (rows("ex.plank,1,1,,,,,,").replace(b"plank", b"pl\xffnk"), "not UTF-8"),
    (rows("ex.plank,1"), "line 2: a row has 9 cells"),
    (rows(",1,1,,,,,,"), "line 2: the exercise is required"),
    (rows("ex.plank,,1,,,,,,"), "line 2: the set number is required"),
    (rows("ex.plank,1,-3,,,,,,"), "line 2: reps is a whole number"),
    (rows('ex.plank,1,1,"20,5",,,,,'), "line 2: load_kg is a number with a dot"),
    (rows("ex.plank,1,1,inf,,,,,"), "line 2: load_kg is a number with a dot"),
    # Review of P12.8: these reached int() or float() and raised a bare ValueError.
    (rows("ex.plank,1,1,.,,,,,"), "line 2: load_kg is a number with a dot"),
    (rows("ex.plank,1,²,,,,,,"), "line 2: reps is a whole number"),
    (rows("ex.plank,²,1,,,,,,"), "line 2: set is a whole number"),
    (rows("ex.plank,1,1,²,,,,,"), "line 2: load_kg is a number with a dot"),
    (rows("ex.plank,1," + "9" * 5000 + ",,,,,,"), "line 2: reps is a whole number"),
    (rows("ex.plank,1,1,1e3,,,,,"), "line 2: load_kg is a number with a dot"),
    (rows(""), "the file has no sets"),
    (rows(*(["ex.plank,1,1,,,,,,"] * 201)), "more than 200 rows"),
    (b"a" * (64 * 1024 + 1), "larger than 64 KiB"),
]


@pytest.mark.req("INT-FIL-001", "INT-FIL-003")
@pytest.mark.parametrize(("data", "message"), REFUSED, ids=[item[1] for item in REFUSED])
def test_anything_else_is_refused_with_its_line_never_its_text(data: bytes, message: str) -> None:
    with pytest.raises(TemplateError) as refused:
        parse(data)
    assert message in str(refused.value)
    assert "pl" + chr(0xFF) not in str(refused.value)


@pytest.mark.req("INT-FIL-001", "SEC-INJ-004")
@pytest.mark.acceptance("AT-CSV-001")
def test_an_export_protects_formula_like_text_and_imports_again() -> None:
    written = [
        SetRow(0, "ex.goblet_squat", 1, 10, 20.0, 7.5, None, 10, 22.679999, 7.0),
        SetRow(0, "=HYPERLINK(1)", 2, 8),  # data, never a formula
        SetRow(0, "@SUM(A1)", 3, None, 0.000001),
    ]
    text = export(written)
    lines = text.splitlines()
    assert lines[0] == HEADER
    assert lines[1] == "ex.goblet_squat,1,10,20,7.5,,10,22.679999,7"  # numbers stay numbers
    assert lines[2].startswith("'=HYPERLINK(1),2,8")
    assert lines[3].startswith("'@SUM(A1),3,,0.000001")  # never an exponent
    again = parse(text.encode("utf-8"))
    assert [(row.exercise, row.set_number, row.load_kg, row.target_load_kg) for row in again] == [
        ("ex.goblet_squat", 1, 20.0, 22.679999),
        ("=HYPERLINK(1)", 2, None, None),
        ("@SUM(A1)", 3, 0.000001, None),
    ]
