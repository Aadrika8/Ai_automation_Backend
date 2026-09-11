"""Loading a release's folder into snapshots: discovery, snapshot creation,
history, deletion, and the path errors that surface as messages.

Every load records a complete, read-only snapshot of one *workbook*. Files are
never combined, so a testing type fed by two workbooks holds two independent
datasets. There is no merge-or-replace choice — loading again writes a new
snapshot for that file, or nothing at all when its data has not changed.
"""
from datetime import datetime, timezone

import pytest_asyncio

from tests.helpers_xlsx import REAL_FILE, SIMPLE, build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
SOURCE = f"{RELEASE}/source"
SNAPSHOTS = f"{RELEASE}/snapshots"
LAYER = f"{RELEASE}/layers/system"
RECORDS = f"{LAYER}/records"
DASHBOARD = f"{LAYER}/dashboard"
HISTORY = f"{LAYER}/snapshots"

SEPTEMBER = {"year": 2026, "month": 9}
OCTOBER = {"year": 2026, "month": 10}

SMALLER = [
    ["Camera testing"],
    ["Test spec name", "Test spec tab name", "Test Count"],
    ["DP99", "(TS) Fresh", 7],
]


def write(folder, rows, name="system.xlsx"):
    """Drop a workbook into a release folder, named after the layer it feeds."""
    (folder / name).write_bytes(build_workbook(rows))


async def load(client, headers, layers=None, period=SEPTEMBER, url=SNAPSHOTS):
    body: dict = {}
    if period is not None:
        body["period"] = period
    if layers is not None:
        body["layers"] = layers
    return await client.post(url, headers=headers, json=body)


def file_result(res, file="system.xlsx"):
    """The outcome for one workbook — a load reports one entry per file."""
    return next(f for f in res.json()["files"] if f["file"] == file)


@pytest_asyncio.fixture
async def folder(client, admin_headers, tmp_path):
    """Point the Excel root at <tmp>, so the seeded v4.4 release reads
    <tmp>/cellSens/v4.4 — <application name>/<release name>, no other
    configuration. Workbooks written here belong to that release alone."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    release_dir.mkdir(parents=True)
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.delete(RECORDS, headers=admin_headers)
    yield release_dir
    await client.delete(RECORDS, headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- discovery ----------------------------------------------------------


async def test_source_matches_workbooks_to_layers(client, qa_headers, folder):
    write(folder, SIMPLE, "system.xlsx")
    write(folder, SIMPLE, "regression.xlsx")
    write(folder, SIMPLE, "unrelated.xlsx")  # matches no layer
    (folder / "notes.txt").write_text("not a workbook")

    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is True
    matched = {l["layerId"]: l for l in body["layers"] if l["files"]}
    assert set(matched) == {"system", "regression"}
    assert matched["system"]["files"][0]["name"] == "system.xlsx"
    assert matched["system"]["files"][0]["changed"] is True  # never loaded
    assert [f["name"] for f in body["unmatchedFiles"]] == ["unrelated.xlsx"]


async def test_a_folder_per_layer_offers_every_file_in_it(client, qa_headers, folder):
    layer_dir = folder / "system"
    layer_dir.mkdir()
    write(layer_dir, SIMPLE, "part-a.xlsx")
    write(layer_dir, SIMPLE, "part-b.xlsx")
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    system = next(l for l in body["layers"] if l["layerId"] == "system")
    # the folder name matches the layer, so both files feed it — which is
    # ordinary now: each becomes its own dataset, nothing to resolve
    assert sorted(f["relativePath"] for f in system["files"]) == [
        "system/part-a.xlsx", "system/part-b.xlsx"]
    assert "conflict" not in system


# --- creating snapshots --------------------------------------------------


async def test_first_load_creates_a_snapshot(client, qa_headers, folder):
    write(folder, SIMPLE)
    res = await load(client, qa_headers)
    assert res.status_code == 200, res.text
    body = file_result(res)
    assert body["created"] is True
    assert body["sequence"] == 1
    assert body["rowCount"] == 3
    assert body["totalRows"] == 4
    assert body["duplicatesSkipped"] == 1
    assert body["fileName"] == "system.xlsx"
    # everything is new against nothing
    assert body["diff"] == {"comparable": True, "comparedTo": None,
                            "added": 3, "changed": 0, "removed": 0, "reason": None}
    assert res.json()["period"] == SEPTEMBER


async def test_unchanged_data_records_nothing(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    again = file_result(await load(client, qa_headers))
    assert again["created"] is False
    assert "No change" in again["reason"]
    assert again["snapshotId"] is None
    history = (await client.get(HISTORY, headers=qa_headers)).json()
    assert len(history) == 1


async def test_changed_data_records_a_second_snapshot(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)

    changed = [row[:] for row in SIMPLE]
    changed[2] = ["DP23", "(TS) Color", 999]  # same identity, new measure
    changed.append(["DP99", "(TS) Brand New", 42])  # a new row
    write(folder, changed)

    body = file_result(await load(client, qa_headers))
    assert body["created"] is True and body["sequence"] == 2
    assert body["diff"]["added"] == 1
    assert body["diff"]["changed"] == 1
    assert body["diff"]["removed"] == 0
    assert body["diff"]["comparedTo"] == 1

    history = (await client.get(HISTORY, headers=qa_headers)).json()
    assert [h["sequence"] for h in history] == [2, 1]
    assert [h["isCurrent"] for h in history] == [True, False]


async def test_the_earlier_snapshot_keeps_its_numbers(client, qa_headers, folder):
    """The point of the whole design: loading again must not rewrite history."""
    write(folder, SIMPLE)
    await load(client, qa_headers)
    first = (await client.get(HISTORY, headers=qa_headers)).json()[0]

    write(folder, SMALLER)
    await load(client, qa_headers)

    old = (await client.get(f"{RECORDS}?snapshot={first['id']}",
                            headers=qa_headers)).json()
    new = (await client.get(RECORDS, headers=qa_headers)).json()
    assert old["total"] == 3            # as it was taken
    assert new["total"] == 1            # the sheet shrank; current follows it
    assert old["snapshot"]["isCurrent"] is False
    assert new["snapshot"]["isCurrent"] is True

    old_dash = (await client.get(f"{DASHBOARD}?snapshot={first['id']}",
                                 headers=qa_headers)).json()
    assert old_dash["totals"] == {"total_tests": 35}
    assert (await client.get(DASHBOARD, headers=qa_headers)).json()["totals"] == {
        "total_tests": 7}


async def test_rows_removed_from_the_sheet_leave_the_current_snapshot(
        client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    write(folder, SMALLER)
    body = file_result(await load(client, qa_headers))
    assert body["diff"] == {"comparable": True, "comparedTo": 1,
                            "added": 1, "changed": 0, "removed": 3, "reason": None}


async def test_several_workbooks_stay_separate(client, qa_headers, folder):
    """The core guarantee: two files feeding one testing type are two
    datasets, each with its own snapshot, records and count."""
    layer_dir = folder / "system"
    layer_dir.mkdir()
    write(layer_dir, SIMPLE, "part-a.xlsx")
    write(layer_dir, SMALLER, "part-b.xlsx")
    res = await load(client, qa_headers, layers=[
        {"layerId": "system", "files": ["system/part-a.xlsx", "system/part-b.xlsx"]}])

    a = file_result(res, "system/part-a.xlsx")
    b = file_result(res, "system/part-b.xlsx")
    assert a["created"] is True and b["created"] is True
    assert a["rowCount"] == 3 and b["rowCount"] == 1      # never 4
    assert a["sequence"] == b["sequence"] == 1            # each numbered its own

    files = (await client.get(f"{LAYER}/files", headers=qa_headers)).json()
    assert sorted(f["file"] for f in files) == [
        "system/part-a.xlsx", "system/part-b.xlsx"]
    assert {f["file"]: f["rowCount"] for f in files} == {
        "system/part-a.xlsx": 3, "system/part-b.xlsx": 1}

    # and the records behind each are that file's alone
    for path, expected in (("system/part-a.xlsx", 3), ("system/part-b.xlsx", 1)):
        body = (await client.get(f"{RECORDS}?file={path}", headers=qa_headers)).json()
        assert body["total"] == expected
        assert body["snapshot"]["file"] == path


async def test_a_file_arriving_later_starts_its_own_series(
        client, qa_headers, folder):
    """A workbook added to the folder later gets its own #1 — it does not
    disturb, or join, the file that was already there."""
    layer_dir = folder / "system"
    layer_dir.mkdir()
    write(layer_dir, SIMPLE, "part-a.xlsx")
    first = file_result(await load(client, qa_headers, layers=[
        {"layerId": "system", "files": ["system/part-a.xlsx"]}]), "system/part-a.xlsx")
    assert first["rowCount"] == 3 and first["sequence"] == 1

    write(layer_dir, SMALLER, "part-b.xlsx")
    res = await load(client, qa_headers, layers=[
        {"layerId": "system",
         "files": ["system/part-a.xlsx", "system/part-b.xlsx"]}])
    assert file_result(res, "system/part-a.xlsx")["created"] is False   # unchanged
    b = file_result(res, "system/part-b.xlsx")
    assert b["created"] is True and b["sequence"] == 1 and b["rowCount"] == 1

    a_history = (await client.get(f"{HISTORY}?file=system/part-a.xlsx",
                                  headers=qa_headers)).json()
    b_history = (await client.get(f"{HISTORY}?file=system/part-b.xlsx",
                                  headers=qa_headers)).json()
    assert [h["rowCount"] for h in a_history] == [3]
    assert [h["rowCount"] for h in b_history] == [1]


async def test_real_file_end_to_end(client, qa_headers, folder):
    (folder / "system.xlsx").write_bytes(REAL_FILE.read_bytes())
    body = file_result(await load(client, qa_headers))
    assert body["totalRows"] == 218
    assert body["rowCount"] == 206
    assert body["duplicatesSkipped"] == 12
    dash = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert dash["totals"] == {"total_tests": 140736}


# --- periods -------------------------------------------------------------


async def test_period_defaults_to_the_current_month(client, qa_headers, folder):
    write(folder, SIMPLE)
    res = await load(client, qa_headers, period=None)
    now = datetime.now(timezone.utc)
    assert res.json()["period"] == {"year": now.year, "month": now.month}


async def test_two_loads_in_one_month_both_stay(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers, period=SEPTEMBER)
    write(folder, SMALLER)
    await load(client, qa_headers, period=SEPTEMBER)

    history = (await client.get(HISTORY, headers=qa_headers)).json()
    assert [h["period"] for h in history] == [SEPTEMBER, SEPTEMBER]
    # the month resolves to the last load in it
    by_month = (await client.get(f"{RECORDS}?month=2026-09", headers=qa_headers)).json()
    assert by_month["total"] == 1
    assert by_month["snapshot"]["sequence"] == 2


async def test_months_are_read_separately(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers, period=SEPTEMBER)
    write(folder, SMALLER)
    await load(client, qa_headers, period=OCTOBER)

    september = (await client.get(f"{RECORDS}?month=2026-09", headers=qa_headers)).json()
    october = (await client.get(f"{RECORDS}?month=2026-10", headers=qa_headers)).json()
    assert september["total"] == 3
    assert october["total"] == 1
    empty = (await client.get(f"{RECORDS}?month=2026-01", headers=qa_headers)).json()
    assert empty["total"] == 0 and empty["snapshot"] is None


async def test_a_snapshots_period_can_be_corrected(client, qa_headers, admin_headers,
                                                   folder):
    write(folder, SIMPLE)
    await load(client, qa_headers, period=SEPTEMBER)
    snapshot = (await client.get(HISTORY, headers=qa_headers)).json()[0]

    res = await client.patch(f"{RELEASE}/snapshots/{snapshot['id']}",
                             headers=admin_headers, json={"period": OCTOBER})
    assert res.status_code == 200
    assert res.json()["period"] == OCTOBER
    # the rows moved with it, and their values did not change
    moved = (await client.get(f"{RECORDS}?month=2026-10", headers=qa_headers)).json()
    assert moved["total"] == 3
    assert (await client.get(f"{RECORDS}?month=2026-09",
                             headers=qa_headers)).json()["total"] == 0


async def test_a_bad_month_is_rejected(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    assert (await client.get(f"{RECORDS}?month=2026-13",
                             headers=qa_headers)).status_code == 422
    assert (await client.post(SNAPSHOTS, headers=qa_headers,
                              json={"period": {"year": 2026, "month": 0}})).status_code == 422


# --- deleting a bad snapshot ---------------------------------------------


async def test_deleting_a_snapshot_promotes_the_one_before_it(
        client, qa_headers, admin_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    write(folder, SMALLER)          # pretend this load was the wrong file
    await load(client, qa_headers)

    history = (await client.get(HISTORY, headers=qa_headers)).json()
    wrong = history[0]
    assert wrong["sequence"] == 2

    res = await client.delete(f"{RELEASE}/snapshots/{wrong['id']}", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["deleted"] == 1

    left = (await client.get(HISTORY, headers=qa_headers)).json()
    assert [h["sequence"] for h in left] == [1]
    assert left[0]["isCurrent"] is True
    current = (await client.get(RECORDS, headers=qa_headers)).json()
    assert current["total"] == 3    # the good snapshot is current again


async def test_corrected_data_loads_as_the_next_snapshot(
        client, qa_headers, admin_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    write(folder, SMALLER)
    await load(client, qa_headers)
    wrong = (await client.get(HISTORY, headers=qa_headers)).json()[0]
    await client.delete(f"{RELEASE}/snapshots/{wrong['id']}", headers=admin_headers)

    corrected = [row[:] for row in SIMPLE] + [["DP77", "(TS) Corrected", 3]]
    write(folder, corrected)
    body = file_result(await load(client, qa_headers))
    assert body["created"] is True
    # the deleted load never should have existed, so the correction takes its
    # number: this really is the second snapshot of this testing type
    assert body["sequence"] == 2
    # and it is compared against #1, the snapshot it now follows
    assert body["diff"]["comparedTo"] == 1
    assert body["diff"]["added"] == 1
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 4


async def test_deleting_an_unknown_snapshot_is_404(client, admin_headers):
    res = await client.delete(f"{RELEASE}/snapshots/nope", headers=admin_headers)
    assert res.status_code == 404


async def test_clearing_a_layer_removes_all_of_its_snapshots(
        client, qa_headers, admin_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    write(folder, SMALLER)
    await load(client, qa_headers)
    res = await client.delete(RECORDS, headers=admin_headers)
    assert res.json()["deleted"] == 4    # 3 rows + 1 row across two snapshots
    assert (await client.get(HISTORY, headers=qa_headers)).json() == []
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert body["total"] == 0 and body["snapshot"] is None and body["columns"] == []


# --- unchanged read paths ------------------------------------------------


async def test_records_grouping_search_pagination(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert [s["name"] for s in body["sections"]] == ["Camera testing", "Microscope testing"]
    assert body["sections"][0]["rowCount"] == 2
    assert body["sections"][0]["rows"][1]["data"]["test_spec_name"] == "DP23"

    search = (await client.get(RECORDS, params={"search": "ix73"}, headers=qa_headers)).json()
    assert search["total"] == 1
    numeric = (await client.get(RECORDS, params={"search": "20"}, headers=qa_headers)).json()
    assert numeric["total"] == 1

    paged = (await client.get(RECORDS, params={"pageSize": 2, "page": 2},
                              headers=qa_headers)).json()
    assert paged["total"] == 3
    assert sum(s["rowCount"] for s in paged["sections"]) == 1


async def test_dashboard_aggregates(client, qa_headers, folder):
    write(folder, SIMPLE)
    await load(client, qa_headers)
    body = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert body["totalRows"] == 3
    assert body["sectionCount"] == 2
    assert body["totals"] == {"total_tests": 35}
    camera = next(s for s in body["bySection"] if s["section"] == "Camera testing")
    assert camera["sums"] == {"total_tests": 30}
    assert body["topRows"][0]["value"] == 20
    assert body["snapshot"]["sequence"] == 1


async def test_a_layer_never_loaded_reads_as_empty(client, qa_headers):
    body = (await client.get(f"{RELEASE}/layers/unit/records", headers=qa_headers)).json()
    assert body["total"] == 0 and body["snapshot"] is None and body["columns"] == []
    dash = (await client.get(f"{RELEASE}/layers/unit/dashboard", headers=qa_headers)).json()
    assert dash["totalRows"] == 0 and dash["snapshot"] is None


# --- per-release folders --------------------------------------------------


OLD_RELEASE = "v4-3"
OTHER_SHAPE = [
    ["Pattern No.", "OS ver.", "TC count"],
    [1, "Win11 Pro 64bit", 57],
]


async def test_each_release_reads_only_its_own_folder(
        client, qa_headers, admin_headers, folder):
    """`folder` is <root>/cellSens/v4.4. Give v4.3 a folder of its own, holding
    a different workbook for a different layer, and check neither release can
    see the other's files."""
    old_dir = folder.parent / "v4.3"
    old_dir.mkdir()
    write(folder, SIMPLE, "system.xlsx")
    write(old_dir, OTHER_SHAPE, "regression.xlsx")

    new_status = (await client.get(SOURCE, headers=qa_headers)).json()
    old_status = (await client.get(
        f"/api/apps/cellsens/releases/{OLD_RELEASE}/source", headers=qa_headers)).json()
    assert new_status["ok"] and old_status["ok"]
    assert new_status["resolvedPath"].endswith("v4.4")
    assert old_status["resolvedPath"].endswith("v4.3")
    assert new_status["releaseName"] == "v4.4"
    assert {l["layerId"] for l in new_status["layers"] if l["files"]} == {"system"}
    assert {l["layerId"] for l in old_status["layers"] if l["files"]} == {"regression"}

    # loading v4.3 leaves v4.4 empty, and gives v4.3 its own column schema
    res = await load(client, qa_headers,
                     url=f"/api/apps/cellsens/releases/{OLD_RELEASE}/snapshots")
    assert res.status_code == 200, res.text
    assert res.json()["releaseId"] == OLD_RELEASE
    old_records = (await client.get(
        f"/api/apps/cellsens/releases/{OLD_RELEASE}/layers/regression/records",
        headers=qa_headers)).json()
    assert old_records["total"] == 1
    assert [c["key"] for c in old_records["columns"]] == ["pattern_no", "os_ver", "tc_count"]
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 0

    await client.delete(
        f"/api/apps/cellsens/releases/{OLD_RELEASE}/layers/regression/records",
        headers=admin_headers)


# --- path errors surface as messages, never stack traces ------------------


async def test_missing_root_is_reported_clearly(client, qa_headers, admin_headers, tmp_path):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path / "nope")})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False
    assert body["errorCode"] == "root_missing"
    assert "does not exist" in body["error"]

    res = await load(client, qa_headers)
    assert res.status_code == 400
    assert "does not exist" in res.json()["detail"]
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_unconfigured_root_is_reported_clearly(client, qa_headers, admin_headers):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "root_not_configured"


async def test_folder_cannot_escape_the_root(client, qa_headers, admin_headers, tmp_path):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.patch(RELEASE, headers=admin_headers, json={"excelPath": "../secrets"})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "folder_invalid"
    await client.patch(RELEASE, headers=admin_headers, json={"excelPath": ""})
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_empty_folder_and_unknown_app(client, qa_headers, admin_headers, tmp_path):
    (tmp_path / "cellSens" / "v4.4").mkdir(parents=True)
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "no_excel_files"

    assert (await client.get("/api/apps/nope/releases/v4-4/source",
                             headers=qa_headers)).status_code == 404
    assert (await client.get("/api/apps/UPPER/releases/v4-4/source",
                             headers=qa_headers)).status_code == 422
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_missing_release_folder_is_reported_clearly(
        client, qa_headers, admin_headers, tmp_path):
    (tmp_path / "cellSens").mkdir()  # app folder exists, release folder does not
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "folder_missing"
    assert "v4.4" in body["error"]
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_an_override_folder_is_used_instead_of_the_default(
        client, qa_headers, admin_headers, tmp_path):
    """A release whose workbooks live somewhere else carries its own path —
    the only per-release configuration there is."""
    elsewhere = tmp_path / "archive" / "cellsens-44"
    elsewhere.mkdir(parents=True)
    write(elsewhere, SIMPLE, "system.xlsx")
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.patch(RELEASE, headers=admin_headers,
                       json={"excelPath": "archive/cellsens-44"})

    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is True
    assert body["customFolder"] is True
    assert body["folder"] == "archive/cellsens-44"
    assert body["resolvedPath"].endswith("cellsens-44")

    await client.patch(RELEASE, headers=admin_headers, json={"excelPath": ""})
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})
