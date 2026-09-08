"""Deciding what a workbook is, from its columns alone — no database, no HTTP.

The acceptance sheet's real column set is the case that matters: `Pattern No.`,
`OS ver.`, `Browser ver.`, `type`, `cellSens Edition`, `Solution License`,
`Authentication`, `Tester`, `TC count`, `Pass`, `Fail`, `NA`, `%age`, and three
columns nobody filled in.
"""
import pytest

from app.services import profile as prof


def cols(*specs) -> list[dict]:
    """(label, type) pairs in sheet order, keyed the way the parser keys them."""
    return [{"key": label.lower().replace(" ", "_").replace(".", "").replace("%", "pct"),
             "label": label, "type": kind}
            for label, kind in specs]


ACCEPTANCE = cols(
    ("Pattern No.", "number"), ("OS ver.", "string"), ("Browser ver.", "string"),
    ("type", "string"), ("cellSens Edition", "string"), ("Solution License", "string"),
    ("Authentication", "string"), ("Tester", "string"), ("TC count", "number"),
    ("Pass", "number"), ("Fail", "number"), ("NA", "number"), ("%age", "number"),
)
ACCEPTANCE_TOTALS = {"tc_count": 1412, "pass": 1349, "fail": 37, "na": 26,
                     "pctage": 3691.2, "pattern_no": 1362}


# --- what the sheet is ---------------------------------------------------


def test_the_real_acceptance_columns_are_read_as_a_run():
    found = prof.detect_run_columns(ACCEPTANCE)
    assert found["passedLabel"] == "Pass"
    assert found["failedLabel"] == "Fail"
    assert found["notRunLabel"] == "NA"
    assert found["totalLabel"] == "TC count"
    assert prof.is_run_results(found)


def test_a_counting_sheet_is_not_a_run():
    """The regression workbook: one measure, nothing about outcomes."""
    found = prof.detect_run_columns(
        cols(("Test spec name", "string"), ("Test spec tab name", "string"),
             ("Test Count", "number")))
    assert not prof.is_run_results(found)
    assert prof.describe(
        cols(("Test spec name", "string"), ("Test Count", "number")),
        {"test_count": 140736})["kind"] == "volume"


def test_pass_and_fail_as_text_columns_are_not_a_run():
    """The System workbook has Pass and Fail columns, but somebody typed
    "make only excel" into one, so the parser typed them as text. Counting
    words as passes is exactly the mistake this guards."""
    found = prof.detect_run_columns(
        cols(("Topics", "string"), ("Status", "string"),
             ("Pass", "string"), ("Fail", "string")))
    assert found["passedColumn"] == ""
    assert not prof.is_run_results(found)


def test_passes_alone_are_not_a_run():
    """A count of passes with nothing to read it against is not a result."""
    found = prof.detect_run_columns(cols(("Spec", "string"), ("Pass", "number")))
    assert found["passedLabel"] == "Pass"
    assert not prof.is_run_results(found)


def test_passes_with_only_a_total_are_enough():
    found = prof.detect_run_columns(
        cols(("Spec", "string"), ("Passed", "number"), ("Test Count", "number")))
    assert prof.is_run_results(found)
    assert found["totalLabel"] == "Test Count"


def test_one_column_never_fills_two_roles():
    """`Pass` would match the total pattern too if it were still in the pool
    when the total is looked for."""
    found = prof.detect_run_columns(
        cols(("Pass Count", "number"), ("Fail Count", "number"),
             ("Total Count", "number")))
    assert found["passedLabel"] == "Pass Count"
    assert found["failedLabel"] == "Fail Count"
    assert found["totalLabel"] == "Total Count"
    assert len({found["passedColumn"], found["failedColumn"],
                found["totalColumn"]}) == 3


def test_a_text_reviewer_column_is_not_a_pass_count():
    found = prof.detect_run_columns(
        cols(("Passed by", "string"), ("Failure notes", "string"),
             ("Test Count", "number")))
    assert found["passedColumn"] == "" and found["failedColumn"] == ""


# --- what the run said ---------------------------------------------------


def test_the_pass_rate_is_over_what_was_executed():
    """1,349 of the 1,386 actually run, not of the 1,412 planned. The two
    differ by more than a rounding — 97.3% against 95.5% — so which one is
    meant has to be settled rather than left to the reader."""
    run = prof.summarise_run(ACCEPTANCE_TOTALS, prof.detect_run_columns(ACCEPTANCE))
    assert run["executed"] == 1386
    assert run["passRatePct"] == 97.3
    assert run["executedPct"] == 98.2


def test_the_tests_nobody_ran_are_carried_not_folded_in():
    run = prof.summarise_run(ACCEPTANCE_TOTALS, prof.detect_run_columns(ACCEPTANCE))
    assert run["notRun"] == 26
    assert run["total"] == 1412
    assert run["passed"] + run["failed"] + run["notRun"] == run["total"]


def test_the_real_sheet_reconciles():
    run = prof.summarise_run(ACCEPTANCE_TOTALS, prof.detect_run_columns(ACCEPTANCE))
    assert run["reconciles"] is True
    assert run["unaccounted"] == 0.0


def test_columns_that_do_not_add_up_are_reported():
    """A results sheet arguing with itself is a finding, not something to
    average away."""
    found = prof.detect_run_columns(ACCEPTANCE)
    run = prof.summarise_run({**ACCEPTANCE_TOTALS, "tc_count": 1500}, found)
    assert run["reconciles"] is False
    assert run["unaccounted"] == 88          # 1500 - (1349 + 37 + 26)
    assert run["passRatePct"] == 97.3        # over executed, so still right


def test_a_sheet_with_no_total_column_adds_its_own_up():
    found = prof.detect_run_columns(
        cols(("Spec", "string"), ("Pass", "number"), ("Fail", "number")))
    run = prof.summarise_run({"pass": 90, "fail": 10}, found)
    assert run["total"] == 100 and run["executed"] == 100
    assert run["passRatePct"] == 90.0
    assert run["reconciles"] is True         # nothing to disagree with


def test_a_run_where_nothing_was_executed_has_no_rate():
    found = prof.detect_run_columns(
        cols(("Spec", "string"), ("Pass", "number"), ("Fail", "number"),
             ("NA", "number")))
    run = prof.summarise_run({"pass": 0, "fail": 0, "na": 40}, found)
    assert run["passRatePct"] is None
    assert run["notRun"] == 40


def test_the_percentage_column_is_never_summed_into_the_answer():
    """`%age` sums to 3,691 and is what the generic view used to headline."""
    run = prof.summarise_run(ACCEPTANCE_TOTALS, prof.detect_run_columns(ACCEPTANCE))
    assert 3691.2 not in run.values()
    assert run["passRatePct"] == 97.3


# --- what to break it down by --------------------------------------------

ACCEPTANCE_DISTINCTS = {"os_ver": 2, "browser_ver": 3, "type": 8,
                        "cellsens_edition": 25, "solution_license": 13,
                        "authentication": 3, "tester": 7}


def test_dimensions_are_ranked_richest_first():
    dims = prof.rank_dimensions(ACCEPTANCE, ACCEPTANCE_DISTINCTS, 39)
    assert [d["label"] for d in dims] == [
        "type", "Tester", "Authentication", "Browser ver.", "OS ver."]
    assert prof.choose_dimension(dims) == "type"


def test_columns_with_too_many_values_are_not_dimensions():
    """25 editions over 39 rows groups nothing; 13 licences barely more."""
    dims = prof.rank_dimensions(ACCEPTANCE, ACCEPTANCE_DISTINCTS, 39)
    labels = {d["label"] for d in dims}
    assert "cellSens Edition" not in labels
    assert "Solution License" not in labels


def test_a_column_with_one_value_is_not_a_dimension():
    dims = prof.rank_dimensions(cols(("Team", "string")), {"team": 1}, 20)
    assert dims == []


def test_an_empty_column_is_not_a_dimension():
    dims = prof.rank_dimensions(cols(("Remarks", "string")), {"remarks": 0}, 39)
    assert dims == []


def test_a_requested_dimension_the_sheet_lacks_falls_back():
    dims = prof.rank_dimensions(ACCEPTANCE, ACCEPTANCE_DISTINCTS, 39)
    assert prof.choose_dimension(dims, "browser_ver") == "browser_ver"
    assert prof.choose_dimension(dims, "nonexistent") == "type"
    assert prof.choose_dimension([], "anything") == ""


def test_a_row_is_named_by_its_number_and_what_distinguishes_it():
    """Rather than the generic view's join of every text column, which reads
    "Win11 Pro 64bit · MS Edge · Clean Install · Dimension · Full · Online ·
    Sreelakshmi"."""
    dims = prof.rank_dimensions(ACCEPTANCE, ACCEPTANCE_DISTINCTS, 39)
    assert prof.label_columns(ACCEPTANCE, dims) == ["pattern_no", "type", "tester"]


def test_a_sheet_with_no_serial_column_is_named_by_its_dimensions():
    columns = cols(("Tester", "string"), ("type", "string"),
                   ("Pass", "number"), ("Fail", "number"))
    dims = prof.rank_dimensions(columns, {"tester": 4, "type": 3}, 20)
    assert prof.label_columns(columns, dims) == ["tester", "type"]


# --- the breakdown arithmetic --------------------------------------------


def test_each_bucket_carries_its_own_rate():
    bucket = prof.bucket_rate({"value": "Clean Install", "rowCount": 12,
                               "passed": 90.0, "failed": 10.0}, {})
    assert bucket["executed"] == 100.0
    assert bucket["passRatePct"] == 90.0


def test_a_bucket_that_ran_nothing_has_no_rate():
    bucket = prof.bucket_rate({"value": "Deferred", "rowCount": 3,
                               "passed": 0.0, "failed": 0.0}, {})
    assert bucket["passRatePct"] is None


# --- the whole decision ---------------------------------------------------


def test_describe_names_the_columns_that_decided_it():
    shape = prof.describe(ACCEPTANCE, ACCEPTANCE_TOTALS)
    assert shape["kind"] == "run_results"
    assert "'Pass'" in shape["reason"] and "'Fail'" in shape["reason"]
    assert shape["runResults"]["passRatePct"] == 97.3


def test_describe_says_why_a_sheet_stayed_generic():
    shape = prof.describe(cols(("ID", "string"), ("Summary", "string")), {})
    assert shape["kind"] == "volume"
    assert shape["runResults"] is None
    assert shape["reason"]


# --- outcomes recorded as a word per row ---------------------------------

STATUS_COLUMNS = cols(
    ("Test ID", "string"), ("Requirement ID", "string"),
    ("Test Description", "string"), ("Status", "string"), ("Test Cases", "number"),
)


def stats(**kw) -> dict:
    """{key: {distinct, filled, values}} the way the repository reports it."""
    return {key: {"distinct": len(values), "filled": filled, "values": values}
            for key, (filled, values) in kw.items()}


STATUS_STATS = stats(
    test_id=(4, ["REG-1001", "REG-1002", "REG-1003", "REG-1004"]),
    requirement_id=(4, ["FL-5773", "FL-5774", "FL-5775", "FL-5776"]),
    test_description=(4, ["Microscope connection", "Image acquisition",
                          "Export workflow", "Settings persistence"]),
    status=(4, ["Passed", "Failed"]),
)


@pytest.mark.parametrize("value,kind", [
    ("Passed", "passing"), ("passed", "passing"), ("PASS", "passing"),
    ("Done", "passing"), ("OK", "passing"), ("Complete", "passing"),
    ("Failed", "failing"), ("fail", "failing"), ("Blocked", "failing"),
    ("NG", "failing"), ("Error", "failing"),
    ("In Progress", "pending"), ("Not started", "pending"), ("N/A", "pending"),
    ("Deferred", "pending"), ("Skipped", "pending"),
    ("Microscope connection", ""), ("make only excel", ""), ("", ""), (None, ""),
])
def test_which_way_a_status_word_points(value, kind):
    assert prof.outcome_of(value) == kind


def test_whitespace_and_punctuation_do_not_hide_an_outcome():
    assert prof.outcome_of("  Passed. ") == "passing"
    assert prof.outcome_of("in  progress") == "pending"


def test_the_status_column_is_found_by_its_values():
    found = prof.detect_status_column(STATUS_COLUMNS, STATUS_STATS, 4)
    assert found["statusColumn"] == "status"
    assert found["statusLabel"] == "Status"
    assert prof.is_status(found)


def test_a_column_named_pass_holding_a_note_is_not_a_status():
    """The real System sheet: a column called Pass, one row in nine filled,
    and what is in it reads "make only excel". Labelled like an outcome,
    meaning nothing like one."""
    columns = cols(("Topics", "string"), ("Pass", "string"), ("Remarks", "string"))
    found = prof.detect_status_column(columns, {
        "topics": {"distinct": 9, "filled": 9, "values": ["a", "b"]},
        "pass": {"distinct": 1, "filled": 1, "values": ["make only excel"]},
        "remarks": {"distinct": 1, "filled": 1, "values": ["with Matthias"]},
    }, 9)
    assert not prof.is_status(found)


def test_a_barely_filled_outcome_column_is_not_a_status():
    """A results column nobody completed is a completeness problem, and a
    dashboard drawn off two filled cells would imply otherwise."""
    columns = cols(("Topic", "string"), ("Status", "string"))
    found = prof.detect_status_column(columns, {
        "topic": {"distinct": 9, "filled": 9, "values": ["a", "b"]},
        "status": {"distinct": 1, "filled": 2, "values": ["Passed"]},
    }, 9)
    assert not prof.is_status(found)


def test_ordinary_text_columns_are_not_statuses():
    """Testers, browsers and install types all repeat, and none is an outcome."""
    columns = cols(("Tester", "string"), ("Browser ver.", "string"), ("type", "string"))
    found = prof.detect_status_column(columns, stats(
        tester=(39, ["Ajay", "Arpita", "Chandni"]),
        browser_ver=(39, ["---", "Google Chrome", "MS Edge"]),
        type=(39, ["Clean Install", "Upgrade", "Version Update"])), 39)
    assert not prof.is_status(found)


def test_a_column_that_says_it_is_the_status_wins():
    columns = cols(("Result", "string"), ("Gate", "string"))
    found = prof.detect_status_column(columns, stats(
        result=(10, ["Passed", "Failed"]), gate=(10, ["ok", "blocked"])), 10)
    assert found["statusColumn"] == "result"


# --- the measure a status is weighted by ----------------------------------


def test_the_measure_skips_percentages_and_row_numbers():
    """On the acceptance sheet `%age` is rightmost and `Pattern No.` leftmost;
    neither counts anything, so neither may stand in as the measure."""
    assert prof.primary_measure(ACCEPTANCE)["label"] == "NA"
    assert prof.primary_measure(STATUS_COLUMNS)["label"] == "Test Cases"


def test_a_sheet_with_no_countable_column_has_no_measure():
    assert prof.primary_measure(cols(("ID", "string"), ("Sr. No.", "number"))) is None


# --- what the statuses said ----------------------------------------------

BUCKETS = [{"value": "Passed", "rowCount": 3, "measure": 44},
           {"value": "Failed", "rowCount": 1, "measure": 9}]
STATUS = {"statusColumn": "status", "statusLabel": "Status"}
CASES = {"key": "test_cases", "label": "Test Cases"}


def test_the_rate_is_weighted_by_what_each_row_covers():
    """Three passing rows of four is 75%; the 44 test cases they carry out of
    53 is 83%. Those are different claims, so the basis is reported."""
    summary = prof.summarise_status(BUCKETS, STATUS, CASES)
    assert summary["basis"] == "measure"
    assert summary["passed"] == 44 and summary["failed"] == 9
    assert summary["decided"] == 53
    assert summary["passRatePct"] == 83.0


def test_without_a_measure_the_rate_is_over_rows_and_says_so():
    summary = prof.summarise_status(BUCKETS, STATUS, None)
    assert summary["basis"] == "row_count"
    assert summary["passed"] == 3 and summary["failed"] == 1
    assert summary["passRatePct"] == 75.0
    assert summary["measureLabel"] == "rows"


def test_pending_rows_are_carried_beside_the_rate_not_against_it():
    summary = prof.summarise_status(
        BUCKETS + [{"value": "In Progress", "rowCount": 2, "measure": 20}],
        STATUS, CASES)
    assert summary["pending"] == 20
    assert summary["decided"] == 53          # unchanged by the pending rows
    assert summary["passRatePct"] == 83.0
    assert summary["total"] == 73


def test_a_word_nobody_recognises_is_shown_but_not_scored():
    summary = prof.summarise_status(
        BUCKETS + [{"value": "Waiting on hardware", "rowCount": 1, "measure": 7}],
        STATUS, CASES)
    assert summary["unrecognised"] == 7
    assert summary["passRatePct"] == 83.0
    odd = next(s for s in summary["statuses"] if s["value"] == "Waiting on hardware")
    assert odd["kind"] == ""


def test_statuses_come_back_largest_first():
    summary = prof.summarise_status(BUCKETS, STATUS, CASES)
    assert [s["value"] for s in summary["statuses"]] == ["Passed", "Failed"]
    assert [s["kind"] for s in summary["statuses"]] == ["passing", "failing"]


def test_a_sheet_where_nothing_is_decided_has_no_rate():
    summary = prof.summarise_status(
        [{"value": "Not started", "rowCount": 4, "measure": 53}], STATUS, CASES)
    assert summary["passRatePct"] is None
    assert summary["pending"] == 53


# --- naming a row on a sheet with nothing to group by ---------------------


def test_a_status_row_is_named_by_its_id_and_description():
    """No serial column and no dimensions: the sheet's leading column plus its
    most descriptive one, rather than every text column joined."""
    keys = prof.label_columns(STATUS_COLUMNS, [], STATUS_STATS, ("status",))
    assert keys == ["test_id", "test_description"]


def test_the_acceptance_row_label_is_unchanged_by_the_fallback():
    dims = prof.rank_dimensions(ACCEPTANCE, ACCEPTANCE_DISTINCTS, 39)
    assert prof.label_columns(ACCEPTANCE, dims) == ["pattern_no", "type", "tester"]


# --- the whole decision, with statuses in play ----------------------------


def test_a_status_sheet_is_described_as_one():
    shape = prof.describe(STATUS_COLUMNS, {"test_cases": 53}, STATUS_STATS, 4)
    assert shape["kind"] == "status"
    assert "'Status'" in shape["reason"]
    assert shape["runResults"] is None


def test_counted_outcomes_beat_a_status_word():
    """A sheet carrying both is reporting numbers, and the numbers are the more
    precise answer."""
    columns = ACCEPTANCE + cols(("Status", "string"))
    shape = prof.describe(
        columns, ACCEPTANCE_TOTALS,
        {**STATUS_STATS,
         "status": {"distinct": 2, "filled": 39, "values": ["Passed", "Failed"]}}, 39)
    assert shape["kind"] == "run_results"


def test_a_counting_sheet_is_still_volume_with_stats_available():
    columns = cols(("Test spec name", "string"), ("Test Count", "number"))
    shape = prof.describe(columns, {"test_count": 140736},
                          stats(test_spec_name=(206, ["DP23_Type2", "IX73"])), 206)
    assert shape["kind"] == "volume"
    assert shape["status"] is None


# --- a list of things, with nothing to count -----------------------------

FEATURE_COLUMNS = cols(("ID", "string"), ("Summary (cellSens)", "string"),
                       ("PBI / Related Item", "string"))
FEATURE_STATS = stats(
    id=(13, ["FL-5794", "FL-5793", "FL-5685", "FL-5773"]),
    summary_cellsens=(13, ["TIRF Support (4L/1L) with XRTC"]),
    pbi_related_item=(12, ["CS-4816 - FL-5794 - PBI1 - TIRF General Firmware"]),
)


def feature_rows(*specs) -> list[dict]:
    return [{"data": {"id": i, "summary_cellsens": s, "pbi_related_item": p}}
            for i, s, p in specs]


def test_a_sheet_of_identifiers_with_no_measure_is_an_inventory():
    shape = prof.describe(FEATURE_COLUMNS, {}, FEATURE_STATS, 13)
    assert shape["kind"] == "inventory"
    assert shape["inventory"]["idColumn"] == "id"
    assert shape["inventory"]["idLabel"] == "ID"


def test_a_sheet_that_counts_something_is_not_an_inventory():
    """Identifiers and a measure together: the measure is worth grouping, and
    the count-and-group view is right for it."""
    columns = FEATURE_COLUMNS + cols(("Test Count", "number"))
    shape = prof.describe(columns, {"test_count": 500}, FEATURE_STATS, 13)
    assert shape["kind"] == "volume"


def test_a_first_column_of_prose_is_not_an_inventory():
    columns = cols(("Test Description", "string"), ("Owner", "string"))
    shape = prof.describe(columns, {}, stats(
        test_description=(4, ["Microscope connection", "Image acquisition",
                              "Export workflow"]),
        owner=(4, ["Meera", "Rahul"])), 4)
    assert shape["kind"] == "volume"


def test_the_identifier_family_is_discovered_not_configured():
    inv = prof.summarise_inventory(
        feature_rows(("PS-1", "One", "REQ-9 - a"), ("PS-2", "Two", "REQ-8 - b")),
        {"idColumn": "id", "idLabel": "ID"})
    assert inv["family"] == "PS"
    assert inv["linkFamily"] == "REQ"


def test_the_real_feature_sheet_counts_its_links():
    """Twelve of thirteen features name the work behind them; one names none,
    and that one entry is the thing this dashboard exists to surface."""
    rows = feature_rows(
        ("FL-5773", "Helix support", "CS-4614 - US1\nCS-4615 - US2\nCS-4616 - US3"),
        ("FL-5776", "FIJI bridge", "CS-4759 - PBI 1\nCS-4798 - PBI 2"),
        ("FL-5807", "Cicero Spinning Disk (CREST)", None),
    )
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert inv["items"] == 3
    assert inv["family"] == "FL" and inv["linkFamily"] == "CS"
    assert inv["links"] == 5
    assert inv["linked"] == 2 and inv["unlinked"] == 1
    assert inv["linkedPct"] == 66.7
    assert [i["id"] for i in inv["unlinkedItems"]] == ["FL-5807"]
    assert inv["unlinkedItems"][0]["detail"] == "Cicero Spinning Disk (CREST)"


def test_entries_are_ranked_by_how_much_work_they_carry():
    rows = feature_rows(
        ("FL-1", "One", "CS-1 - a"),
        ("FL-2", "Two", "CS-2 - a\nCS-3 - b\nCS-4 - c"),
        ("FL-3", "Three", "CS-5 - a\nCS-6 - b"),
    )
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert [(i["id"], i["links"]) for i in inv["mostLinked"]] == [
        ("FL-2", 3), ("FL-3", 2), ("FL-1", 1)]


def test_a_stray_identifier_in_a_description_is_not_a_linkage_convention():
    """The real System sheet says "Helix: Support of new BX57/47 microscopes".
    BX57 is a microscope. Read as the link family it would report that sheet as
    11% linked, when the truth is that it carries no links at all."""
    rows = feature_rows(
        ("FL-5773", "Helix: Support of new BX57/47 microscopes", None),
        ("FL-5794", "TIRF Support", None),
        ("FL-5685", "Spectra X Gen3", None),
        ("FL-5777", "X-Cite TETREM", None),
    )
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert inv["linkFamily"] == ""
    assert inv["links"] == 0
    assert inv["linked"] == 0 and inv["unlinked"] == 0   # nothing is claimed
    assert inv["linkedPct"] is None
    assert inv["items"] == 4


def test_a_family_naming_most_entries_is_a_convention():
    rows = feature_rows(("FL-1", "a", "CS-1"), ("FL-2", "b", "CS-2"),
                        ("FL-3", "c", None), ("FL-4", "d", None))
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert inv["linkFamily"] == "CS"      # 2 of 4 entries clears the bar
    assert inv["linked"] == 2 and inv["unlinked"] == 2


def test_rows_with_no_readable_identifier_are_counted_not_dropped():
    rows = feature_rows(("FL-1", "a", "CS-1"),
                        ("a note somebody typed in the id column", "b", None))
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert inv["items"] == 1
    assert inv["unreadable"] == 1


def test_the_same_identifier_twice_is_a_duplicate():
    rows = feature_rows(("FL-1", "a", "CS-1"), ("fl 1", "a again", "CS-2"))
    inv = prof.summarise_inventory(rows, {"idColumn": "id", "idLabel": "ID"})
    assert inv["items"] == 1
    assert inv["duplicates"] == 1
