"""What one workbook's own read cost, judged without any history.

Every case here is built from a sheet and nothing else — no database, no
previous snapshot, no other file. A warning is a property of the workbook, so
it must read the same on the first load as on the fifth.
"""
import pytest

from app.services import quality
from app.services.excel_ingest import parse_workbook
from tests.helpers_xlsx import REAL_FILE, SIMPLE, build_workbook


def inspect(rows) -> list[dict]:
    return quality.inspect(parse_workbook(build_workbook(rows)))


def codes(rows) -> list[str]:
    return [w["code"] for w in inspect(rows)]


def one(rows, code) -> dict:
    return next(w for w in inspect(rows) if w["code"] == code)


# --- a clean sheet says nothing ------------------------------------------


def test_a_clean_sheet_warns_about_nothing():
    """Most sheets are fine, and a report that always finds something is one
    nobody reads."""
    assert inspect([["Module", "Suite", "Cases"],
                    ["imaging", "colour", 320],
                    ["imaging", "exposure", 145]]) == []


def test_the_real_workbook_reports_only_its_duplicates():
    """cellSens-Count.xlsx: 218 rows read, 206 kept, and the 12 dropped are
    identical in every column — a notice, not a problem."""
    found = quality.inspect(parse_workbook(REAL_FILE.read_bytes()))
    assert [w["code"] for w in found] == ["duplicate_rows"]
    assert found[0]["severity"] == "notice"
    assert "12 row(s)" in found[0]["message"]


# --- rows the read collapsed ---------------------------------------------

CONFLICTING = [
    ["OS ver.", "type", "Tester", "Pass"],
    ["Win10 Pro 64bit", "Version Update", "Rahul", 24],
    ["Win11 Pro 64bit", "Clean Install", "Meera", 18],
    ["Win10 Pro 64bit", "Version Update", "Rahul", 9],   # same identity, new count
]


def test_rows_that_disagreed_are_a_problem():
    warning = one(CONFLICTING, "conflicting_duplicate")
    assert warning["severity"] == "problem"
    assert "Win10 Pro 64bit · Version Update · Rahul" in warning["message"]
    assert "24" in warning["message"] and "9" in warning["message"]


def test_it_says_which_column_disagreed():
    assert "Pass" in one(CONFLICTING, "conflicting_duplicate")["message"]


def test_it_does_not_pretend_to_know_which_row_is_right():
    """The sheet does not say, so neither can we — the honest advice is to make
    the two rows distinguishable."""
    message = one(CONFLICTING, "conflicting_duplicate")["message"]
    assert "does not say which is right" in message
    assert "add a column that tells them apart" in message


def test_identical_rows_are_only_a_notice():
    rows = [["Module", "Suite", "Cases"],
            ["imaging", "colour", 320],
            ["imaging", "colour", 320]]
    warning = one(rows, "duplicate_rows")
    assert warning["severity"] == "notice"
    assert "No figure changed" in warning["message"]
    assert "conflicting_duplicate" not in codes(rows)


def test_both_kinds_are_reported_separately():
    rows = CONFLICTING + [["Win11 Pro 64bit", "Clean Install", "Meera", 18]]
    assert set(codes(rows)) >= {"conflicting_duplicate", "duplicate_rows"}


def test_several_disagreements_are_summarised_not_listed():
    rows = [["OS ver.", "type", "Tester", "Pass"],
            ["Win10", "Update", "Rahul", 24],
            ["Win11", "Clean", "Meera", 18],
            ["Win10", "Update", "Rahul", 9],
            ["Win11", "Clean", "Meera", 4]]
    assert "1 other row(s) disagree" in one(rows, "conflicting_duplicate")["message"]


# --- columns that should hold numbers ------------------------------------


def test_a_measure_column_with_a_few_stray_cells():
    """The column still works — it is the total that is short, because those
    cells are numbers to nobody and every sum steps over them."""
    rows = [["Spec", "Test Count"], ["a", 10], ["b", 20], ["c", 30],
            ["d", "n/a"], ["e", 40]]
    warning = one(rows, "text_in_measure")
    assert warning["severity"] == "problem"
    assert "short by 1 row(s)" in warning["message"]
    assert "“n/a”" in warning["message"]
    assert "4 of 5" in warning["message"]
    # and the column is still a measure, so the sheet still has one
    assert [c["type"] for c in parse_workbook(build_workbook(rows)).columns] == [
        "string", "number"]


def test_a_measure_named_column_holding_only_text():
    """The System workbook's case: a column called Pass whose one filled cell
    reads "make only excel". It is 0% numeric, so a mostly-numeric threshold
    would miss exactly the sheet that needs the warning."""
    rows = [["Topic", "Pass"],
            ["TIRF support", "make only excel"],
            ["Helix frame", None],
            ["Spectra X", None]]
    warning = one(rows, "measure_column_not_numeric")
    assert warning["severity"] == "problem"
    assert "Pass cannot be totalled" in warning["message"]
    assert "make only excel" in warning["message"]


@pytest.mark.parametrize("label", ["Pass", "Fail", "NA", "Test Count", "Total"])
def test_every_measure_word_is_recognised(label):
    rows = [["Topic", label], ["one", "tbd"], ["two", "tbd"]]
    assert "measure_column_not_numeric" in codes(rows)


def test_an_ordinary_text_column_is_not_a_lost_measure():
    """Testers and descriptions are text on purpose."""
    rows = [["Tester", "Description"], ["Rahul", "Camera capture"],
            ["Meera", "Stage calibration"]]
    assert codes(rows) == []


# --- columns nobody filled in --------------------------------------------


def test_empty_columns_are_grouped_into_one_warning():
    """The System workbook had four. One warning per column is four warnings
    for one fact, and the reader stops at the third."""
    rows = [["Topic", "End Date", "Build", "Status", "Fail", "Count"],
            ["one", None, None, None, None, 3],
            ["two", None, None, None, None, 4]]
    warning = one(rows, "empty_columns")
    assert warning["severity"] == "notice"
    assert "4 column(s)" in warning["message"]
    assert "End Date, Build, Status +1 more" in warning["message"]
    assert len([w for w in inspect(rows) if w["code"] == "empty_columns"]) == 1


def test_a_column_filled_on_almost_no_rows():
    rows = [["Topic", "Remarks", "Count"]] + \
           [[f"row {i}", None, i] for i in range(1, 10)] + \
           [["row 10", "with the help of Matthias", 10]]
    warning = one(rows, "sparse_columns")
    assert warning["severity"] == "notice"
    assert "Remarks (1 of 10)" in warning["message"]


def test_a_well_filled_column_is_not_sparse():
    rows = [["Topic", "Owner", "Count"],
            ["a", "Rahul", 1], ["b", "Meera", 2], ["c", None, 3]]
    assert "sparse_columns" not in codes(rows)


# --- what the structure cost ---------------------------------------------


def test_rows_dropped_as_separators_are_counted():
    """This filter runs before total_rows, so without the warning a discarded
    row leaves no trace anywhere at all."""
    # Three columns on purpose: a row with one filled cell would be read as a
    # section title and never reach this filter at all.
    rows = [["Spec", "Tab", "Count"],
            ["DP23", "(TS) Color", 10],
            ["DP23", "(TS) Gray", None],      # carries no measure — a separator
            ["IX73", "(TS) Devices", 20]]
    warning = one(rows, "separator_rows")
    assert warning["severity"] == "notice"
    assert "1 row(s)" in warning["message"]


def test_a_sheet_with_no_numeric_column_drops_nothing():
    rows = [["ID", "Summary"], ["FL-1", "one"], ["FL-2", "two"]]
    assert "separator_rows" not in codes(rows)


def test_a_headerless_sheet_is_flagged():
    rows = [["CS-4817", "TIRF firmware support", "7-Sep-26"],
            ["CS-4830", "Release candidate", "9-Sep-26"],
            ["CS-4799", "Spectra X control", "10-Sep-26"]]
    warning = one(rows, "no_header")
    assert warning["severity"] == "notice"
    assert "Column 1" in warning["message"]


def test_a_sheet_with_a_header_is_not_flagged():
    assert "no_header" not in codes(SIMPLE)


# --- a sheet that contradicts itself -------------------------------------
#
# The real Feature workbook keys each row on an FL id and describes the work in
# free text. Three of thirteen rows name a *different* FL id in that text, and
# the three split two ways: one names a feature sitting a few rows above it,
# two name features this release does not contain. Nothing is dropped either
# way — ids of the sheet’s own family are never read as links — so both of
# these would be invisible under the "what did the read cost" rule.

MISMATCH = [
    ["ID", "Summary (cellSens)", "PBI / Related Item"],
    ["FL-5778", "New version of scanR (3.7)", "CS-4645 - scanR PBI1: LabView"],
    ["FL-5777", "Basic Support of X-Cite TETREM light source",
     "CS-4800 - FL-5778 PBI: Basic Support of X-Cite TETREM light source"],
    ["FL-5773", "Helix: Support of new BX57/47 microscopes",
     "CS-4614 - BX53/43 successor (Helix) US1: Manual frame support"],
]


def test_a_row_naming_another_row_in_the_sheet_is_a_problem():
    """FL-5777’s text names FL-5778, which is a row of its own two lines up.
    That is a copy of a neighbouring row whose id was never changed."""
    warning = one(MISMATCH, "mismatched_id")
    assert warning["severity"] == "problem"
    assert "FL-5777" in warning["message"]
    assert "FL-5778" in warning["message"]
    assert "separate row in this sheet" in warning["message"]


def test_the_problem_does_not_claim_to_know_which_half_is_stale():
    """Either the id is wrong or the description is, and the sheet does not
    say. A warning that picked one would be guessing."""
    message = one(MISMATCH, "mismatched_id")["message"]
    assert "not something the sheet says" in message
    assert "counted against the wrong FL id" in message


OUTSIDE = [["ID", "Summary", "PBI / Related Item"],
           ["FL-5786", "Setup PFE flags - Follow-Up",
            "CS-4294 - FL-5651 Setup PFE: PBI 4 - Implementation"],
           ["FL-5761", "Blackwell for Pytorch",
            "CS-4312 - FL-5668 - GPU: Support Blackwell: PBI 1"],
           ["FL-5773", "Helix microscopes", "CS-4614 - Helix US1"],
           ["FL-5776", "FIJI bridge", "CS-4759 - FIJI PBI 1"],
           ["FL-5775", "Deconvolution", "CS-4760 - Deconvolution PBI 0"]]


def references(rows) -> dict | None:
    """The detection itself, as Traceability calls it."""
    parsed = parse_workbook(build_workbook(rows))
    return quality.id_references(parsed.columns[0]["key"], [r.values for r in parsed.rows])


def test_a_reference_outside_the_sheet_is_not_a_workbook_warning():
    """FL-5651 is not in this sheet. That is a question about the release’s
    scope, answered by Traceability, not a fault in the file — so the load’s
    warnings say nothing about it."""
    assert codes(OUTSIDE) == []


def test_the_detection_behind_it_is_unchanged():
    """Both references are still found by the same rule, and nothing here
    points inside the sheet."""
    found = references(OUTSIDE)
    assert found["family"] == "FL"
    assert found["inside"] == []
    assert sorted((own, named) for own, named, _text in found["outside"]) == [
        ("FL-5761", "FL-5668"), ("FL-5786", "FL-5651")]


def test_only_the_in_sheet_mismatch_is_a_workbook_warning():
    rows = MISMATCH + [
        ["FL-5786", "Setup PFE flags - Follow-Up",
         "CS-4294 - FL-5651 Setup PFE: PBI 4"],
        ["FL-5817", "Hamamatsu camera", "CS-5206 - PBI1: Hamamatsu C17940-20U"],
        ["FL-5866", "FFmpeg update", "CS-5200 - PBI: Update FFmpeg"],
    ]
    assert codes(rows) == ["mismatched_id"]
    found = references(rows)
    assert [(own, named) for own, named, _t in found["inside"]] == [("FL-5777", "FL-5778")]
    assert [(own, named) for own, named, _t in found["outside"]] == [("FL-5786", "FL-5651")]


def test_a_clean_identifier_sheet_says_nothing():
    assert codes([["ID", "Summary", "PBI / Related Item"],
                  ["FL-5778", "scanR 3.7", "CS-4645 - scanR PBI1"],
                  ["FL-5777", "X-Cite TETREM", "CS-4800 - PBI: X-Cite TETREM"],
                  ["FL-5773", "Helix", "CS-4614 - Helix US1"]]) == []


def test_a_rows_own_id_repeated_in_its_text_is_not_a_mismatch():
    """Most of the real sheet does this — `CS-4816 - FL-5794 - PBI1 - …` on
    FL-5794’s own row is the correct spelling, not a contradiction."""
    assert codes([["ID", "Summary", "PBI / Related Item"],
                  ["FL-5794", "TIRF Support (4L/1L) with XRTC",
                   "CS-4816 - FL-5794 - PBI1 - TIRF General Firmware Support"],
                  ["FL-5793", "DP2-AOU Follow-Up",
                   "CS-4276 - FL-5793 DP2-AOU: PBI 2 - Test DP2-AOU"],
                  ["FL-5685", "Spectra X Gen3", "CS-4635 - Spectra X PBI1"]]) == []


def test_ids_of_another_family_are_not_compared():
    """The CS ids are the *links*; the whole point is that they are a different
    family from the key, so they can never be a mismatch of it."""
    assert codes([["ID", "Summary", "PBI / Related Item"],
                  ["FL-5773", "Helix", "CS-4614, CS-4615, CS-4616, CS-4617"],
                  ["FL-5776", "FIJI bridge", "CS-4759, CS-4798, CS-4803"],
                  ["FL-5775", "Deconvolution", "CS-4760, CS-4751"]]) == []


def test_a_trailing_id_in_the_identifier_column_still_counts():
    """`FL-5773 / FL-5651 - description` in column A carries both: the first
    token is the key and the rest of the cell is description like any other."""
    warning = one([["ID", "Summary"],
                   ["FL-5773 / FL-5776 - Helix", "Helix microscopes"],
                   ["FL-5776", "FIJI bridge"],
                   ["FL-5775", "Deconvolution"]], "mismatched_id")
    assert "FL-5776" in warning["message"]


def test_a_family_too_few_rows_carry_is_not_compared():
    """The regression workbook is keyed on microscope models — IX73, IX83,
    IX85 on 65 of 206 rows — and `IX3` falls out of part numbers like
    IX3-D6REA. Comparing those to each other produced five warnings about
    nothing, so a prefix under half the rows is not treated as a convention."""
    rows = [["Test spec name", "Test spec tab name", "Test Count"],
            ["IX73 spec", "(TS) Calibration (IX3-D6REA)", 12],
            ["IX83 spec", "(TS) IX3-ZDC", 8],
            ["Camera spec", "(TS) Exposure", 14],
            ["Colour spec", "(TS) Balance", 9],
            ["Stage spec", "(TS) Travel", 11],
            ["Filter spec", "(TS) Wheel", 6]]
    assert "mismatched_id" not in codes(rows)
    assert "unknown_id_reference" not in codes(rows)


def test_a_cross_reference_column_is_not_a_mistake_in_one():
    """A sheet where most rows name another id has a column for doing that.
    Warning on nearly every row is what this module exists not to do."""
    rows = [["ID", "Summary", "Depends on"],
            ["FL-1", "one", "FL-2"],
            ["FL-2", "two", "FL-3"],
            ["FL-3", "three", "FL-1"],
            ["FL-4", "four", "FL-2"]]
    assert codes(rows) == []


def test_a_sheet_with_no_identifier_column_is_left_alone():
    """Acceptance opens with `Pattern No.`, a column of 1..39. Those read as
    identifiers but have no family, so there is nothing to contradict."""
    assert "mismatched_id" not in codes(
        [["Pattern No.", "OS ver.", "TC count"],
         [1, "Win11 Pro 64bit", 57],
         [2, "Win10 Pro 64bit", 43],
         [3, "Win11 Pro 64bit", 51]])


def test_case_and_separators_do_not_make_a_mismatch():
    """`FL-5777`, `fl 5777` and `FL_5777` are one id written by three people.
    Reporting that as a contradiction would be the most annoying kind of wrong
    answer, because the id is right there in both cells."""
    assert codes([["ID", "Summary"],
                  ["FL-5777", "X-Cite TETREM, see fl 5777 and FL_5777"],
                  ["FL-5778", "scanR 3.7"],
                  ["FL-5773", "Helix"]]) == []


# --- how the report reads ------------------------------------------------


def test_problems_sort_above_notices():
    rows = [["OS ver.", "type", "Tester", "Pass", "Spare"],
            ["Win10", "Update", "Rahul", 24, None],
            ["Win11", "Clean", "Meera", 18, None],
            ["Win10", "Update", "Rahul", 9, None]]
    found = inspect(rows)
    severities = [w["severity"] for w in found]
    assert severities == sorted(severities, key=lambda s: s != "problem")
    assert found[0]["code"] == "conflicting_duplicate"


def test_the_report_is_capped():
    assert len(inspect(SIMPLE)) <= quality.MAX_WARNINGS


def test_every_warning_carries_a_code_and_a_severity():
    for warning in inspect(CONFLICTING):
        assert warning["code"] and warning["message"]
        assert warning["severity"] in ("problem", "notice")
