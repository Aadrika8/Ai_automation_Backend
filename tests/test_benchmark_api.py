"""Automation coverage over HTTP.

The endpoint answers whether or not Evident can be measured: the industry
reference is curated, so it reads even when no workbook carries an automated
count. "We do not measure this" is a finding, not an empty response.
"""
import pytest
import pytest_asyncio

from tests.helpers_xlsx import build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
AUTOMATION = f"{RELEASE}/benchmark/automation"
SEPTEMBER = {"year": 2026, "month": 9}

# One spec row covers many tests, so the automated figure is a count, not a flag.
WITH_AUTOMATION = [
    ["Test spec name", "Test Count", "Automated Test Count"],
    ["DP23 Colour", 1000, 250],
    ["DP23 Gray", 3000, 150],
]
WITHOUT_AUTOMATION = [
    ["Test spec name", "Test Count"],
    ["Licensing", 500],
]


@pytest_asyncio.fixture
async def loaded(client, admin_headers, qa_headers, tmp_path):
    """v4.4 with an automated count in regression and none in system."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    release_dir.mkdir(parents=True)
    (release_dir / "regression.xlsx").write_bytes(build_workbook(WITH_AUTOMATION))
    (release_dir / "system.xlsx").write_bytes(build_workbook(WITHOUT_AUTOMATION))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    for layer in ("regression", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    res = await client.post(f"{RELEASE}/snapshots", headers=qa_headers,
                            json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text

    yield release_dir

    for layer in ("regression", "system"):
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


async def test_the_release_figure_is_count_weighted(client, manager_headers, loaded):
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    evident = body["evident"]
    assert evident["measured"] is True
    assert evident["automated"] == 400      # 250 + 150
    assert evident["total"] == 4000         # 1000 + 3000
    assert evident["coveragePct"] == 10.0
    assert evident["basis"] == "test_count"


async def test_a_layer_without_the_column_is_named_not_counted(
        client, manager_headers, loaded):
    """System holds 500 tests but records no automated count; including it
    would report a coverage it never claimed."""
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    assert "system" in body["evident"]["unmeasuredLayers"]
    assert body["evident"]["total"] == 4000

    system = next(l for l in body["evident"]["layers"] if l["layerId"] == "system")
    assert system["measured"] is False
    assert system["coveragePct"] is None
    assert "no automated-count column" in system["reason"]


async def test_each_layer_reports_the_columns_it_was_read_from(
        client, manager_headers, loaded):
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    regression = next(l for l in body["evident"]["layers"] if l["layerId"] == "regression")
    assert regression["automatedLabel"] == "Automated Test Count"
    assert regression["totalLabel"] == "Test Count"
    assert regression["basisLabel"] == "Test Count"


async def test_the_reference_is_returned_beside_the_measurement(
        client, manager_headers, loaded):
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    reference = body["reference"]
    assert reference["low"] == 33.0 and reference["high"] == 44.0
    assert len(reference["sources"]) == 3
    assert reference["scope"] == "all-industry, cross-sector"


async def test_the_reference_declares_its_own_limits(client, manager_headers, loaded):
    reference = (await client.get(AUTOMATION, headers=manager_headers)).json()["reference"]
    assert reference["synthesised"] is True
    assert reference["selfReported"] is True
    assert reference["lifeScienceSpecific"] is False
    assert reference["caveats"]


async def test_every_source_names_its_publisher(client, manager_headers, loaded):
    reference = (await client.get(AUTOMATION, headers=manager_headers)).json()["reference"]
    assert all(s["publisher"] for s in reference["sources"])
    assert {s["value"] for s in reference["sources"]} == {33.0, 40.0, 44.0}


async def test_the_distance_is_whole_points(client, manager_headers, loaded):
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    assert body["distance"]["position"] == "below"
    assert body["distance"]["points"] == 23          # 33 − 10
    assert isinstance(body["distance"]["points"], int)


async def test_the_two_sides_stay_apart_in_the_response(client, manager_headers, loaded):
    """Nothing merges them: the measurement holds no reference values, and the
    reference holds no measured ones."""
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    assert "low" not in body["evident"] and "high" not in body["evident"]
    assert "coveragePct" not in body["reference"]


async def test_a_release_with_no_data_is_unmeasured_but_still_shows_the_reference(
        client, manager_headers):
    """v4.3 was never loaded — the reference must still read."""
    body = (await client.get("/api/apps/cellsens/releases/v4-3/benchmark/automation",
                             headers=manager_headers)).json()
    assert body["evident"]["measured"] is False
    assert body["evident"]["coveragePct"] is None
    assert body["distance"]["position"] == "unmeasured"
    assert body["reference"]["low"] == 33.0


async def test_which_release_and_month_the_figure_came_from(
        client, manager_headers, loaded):
    body = (await client.get(AUTOMATION, headers=manager_headers)).json()
    assert body["releaseId"] == "v4-4"
    assert body["latestPeriod"] == SEPTEMBER


async def test_any_authenticated_role_may_read(client, qa_headers, manager_headers, loaded):
    for headers in (qa_headers, manager_headers):
        assert (await client.get(AUTOMATION, headers=headers)).status_code == 200


async def test_reading_needs_a_token(client, loaded):
    assert (await client.get(AUTOMATION)).status_code == 401


async def test_an_unknown_release_is_404(client, manager_headers):
    res = await client.get("/api/apps/cellsens/releases/nope/benchmark/automation",
                           headers=manager_headers)
    assert res.status_code == 404


async def test_an_unknown_app_is_404(client, manager_headers):
    res = await client.get("/api/apps/nope/releases/v4-4/benchmark/automation",
                           headers=manager_headers)
    assert res.status_code == 404
