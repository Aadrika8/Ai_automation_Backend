"""Releases: creation, layer structure, and the isolation guarantee.

The point of the feature is that a release is a sealed box — loading v4.4 from
workbooks of a completely different shape must leave v4.3's columns, rows and
dashboard exactly as they were. Most of what follows checks that from a
different angle.

Data gets in one way, the same way it does in production: the server reads the
release's own folder. Each release's folder is <root>/<application>/<release>,
derived from the names unless the release overrides it.
"""
import pytest_asyncio

from app.layer_defaults import DEFAULT_LAYERS
from tests.helpers_xlsx import SIMPLE, build_workbook

OLD, NEW = "v4-3", "v4-4"
OLD_FOLDER, NEW_FOLDER = "v4.3", "v4.4"   # named after the release, not its slug
APP_FOLDER = "cellSens"                   # named after the application


def api(release: str, tail: str = "") -> str:
    return f"/api/apps/cellsens/releases/{release}{tail}"


# a sheet with a different shape entirely: no section rows, four columns,
# two of them numeric — nothing like SIMPLE's spec/tab/count layout
RESHAPED = [
    ["Pattern No.", "OS ver.", "TC count", "Pass"],
    [1, "Win11 Pro 64bit", 57, 54],
    [2, "Win10 Pro 64bit", 43, 41],
]


@pytest_asyncio.fixture
async def catalog(client, admin_headers, tmp_path):
    """Point the Excel root at a temp folder and hand back a helper that
    creates <root>/cellSens/<release folder> on demand."""
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})

    def folder(release_folder: str):
        path = tmp_path / APP_FOLDER / release_folder
        path.mkdir(parents=True, exist_ok=True)
        return path

    yield folder
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


PERIOD = {"year": 2026, "month": 9}


async def load(client, headers, release, folder, layer, rows, period=PERIOD):
    """Write a layer's workbook into a release's folder, then snapshot it."""
    (folder / f"{layer}.xlsx").write_bytes(build_workbook(rows))
    return await client.post(
        api(release, "/snapshots"), headers=headers,
        json={"period": period,
              "layers": [{"layerId": layer, "files": [f"{layer}.xlsx"]}]})


def layer_result(res, layer_id="system"):
    """The outcome for that testing type's one workbook in this suite."""
    return next(f for f in res.json()["files"] if f["layerId"] == layer_id)


@pytest_asyncio.fixture(autouse=True)
async def clean_releases(client, admin_headers):
    """Both seeded releases start and end each test with an empty system layer,
    and any release a test created is removed."""
    async def reset():
        for release in (OLD, NEW):
            await client.delete(api(release, "/layers/system/records"), headers=admin_headers)
        existing = (await client.get("/api/apps/cellsens/releases",
                                     headers=admin_headers)).json()
        for r in existing:
            if r["id"] not in (OLD, NEW):
                await client.delete(api(r["id"]), headers=admin_headers)
        await client.patch(api(NEW), headers=admin_headers, json={"current": True})

    await reset()
    yield
    await reset()


# --- structure ----------------------------------------------------------


async def test_seeded_releases(client, manager_headers):
    releases = (await client.get("/api/apps/cellsens/releases",
                                 headers=manager_headers)).json()
    # newest first — the order the switcher lists them in
    assert [r["id"] for r in releases] == [NEW, OLD]
    assert [r["name"] for r in releases] == ["v4.4", "v4.3"]
    assert [r["current"] for r in releases] == [True, False]
    assert all(r["layerCount"] == len(DEFAULT_LAYERS) for r in releases)
    assert all(r["recordCount"] == 0 for r in releases)

    apps = (await client.get("/api/apps", headers=manager_headers)).json()
    assert apps[0]["releaseCount"] == 2
    assert apps[0]["currentRelease"] == "v4.4"
    assert apps[0]["currentReleaseId"] == NEW


async def test_every_release_has_its_own_pyramid(client, manager_headers):
    for release in (OLD, NEW):
        layers = (await client.get(api(release, "/layers"), headers=manager_headers)).json()
        assert [l["id"] for l in layers] == [d["layerId"] for d in DEFAULT_LAYERS]
        assert [l["order"] for l in layers] == [0, 1, 2, 3, 4]


async def test_create_release_lifecycle(client, admin_headers):
    res = await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                            json={"name": "v5.0", "desc": "Next major"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["id"] == "v5-0"  # slugified, so the URL is stable
    assert body["name"] == "v5.0"
    assert body["current"] is True  # newly created releases are opened by default
    assert body["excelPath"] == ""  # no override: the folder follows the names
    assert body["layerCount"] == len(DEFAULT_LAYERS)
    assert body["recordCount"] == 0

    # creating it demoted the previous current release
    releases = (await client.get("/api/apps/cellsens/releases", headers=admin_headers)).json()
    assert [r["id"] for r in releases] == ["v5-0", NEW, OLD]
    assert [r["current"] for r in releases] == [True, False, False]

    dup = await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                            json={"name": "v5.0"})
    assert dup.status_code == 409

    assert (await client.delete(api("v5-0"), headers=admin_headers)).status_code == 204
    releases = (await client.get("/api/apps/cellsens/releases", headers=admin_headers)).json()
    assert [r["id"] for r in releases] == [NEW, OLD]
    # the application is never left without a release to open
    assert sum(r["current"] for r in releases) == 1


async def test_create_release_without_making_it_current(client, admin_headers):
    res = await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                            json={"name": "v4.5", "makeCurrent": False,
                                  "excelPath": "archive/v4.5"})
    assert res.json()["current"] is False
    assert res.json()["excelPath"] == "archive/v4.5"  # an explicit folder is kept
    releases = (await client.get("/api/apps/cellsens/releases", headers=admin_headers)).json()
    assert next(r for r in releases if r["id"] == NEW)["current"] is True


async def test_rename_keeps_the_id_and_the_data(client, admin_headers, qa_headers, catalog):
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    res = await client.patch(api(OLD), headers=admin_headers,
                             json={"name": "v4.3 (shipped)", "desc": "GA"})
    assert res.status_code == 200
    assert res.json()["id"] == OLD  # renaming must not orphan existing links
    assert res.json()["name"] == "v4.3 (shipped)"
    records = (await client.get(api(OLD, "/layers/system/records"),
                                headers=qa_headers)).json()
    assert records["total"] == 3
    await client.patch(api(OLD), headers=admin_headers, json={"name": "v4.3"})


async def test_promote_an_older_release(client, admin_headers):
    res = await client.patch(api(OLD), headers=admin_headers, json={"current": True})
    assert res.json()["current"] is True
    releases = (await client.get("/api/apps/cellsens/releases", headers=admin_headers)).json()
    assert next(r for r in releases if r["id"] == NEW)["current"] is False
    apps = (await client.get("/api/apps", headers=admin_headers)).json()
    assert apps[0]["currentReleaseId"] == OLD


# --- layer structure per release ----------------------------------------


async def test_copy_layers_from_another_release(client, admin_headers, qa_headers, catalog):
    # give v4.3 an extra layer and some data, then clone its structure
    await client.post(api(OLD, "/layers"), headers=admin_headers,
                      json={"name": "Smoke testing", "order": 1})
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "smoke-testing", SIMPLE)

    res = await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                            json={"name": "v4.6", "copyLayersFrom": OLD})
    assert res.status_code == 201, res.text

    layers = (await client.get(api("v4-6", "/layers"), headers=admin_headers)).json()
    assert [l["id"] for l in layers] == [
        "unit", "smoke-testing", "regression", "feature", "system", "acceptance"]
    # structure only: no rows and no column definitions came across
    assert all(l["recordCount"] == 0 for l in layers)
    body = (await client.get(api("v4-6", "/layers/smoke-testing/records"),
                             headers=admin_headers)).json()
    assert body["total"] == 0 and body["columns"] == [] and body["snapshot"] is None
    # and the source release is untouched
    old = (await client.get(api(OLD, "/layers/smoke-testing/records"),
                            headers=admin_headers)).json()
    assert old["total"] == 3

    await client.delete(api(OLD, "/layers/smoke-testing"), headers=admin_headers)


async def test_copy_from_unknown_release_is_404(client, admin_headers):
    res = await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                            json={"name": "v9.9", "copyLayersFrom": "nope"})
    assert res.status_code == 404


async def test_adding_a_layer_touches_only_its_own_release(client, admin_headers):
    await client.post(api(NEW, "/layers"), headers=admin_headers,
                      json={"name": "Exploratory testing"})
    new_layers = (await client.get(api(NEW, "/layers"), headers=admin_headers)).json()
    old_layers = (await client.get(api(OLD, "/layers"), headers=admin_headers)).json()
    assert "exploratory-testing" in [l["id"] for l in new_layers]
    assert "exploratory-testing" not in [l["id"] for l in old_layers]
    assert len(old_layers) == len(DEFAULT_LAYERS)

    # removing it from v4.4 likewise leaves v4.3 alone
    await client.delete(api(NEW, "/layers/exploratory-testing"), headers=admin_headers)
    assert len((await client.get(api(OLD, "/layers"), headers=admin_headers)).json()) == 5


# --- the isolation guarantee --------------------------------------------


async def test_releases_hold_differently_shaped_data_side_by_side(
        client, qa_headers, catalog):
    """The requirement in one test: two releases, two Excel formats, one layer,
    each read from its own folder."""
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    await load(client, qa_headers, NEW, catalog(NEW_FOLDER), "system", RESHAPED)

    old = (await client.get(api(OLD, "/layers/system/records"), headers=qa_headers)).json()
    new = (await client.get(api(NEW, "/layers/system/records"), headers=qa_headers)).json()

    # each release kept the schema of its own workbook
    assert [c["key"] for c in old["columns"]] == [
        "test_spec_name", "test_spec_tab_name", "test_count"]
    assert [c["key"] for c in new["columns"]] == ["pattern_no", "os_ver", "tc_count", "pass"]
    assert old["total"] == 3 and new["total"] == 2

    # and its own aggregates
    old_dash = (await client.get(api(OLD, "/layers/system/dashboard"),
                                 headers=qa_headers)).json()
    new_dash = (await client.get(api(NEW, "/layers/system/dashboard"),
                                 headers=qa_headers)).json()
    assert old_dash["totals"] == {"test_count": 35}
    assert new_dash["totals"] == {"pattern_no": 3, "tc_count": 100, "pass": 95}

    # per-release record counts roll up to the release list
    releases = (await client.get("/api/apps/cellsens/releases", headers=qa_headers)).json()
    assert {r["id"]: r["recordCount"] for r in releases} == {OLD: 3, NEW: 2}


async def test_identical_rows_coexist_in_two_releases(client, qa_headers, catalog):
    """The same sheet in both release folders: the rowKey uniqueness index is
    scoped per release, so the second load inserts rather than colliding."""
    first = await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    second = await load(client, qa_headers, NEW, catalog(NEW_FOLDER), "system", SIMPLE)
    assert first.status_code == 200 and second.status_code == 200
    assert layer_result(first)["rowCount"] == layer_result(second)["rowCount"] == 3
    for release in (OLD, NEW):
        body = (await client.get(api(release, "/layers/system/records"),
                                 headers=qa_headers)).json()
        assert body["total"] == 3


async def test_reloading_one_release_leaves_the_other_intact(client, qa_headers, catalog):
    """A second load writes a new snapshot in that release and nothing anywhere
    else — including in the release next door."""
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    await load(client, qa_headers, NEW, catalog(NEW_FOLDER), "system", SIMPLE)
    await load(client, qa_headers, NEW, catalog(NEW_FOLDER), "system", RESHAPED)

    old = (await client.get(api(OLD, "/layers/system/records"), headers=qa_headers)).json()
    new = (await client.get(api(NEW, "/layers/system/records"), headers=qa_headers)).json()
    assert old["total"] == 3
    assert new["total"] == 2  # the newer snapshot is what "current" means
    # v4.3 still holds exactly one snapshot, untouched
    history = (await client.get(api(OLD, "/layers/system/snapshots"),
                                headers=qa_headers)).json()
    assert [h["sequence"] for h in history] == [1]


async def test_clearing_a_release_leaves_the_other_intact(
        client, qa_headers, admin_headers, catalog):
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    await load(client, qa_headers, NEW, catalog(NEW_FOLDER), "system", SIMPLE)
    res = await client.delete(api(NEW, "/layers/system/records"), headers=admin_headers)
    assert res.json()["deleted"] == 3
    old = (await client.get(api(OLD, "/layers/system/records"), headers=qa_headers)).json()
    assert old["total"] == 3 and old["columns"] != []


async def test_deleting_a_release_keeps_the_history_of_the_others(
        client, qa_headers, admin_headers, catalog):
    await load(client, qa_headers, OLD, catalog(OLD_FOLDER), "system", SIMPLE)
    await client.post("/api/apps/cellsens/releases", headers=admin_headers,
                      json={"name": "v4.7"})
    await load(client, qa_headers, "v4-7", catalog("v4.7"), "system", RESHAPED)

    assert (await client.delete(api("v4-7"), headers=admin_headers)).status_code == 204
    # its data went with it, and only its data
    old = (await client.get(api(OLD, "/layers/system/records"), headers=qa_headers)).json()
    assert old["total"] == 3
    assert (await client.get(api("v4-7", "/layers/system/records"),
                             headers=qa_headers)).status_code == 404


async def test_deleting_an_application_removes_its_releases(client, admin_headers):
    await client.post("/api/apps", headers=admin_headers, json={"name": "Throwaway"})
    await client.post("/api/apps/throwaway/releases", headers=admin_headers,
                      json={"name": "v1.0"})
    assert (await client.delete("/api/apps/throwaway", headers=admin_headers)).status_code == 204
    assert (await client.get("/api/apps/throwaway/releases",
                             headers=admin_headers)).status_code == 404


async def test_unknown_release_is_404(client, manager_headers):
    for tail in ("/layers", "/source", "/layers/system/records",
                 "/layers/system/dashboard"):
        res = await client.get(api("v9-9", tail), headers=manager_headers)
        assert res.status_code == 404, tail
    res = await client.get(api("v4-4", "/layers/nope/records"), headers=manager_headers)
    assert res.status_code == 404
