"""Unit tests for the generic Excel parser."""
import pytest

from app.services.excel_ingest import IngestError, parse_workbook, sanitize_key
from tests.helpers_xlsx import REAL_FILE, SIMPLE, build_workbook


def test_sections_headers_and_forward_fill():
    parsed = parse_workbook(build_workbook(SIMPLE))
    assert parsed.sections == ["Camera testing", "Microscope testing"]
    assert [c["label"] for c in parsed.columns] == [
        "Test spec name", "Test spec tab name", "Test Count"]
    assert [c["type"] for c in parsed.columns] == ["string", "string", "number"]
    # second camera row inherited the spec name from the row above
    assert parsed.rows[1].values["test_spec_name"] == "DP23"
    assert parsed.rows[1].section == "Camera testing"


def test_duplicates_are_skipped():
    parsed = parse_workbook(build_workbook(SIMPLE))
    assert parsed.total_rows == 4
    assert len(parsed.rows) == 3
    assert parsed.duplicates_skipped == 1


def test_changed_measure_same_identity_is_one_row():
    # same (section, spec, tab) with a different count: identity hashes only
    # string columns, so this is a duplicate (first occurrence wins), not a new row
    rows = SIMPLE[:4] + [["DP23", "(TS) Color", 99]]
    parsed = parse_workbook(build_workbook(rows))
    camera = [r for r in parsed.rows if r.section == "Camera testing"]
    assert len(camera) == 2
    assert parsed.duplicates_skipped == 1


def test_nbsp_and_whitespace_normalized():
    rows = [
        ["Specs"],
        ["Name", "Count"],
        ["Intelligent\xa0 Shading  Correction ", 3],
    ]
    parsed = parse_workbook(build_workbook(rows))
    assert parsed.rows[0].values["name"] == "Intelligent Shading Correction"


def test_numeric_strings_coerced():
    parsed = parse_workbook(build_workbook([["S"], ["Name", "Count"], ["a", "42"], ["b", "1.5"]]))
    assert parsed.rows[0].values["count"] == 42
    assert parsed.rows[1].values["count"] == 1.5
    assert parsed.columns[1]["type"] == "number"


def test_sheet_without_sections_gets_default_section():
    parsed = parse_workbook(build_workbook([["Name", "Count"], ["a", 1]]))
    assert parsed.sections == ["General"]
    assert parsed.rows[0].section == "General"


def test_sloppy_repeated_headers_are_skipped():
    # per-section headers where the first cell carries a section/device name
    # instead of the column label — everything else identical
    rows = [
        ["Device testing"],
        ["North Gate Monitor", "Test spec tab name", "Test Count"],
        ["Lobby Monitor", "(TS) Color", 10],
        ["Other section"],
        ["Parking Monitor", "Test spec tab name", "Test Count"],
        ["Warehouse Monitor", "(TS) Devices", 5],
    ]
    parsed = parse_workbook(build_workbook(rows))
    # the first header seen defines the columns; later near-identical headers are skipped
    assert [c["label"] for c in parsed.columns] == [
        "North Gate Monitor", "Test spec tab name", "Test Count"]
    assert parsed.total_rows == 2
    assert [r.values[parsed.columns[0]["key"]] for r in parsed.rows] == [
        "Lobby Monitor", "Warehouse Monitor"]


def test_wide_sheet_with_messy_title_and_junk_rows():
    # arbitrary column count; a decorative multi-cell title above the real
    # header; separator rows with junk in the leading columns; sloppy
    # repeated headers — everything a hand-maintained sheet throws at us
    rows = [
        ["Device testing", "Sub Category", None, None],   # decorative title row
        ["Monitor", "Category", "Tab", "Count"],          # the real 4-column header
        ["M1", "C1", "(TS) A", 10],
        ["M2", "C2", None, None],                         # junk separator (no measure)
        ["M2", "C2", "Tab", "Count"],                     # sloppy repeated header
        ["M3", "C3", "(TS) B", 5],
    ]
    parsed = parse_workbook(build_workbook(rows))
    assert [c["label"] for c in parsed.columns] == ["Monitor", "Category", "Tab", "Count"]
    assert [c["type"] for c in parsed.columns] == ["string", "string", "string", "number"]
    assert parsed.sections == ["Device testing"]
    assert parsed.total_rows == 2
    assert parsed.rows[0].values == {"monitor": "M1", "category": "C1", "tab": "(TS) A", "count": 10}
    assert parsed.rows[1].values["count"] == 5


@pytest.mark.parametrize("rows,code", [
    ([[None]], "no_header_row"),
    ([["Only a title"]], "no_header_row"),
    ([["Name", "Count"]], "empty_sheet"),
])
def test_structural_errors(rows, code):
    with pytest.raises(IngestError) as exc:
        parse_workbook(build_workbook(rows))
    assert exc.value.code == code


def test_unreadable_file():
    with pytest.raises(IngestError) as exc:
        parse_workbook(b"this is not a workbook")
    assert exc.value.code == "unreadable_file"


def test_file_too_large():
    from app.services import excel_ingest
    with pytest.raises(IngestError) as exc:
        parse_workbook(b"x" * (excel_ingest.MAX_FILE_BYTES + 1))
    assert exc.value.code == "file_too_large"


def test_sanitize_key():
    assert sanitize_key("Test Count") == "test_count"
    assert sanitize_key("  Weird!! label?? ") == "weird_label"
    assert sanitize_key("!!!") == "column"


def test_real_cellsens_file():
    parsed = parse_workbook(REAL_FILE.read_bytes())
    assert parsed.total_rows == 218
    assert len(parsed.rows) == 206
    assert parsed.duplicates_skipped == 12
    assert parsed.sections == ["Camera testing", "Microscope testing", "Regression test"]
    assert [c["key"] for c in parsed.columns] == [
        "test_spec_name", "test_spec_tab_name", "test_count"]
    assert sum(r.values["test_count"] or 0 for r in parsed.rows) == 140736
