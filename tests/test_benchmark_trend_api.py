"""Automation coverage across releases, over HTTP.

Release against release is measured against measured — the same sheets, the
same rules — so these tests care most that a release's figure in the series is
identical to its figure on its own panel, and that a release without an
automated count is named rather than silently counted as zero.
"""
import pytest
import pytest_asyncio

from tests.helpers_xlsx import build_workbook

TREND = "/api/apps/cellsens/benchmark/automation/trend"
SEPTEMBER = {"year": 2026, "month": 9}


def sheet(total, automated):
    return [["Test spec name", "Test Count", "Automated Test Count"],
            ["Spec A", total, automated]]


@pytest_asyncio.fixture
async def two_releases(client, admin_headers, qa_headers, tmp_path):
    """v4.3 at 10% and v4.4 at 25%, so the series has a real slope."""
    for name, total, automated in (("v4.3", 1000, 100), ("v4.4", 1000, 250)):
        folder = tmp_path / "cellSens" / name
        folder.mkdir(parents=True)
        (folder / "regression.xlsx").write_bytes(build_workbook(sheet(total, automated)))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    for release in ("v4-3", "v4-4"):
        await client.delete(
            f"/api/apps/cellsens/releases/{release}/layers/regression/records",
            headers=admin_headers)
        res = await client.post(f"/api/apps/cellsens/releases/{release}/snapshots",
                                headers=qa_headers, json={"period": SEPTEMBER})
        assert res.status_code == 200, res.text

    yield

    for release in ("v4-3", "v4-4"):
        await client.delete(
            f"/api/apps/cellsens/releases/{release}/layers/regression/records",
            headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


async def test_the_series_runs_oldest_first(client, manager_headers, two_releases):
    """So it reads left to right on a chart without the screen re-sorting it."""
    points = (await client.get(TREND, headers=manager_headers)).json()["points"]
    assert [p["releaseName"] for p in points] == ["v4.3", "v4.4"]
    assert points[0]["order"] < points[1]["order"]


async def test_each_release_carries_its_own_measurement(
        client, manager_headers, two_releases):
    points = (await client.get(TREND, headers=manager_headers)).json()["points"]
    by_name = {p["releaseName"]: p for p in points}
    assert by_name["v4.3"]["coveragePct"] == 10.0
    assert by_name["v4.4"]["coveragePct"] == 25.0
    assert by_name["v4.4"]["automated"] == 250 and by_name["v4.4"]["total"] == 1000


async def test_the_change_from_the_previous_release_is_reported(
        client, manager_headers, two_releases):
    points = (await client.get(TREND, headers=manager_headers)).json()["points"]
    assert points[0]["deltaPct"] is None      # nothing before it
    assert points[1]["deltaPct"] == 15.0
    assert points[1]["comparedTo"] == "v4.3"


async def test_a_release_agrees_with_its_own_panel(client, manager_headers, two_releases):
    """The series and the single-release panel share one measurement path;
    if they ever disagreed, one of them would be lying."""
    series = (await client.get(TREND, headers=manager_headers)).json()["points"]
    in_series = next(p for p in series if p["releaseId"] == "v4-4")

    panel = (await client.get(
        "/api/apps/cellsens/releases/v4-4/benchmark/automation",
        headers=manager_headers)).json()["evident"]

    assert in_series["coveragePct"] == panel["coveragePct"]
    assert in_series["automated"] == panel["automated"]
    assert in_series["total"] == panel["total"]


async def test_which_release_is_current_is_marked(client, manager_headers, two_releases):
    points = (await client.get(TREND, headers=manager_headers)).json()["points"]
    assert sum(1 for p in points if p["current"]) == 1


async def test_layers_without_an_automated_count_are_named_per_release(
        client, manager_headers, two_releases):
    """Only regression was loaded, so the other four are excluded by name."""
    points = (await client.get(TREND, headers=manager_headers)).json()["points"]
    assert "system" in points[-1]["unmeasuredLayers"]


async def test_the_reference_travels_with_the_series_unchanged(
        client, manager_headers, two_releases):
    body = (await client.get(TREND, headers=manager_headers)).json()
    assert body["reference"]["low"] == 33.0 and body["reference"]["high"] == 44.0
    # it is a constant beside the series, never folded into a point
    assert all("low" not in p for p in body["points"])


async def test_how_many_releases_could_be_measured(client, manager_headers, two_releases):
    body = (await client.get(TREND, headers=manager_headers)).json()
    assert body["measuredReleases"] == 2
    assert body["appId"] == "cellsens"


async def test_an_app_with_no_loaded_release_still_answers(client, manager_headers):
    """Every release unmeasured is a finding, not an error."""
    body = (await client.get(TREND, headers=manager_headers)).json()
    assert body["measuredReleases"] == 0
    assert all(p["measured"] is False for p in body["points"])
    assert all(p["deltaPct"] is None for p in body["points"])
    assert body["reference"]["sources"]


async def test_any_authenticated_role_may_read(client, qa_headers, manager_headers):
    for headers in (qa_headers, manager_headers):
        assert (await client.get(TREND, headers=headers)).status_code == 200


async def test_reading_needs_a_token(client):
    assert (await client.get(TREND)).status_code == 401


async def test_an_unknown_app_is_404(client, manager_headers):
    res = await client.get("/api/apps/nope/benchmark/automation/trend",
                           headers=manager_headers)
    assert res.status_code == 404
