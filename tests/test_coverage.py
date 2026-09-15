"""The coverage service on its own — no database, no HTTP.

Four rules are under test, and they are the whole design:

  R1  the identifier comes from the first column of each *file*
  R2  only that identifier is a traceability key
  R3  Feature -> System gaps carry the supporting ids from the feature row
  R4  System -> Feature gaps carry nothing from the feature side

No identifier format is assumed anywhere. Sheets in the wild use FL-5773,
PS-101, REQ-88, ABC_123 or bare numbers, and the code must not care which.
"""
import pytest

from app.services import coverage as cov
from app.services.excel_ingest import parse_workbook
from tests.helpers_xlsx import FEATURE_SHEET, SYSTEM_SHEET, build_workbook, rows_of

FIXTURE_SYSTEM = "tests/fixtures/system-no-header.xlsx"

# A feature sheet with no separate id column: the id leads the first cell and
# the description follows it. CS ids sit in a column of their own.
FEATURE_PACKED = [
    ["Feature", "Test spec", "Customer stories"],
    ["FL-1001 - Camera control rework", "(TS) CameraControl", "CS-1142, CS-1187"],
    ["FL-1002 - Deconvolution modalities", "(TS) Deconv", "CS-1203"],
    ["FL-1003 - Sub array feature", "(TS) SubArray", "CS-1210"],
]


def parse(rows):
    return parse_workbook(build_workbook(rows))


def sides(feature_rows=FEATURE_SHEET, system_rows=SYSTEM_SHEET, config=None):
    """Both sides as the service consumes them, under the rule by default."""
    f_rows = rows_of(parse(feature_rows), "feature.xlsx")
    s_rows = rows_of(parse(system_rows), "system.xlsx")
    return f_rows, s_rows, config or {}


def coverage(feature_rows=FEATURE_SHEET, system_rows=SYSTEM_SHEET, config=None):
    return cov.build_coverage(*sides(feature_rows, system_rows, config))


def by_id(result):
    return {e["id"]: e for e in result["entries"] if e["id"]}


# --- reading a value ----------------------------------------------------


@pytest.mark.parametrize("cell,expected", [
    ("FL-5773", "FL-5773"),
    ("PS-101", "PS-101"),
    ("REQ-88", "REQ-88"),
    ("ABC_123", "ABC_123"),
    ("PS101", "PS101"),
    ("FL 5773", "FL 5773"),
    # the id leads, the description follows — the sheet with no id column
    ("FL-5773 - Helix: support of new BX57/47 microscopes", "FL-5773"),
    ("FL-5773 Helix: support of multi-camera exposure sync", "FL-5773"),
    # a clean cell with no prefix at all
    ("5773", "5773"),
    ("1.2.3", "1.2.3"),
])
def test_no_identifier_format_is_assumed(cell, expected):
    assert cov.read_identifier(cell)[1] == expected


def test_the_first_token_wins_and_the_rest_is_description():
    """A cell naming several ids has one identity — the one it leads with."""
    key, shown, family = cov.read_identifier("FL-5773 / CS-114 - exposure sync")
    assert shown == "FL-5773"
    assert family == "FL"
    assert key == "fl5773"


def test_matching_ignores_case_and_separator():
    """Three people spelling one id three ways is not a coverage gap."""
    assert len({cov.canonical(v) for v in
                ("FL-5773", "fl 5773", "FL_5773", "fl-5773")}) == 1


def test_display_keeps_the_sheet_s_own_spelling():
    assert cov.read_identifier("fl 5685")[1] == "fl 5685"


def test_prose_is_not_an_identifier():
    """A first column of descriptions must not manufacture keys — that is how
    a report claims perfect coverage of nothing."""
    assert cov.read_identifier("Miscellaneous checks for the export path") is None
    assert cov.read_identifier("Camera testing") is None
    assert cov.read_identifier("x" * 200) is None


def test_blank_cells_yield_nothing():
    assert cov.read_identifier(None) is None
    assert cov.read_identifier("   ") is None


def test_family_is_the_prefix_not_a_hardcoded_value():
    assert cov.family_of("FL-5773") == "FL"
    assert cov.family_of("req_88") == "REQ"
    assert cov.family_of("1001") == ""


def test_an_override_pattern_is_used_when_given():
    assert cov.read_identifier("row 12 / SPEC[447]", r"SPEC\[\d+\]")[1] == "SPEC[447]"


def test_a_broken_override_falls_back_rather_than_failing_the_report():
    assert cov.read_identifier("FL-1", "[unclosed")[1] == "FL-1"


# --- R1: the first column of each file ----------------------------------


def test_the_identifier_comes_from_the_first_column():
    result = coverage()
    assert set(by_id(result)) == {"FL-1001", "FL-1002", "FL-1003", "FL-2001"}


def test_a_sheet_with_no_id_column_still_works():
    """FL-1003 is only ever written inside a sentence."""
    result = coverage(FEATURE_PACKED)
    assert by_id(result)["FL-1003"]["status"] == "missing_in_system"


def test_two_files_in_one_layer_are_read_by_their_own_shapes():
    """A layer split across differently shaped workbooks covers what both do."""
    a = rows_of(parse([["Feature ID", "Name"], ["FL-1", "a"]]), "part-a.xlsx")
    b = rows_of(parse([["Feature", "Owner", "Notes"],
                       ["FL-2 - second workbook", "team-b", ""]]), "part-b.xlsx")
    result = cov.build_coverage(a + b, [], {})
    assert set(by_id(result)) == {"FL-1", "FL-2"}


def test_a_column_override_is_honoured_where_the_file_has_it():
    config = {"layers": {"feature": {"column": "owner"}}}
    f_rows, _s, _c = sides()
    result = cov.build_coverage(f_rows, [], config)
    assert set(by_id(result)) == set()  # "team-a" holds no identifier
    assert result["summary"]["unresolved"] == 3


def test_a_column_override_the_file_lacks_falls_back_to_the_first_column():
    """A stale override would otherwise match nothing and read as 0%."""
    config = {"layers": {"feature": {"column": "gone_away"}}}
    f_rows, _s, _c = sides()
    result = cov.build_coverage(f_rows, [], config)
    assert set(by_id(result)) == {"FL-1001", "FL-1002", "FL-1003"}


# --- R2: one key, and only one ------------------------------------------


def test_supporting_ids_never_become_entries_of_their_own():
    """CS ids are evidence. If they were keys, every one of them would show up
    here as an uncovered gap."""
    result = coverage(FEATURE_PACKED)
    assert not any(e["id"].upper().startswith("CS") for e in result["entries"])


def test_supporting_ids_do_not_move_the_coverage_figures():
    with_cs = coverage(FEATURE_PACKED, SYSTEM_SHEET)["summary"]
    without = coverage([r[:2] for r in FEATURE_PACKED], SYSTEM_SHEET)["summary"]
    assert with_cs["forwardCoveragePct"] == without["forwardCoveragePct"]
    assert with_cs["featureTotal"] == without["featureTotal"]


def test_the_key_family_is_discovered_not_configured():
    assert coverage(FEATURE_PACKED)["summary"]["family"] == "FL"
    assert coverage([["ID", "Name"], ["PS-101", "a"]],
                    [["ID", "Scenario"], ["PS-101", "x"]])["summary"]["family"] == "PS"


def test_the_supporting_family_is_discovered_too():
    """So the screen can say "Related CS ids" without the backend knowing CS."""
    assert coverage(FEATURE_PACKED)["summary"]["relatedFamily"] == "CS"


# --- R3: Feature -> System carries its supporting detail ----------------


def test_a_feature_gap_carries_its_supporting_ids():
    gap = by_id(coverage(FEATURE_PACKED))["FL-1003"]
    assert gap["status"] == "missing_in_system"
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-1210"]
    assert gap["feature"]["data"]["test_spec"] == "(TS) SubArray"
    assert gap["system"] is None


def test_supporting_ids_are_gathered_across_a_forward_filled_group():
    """One feature can span a group of specs, each naming its own stories."""
    rows = [
        ["Feature", "Test spec", "Customer stories"],
        ["FL-9 - multi-camera sync", "(TS) Control", "CS-1"],
        [None, "(TS) Exposure", "CS-2"],
        [None, "(TS) Sync", "CS-3"],
    ]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-1", "CS-2", "CS-3"]


def test_supporting_ids_survive_the_continuation_rows_they_arrived_on():
    """The real Feature sheet writes a feature's extra related items on rows of
    their own, filling only the last column. The parser joins them onto the
    feature; every one of them has to reach the gap as evidence.

    Before the parser distinguished a continuation from a section title, these
    rows were discarded and this feature reported one supporting id out of four.
    """
    rows = [
        ["ID", "Summary (cellSens)", "PBI / Related Item"],
        ["FL-9", "Helix: support of new BX57/47", "CS-1 - Helix US1: Manual control"],
        [None, None, "CS-2 - Helix US2: Basic support"],
        [None, None, "CS-3 - Helix US3: Motorized frame"],
        [None, None, "CS-4 - Helix US4: Setup"],
    ]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-1", "CS-2", "CS-3", "CS-4"]
    # each carries the text it sat in, so the reader can see what it refers to
    assert gap["relatedIds"][3]["text"].endswith("Helix US4: Setup")
    # and the feature is still one row, counted once
    assert gap["featureCount"] == 1 and gap["duplicate"] is False


def test_supporting_ids_come_from_the_whole_row_not_one_column():
    """A supporting id moves between columns from one release to the next."""
    rows = [["Feature", "Story", "Notes"],
            ["FL-9 - a feature", "CS-1", "supersedes CS-2"]]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-1", "CS-2"]
    assert [r["text"] for r in gap["relatedIds"]] == ["CS-1", "supersedes CS-2"]


def test_supporting_ids_can_trail_the_id_in_the_first_column():
    rows = [["Feature", "Notes"], ["FL-9 / CS-114 - a feature", "team-a"]]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-114"]


def test_a_date_column_is_not_mistaken_for_a_supporting_id():
    """"7-Sep-26" is shaped exactly like an identifier."""
    rows = [["Feature", "Target date"], ["FL-9 - a feature", "7-Sep-26"]]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert gap["relatedIds"] == []


def test_only_one_supporting_family_survives():
    """A row naming a customer story, a work item and a product code has said
    one useful thing and two incidental ones. Deciding which across the whole
    sheet keeps every gap listing the same kind of thing."""
    rows = [
        ["Feature", "Related item"],
        ["FL-1 - a feature", "CS-4312 - GPU work: PBI 1 - Compile"],
        ["FL-2 - another", "CS-4276 - DP2-AOU: PBI 2 - Test on new hardware"],
        ["FL-3 - a third", "CS-4645 - scanR update PBI1: LabView"],
    ]
    result = coverage(rows, [["ID", "S"], ["FL-99", "x"]])
    assert result["summary"]["relatedFamily"] == "CS"
    for entry in result["entries"]:
        assert all(r["id"].startswith("CS-") for r in entry["relatedIds"])


def test_a_supporting_id_carries_the_cell_it_came_from():
    """`CS-4312` means nothing until you can see the text around it, but the
    table stays scannable by keeping that text out of the way until asked."""
    rows = [["Feature", "Related item"],
            ["FL-1 - a feature", "CS-4312 - GPU: Support Blackwell Technology"]]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-1"]
    assert gap["relatedIds"] == [
        {"id": "CS-4312", "text": "CS-4312 - GPU: Support Blackwell Technology"}]


def test_key_family_ids_elsewhere_in_the_row_are_not_listed_as_supporting():
    """They are keys. Listing one here invites it to be read as a second link."""
    rows = [["Feature", "See also"], ["FL-9 - a feature", "FL-10, CS-3"]]
    gap = by_id(coverage(rows, [["ID", "S"], ["FL-99", "x"]]))["FL-9"]
    assert [r["id"] for r in gap["relatedIds"]] == ["CS-3"]


# --- R4: System -> Feature carries nothing from the feature side --------


def test_a_system_gap_shows_only_its_system_side():
    gap = by_id(coverage(FEATURE_PACKED))["FL-2001"]
    assert gap["status"] == "missing_in_feature"
    assert gap["feature"] is None
    assert gap["relatedIds"] == []
    assert gap["system"]["data"]["scenario"] == "Licensing smoke test"


def test_no_supporting_ids_are_borrowed_from_an_unrelated_feature_row():
    """The FL is not in feature at all, so there is nothing to read them from
    and nothing is guessed at."""
    result = coverage(FEATURE_PACKED)
    assert all(not e["relatedIds"] for e in result["entries"]
               if e["status"] == "missing_in_feature")


# --- forward fill -------------------------------------------------------


def test_a_forward_filled_id_is_one_occurrence_not_a_duplicate():
    """The parser fills a sparse leading column down a group. Flagging that as
    a duplicate would light up most rows of a real workbook."""
    rows = [["Feature ID", "Test spec"],
            ["FL-1", "(TS) Control"], [None, "(TS) Exposure"], [None, "(TS) Sync"]]
    entry = by_id(coverage(rows, [["ID", "S"], ["FL-1", "x"]]))["FL-1"]
    assert entry["status"] == "covered"
    assert entry["featureCount"] == 1
    assert entry["duplicate"] is False


def test_the_same_id_further_down_the_sheet_is_still_a_duplicate():
    """A repeat that is not contiguous was not produced by the fill."""
    rows = [["Feature ID", "Test spec"],
            ["FL-1", "(TS) Control"], ["FL-2", "(TS) Other"], ["FL-1", "(TS) Again"]]
    entry = by_id(coverage(rows, [["ID", "S"], ["FL-1", "x"]]))["FL-1"]
    assert entry["duplicate"] is True
    assert entry["featureCount"] == 2


def test_a_duplicate_is_a_data_quality_flag_not_a_coverage_verdict():
    rows = [["Feature ID", "Name"], ["FL-1", "a"], ["FL-2", "b"], ["FL-1", "c"]]
    entry = by_id(coverage(rows, [["ID", "S"], ["FL-1", "x"]]))["FL-1"]
    assert entry["status"] == "covered"
    assert entry["duplicate"] is True


# --- the bidirectional comparison ---------------------------------------


def test_both_directions_are_reported_from_one_matrix():
    status = {i: e["status"] for i, e in by_id(coverage()).items()}
    assert status["FL-1001"] == "covered"
    assert status["FL-1002"] == "covered"
    # Feature -> System: planned, never verified
    assert status["FL-1003"] == "missing_in_system"
    # System -> Feature: verified, never planned
    assert status["FL-2001"] == "missing_in_feature"


def test_the_two_coverage_percentages():
    summary = coverage()["summary"]
    assert summary["featureTotal"] == 3 and summary["systemTotal"] == 3
    assert summary["covered"] == 2
    assert summary["forwardCoveragePct"] == pytest.approx(66.7)
    assert summary["backwardCoveragePct"] == pytest.approx(66.7)


def test_full_coverage_reads_100_both_ways():
    summary = coverage([["Feature ID", "Name"], ["FL-1", "a"], ["FL-2", "b"]],
                       [["System ID", "Scenario"], ["FL-1", "x"], ["FL-2", "y"]])["summary"]
    assert summary["forwardCoveragePct"] == 100.0
    assert summary["backwardCoveragePct"] == 100.0
    assert summary["missingInSystem"] == 0 and summary["missingInFeature"] == 0


def test_gaps_sort_before_what_is_already_covered():
    """The report exists to be acted on."""
    statuses = [e["status"] for e in coverage()["entries"]]
    assert statuses.index("missing_in_system") < statuses.index("covered")


def test_ids_sort_naturally_within_a_group():
    entries = coverage(
        [["Feature ID", "Name"], ["FL-2", "b"], ["FL-10", "j"], ["FL-1", "a"]],
        [["System ID", "Scenario"], ["FL-99", "z"]])["entries"]
    gaps = [e["id"] for e in entries if e["status"] == "missing_in_system"]
    assert gaps == ["FL-1", "FL-2", "FL-10"]


@pytest.mark.parametrize("fid,sid", [
    ("PS-101", "PS-101"), ("REQ-88", "REQ-88"), ("1001", "1001"),
    ("ABC_7", "ABC_7"), ("FL-1", "fl 1"), ("fl_1", "FL-1"),
])
def test_coverage_works_whatever_the_id_format(fid, sid):
    summary = coverage([["Feature ID", "Name"], [fid, "a"]],
                       [["System ID", "Scenario"], [sid, "x"]])["summary"]
    assert summary["forwardCoveragePct"] == 100.0


def test_an_unreadable_id_is_surfaced_not_dropped():
    """A row whose id cannot be read is absent from *both* directions —
    exactly how a report reaches 100% while being wrong."""
    result = coverage([["Feature", "Name"], ["FL-1", "a"],
                       ["No identifier on this row at all", "b"]],
                      [["System ID", "Scenario"], ["FL-1", "x"]])
    assert result["summary"]["unresolved"] == 1
    unresolved = [e for e in result["entries"] if e["status"] == "unresolved"]
    assert unresolved[0]["side"] == "feature"
    assert unresolved[0]["feature"]["data"]["name"] == "b"


def test_unresolved_entries_have_distinct_keys():
    """They all share an empty id, so the list key cannot be it."""
    f_rows = rows_of(parse([["Feature", "Name"],
                            ["no identifier whatsoever here", "a"],
                            ["nor any identifier on this one", "b"]]), "f.xlsx")
    entries = cov.build_coverage(f_rows, [], {})["entries"]
    assert len({e["key"] for e in entries}) == len(entries)


def test_an_empty_side_is_vacuously_covered():
    """Reporting 0% would read as a failure when there is nothing to check."""
    summary = cov.build_coverage([], [], {})["summary"]
    assert summary["forwardCoveragePct"] == 100.0
    assert summary["backwardCoveragePct"] == 100.0


def test_each_side_carries_its_row_detail_for_the_dashboard():
    gap = by_id(coverage())["FL-1003"]
    assert gap["feature"]["data"]["feature_name"] == "Sub array feature"
    assert gap["feature"]["file"] == "feature.xlsx"
    assert gap["system"] is None


# --- warnings -----------------------------------------------------------


def test_two_sides_naming_different_things_is_reported():
    """Otherwise this reads as a catastrophic coverage failure."""
    result = coverage([["Feature ID", "Name"], ["FL-1", "a"]],
                      [["System ID", "Scenario"], ["SYS-1", "x"]])
    codes = [w["code"] for w in result["warnings"]]
    assert "family_mismatch" in codes
    assert result["summary"]["covered"] == 0  # it still runs


def test_a_first_column_of_row_numbers_is_reported_not_blocked():
    result = coverage([["No", "Name"], [1, "a"], [2, "b"], [3, "c"]],
                      [["System ID", "Scenario"], ["FL-1", "x"]])
    warning = next(w for w in result["warnings"] if w["code"] == "numeric_key_column")
    assert warning["side"] == "feature"
    assert result["summary"]["featureTotal"] == 3  # computed anyway


def test_a_clean_pair_of_sheets_warns_about_nothing():
    assert coverage()["warnings"] == []


# --- an id that landed in a section heading -----------------------------


def test_an_id_swallowed_as_a_section_still_counts():
    """A row holding only its id is read by the parser as a section heading.

    FL-5794 is that row in the real fixture. Read from the column alone it
    would be reported as an uncovered gap while sitting in the file.
    """
    parsed = parse_workbook(open(FIXTURE_SYSTEM, "rb").read())
    rows = rows_of(parsed, "system.xlsx")
    assert parsed.sections == ["FL-5794"]
    assert not any(r["data"].get("column_1") == "FL-5794" for r in rows)

    result = cov.build_coverage([], rows, {})
    assert "FL-5794" in by_id(result)


def test_a_section_heading_id_is_counted_once_not_once_per_row():
    """A heading repeats over every row beneath it. Counting those occurrences
    would report one id as a dozen duplicates."""
    rows = rows_of(parse_workbook(open(FIXTURE_SYSTEM, "rb").read()), "system.xlsx")
    heading = by_id(cov.build_coverage([], rows, {}))["FL-5794"]
    assert heading["systemCount"] == 1
    assert heading["duplicate"] is False


def test_a_section_heading_id_does_not_borrow_another_rows_detail():
    """Its own row was consumed by the parser, so there is no detail to show —
    showing the next row's would name the wrong feature."""
    rows = rows_of(parse_workbook(open(FIXTURE_SYSTEM, "rb").read()), "system.xlsx")
    heading = by_id(cov.build_coverage([], rows, {}))["FL-5794"]
    assert heading["system"]["data"] == {}
    assert heading["system"]["section"] == "FL-5794"
    assert heading["system"]["file"] == "system.xlsx"


def test_ordinary_section_titles_are_not_read_as_ids():
    """"Camera testing 2" is shaped like an id but is not of the key family."""
    rows = [["Camera testing 2"], ["Feature ID", "Name"], ["FL-1", "a"]]
    assert set(by_id(coverage(rows, [["ID", "S"], ["FL-1", "x"]]))) == {"FL-1"}


# --- what the admin screen shows ----------------------------------------


def test_the_read_is_described_per_file_for_the_admin_screen():
    parsed = parse(FEATURE_PACKED)
    described = cov.describe_side(rows_of(parsed, "feature.xlsx"), parsed.columns)
    assert described["family"] == "FL"
    assert described["distinctIds"] == 3
    assert described["unresolvedRows"] == 0
    assert described["files"] == [{"fileName": "feature.xlsx", "column": "feature",
                                   "columnLabel": "Feature"}]
    assert described["extracted"][:2] == ["FL-1001", "FL-1002"]


def test_the_headerless_fixture_is_read_from_its_numbered_first_column():
    """The real sheet has no header, so its columns are only numbered."""
    parsed = parse_workbook(open(FIXTURE_SYSTEM, "rb").read())
    described = cov.describe_side(rows_of(parsed, "system.xlsx"), parsed.columns)
    assert described["files"][0]["column"] == "column_1"
    assert described["family"] == "FL"


# --- the identifier column is a default, not a rule ----------------------
# Real workbooks open with a serial number and keep the identifier in the
# second column. The first column is where we look unless told otherwise.

SERIAL_FIRST = [
    ["Serial No.", "Feature ID", "Feature Name"],
    [1, "FL-1001", "Camera control rework"],
    [2, "FL-1002", "Deconvolution modalities"],
    [3, "FL-1003", "Sub array feature"],
]


def test_every_column_is_offered_for_the_identifier_picker():
    """Offering only the column already in use makes the control useless —
    the reason to open it is that the default picked the wrong one."""
    parsed = parse(SERIAL_FIRST)
    read = cov.describe_side(rows_of(parsed, "feature.xlsx"), parsed.columns)
    assert [c["key"] for c in read["columns"]] == [
        "serial_no", "feature_id", "feature_name"]


def test_a_serial_number_first_column_reads_as_numeric_ids():
    """The symptom an admin sees before they change the column."""
    parsed = parse(SERIAL_FIRST)
    read = cov.describe_side(rows_of(parsed, "feature.xlsx"), parsed.columns)
    assert read["extracted"] == ["1", "2", "3"]
    assert read["numericRatio"] == 1.0
    assert read["files"][0]["columnLabel"] == "Serial No."


def test_naming_the_real_identifier_column_fixes_the_read():
    parsed = parse(SERIAL_FIRST)
    read = cov.describe_side(rows_of(parsed, "feature.xlsx"), parsed.columns,
                             override="feature_id")
    assert read["extracted"] == ["FL-1001", "FL-1002", "FL-1003"]
    assert read["family"] == "FL"
    assert read["numericRatio"] == 0.0
    assert read["files"][0]["columnLabel"] == "Feature ID"


def test_coverage_against_a_serial_numbered_sheet_needs_the_override():
    feature = parse(SERIAL_FIRST)
    system = parse([["System ID", "Scenario"],
                    ["FL-1001", "camera capture"],
                    ["FL-1002", "deconvolution pipeline"]])
    f_rows = rows_of(feature, "feature.xlsx")
    s_rows = rows_of(system, "system.xlsx")

    # left on the first column, the two sides share nothing at all
    blind = cov.build_coverage(f_rows, s_rows, {"layers": {}})
    assert blind["summary"]["covered"] == 0

    named = cov.build_coverage(f_rows, s_rows, {
        "layers": {"feature": {"column": "feature_id"}, "system": {"column": ""}}})
    assert named["summary"]["covered"] == 2
    assert named["summary"]["backwardCoveragePct"] == 100.0


def test_an_override_a_file_lacks_falls_back_to_its_first_column():
    """Two workbooks feeding one layer need not be shaped alike."""
    shaped = parse(SERIAL_FIRST)
    plain = parse([["Feature ID", "Feature Name"], ["FL-2001", "other workbook"]])
    rows = rows_of(shaped, "a.xlsx") + rows_of(plain, "b.xlsx")
    read = cov.describe_side(rows, shaped.columns + plain.columns,
                             override="feature_id")
    assert set(read["extracted"]) == {"FL-1001", "FL-1002", "FL-1003", "FL-2001"}
