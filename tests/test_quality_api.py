"""Data-quality warnings over HTTP.

Two things are being protected. A load has to say what was wrong with each
workbook it read — and it has to keep saying it, on every load, including the
one that wrote nothing because the file had not changed. A bad sheet that has
not been touched is still a bad sheet.

Nothing here compares a workbook with an earlier reading. That is deliberate
and it is what these tests pin: the same file gives the same warnings on its
first load and its second.
"""
import pytest
import pytest_asyncio

from app import db as db_module

from tests.helpers_xlsx import build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
SNAPSHOTS = f"{RELEASE}/snapshots"
SEPTEMBER = {"year": 2026, "month": 9}
LAYERS = ("system", "unit", "feature")

# same identity, different counts: 9 test cases go missing
MESSY = [
    ["OS ver.", "type", "Tester", "Pass", "Remarks"],
    ["Win10 Pro 64bit", "Version Update", "Rahul", 24, None],
    ["Win11 Pro 64bit", "Clean Install", "Meera", 18, None],
    ["Win10 Pro 64bit", "Version Update", "Rahul", 9, None],
]
CLEAN = [
    ["Module", "Suite", "Cases"],
    ["imaging", "colour", 320],
    ["imaging", "exposure", 145],
]
# shaped like the real Feature workbook: keyed on an FL id, work described in
# free text. FL-5777 names FL-5778, a row of its own; FL-5786 names FL-5651,
# which this sheet does not contain.
FEATURES = [
    ["ID", "Summary (cellSens)", "PBI / Related Item"],
    ["FL-5778", "New version of scanR (3.7)", "CS-4645 - scanR PBI1: LabView"],
    ["FL-5777", "Basic Support of X-Cite TETREM light source",
     "CS-4800 - FL-5778 PBI: Basic Support of X-Cite TETREM light source"],
    ["FL-5786", "Setup PFE flags - Follow-Up",
     "CS-4294 - FL-5651 Setup PFE: PBI 4 - Implementation"],
    ["FL-5773", "Helix: Support of new BX57/47 microscopes",
     "CS-4614 - BX53/43 successor (Helix) US1: Manual frame support"],
    ["FL-5776", "FIJI/ImageJ Bridge for cellSens", "CS-4759 - FIJI PBI 1"],
]


async def load(client, headers):
    res = await client.post(SNAPSHOTS, headers=headers, json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text
    return res.json()


def for_file(run, name):
    return next(f for f in run["files"] if f["fileName"] == name)


@pytest_asyncio.fixture
async def folder(client, admin_headers, qa_headers, tmp_path):
    """A workbook with problems, one without, and one that contradicts
    itself."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    release_dir.mkdir(parents=True)
    (release_dir / "system.xlsx").write_bytes(build_workbook(MESSY))
    (release_dir / "unit.xlsx").write_bytes(build_workbook(CLEAN))
    (release_dir / "feature.xlsx").write_bytes(build_workbook(FEATURES))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    for layer in LAYERS:
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)

    yield release_dir

    for layer in LAYERS:
        await client.delete(f"{RELEASE}/layers/{layer}/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- what a load reports --------------------------------------------------


async def test_a_load_reports_what_was_wrong_with_each_file(
        client, qa_headers, folder):
    run = await load(client, qa_headers)
    messy = for_file(run, "system.xlsx")
    assert [w["code"] for w in messy["warnings"]] == [
        "conflicting_duplicate", "empty_columns"]
    assert messy["warnings"][0]["severity"] == "problem"


async def test_a_sheet_that_contradicts_itself_says_so_on_the_load(
        client, qa_headers, folder):
    """The mismatch check rides the mechanism that was already there — the
    warning arrives on the Create Snapshot result beside the duplicate and
    empty-column notices, with no route or model of its own."""
    run = await load(client, qa_headers)
    warnings = for_file(run, "feature.xlsx")["warnings"]
    by_code = {w["code"]: w for w in warnings}
    assert "mismatched_id" in by_code
    assert by_code["mismatched_id"]["severity"] == "problem"
    assert "FL-5777" in by_code["mismatched_id"]["message"]
    assert "FL-5778" in by_code["mismatched_id"]["message"]
    # FL-5786 naming FL-5651 is a question about scope, listed by Traceability
    assert "unknown_id_reference" not in by_code


async def test_an_outside_reference_is_left_to_traceability(
        client, qa_headers, folder):
    """FL-5786 names FL-5651, which this sheet does not list. Nothing is wrong
    with the workbook, so the load reports only the in-sheet contradiction."""
    run = await load(client, qa_headers)
    codes = [w["code"] for w in for_file(run, "feature.xlsx")["warnings"]]
    assert codes == ["mismatched_id"]


async def test_a_warning_saved_before_the_move_is_no_longer_shown(
        client, qa_headers, folder):
    """Snapshots saved while the outside reference was still a workbook notice
    carry it. Wherever saved warnings are read back out, it is dropped."""
    run = await load(client, qa_headers)
    snapshot_id = for_file(run, "feature.xlsx")["snapshotId"]
    await db_module.get_db().snapshots.update_one(
        {"_id": snapshot_id},
        {"$push": {"warnings": {
            "code": "unknown_id_reference", "severity": "notice",
            "message": "1 row(s) reference FL ids this sheet does not contain (FL-5651)."}}})

    dash = (await client.get(f"{RELEASE}/layers/feature/dashboard",
                             headers=qa_headers)).json()
    assert [w["code"] for w in dash["warnings"]] == ["mismatched_id"]
    assert [w["code"] for w in dash["snapshot"]["warnings"]] == ["mismatched_id"]
    history = (await client.get(f"{RELEASE}/layers/feature/snapshots",
                                headers=qa_headers)).json()
    assert history
    assert all(w["code"] != "unknown_id_reference"
               for snap in history for w in snap["warnings"])


async def test_a_contradiction_never_blocks_the_load(client, qa_headers, folder):
    """Nothing was lost, so all five features are stored regardless."""
    run = await load(client, qa_headers)
    feature = for_file(run, "feature.xlsx")
    assert feature["created"] is True
    assert feature["rowCount"] == 5
    assert feature["error"] is None


async def test_an_unchanged_reload_still_reports_the_contradiction(
        client, qa_headers, folder):
    """The second load writes nothing. A sheet that contradicts itself and has
    not been touched still contradicts itself."""
    await load(client, qa_headers)
    again = await load(client, qa_headers)
    feature = for_file(again, "feature.xlsx")
    assert feature["created"] is False
    assert [w["code"] for w in feature["warnings"]] == ["mismatched_id"]


async def test_a_clean_file_reports_nothing(client, qa_headers, folder):
    run = await load(client, qa_headers)
    assert for_file(run, "unit.xlsx")["warnings"] == []


async def test_the_warning_names_the_rows_that_disagreed(client, qa_headers, folder):
    run = await load(client, qa_headers)
    message = for_file(run, "system.xlsx")["warnings"][0]["message"]
    assert "Win10 Pro 64bit · Version Update · Rahul" in message
    assert "24" in message and "9" in message


async def test_a_problem_never_blocks_the_load(client, qa_headers, folder):
    """The snapshot is still written. A load that refuses leaves the reader
    with nothing to act on."""
    run = await load(client, qa_headers)
    messy = for_file(run, "system.xlsx")
    assert messy["created"] is True
    assert messy["snapshotId"] and messy["rowCount"] == 2
    assert messy["error"] is None


# --- the case that matters most -------------------------------------------


async def test_an_unchanged_reload_still_reports_them(client, qa_headers, folder):
    """Nothing is written the second time, because the content hash matches.
    The sheet's problems have not gone away, so they are reported anyway —
    otherwise a reload of a bad file comes back looking clean."""
    await load(client, qa_headers)
    again = for_file(await load(client, qa_headers), "system.xlsx")

    assert again["created"] is False
    assert "No change since snapshot #1" in again["reason"]
    assert [w["code"] for w in again["warnings"]] == [
        "conflicting_duplicate", "empty_columns"]


async def test_the_same_file_warns_the_same_on_every_load(client, qa_headers, folder):
    """A warning is a property of the file, not of its history."""
    first = for_file(await load(client, qa_headers), "system.xlsx")["warnings"]
    second = for_file(await load(client, qa_headers), "system.xlsx")["warnings"]
    assert first == second


# --- where they are kept --------------------------------------------------


async def test_they_are_stored_on_the_snapshot(client, qa_headers, folder):
    await load(client, qa_headers)
    history = (await client.get(f"{RELEASE}/layers/system/snapshots",
                                headers=qa_headers)).json()
    assert [w["code"] for w in history[0]["warnings"]] == [
        "conflicting_duplicate", "empty_columns"]


async def test_the_dashboard_carries_the_warnings_of_the_file_it_read(
        client, qa_headers, folder):
    await load(client, qa_headers)
    body = (await client.get(f"{RELEASE}/layers/system/dashboard",
                             headers=qa_headers)).json()
    assert [w["code"] for w in body["warnings"]] == [
        "conflicting_duplicate", "empty_columns"]


async def test_a_clean_layer_dashboard_carries_none(client, qa_headers, folder):
    await load(client, qa_headers)
    body = (await client.get(f"{RELEASE}/layers/unit/dashboard",
                             headers=qa_headers)).json()
    assert body["warnings"] == []


async def test_a_layer_never_loaded_answers_without_warnings(client, manager_headers):
    body = (await client.get(f"{RELEASE}/layers/feature/dashboard",
                             headers=manager_headers)).json()
    assert body["warnings"] == []


# --- what is deliberately absent ------------------------------------------


async def test_a_changed_reload_reports_the_file_not_the_change(
        client, qa_headers, folder):
    """Editing the sheet writes snapshot #2 with a real diff — and the
    warnings still describe only the new file, never the difference."""
    (folder / "system.xlsx").write_bytes(build_workbook(MESSY[:3]))
    await load(client, qa_headers)
    second = for_file(await load(client, qa_headers), "system.xlsx")

    # the duplicate is gone from the sheet, so its warning is gone too
    assert "conflicting_duplicate" not in [w["code"] for w in second["warnings"]]
    assert all("snapshot" not in w["message"].lower() for w in second["warnings"])
    assert all("previous" not in w["message"].lower() for w in second["warnings"])
