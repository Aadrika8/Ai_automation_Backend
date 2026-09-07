"""Feature <-> System coverage over HTTP.

The endpoints answer both directions from one release's current data, and are
scoped to that release like everything else below an application.
"""
import pytest
import pytest_asyncio

from app import db as db_module
from tests.helpers_xlsx import FEATURE_SHEET, SYSTEM_SHEET, build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
OTHER_RELEASE = "/api/apps/cellsens/releases/v4-3"
COVERAGE = f"{RELEASE}/traceability"
CONFIG = f"{COVERAGE}/config"
PREVIEW = f"{COVERAGE}/preview"
CSV = f"{RELEASE}/traceability.csv"

SEPTEMBER = {"year": 2026, "month": 9}


async def clear_trace_config():
    """A saved configuration outlives the test that saved it, and the next
    test would then be measured against someone else's columns."""
    await db_module.get_db().trace_configs.delete_many({"appId": "cellsens"})


@pytest_asyncio.fixture
async def loaded(client, admin_headers, qa_headers, tmp_path):
    """A v4.4 release holding one feature workbook and one system workbook."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    release_dir.mkdir(parents=True)
    (release_dir / "feature.xlsx").write_bytes(build_workbook(FEATURE_SHEET))
    (release_dir / "system.xlsx").write_bytes(build_workbook(SYSTEM_SHEET))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await clear_trace_config()
    for layer in ("feature", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    res = await client.post(f"{RELEASE}/snapshots", headers=qa_headers,
                            json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text

    yield release_dir

    await clear_trace_config()
    for layer in ("feature", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- configuration ------------------------------------------------------


async def test_an_unconfigured_release_still_answers(client, manager_headers, loaded):
    """The identifier source is a rule, not a setting, so coverage works out
    of the box. The admin screen exists to override the rule, not to switch
    the feature on."""
    res = await client.get(CONFIG, headers=manager_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is False
    assert body["pattern"] == ""
    # empty means the rule: the first column of whichever file the row is in
    assert body["layers"]["feature"]["column"] == ""
    assert body["layers"]["system"]["column"] == ""


async def test_the_config_reports_what_each_file_was_read_from(client, manager_headers,
                                                              loaded):
    """A reader who cannot see which column was used has no way to tell a
    real gap from a misread sheet."""
    body = (await client.get(CONFIG, headers=manager_headers)).json()
    system = body["read"]["system"]
    assert system["family"] == "FL"
    assert system["files"] == [{"fileName": "system.xlsx", "column": "system_id",
                                "columnLabel": "System ID"}]
    assert system["extracted"][:2] == ["FL-1001", "FL-1002"]
    assert system["distinctIds"] == 3
    assert system["unresolvedRows"] == 0


async def test_saving_a_configuration_sticks(client, admin_headers, manager_headers, loaded):
    res = await client.put(CONFIG, headers=admin_headers, json={
        "pattern": r"[A-Za-z]+\-\d+",
        "layers": {"feature": {"column": "feature_id"},
                   "system": {"column": "system_id"}}})
    assert res.status_code == 200, res.text
    assert res.json()["configured"] is True
    assert res.json()["updatedBy"] == "admin"

    again = (await client.get(CONFIG, headers=manager_headers)).json()
    assert again["configured"] is True


async def test_a_broken_pattern_is_rejected(client, admin_headers, loaded):
    res = await client.put(CONFIG, headers=admin_headers,
                           json={"pattern": "[unclosed", "layers": {}})
    assert res.status_code == 422


async def test_only_an_admin_may_configure(client, qa_headers, manager_headers, loaded):
    for headers in (qa_headers, manager_headers):
        res = await client.put(CONFIG, headers=headers, json={"pattern": "", "layers": {}})
        assert res.status_code == 403


async def test_configuration_needs_a_token(client, loaded):
    assert (await client.get(CONFIG)).status_code == 401


async def test_preview_shows_what_the_rule_reads(client, admin_headers, loaded):
    """With no override this is the rule itself: the first column of each
    of the layer's workbooks."""
    res = await client.get(PREVIEW, headers=admin_headers, params={"layer": "system"})
    assert res.status_code == 200, res.text
    read = res.json()["read"]
    assert read["extracted"][:2] == ["FL-1001", "FL-1002"]
    assert read["matchedRows"] == read["totalRows"] == 3
    assert read["distinctIds"] == 3
    assert read["files"][0]["column"] == "system_id"


async def test_preview_shows_what_an_override_would_do_instead(client, admin_headers,
                                                              loaded):
    """Overriding the rule blind is how a report ends up measuring the
    wrong thing, so the numbers move before anything is saved."""
    res = await client.get(PREVIEW, headers=admin_headers,
                           params={"layer": "system", "column": "scenario"})
    read = res.json()["read"]
    assert read["distinctIds"] == 0
    assert read["unresolvedRows"] == 3


def _ids(body, status):
    return [e["id"] for e in body["entries"] if e["status"] == status]


# --- the comparison -----------------------------------------------------


async def test_both_directions_in_one_response(client, manager_headers, loaded):
    res = await client.get(COVERAGE, headers=manager_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert _ids(body, "covered") == ["FL-1001", "FL-1002"]
    assert _ids(body, "missing_in_system") == ["FL-1003"]   # planned, unverified
    assert _ids(body, "missing_in_feature") == ["FL-2001"]  # verified, unplanned


async def test_the_summary_carries_both_percentages(client, manager_headers, loaded):
    summary = (await client.get(COVERAGE, headers=manager_headers)).json()["summary"]
    assert summary["featureTotal"] == 3
    assert summary["systemTotal"] == 3
    assert summary["covered"] == 2
    assert summary["forwardCoveragePct"] == pytest.approx(66.7)
    assert summary["backwardCoveragePct"] == pytest.approx(66.7)


async def test_a_gap_carries_the_row_behind_it(client, manager_headers, loaded):
    """The dashboard has to show what the missing feature actually is."""
    body = (await client.get(COVERAGE, headers=manager_headers)).json()
    gap = next(e for e in body["entries"] if e["status"] == "missing_in_system")
    assert gap["feature"]["data"]["feature_name"] == "Sub array feature"
    assert gap["feature"]["fileName"] == "feature.xlsx"
    assert gap["feature"]["snapshotId"]
    assert gap["system"] is None


async def test_which_data_the_figures_came_from(client, manager_headers, loaded):
    body = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert body["feature"]["rowCount"] == 3
    assert body["feature"]["files"] == ["feature.xlsx"]
    assert body["system"]["latestPeriod"] == SEPTEMBER


async def test_any_authenticated_role_may_read(client, qa_headers, manager_headers, loaded):
    for headers in (qa_headers, manager_headers):
        assert (await client.get(COVERAGE, headers=headers)).status_code == 200


async def test_reading_needs_a_token(client, loaded):
    assert (await client.get(COVERAGE)).status_code == 401


# --- when it cannot run -------------------------------------------------


async def test_a_release_with_no_data_says_so(client, manager_headers, loaded):
    """v4.3's folder was never loaded — 0% would read as a failure."""
    res = await client.get(f"{OTHER_RELEASE}/traceability", headers=manager_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["errorCode"] == "no_data"
    assert body["entries"] == []
    assert body["feature"]["missing"] is True


async def test_a_saved_column_the_workbooks_lost_falls_back_to_the_rule(
        client, admin_headers, manager_headers, loaded):
    """It used to be a hard failure. Now the rule is always available
    underneath, so a stale override degrades to a correct answer."""
    await client.put(CONFIG, headers=admin_headers, json={
        "pattern": "",
        "layers": {"feature": {"column": "gone_away"},
                   "system": {"column": "system_id"}}})
    body = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert body.get("errorCode") is None
    assert body["summary"]["featureTotal"] == 3
    assert _ids(body, "missing_in_system") == ["FL-1003"]


async def test_a_first_column_holding_no_identifiers_is_a_failure_not_100_percent(
        client, admin_headers, manager_headers, loaded):
    """Vacuous arithmetic is the one way a coverage report is worse than
    no report — it would read 100% off data nobody could parse."""
    await client.put(CONFIG, headers=admin_headers, json={
        "pattern": "", "layers": {"feature": {"column": "owner"}}})
    body = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert body["errorCode"] == "no_ids"
    assert "feature" in body["error"]


async def test_an_unknown_release_is_404(client, manager_headers):
    res = await client.get("/api/apps/cellsens/releases/nope/traceability",
                           headers=manager_headers)
    assert res.status_code == 404


async def test_an_unknown_app_is_404(client, manager_headers):
    res = await client.get("/api/apps/nope/releases/v4-4/traceability",
                           headers=manager_headers)
    assert res.status_code == 404


# --- release isolation --------------------------------------------------


async def test_a_configuration_belongs_to_one_release(client, admin_headers,
                                                      manager_headers, loaded):
    """Column shapes differ between releases, so the config cannot be shared."""
    await client.put(CONFIG, headers=admin_headers, json={
        "pattern": r"[A-Za-z]+\-\d+",
        "layers": {"feature": {"column": "feature_id"}}})
    other = (await client.get(f"{OTHER_RELEASE}/traceability/config",
                              headers=manager_headers)).json()
    assert other["configured"] is False


# --- several workbooks per layer ----------------------------------------


async def test_every_workbook_feeding_a_layer_counts(client, admin_headers, qa_headers,
                                                     manager_headers, tmp_path):
    """A layer split across two files covers what both of them cover —
    reading only the newest would silently halve the scope."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    (release_dir / "feature").mkdir(parents=True)
    (release_dir / "feature" / "part-a.xlsx").write_bytes(build_workbook(
        [["Feature ID", "Feature Name"], ["FL-1001", "a"]]))
    (release_dir / "feature" / "part-b.xlsx").write_bytes(build_workbook(
        [["Feature ID", "Feature Name"], ["FL-1002", "b"]]))
    (release_dir / "system.xlsx").write_bytes(build_workbook(
        [["System ID", "Scenario"], ["FL-1001", "x"], ["FL-1002", "y"]]))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await clear_trace_config()
    for layer in ("feature", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.post(f"{RELEASE}/snapshots", headers=qa_headers, json={
        "period": SEPTEMBER,
        "layers": [{"layerId": "feature",
                    "files": ["feature/part-a.xlsx", "feature/part-b.xlsx"]},
                   {"layerId": "system", "files": ["system.xlsx"]}]})

    body = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert body["summary"]["featureTotal"] == 2
    assert body["summary"]["forwardCoveragePct"] == 100.0
    assert sorted(body["feature"]["files"]) == ["feature/part-a.xlsx",
                                                "feature/part-b.xlsx"]

    for layer in ("feature", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- export -------------------------------------------------------------
# CSV EXPORT DISABLED: these two are kept intact alongside the commented-out
# endpoint in routers/traceability.py, and un-skip with it.

csv_disabled = pytest.mark.skip(reason="CSV export is disabled — see routers/traceability.py")


@csv_disabled
async def test_the_matrix_downloads_as_csv(client, manager_headers, loaded):
    res = await client.get(CSV, headers=manager_headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "coverage-cellsens-v4-4.csv" in res.headers["content-disposition"]
    lines = res.text.strip().splitlines()
    assert lines[0].startswith("ID,Status,Duplicate")
    assert any(line.startswith("FL-1003,missing_in_system") for line in lines)
    assert any(line.startswith("FL-2001,missing_in_feature") for line in lines)


@csv_disabled
async def test_csv_refuses_rather_than_exporting_a_meaningless_file(
        client, manager_headers, loaded):
    res = await client.get(f"{OTHER_RELEASE}/traceability.csv", headers=manager_headers)
    assert res.status_code == 409


async def test_preview_reflects_a_pattern_override(client, admin_headers, loaded):
    """The pattern box beside the preview has to change the preview, or the
    number shown disagrees with the report it is meant to explain."""
    base = {"layer": "feature"}
    rule = (await client.get(PREVIEW, headers=admin_headers, params=base)).json()
    narrowed = (await client.get(PREVIEW, headers=admin_headers,
                                 params={**base, "pattern": r"FL-100[12]"})).json()
    assert rule["read"]["distinctIds"] == 3
    assert narrowed["read"]["distinctIds"] == 2
    assert narrowed["read"]["unresolvedRows"] == 1


# --- the identifier column is pickable ----------------------------------


async def test_config_offers_every_column_of_both_sides(client, manager_headers, loaded):
    """The picker needs the whole list, not just the column already read."""
    body = (await client.get(CONFIG, headers=manager_headers)).json()
    assert [c["key"] for c in body["read"]["feature"]["columns"]] == [
        "feature_id", "feature_name", "owner"]
    assert [c["key"] for c in body["read"]["system"]["columns"]] == [
        "system_id", "scenario", "target_date"]


async def test_naming_a_different_identifier_column_changes_the_report(
        client, admin_headers, manager_headers, loaded):
    """Pointing Feature at its name column leaves nothing to match on —
    proof the choice reaches the comparison rather than only the preview."""
    before = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert before["summary"]["covered"] == 2

    res = await client.put(CONFIG, headers=admin_headers, json={
        "pattern": "",
        "layers": {"feature": {"column": "feature_name"}, "system": {"column": ""}}})
    assert res.status_code == 200, res.text

    after = (await client.get(COVERAGE, headers=manager_headers)).json()
    assert after["summary"]["covered"] == 0
