"""Excel upload, merge semantics, records, dashboard and clearing.

SKIPPED: browser upload is disabled — data is read from each release's folder
instead (see tests/test_sync.py and tests/test_releases.py, which cover the
same merge, replace, records and clearing semantics through that path). These
tests are kept intact so they can be un-skipped along with the endpoint in
app/routers/apps.py.
"""
import pytest
import pytest_asyncio

pytestmark = pytest.mark.skip(reason="browser upload is disabled; see app/routers/apps.py")

from tests.helpers_xlsx import REAL_FILE, SIMPLE, build_workbook


def xlsx(payload: bytes, name: str = "data.xlsx"):
    return {"file": (name, payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


UPLOAD = "/api/apps/cellsens/releases/v4-4/layers/system/uploads"
RECORDS = "/api/apps/cellsens/releases/v4-4/layers/system/records"
DASHBOARD = "/api/apps/cellsens/releases/v4-4/layers/system/dashboard"


@pytest_asyncio.fixture(autouse=True)
async def clean_layer(client, admin_headers):
    """Each test starts and ends with an empty 'system' layer."""
    await client.delete(RECORDS, headers=admin_headers)
    yield
    await client.delete(RECORDS, headers=admin_headers)


async def test_upload_and_counts(client, qa_headers):
    res = await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["totalRows"] == 4
    assert body["inserted"] == 3
    assert body["duplicatesSkipped"] == 1
    assert body["updated"] == body["unchanged"] == 0
    assert body["uploadedBy"] == "qa"
    assert [c["key"] for c in body["columns"]] == [
        "test_spec_name", "test_spec_tab_name", "test_count"]


async def test_reupload_same_file_changes_nothing(client, qa_headers):
    payload = build_workbook(SIMPLE)
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(payload))
    res = await client.post(UPLOAD, headers=qa_headers, files=xlsx(payload))
    body = res.json()
    assert body["inserted"] == 0 and body["updated"] == 0 and body["unchanged"] == 3
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert records["total"] == 3


async def test_reupload_with_changed_count_updates_in_place(client, qa_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    changed = [row[:] if isinstance(row, list) else row for row in SIMPLE]
    changed[2] = ["DP23", "(TS) Color", 999]  # same identity, new measure
    res = await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(changed)))
    body = res.json()
    assert body["inserted"] == 0 and body["updated"] == 1 and body["unchanged"] == 2
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert records["total"] == 3
    camera = next(s for s in records["sections"] if s["name"] == "Camera testing")
    assert {r["data"]["test_count"] for r in camera["rows"]} == {999, 20}


async def test_records_grouping_search_pagination(client, qa_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert [s["name"] for s in body["sections"]] == ["Camera testing", "Microscope testing"]
    assert body["sections"][0]["rowCount"] == 2
    # forward-filled spec name is stored on the second row
    assert body["sections"][0]["rows"][1]["data"]["test_spec_name"] == "DP23"

    search = (await client.get(RECORDS, params={"search": "ix73"}, headers=qa_headers)).json()
    assert search["total"] == 1
    numeric = (await client.get(RECORDS, params={"search": "20"}, headers=qa_headers)).json()
    assert numeric["total"] == 1

    paged = (await client.get(RECORDS, params={"pageSize": 2, "page": 2}, headers=qa_headers)).json()
    assert paged["total"] == 3
    assert sum(s["rowCount"] for s in paged["sections"]) == 1


async def test_dashboard_aggregates(client, qa_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    body = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert body["totalRows"] == 3
    assert body["sectionCount"] == 2
    assert body["totals"] == {"test_count": 35}
    camera = next(s for s in body["bySection"] if s["section"] == "Camera testing")
    assert camera["sums"] == {"test_count": 30}
    assert body["topRows"][0]["value"] == 20


async def test_real_file_end_to_end(client, qa_headers):
    res = await client.post(UPLOAD, headers=qa_headers,
                            files=xlsx(REAL_FILE.read_bytes(), "cellSens-Count.xlsx"))
    body = res.json()
    assert body["totalRows"] == 218
    assert body["inserted"] == 206
    assert body["duplicatesSkipped"] == 12
    dash = (await client.get(DASHBOARD, headers=qa_headers)).json()
    assert dash["totals"] == {"test_count": 140736}


async def test_replace_mode_wipes_previous_rows(client, qa_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    smaller = [
        ["Camera testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ]
    res = await client.post(f"{UPLOAD}?mode=replace", headers=qa_headers,
                            files=xlsx(build_workbook(smaller)))
    body = res.json()
    assert body["inserted"] == 1 and body["updated"] == 0 and body["unchanged"] == 0
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert records["total"] == 1  # old rows gone — the file is the whole dataset


async def test_delete_last_upload_only(client, qa_headers, admin_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    extra = [
        ["Extra testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["DP99", "(TS) Fresh", 7],
    ]
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(extra)))
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 4

    res = await client.delete(f"{RECORDS}?scope=last", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"deleted": 1, "scope": "last"}
    records = (await client.get(RECORDS, headers=qa_headers)).json()
    assert records["total"] == 3  # first upload's rows survive
    assert records["lastUpload"] is not None
    assert records["columns"] != []


async def test_admin_clear_records(client, qa_headers, admin_headers):
    await client.post(UPLOAD, headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    res = await client.delete(RECORDS, headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["deleted"] == 3
    body = (await client.get(RECORDS, headers=qa_headers)).json()
    assert body["total"] == 0 and body["lastUpload"] is None and body["columns"] == []


async def test_upload_validation_errors(client, qa_headers):
    res = await client.post(UPLOAD, headers=qa_headers,
                            files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 400
    res = await client.post(UPLOAD, headers=qa_headers, files=xlsx(b"garbage bytes"))
    assert res.status_code == 400
    res = await client.post("/api/apps/cellsens/releases/v4-4/layers/nope/uploads",
                            headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    assert res.status_code == 404
    res = await client.post("/api/apps/UPPER/releases/v4-4/layers/x/uploads",
                            headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    assert res.status_code == 422  # slug pattern rejects uppercase ids
