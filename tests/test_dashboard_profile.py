"""The dashboard adapting to what a workbook actually holds, over HTTP.

Two things are being protected at once. A sheet that records run outcomes has
to get a dashboard about them — and a sheet that only counts things has to keep
exactly the dashboard it already had, because that view is correct for it and
nothing about this change should move it.
"""
import pytest
import pytest_asyncio

from tests.helpers_xlsx import (INVENTORY_SHEET, RUN_RESULTS, SIMPLE,
                                STATUS_SHEET, build_workbook)

RELEASE = "/api/apps/cellsens/releases/v4-4"
ACCEPTANCE = f"{RELEASE}/layers/acceptance/dashboard"
UNIT = f"{RELEASE}/layers/unit/dashboard"
REGRESSION = f"{RELEASE}/layers/regression/dashboard"
FEATURE = f"{RELEASE}/layers/feature/dashboard"
SEPTEMBER = {"year": 2026, "month": 9}
LAYERS = ("acceptance", "unit", "regression", "feature")


@pytest_asyncio.fixture
async def loaded(client, admin_headers, qa_headers, tmp_path):
    """One workbook of each shape: counted outcomes, counts, status words, a list."""
    folder = tmp_path / "cellSens" / "v4.4"
    folder.mkdir(parents=True)
    (folder / "acceptance.xlsx").write_bytes(build_workbook(RUN_RESULTS))
    (folder / "unit.xlsx").write_bytes(build_workbook(SIMPLE))
    (folder / "regression.xlsx").write_bytes(build_workbook(STATUS_SHEET))
    (folder / "feature.xlsx").write_bytes(build_workbook(INVENTORY_SHEET))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    for layer in LAYERS:
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    res = await client.post(f"{RELEASE}/snapshots", headers=qa_headers,
                            json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text

    yield

    for layer in LAYERS:
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- a counting sheet keeps the dashboard it had --------------------------


async def test_a_counting_workbook_stays_on_the_volume_view(
        client, manager_headers, loaded):
    body = (await client.get(UNIT, headers=manager_headers)).json()
    assert body["profile"]["kind"] == "volume"
    assert body["profile"]["runResults"] is None
    assert body["profile"]["reason"]


async def test_the_volume_response_is_unchanged(client, manager_headers, loaded):
    """Everything the old dashboard returned is still there, saying the same
    thing: the profile is added beside it, never in place of it."""
    body = (await client.get(UNIT, headers=manager_headers)).json()
    assert body["totalRows"] == 3
    assert body["sectionCount"] == 2
    assert [c["label"] for c in body["numericColumns"]] == ["Test count"]
    assert body["totals"]["total_tests"] == 35
    assert {s["section"] for s in body["bySection"]} == {
        "Camera testing", "Microscope testing"}
    assert body["topRows"]


async def test_a_layer_never_loaded_still_answers(client, manager_headers):
    body = (await client.get(f"{RELEASE}/layers/system/dashboard",
                             headers=manager_headers)).json()
    assert body["totalRows"] == 0
    assert body["profile"]["kind"] == "volume"


# --- a results sheet gets a results dashboard -----------------------------


async def test_a_run_results_workbook_is_recognised(client, manager_headers, loaded):
    body = (await client.get(ACCEPTANCE, headers=manager_headers)).json()
    assert body["profile"]["kind"] == "run_results"
    assert "Pass" in body["profile"]["reason"]


async def test_the_figures_are_the_sheet_s_own(client, manager_headers, loaded):
    run = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]["runResults"]
    assert run["passed"] == 88          # 26 + 24 + 18 + 9 + 11
    assert run["failed"] == 5
    assert run["notRun"] == 3
    assert run["total"] == 96           # the sheet's own TC count
    assert run["executed"] == 93        # passed + failed


async def test_the_pass_rate_is_over_what_was_executed(client, manager_headers, loaded):
    run = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]["runResults"]
    assert run["passRatePct"] == 94.6   # 88 / 93, not 88 / 96 (which is 91.7)
    assert run["executedPct"] == 96.9
    assert run["reconciles"] is True


async def test_each_measure_names_the_column_it_came_from(
        client, manager_headers, loaded):
    run = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]["runResults"]
    assert run["passedLabel"] == "Pass"
    assert run["failedLabel"] == "Fail"
    assert run["notRunLabel"] == "NA"
    assert run["totalLabel"] == "TC count"


async def test_the_percentage_column_is_not_the_headline(
        client, manager_headers, loaded):
    """Summed, `%age` comes to 456.9. The old view made that a KPI tile."""
    body = (await client.get(ACCEPTANCE, headers=manager_headers)).json()
    assert body["totals"]["age"] == pytest.approx(456.9)   # still aggregated
    run = body["profile"]["runResults"]
    assert run["passRatePct"] == 94.6                      # but not what is shown


# --- the breakdown --------------------------------------------------------


async def test_it_breaks_down_by_the_richest_repeating_column(
        client, manager_headers, loaded):
    profile = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]
    assert profile["dimension"] == "type"     # 4 install types beats 3 testers
    labels = [d["label"] for d in profile["dimensions"]]
    assert "type" in labels and "Tester" in labels
    assert "Pattern No." not in labels        # numeric, and a serial at that


async def test_each_bucket_carries_its_own_outcome(client, manager_headers, loaded):
    profile = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]
    by_value = {b["value"]: b for b in profile["byDimension"]}
    clean = by_value["Clean Install"]
    assert clean["passed"] == 44 and clean["failed"] == 2      # 26+18, 1+1
    assert clean["rowCount"] == 2
    assert clean["passRatePct"] == pytest.approx(95.7, abs=0.1)


async def test_the_breakdown_can_be_changed(client, manager_headers, loaded):
    body = (await client.get(f"{ACCEPTANCE}?dimension=tester",
                             headers=manager_headers)).json()
    assert body["profile"]["dimension"] == "tester"
    by_value = {b["value"]: b for b in body["profile"]["byDimension"]}
    assert by_value["Rahul"]["passed"] == 33      # 24 + 9
    assert by_value["Meera"]["passed"] == 29      # 18 + 11
    assert by_value["Sreelakshmi"]["passed"] == 26


async def test_an_unknown_breakdown_falls_back_rather_than_erroring(
        client, manager_headers, loaded):
    res = await client.get(f"{ACCEPTANCE}?dimension=not_a_column",
                           headers=manager_headers)
    assert res.status_code == 200
    assert res.json()["profile"]["dimension"] == "type"


# --- where it failed ------------------------------------------------------


async def test_the_rows_that_failed_are_listed_worst_first(
        client, manager_headers, loaded):
    profile = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]
    rows = profile["failingRows"]
    # pattern 4 failed nothing, so it is not here at all
    assert [r["failed"] for r in rows] == [2, 1, 1, 1]
    assert rows[0]["passed"] == 24


async def test_a_failing_row_is_named_by_what_distinguishes_it(
        client, manager_headers, loaded):
    profile = (await client.get(ACCEPTANCE, headers=manager_headers)).json()["profile"]
    assert profile["failingRows"][0]["label"] == "2 · Version Update · Rahul"


# --- access ---------------------------------------------------------------


async def test_any_authenticated_role_may_read(client, qa_headers, manager_headers, loaded):
    for headers in (qa_headers, manager_headers):
        assert (await client.get(ACCEPTANCE, headers=headers)).status_code == 200


async def test_reading_needs_a_token(client, loaded):
    assert (await client.get(ACCEPTANCE)).status_code == 401


# --- a sheet whose outcome is a word per row ------------------------------


async def test_a_status_workbook_is_recognised(client, manager_headers, loaded):
    body = (await client.get(REGRESSION, headers=manager_headers)).json()
    assert body["profile"]["kind"] == "status"
    assert "Status" in body["profile"]["reason"]
    assert body["profile"]["runResults"] is None


async def test_the_rate_is_weighted_by_the_test_cases_behind_each_row(
        client, manager_headers, loaded):
    """Three passing rows of four is 75%. The 44 test cases they carry out of
    53 is 83%, and that is the claim the sheet actually supports."""
    s = (await client.get(REGRESSION, headers=manager_headers)).json()["profile"]["status"]
    assert s["basis"] == "measure"
    assert s["measureLabel"] == "Test Cases"
    assert s["passed"] == 44 and s["failed"] == 9
    assert s["decided"] == 53
    assert s["passRatePct"] == 83.0
    assert s["rowCount"] == 4


async def test_every_status_value_is_listed_with_its_kind(
        client, manager_headers, loaded):
    s = (await client.get(REGRESSION, headers=manager_headers)).json()["profile"]["status"]
    by_value = {b["value"]: b for b in s["statuses"]}
    assert by_value["Passed"]["kind"] == "passing"
    assert by_value["Passed"]["rowCount"] == 3
    assert by_value["Passed"]["measure"] == 44
    assert by_value["Failed"]["kind"] == "failing"
    assert by_value["Failed"]["measure"] == 9


async def test_the_status_column_is_not_offered_as_a_breakdown(
        client, manager_headers, loaded):
    """Breaking the status down by the status would say nothing."""
    profile = (await client.get(REGRESSION, headers=manager_headers)).json()["profile"]
    assert "status" not in [d["key"] for d in profile["dimensions"]]


async def test_the_rows_still_open_are_listed(client, manager_headers, loaded):
    profile = (await client.get(REGRESSION, headers=manager_headers)).json()["profile"]
    rows = profile["openRows"]
    assert len(rows) == 1
    assert rows[0]["status"] == "Failed"
    assert rows[0]["measure"] == 9
    # named by the sheet's own id and its description, not every text column
    assert rows[0]["label"] == "REG-1003 · Export workflow"


async def test_the_counted_outcomes_sheet_is_unaffected(
        client, manager_headers, loaded):
    """Acceptance carries Pass and Fail as numbers, so it stays on the run
    profile even now that status words are recognised."""
    body = (await client.get(ACCEPTANCE, headers=manager_headers)).json()
    assert body["profile"]["kind"] == "run_results"
    assert body["profile"]["status"] is None


# --- a sheet that lists things rather than counting them ------------------


async def test_a_workbook_with_no_measure_is_an_inventory(
        client, manager_headers, loaded):
    body = (await client.get(FEATURE, headers=manager_headers)).json()
    assert body["profile"]["kind"] == "inventory"
    assert body["numericColumns"] == []      # nothing to sum, which is the point
    assert "ID" in body["profile"]["reason"]


async def test_it_counts_what_the_sheet_lists(client, manager_headers, loaded):
    inv = (await client.get(FEATURE, headers=manager_headers)).json()["profile"]["inventory"]
    assert inv["items"] == 3
    assert inv["family"] == "FL"
    assert inv["idLabel"] == "ID"
    assert inv["unreadable"] == 0 and inv["duplicates"] == 0


async def test_it_counts_the_work_named_behind_each_entry(
        client, manager_headers, loaded):
    """The continuation rows are joined onto their feature by the parser, so
    all five CS ids have to reach the dashboard."""
    inv = (await client.get(FEATURE, headers=manager_headers)).json()["profile"]["inventory"]
    assert inv["linkFamily"] == "CS"
    assert inv["links"] == 5
    assert inv["linked"] == 2 and inv["unlinked"] == 1
    assert inv["linkedPct"] == 66.7


async def test_the_entry_naming_nothing_is_listed(client, manager_headers, loaded):
    inv = (await client.get(FEATURE, headers=manager_headers)).json()["profile"]["inventory"]
    assert [i["id"] for i in inv["unlinkedItems"]] == ["FL-5807"]
    assert inv["unlinkedItems"][0]["detail"] == "Cicero Spinning Disk (CREST)"


async def test_entries_are_ranked_by_the_work_they_carry(
        client, manager_headers, loaded):
    inv = (await client.get(FEATURE, headers=manager_headers)).json()["profile"]["inventory"]
    assert [(i["id"], i["links"]) for i in inv["mostLinked"]] == [
        ("FL-5773", 3), ("FL-5776", 2)]
