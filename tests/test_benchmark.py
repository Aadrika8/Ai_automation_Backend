"""Automation coverage on its own — no database, no HTTP.

The measurement and the industry reference are separate things, and these
tests keep them separate: nothing here asserts that Evident's number should
resemble the reference, only that each is computed and carried correctly.
"""
import pytest

from app.benchmark_defaults import automation_reference
from app.services import benchmark as bm

COLS = [
    {"key": "test_spec_name", "label": "Test spec name", "type": "string"},
    {"key": "test_count", "label": "Test Count", "type": "number"},
    {"key": "automated_test_count", "label": "Automated Test Count", "type": "number"},
]


def rows(*pairs):
    return [{"data": {"test_count": t, "automated_test_count": a}} for t, a in pairs]


# --- finding the columns -------------------------------------------------


def test_finds_the_automated_and_total_columns():
    found = bm.detect_columns(COLS)
    assert found["automatedColumn"] == "automated_test_count"
    assert found["totalColumn"] == "test_count"


@pytest.mark.parametrize("label", [
    "Automated", "Automation Count", "Auto Test Count", "automated tests",
])
def test_an_automated_column_announces_itself_however_it_is_worded(label):
    cols = [{"key": "n", "label": "Test Count", "type": "number"},
            {"key": "a", "label": label, "type": "number"}]
    assert bm.detect_columns(cols)["automatedColumn"] == "a"


def test_the_automated_column_is_not_also_read_as_the_total():
    """"Automated Test Count" matches both patterns; it must be taken out first."""
    found = bm.detect_columns(COLS)
    assert found["totalColumn"] != found["automatedColumn"]


def test_a_lone_other_numeric_column_is_taken_as_the_measure():
    cols = [{"key": "cases", "label": "Cases", "type": "number"},
            {"key": "a", "label": "Automated", "type": "number"}]
    assert bm.detect_columns(cols)["totalColumn"] == "cases"


def test_string_columns_are_never_measures():
    cols = [{"key": "name", "label": "Automated by", "type": "string"}]
    assert bm.detect_columns(cols)["automatedColumn"] == ""


# --- measuring one layer -------------------------------------------------


def test_coverage_is_the_share_of_test_cases_not_of_rows():
    layer = bm.measure_layer("regression", "Regression", rows((100, 25), (300, 50)), COLS)
    assert layer["automated"] == 75
    assert layer["total"] == 400
    assert layer["coveragePct"] == 18.8
    assert layer["basis"] == "test_count"


def test_row_count_is_the_fallback_and_says_so():
    """A sheet with no measure column: one row is one test, and the basis is labelled."""
    cols = [{"key": "name", "label": "Spec", "type": "string"},
            {"key": "a", "label": "Automated", "type": "number"}]
    data = [{"data": {"a": 1}}, {"data": {"a": 0}}, {"data": {"a": 1}}]
    layer = bm.measure_layer("unit", "Unit", data, cols)
    assert layer["basis"] == "row_count"
    assert layer["total"] == 3
    assert layer["coveragePct"] == pytest.approx(66.7)


def test_a_layer_with_no_automated_column_is_unmeasured_not_zero():
    """Reporting 0% for a sheet that never recorded the figure invents a finding."""
    cols = [c for c in COLS if c["key"] != "automated_test_count"]
    layer = bm.measure_layer("system", "System", [{"data": {"test_count": 500}}], cols)
    assert layer["measured"] is False
    assert layer["coveragePct"] is None
    assert "no automated-count column" in layer["reason"]


def test_a_layer_with_no_rows_is_unmeasured():
    layer = bm.measure_layer("acceptance", "Acceptance", [], COLS)
    assert layer["measured"] is False


def test_non_numeric_cells_are_ignored_rather_than_crashing():
    data = [{"data": {"test_count": 100, "automated_test_count": 20}},
            {"data": {"test_count": "n/a", "automated_test_count": None}}]
    layer = bm.measure_layer("regression", "Regression", data, COLS)
    assert layer["automated"] == 20 and layer["total"] == 100


def test_the_column_keys_survive_the_merge():
    """A dict merge once replaced the summed counts with the column keys."""
    layer = bm.measure_layer("regression", "Regression", rows((10, 5)), COLS)
    assert isinstance(layer["automated"], float)
    assert layer["automatedColumn"] == "automated_test_count"


# --- the release figure --------------------------------------------------


def make(layer_id, automated, total, measured=True, basis="test_count"):
    return {"layerId": layer_id, "name": layer_id, "measured": measured,
            "automated": automated, "total": total, "basis": basis, "reason": ""}


def test_the_release_figure_is_count_weighted_not_an_average():
    """A 140,000-test layer and a 1,400-test layer do not carry equal weight."""
    result = bm.automation_coverage([
        make("regression", 21000, 140000),   # 15%
        make("acceptance", 700, 1400),       # 50%
    ])
    assert result["coveragePct"] == pytest.approx(15.3, abs=0.1)
    # the average of the two percentages would have been 32.5
    assert result["coveragePct"] < 20


def test_unmeasured_layers_are_excluded_from_both_sides():
    """A release is not less automated because one sheet omits the figure."""
    result = bm.automation_coverage([
        make("regression", 25, 100),
        make("system", 0, 0, measured=False),
    ])
    assert result["coveragePct"] == 25.0
    assert result["unmeasuredLayers"] == ["system"]


def test_a_release_with_nothing_measured_reports_unmeasured():
    result = bm.automation_coverage([make("system", 0, 0, measured=False)])
    assert result["measured"] is False
    assert result["coveragePct"] is None


def test_a_mixed_basis_is_reported_as_mixed():
    result = bm.automation_coverage([
        make("regression", 25, 100),
        make("unit", 1, 4, basis="row_count"),
    ])
    assert result["basis"] == "mixed"


# --- the reference, and the distance -------------------------------------


def test_the_reference_range_is_derived_from_its_own_sources():
    """So the band on screen can never drift from the pills beneath it."""
    ref = automation_reference()
    values = [s["value"] for s in ref["sources"]]
    assert ref["low"] == min(values) == 33.0
    assert ref["high"] == max(values) == 44.0


def test_the_reference_declares_what_it_is_not():
    ref = automation_reference()
    assert ref["synthesised"] is True
    assert ref["selfReported"] is True
    assert ref["lifeScienceSpecific"] is False
    assert len(ref["caveats"]) >= 4


def test_every_source_carries_its_publisher():
    """A figure from a company selling automation tooling reads differently."""
    assert all(s["publisher"] and s["publisherKind"] for s in automation_reference()["sources"])


@pytest.mark.parametrize("value,position,points", [
    (15.9, "below", 17),
    (33.0, "within", 0),
    (38.0, "within", 0),
    (44.0, "within", 0),
    (51.0, "above", 7),
])
def test_distance_to_the_range(value, position, points):
    got = bm.distance_to_range(value, 33.0, 44.0)
    assert got["position"] == position and got["points"] == points


def test_distance_is_whole_points_only():
    """The reference is a spread of self-reported estimates; a decimal would
    claim a precision neither side has."""
    assert bm.distance_to_range(15.9, 33.0, 44.0)["points"] == 17


def test_an_unmeasured_release_has_no_distance():
    assert bm.distance_to_range(None, 33.0, 44.0) == {"position": "unmeasured", "points": None}


def test_the_report_keeps_the_two_sides_apart():
    report = bm.coverage_report([make("regression", 25, 100)], automation_reference())
    assert set(report) == {"evident", "reference", "distance"}
    assert "coveragePct" in report["evident"]
    assert "coveragePct" not in report["reference"]


# --- across releases -----------------------------------------------------


def point(release_id, name, pct, measured=True):
    return {"releaseId": release_id, "releaseName": name,
            "measured": measured, "coveragePct": pct}


def test_each_release_reports_its_change_from_the_one_before():
    series = bm.automation_trend([
        point("v4-2", "v4.2", 11.0),
        point("v4-3", "v4.3", 16.5),
        point("v4-4", "v4.4", 21.1),
    ])
    assert series[0]["deltaPct"] is None and series[0]["comparedTo"] == ""
    assert series[1]["deltaPct"] == 5.5 and series[1]["comparedTo"] == "v4.2"
    assert series[2]["deltaPct"] == pytest.approx(4.6)


def test_a_fall_is_reported_as_a_negative_change():
    series = bm.automation_trend([point("a", "v1", 30.0), point("b", "v2", 24.0)])
    assert series[1]["deltaPct"] == -6.0


def test_an_unmeasured_release_does_not_break_the_chain():
    """A release that never recorded a count should not silently reset the
    trend — the next measured one still compares to the last real figure."""
    series = bm.automation_trend([
        point("v4-2", "v4.2", 11.0),
        point("v4-3", "v4.3", None, measured=False),
        point("v4-4", "v4.4", 21.1),
    ])
    assert series[1]["deltaPct"] is None
    assert series[2]["deltaPct"] == pytest.approx(10.1)
    assert series[2]["comparedTo"] == "v4.2"


def test_the_first_measured_release_has_nothing_to_compare_to():
    series = bm.automation_trend([
        point("v4-2", "v4.2", None, measured=False),
        point("v4-3", "v4.3", 16.5),
    ])
    assert series[1]["deltaPct"] is None


def test_a_trend_of_one_release_is_valid_and_has_no_delta():
    series = bm.automation_trend([point("v4-4", "v4.4", 21.1)])
    assert len(series) == 1 and series[0]["deltaPct"] is None


def test_an_empty_trend_is_empty_rather_than_an_error():
    assert bm.automation_trend([]) == []


def test_the_series_keeps_every_field_it_was_given():
    series = bm.automation_trend([{**point("a", "v1", 10.0), "basis": "test_count"}])
    assert series[0]["basis"] == "test_count"
