"""Path-based Excel sync: discovery, merge semantics, records, dashboard,
clearing and path errors. Replaces the old upload tests — the data now comes
from a configured folder instead of a multipart upload."""
import pytest_asyncio

from tests.helpers_xlsx import REAL_FILE, SIMPLE, build_workbook

SOURCE = "/api/apps/cellsens/source"
SYNC = "/api/apps/cellsens/sync"
RECORDS = "/api/apps/cellsens/layers/system/records"
DASHBOARD = "/api/apps/cellsens/layers/system/dashboard"


def write(folder, rows, name="system.xlsx"):
    """Drop a workbook into the app folder, named after the layer it feeds."""
    (folder / name).write_bytes(build_workbook(rows))


async def sync(client, headers, mode="merge", layers=None):
    body = {"mode": mode}
    if layers is not None:
        body["layers"] = layers
    return await client.post(SYNC, headers=headers, json=body)


def layer_result(res, layer_id="system"):
    return next(l for l in res.json()["layers"] if l["layerId"] == layer_id)


@pytest_asyncio.fixture
async def folder(client, admin_headers, tmp_path):
    """Point the Excel root at a temp dir with a cellsens/ app folder."""
    app_dir = tmp_path / "cellsens"
    app_dir.mkdir()
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.patch("/api/apps/cellsens", headers=admin_headers,
                       json={"excelPath": "cellsens"})
    await client.delete(RECORDS, headers=admin_headers)
    yield app_dir
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
    assert matched["system"]["files"][0]["changed"] is True  # never synced
    assert [f["name"] for f in body["unmatchedFiles"]] == ["unrelated.xlsx"]


async def test_source_reports_conflicts_and_folder_layout(client, qa_headers, folder):
    layer_dir = folder / "system"
    layer_dir.mkdir()
    write(layer_dir, SIMPLE, "part-a.xlsx")
    write(layer_dir, SIMPLE, "part-b.xlsx")
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    system = next(l for l in body["layers"] if l["layerId"] == "system")
    # folder name matches the layer, so both files feed it — and that is a
    # conflict the user has to resolve rather than something to guess at
    assert system["conflict"] is True
    assert sorted(f["relativePath"] for f in system["files"]) == [
        "system/part-a.xlsx", "system/part-b.xlsx"]


# --- ingestion ----------------------------------------------------------


async def test_sync_and_counts(client, qa_headers, folder):
    write(folder, SIMPLE)
    res = await sync(client, qa_headers)
    assert res.status_code == 200, res.text
    body = layer_result(res)
    assert body["totalRows"] == 4
    assert body["inserted"] == 3
    assert body["duplicatesSkipped"] == 1
    assert body["updated"] == body["unchanged"] == 0
    assert body["files"] == ["system.xlsx"]


async def test_resync_unchanged_file_changes_nothing(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    body = layer_result(await sync(client, qa_headers))
    assert body["inserted"] == 0 and body["updated"] == 0 and body["unchanged"] == 3
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 3


async def test_edited_workbook_is_detected_and_updates_in_place(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    status = (await client.get(SOURCE, headers=qa_headers)).json()
    assert next(l for l in status["layers"] if l["layerId"] == "system")["changed"] is False

    changed = [row[:] for row in SIMPLE]
    changed[2] = ["DP23", "(TS) Color", 999]  # same identity, new measure
    write(folder, changed)
    status = (await client.get(SOURCE, headers=qa_headers)).json()
    assert next(l for l in status["layers"] if l["layerId"] == "system")["changed"] is True

    body = layer_result(await sync(client, qa_headers))
    assert body["inserted"] == 0 and body["updated"] == 1 and body["unchanged"] == 2
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    camera = next(s for s in records["sections"] if s["name"] == "Camera testing")
    assert {r["data"]["test_count"] for r in camera["rows"]} == {999, 20}


async def test_new_rows_are_picked_up_without_code_changes(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    grown = [row[:] for row in SIMPLE] + [["New Suite"],
                                          ["Test spec name", "Test spec tab name", "Test Count"],
                                          ["DP99", "(TS) Brand New", 42]]
    write(folder, grown)
    body = layer_result(await sync(client, qa_headers))
    assert body["inserted"] == 1 and body["unchanged"] == 3
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert "New Suite" in [s["name"] for s in records["sections"]]


async def test_several_workbooks_merge_into_one_layer(client, qa_headers, folder):
    layer_dir = folder / "system"
    layer_dir.mkdir()
    write(layer_dir, SIMPLE, "part-a.xlsx")
    write(layer_dir, [
        ["Extra testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ], "part-b.xlsx")
    res = await sync(client, qa_headers, layers=[
        {"layerId": "system", "files": ["system/part-a.xlsx", "system/part-b.xlsx"]}])
    body = layer_result(res)
    assert body["inserted"] == 4  # 3 from the first workbook, 1 from the second
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 4


async def test_replace_mode_wipes_previous_rows(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    write(folder, [
        ["Camera testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ])
    body = layer_result(await sync(client, qa_headers, mode="replace"))
    assert body["inserted"] == 1 and body["updated"] == 0 and body["unchanged"] == 0
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 1


async def test_real_file_end_to_end(client, qa_headers, folder):
    (folder / "system.xlsx").write_bytes(REAL_FILE.read_bytes())
    body = layer_result(await sync(client, qa_headers))
    assert body["totalRows"] == 218
    assert body["inserted"] == 206
    assert body["duplicatesSkipped"] == 12
    dash = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert dash["totals"] == {"test_count": 140736}


# --- unchanged read paths ------------------------------------------------


async def test_records_grouping_search_pagination(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert [s["name"] for s in body["sections"]] == ["Camera testing", "Microscope testing"]
    assert body["sections"][0]["rowCount"] == 2
    assert body["sections"][0]["rows"][1]["data"]["test_spec_name"] == "DP23"

    search = (await client.get(RECORDS, params={"search": "ix73"}, headers=qa_headers)).json()
    assert search["total"] == 1
    numeric = (await client.get(RECORDS, params={"search": "20"}, headers=qa_headers)).json()
    assert numeric["total"] == 1

    paged = (await client.get(RECORDS, params={"pageSize": 2, "page": 2}, headers=qa_headers)).json()
    assert paged["total"] == 3
    assert sum(s["rowCount"] for s in paged["sections"]) == 1


async def test_dashboard_aggregates(client, qa_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    body = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert body["totalRows"] == 3
    assert body["sectionCount"] == 2
    assert body["totals"] == {"test_count": 35}
    camera = next(s for s in body["bySection"] if s["section"] == "Camera testing")
    assert camera["sums"] == {"test_count": 30}
    assert body["topRows"][0]["value"] == 20


async def test_delete_last_load_only(client, qa_headers, admin_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    write(folder, [
        ["Extra testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ], "extra.xlsx")
    await sync(client, qa_headers, layers=[{"layerId": "system", "files": ["extra.xlsx"]}])
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 4

    res = await client.delete(f"{RECORDS}?scope=last", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"deleted": 1, "scope": "last"}
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert records["total"] == 3
    assert records["lastUpload"] is not None
    assert records["columns"] != []


async def test_admin_clear_records(client, qa_headers, admin_headers, folder):
    write(folder, SIMPLE)
    await sync(client, qa_headers)
    res = await client.delete(RECORDS, headers=admin_headers)
    assert res.json()["deleted"] == 3
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert body["total"] == 0 and body["lastUpload"] is None and body["columns"] == []


# --- path errors surface as messages, never stack traces ------------------


async def test_missing_root_is_reported_clearly(client, qa_headers, admin_headers, tmp_path):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path / "nope")})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False
    assert body["errorCode"] == "root_missing"
    assert "does not exist" in body["error"]

    res = await sync(client, qa_headers)
    assert res.status_code == 400
    assert "does not exist" in res.json()["detail"]
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_unconfigured_root_is_reported_clearly(client, qa_headers, admin_headers):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "root_not_configured"


async def test_app_path_cannot_escape_the_root(client, qa_headers, admin_headers, tmp_path):
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.patch("/api/apps/cellsens", headers=admin_headers,
                       json={"excelPath": "../secrets"})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "app_path_invalid"
    await client.patch("/api/apps/cellsens", headers=admin_headers, json={"excelPath": "cellsens"})
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_empty_folder_and_unknown_app(client, qa_headers, admin_headers, tmp_path):
    (tmp_path / "cellsens").mkdir()
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    body = (await client.get(SOURCE, headers=qa_headers)).json()
    assert body["ok"] is False and body["errorCode"] == "no_excel_files"

    assert (await client.get("/api/apps/nope/source", headers=qa_headers)).status_code == 404
    assert (await client.get("/api/apps/UPPER/source", headers=qa_headers)).status_code == 422
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})
