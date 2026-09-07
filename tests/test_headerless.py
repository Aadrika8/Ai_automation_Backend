"""Sheets that carry no header row at all.

The System testing type's workbook is a bare list of (ID, summary, target
date) with no column names. The parser used to promote its first data row to
a header, which silently lost that row and labelled every column after a
value that happened to sit in it — columns came out as "FL-5773",
"Helix: Support of new BX57/47 microscopes" and "7-Sep-26".

`tests/fixtures/system-no-header.xlsx` reproduces that sheet: the original is
not in the repository, so it was rebuilt from the parse the real file
produced, and it reproduced that parse field for field before the fix.
"""
from tests.helpers_xlsx import FIXTURES, SIMPLE, build_workbook

from app.services.excel_ingest import parse_workbook

SYSTEM_FILE = FIXTURES / "system-no-header.xlsx"

# The rows the real sheet holds, in order. Every one is data.
SYSTEM_IDS = ["FL-5773", "FL-5685", "FL-5777", "FL-5817",
              "FL-5776", "FL-5775", "FL-5842", "FL-5861"]


def test_headerless_sheet_keeps_every_row():
    parsed = parse_workbook(SYSTEM_FILE.read_bytes())
    # the row that used to become the header is data like any other
    assert len(parsed.rows) == len(SYSTEM_IDS)
    first = parsed.columns[0]["key"]
    assert [r.values[first] for r in parsed.rows] == SYSTEM_IDS


def test_headerless_sheet_gets_neutral_column_names():
    parsed = parse_workbook(SYSTEM_FILE.read_bytes())
    assert [c["label"] for c in parsed.columns] == ["Column 1", "Column 2", "Column 3"]
    assert [c["key"] for c in parsed.columns] == ["column_1", "column_2", "column_3"]
    # no value from the sheet was mistaken for a column name
    labels = {c["label"] for c in parsed.columns}
    assert not labels & {"FL-5773", "7-Sep-26",
                         "Helix: Support of new BX57/47 microscopes"}


def test_headerless_sheet_keeps_its_section_label():
    """The lone "FL-5794" cell above the list is still a section title —
    a single filled cell means a section, headers or not."""
    parsed = parse_workbook(SYSTEM_FILE.read_bytes())
    assert parsed.sections == ["FL-5794"]
    assert all(r.section == "FL-5794" for r in parsed.rows)


def test_headerless_rows_carry_their_real_values():
    parsed = parse_workbook(SYSTEM_FILE.read_bytes())
    assert parsed.rows[0].values == {
        "column_1": "FL-5773",
        "column_2": "Helix: Support of new BX57/47 microscopes",
        "column_3": "7-Sep-26",
    }
    assert parsed.rows[-1].values == {
        "column_1": "FL-5861",
        "column_2": "Sample detection in cS",
        "column_3": "17-Sep-26",
    }


# --- the fix must not make real headers look like data --------------------


def test_a_real_header_is_still_recognised():
    parsed = parse_workbook(build_workbook(SIMPLE))
    assert [c["label"] for c in parsed.columns] == [
        "Test spec name", "Test spec tab name", "Test Count"]


def test_word_headers_over_word_data_are_still_headers():
    """"Name" over "Alice" shares a shape with its column. Shape alone must
    not be enough to call a header a data row, or every text table breaks."""
    parsed = parse_workbook(build_workbook([
        ["Name", "Team", "Cases"],
        ["Alice", "Imaging", 12],
        ["Bob", "Imaging", 9],
        ["Carol", "Optics", 14],
    ]))
    assert [c["label"] for c in parsed.columns] == ["Name", "Team", "Cases"]
    assert len(parsed.rows) == 3


def test_id_shaped_first_row_is_data_not_a_header():
    """The System case in miniature: identifiers and dates that match the
    column beneath them are values, not column names."""
    parsed = parse_workbook(build_workbook([
        ["CS-4817", "TIRF firmware support", "7-Sep-26"],
        ["CS-4830", "Release candidate for DP2-AOU", "9-Sep-26"],
        ["CS-4799", "Spectra X Gen3 RTC control", "10-Sep-26"],
    ]))
    assert [c["label"] for c in parsed.columns] == ["Column 1", "Column 2", "Column 3"]
    assert len(parsed.rows) == 3


def test_feature_sheet_header_survives():
    """The Feature workbook's shape: an ID column of FL-nnnn under a two-letter
    header. The column has a settled digit pattern, but "ID" does not match
    it, so the header stands."""
    parsed = parse_workbook(build_workbook([
        ["ID", "Summary (cellSens)", "PBI / Related Item"],
        ["FL-5794", "TIRF Support (4L/1L) with XRTC", "CS-4816 - PBI1 - TIRF firmware"],
        ["FL-5793", "New DP2-AOU Software Release", "CS-4276 - PBI 2 - Test on hardware"],
        ["FL-5685", "Lumencor Spectra X Gen3 Light Source", "CS-4635 - PBI1: Refinement"],
    ]))
    assert [c["label"] for c in parsed.columns] == [
        "ID", "Summary (cellSens)", "PBI / Related Item"]
    assert len(parsed.rows) == 3


def test_acceptance_sheet_header_survives():
    """The Acceptance workbook's shape: a numbered first column and several
    counts under text headers. `Tester` stands in for the columns that make
    two otherwise identical configurations distinct rows — identity is the
    string columns, so without one the third row would dedup into the first."""
    parsed = parse_workbook(build_workbook([
        ["Pattern No.", "OS ver.", "type", "Tester", "TC count", "Pass", "Fail"],
        [1, "Win11 Pro 64bit", "Clean Install", "Sreelakshmi", 57, 54, 1],
        [2, "Win10 Pro 64bit", "Version Update", "Rahul", 43, 41, 0],
        [3, "Win11 Pro 64bit", "Clean Install", "Meera", 39, 38, 1],
    ]))
    assert [c["label"] for c in parsed.columns] == [
        "Pattern No.", "OS ver.", "type", "Tester", "TC count", "Pass", "Fail"]
    assert [c["type"] for c in parsed.columns] == [
        "number", "string", "string", "string", "number", "number", "number"]
    assert len(parsed.rows) == 3
