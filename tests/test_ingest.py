"""Unit tests for the generic Excel parser."""
import pytest

from app.services.excel_ingest import (IngestError, diff_rows, parse_workbook,
                                       sanitize_key)
from tests.helpers_xlsx import (FEATURE_CONTINUATION, REAL_FILE, SIMPLE,
                                build_workbook)


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


# --- a lone cell: section title, or a continuation of the row above? ------
# The Feature workbook writes a feature's extra related items on rows of their
# own, filling only the last column. Reading those as section titles discarded
# 28 of that sheet's 41 data rows and titled each feature with the previous
# feature's last PBI.


def test_a_lone_cell_past_the_first_column_continues_the_row_above():
    parsed = parse_workbook(build_workbook(FEATURE_CONTINUATION))
    first, _summary, related = (c["key"] for c in parsed.columns)
    helix = next(r for r in parsed.rows if r.values[first] == "FL-5773")
    assert helix.values[related].split("\n") == [
        "CS-4614 - Helix US1: Manual control",
        "CS-4615 - Helix US2: Basic support",
        "CS-4616 - Helix US3: Motorized frame",
    ]


def test_continuations_are_joined_not_counted_as_rows():
    """The sheet lists three features; the continuations belong to them."""
    parsed = parse_workbook(build_workbook(FEATURE_CONTINUATION))
    first = parsed.columns[0]["key"]
    assert [r.values[first] for r in parsed.rows] == ["FL-5773", "FL-5807", "FL-5775"]
    assert parsed.total_rows == 3


def test_a_lone_cell_in_the_first_column_is_still_a_section():
    """Both conventions in one sheet: only "Deconvolution" opens a section."""
    parsed = parse_workbook(build_workbook(FEATURE_CONTINUATION))
    assert sorted(parsed.sections) == ["Deconvolution", "General"]
    first = parsed.columns[0]["key"]
    by_id = {r.values[first]: r.section for r in parsed.rows}
    assert by_id["FL-5775"] == "Deconvolution"    # after the title
    assert by_id["FL-5773"] == "General"          # before it
    # and no related item was mistaken for one
    assert not any(s.startswith("CS-") for s in parsed.sections)


def test_a_continuation_fills_a_column_the_row_left_empty():
    rows = [["ID", "Summary", "Related"],
            ["FL-1", "Something", None],
            [None, None, "CS-9 - arrived late"]]
    parsed = parse_workbook(build_workbook(rows))
    assert parsed.rows[0].values["related"] == "CS-9 - arrived late"
    assert len(parsed.rows) == 1


def test_a_continuation_never_joins_a_row_from_another_section():
    """A section boundary ends the row a continuation could belong to; with
    nothing above it in this section, it reads as a title again."""
    rows = [["ID", "Summary", "Related"],
            ["FL-1", "Something", "CS-1 - first"],
            ["Camera testing"],
            [None, None, "CS-2 - orphan"],
            ["FL-2", "Another", "CS-3 - second"]]
    parsed = parse_workbook(build_workbook(rows))
    assert parsed.rows[0].values["related"] == "CS-1 - first"   # not joined
    assert "CS-2 - orphan" in parsed.sections


def test_a_lone_cell_with_no_row_above_it_is_a_section():
    rows = [["ID", "Summary", "Related"],
            [None, None, "CS-1 - nothing precedes this"],
            ["FL-1", "Something", "CS-2 - first real row"]]
    parsed = parse_workbook(build_workbook(rows))
    assert "CS-1 - nothing precedes this" in parsed.sections
    assert len(parsed.rows) == 1


def test_a_lone_cell_before_the_header_start_is_a_section():
    """cellSens-Count.xlsx in miniature: its header starts at column 1 while
    its section banners sit at column 0, so "not the first column" has to mean
    the header's span, not the sheet's."""
    rows = [[None, "Test spec name", "Test Count"],
            ["Camera testing"],
            [None, "DP23", 10],
            [None, "IX73", 20]]
    parsed = parse_workbook(build_workbook(rows))
    assert parsed.sections == ["Camera testing"]
    assert len(parsed.rows) == 2


# --- what the read threw away --------------------------------------------
# Parsing is lossy on purpose, and every loss used to be invisible afterwards.
# These record the facts a quality report speaks from; nothing else reads them.


def test_a_dropped_duplicate_keeps_both_rows():
    parsed = parse_workbook(build_workbook([
        ["OS", "Tester", "Pass"],
        ["Win10", "Rahul", 24],
        ["Win10", "Rahul", 9],
    ]))
    assert parsed.duplicates_skipped == 1
    dropped = parsed.dropped_duplicates[0]
    assert dropped["kept"]["pass"] == 24
    assert dropped["dropped"]["pass"] == 9
    assert dropped["conflicts"] == {"pass": [24, 9]}


def test_a_duplicate_that_agreed_records_no_conflict():
    parsed = parse_workbook(build_workbook([
        ["OS", "Tester", "Pass"],
        ["Win10", "Rahul", 24],
        ["Win10", "Rahul", 24],
    ]))
    assert parsed.duplicates_skipped == 1
    assert parsed.dropped_duplicates[0]["conflicts"] == {}


def test_the_count_is_never_capped_even_when_the_detail_is():
    """A sheet that repeats itself a thousand times still reports a thousand."""
    rows = [["OS", "Tester", "Pass"]] + [["Win10", "Rahul", 1]] * 40
    parsed = parse_workbook(build_workbook(rows))
    assert parsed.duplicates_skipped == 39
    assert len(parsed.dropped_duplicates) == 25      # MAX_DROPPED_DETAIL


def test_rows_dropped_as_separators_are_counted():
    """This filter runs before total_rows, so the count is the only trace."""
    parsed = parse_workbook(build_workbook([
        ["Spec", "Tab", "Count"],
        ["DP23", "(TS) Color", 10],
        ["DP23", "(TS) Gray", None],
        ["IX73", "(TS) Devices", 20],
    ]))
    assert parsed.dropped_separators == 1
    assert parsed.total_rows == 2          # counted after the filter, as before


def test_a_headerless_sheet_says_so():
    headerless = parse_workbook(build_workbook([
        ["CS-4817", "TIRF firmware support", "7-Sep-26"],
        ["CS-4830", "Release candidate", "9-Sep-26"],
        ["CS-4799", "Spectra X control", "10-Sep-26"],
    ]))
    assert headerless.headerless is True
    assert parse_workbook(build_workbook(SIMPLE)).headerless is False


def test_a_clean_sheet_records_no_losses():
    parsed = parse_workbook(build_workbook([
        ["Module", "Suite", "Cases"], ["imaging", "colour", 320],
    ]))
    assert parsed.dropped_duplicates == []
    assert parsed.dropped_separators == 0
    assert parsed.headerless is False


# --- what a column holds, and what a row is: two separate decisions -------
# A count column with one cell reading "Not Decided" used to be denied its
# type, which then moved it into the row key — so a later edit to a count read
# as a delete plus an insert instead of a change.


NOT_DECIDED = [
    ["Test spec name", "Test spec tab name", "Test Count"],
    ["DP23", "(TS) Color", 10],
    ["DP23", "(TS) Gray", 20],
    ["IX73", "(TS) Devices", 30],
    ["IX73", "(TS) Focus", "Not Decided"],
]


def test_one_stray_cell_does_not_cost_the_column_its_type():
    parsed = parse_workbook(build_workbook(NOT_DECIDED))
    assert [c["type"] for c in parsed.columns] == ["string", "string", "number"]


def test_a_measure_is_never_part_of_row_identity():
    parsed = parse_workbook(build_workbook(NOT_DECIDED))
    assert parsed.identity_keys == ["test_spec_name", "test_spec_tab_name"]
    assert "test_count" not in parsed.identity_keys


def test_the_stray_value_is_kept_exactly_as_it_was_typed():
    """Typing is a reading of the column, not a rewrite of its cells."""
    parsed = parse_workbook(build_workbook(NOT_DECIDED))
    assert parsed.rows[3].values["test_count"] == "Not Decided"


def test_a_stray_cell_does_not_move_any_row_s_identity():
    """Identity is the same whether or not the odd cell is there, so a sheet
    does not read as an entirely new set of rows because of one word."""
    clean = [row[:] for row in NOT_DECIDED]
    clean[4] = ["IX73", "(TS) Focus", 40]
    a = parse_workbook(build_workbook(clean))
    b = parse_workbook(build_workbook(NOT_DECIDED))
    assert [r.row_key for r in a.rows] == [r.row_key for r in b.rows]


def test_changing_a_count_is_a_change_not_a_replacement():
    """The rule the parser is built on: a re-read with a changed count updates
    the row it belongs to. A stray cell elsewhere must not break it."""
    before = parse_workbook(build_workbook(NOT_DECIDED))
    edited = [row[:] for row in NOT_DECIDED]
    edited[1] = ["DP23", "(TS) Color", 150]          # 10 -> 150
    after = parse_workbook(build_workbook(edited))

    assert before.identity_keys == after.identity_keys
    stored = [{"rowKey": r.row_key, "data": r.values} for r in before.rows]
    assert diff_rows(stored, after.rows) == {
        "added": 0, "changed": 1, "removed": 0, "comparable": True}


def test_a_mostly_text_column_is_still_text():
    """One number among words does not make a measure."""
    rows = [["Topic", "Notes"],
            ["a", "waiting on hardware"], ["b", "blocked"], ["c", 7], ["d", "tbd"]]
    parsed = parse_workbook(build_workbook(rows))
    assert [c["type"] for c in parsed.columns] == ["string", "string"]
    assert parsed.identity_keys == ["topic", "notes"]


def test_a_sheet_of_nothing_but_measures_still_has_an_identity():
    """With every column a measure there is nothing left to identify a row by,
    so identity falls back to the whole row rather than to nothing."""
    rows = [["Pass", "Fail"], [10, 1], [20, 2]]
    parsed = parse_workbook(build_workbook(rows))
    assert [c["type"] for c in parsed.columns] == ["number", "number"]
    assert parsed.identity_keys == ["pass", "fail"]


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
