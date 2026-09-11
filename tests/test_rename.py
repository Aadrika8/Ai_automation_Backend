"""Renaming a workbook carries its history across instead of forking it.

The bug this closes: `file` is a dataset's identity, so a renamed workbook used
to start a fresh series while the old name kept a current snapshot of its own.
A layer's record count sums across its files, so a rename doubled it — 3 rows
became 6 — and that count feeds the testing-pyramid check.

A rename is recognised from two signals the load already has: the old name is
gone from the folder, and the content is byte-for-byte identical. A *copy*
leaves the original in place and so stays two datasets, correctly.
"""
import pytest
import pytest_asyncio

from app.services.excel_source import detect_rename
from tests.helpers_xlsx import SIMPLE, build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
SNAPSHOTS = f"{RELEASE}/snapshots"
LAYERS = f"{RELEASE}/layers"
FILES = f"{LAYERS}/system/files"
SEPTEMBER = {"year": 2026, "month": 9}

OTHER = [["Test spec name", "Test spec tab name", "Test Count"],
         ["DP99", "(TS) Fresh", 7]]


# --- the decision, on its own ---------------------------------------------


def test_a_vanished_file_with_the_same_content_was_renamed():
    known = {"regression.xlsx": "abc", "acceptance.xlsx": "def"}
    assert detect_rename(known, {"regression_test.xlsx", "acceptance.xlsx"},
                         "abc") == "regression.xlsx"


def test_a_file_still_in_the_folder_was_copied_not_renamed():
    known = {"regression.xlsx": "abc"}
    assert detect_rename(known, {"regression.xlsx", "copy.xlsx"}, "abc") == ""


def test_different_content_is_a_new_workbook():
    """A rename *and* an edit in one step reads as new. Recovering it would
    mean guessing at similarity, which is worse than a fresh series."""
    assert detect_rename({"regression.xlsx": "abc"}, set(), "zzz") == ""


def test_two_vanished_files_sharing_a_hash_are_ambiguous():
    """Nothing here can say which one was renamed, so it declines."""
    known = {"a.xlsx": "abc", "b.xlsx": "abc"}
    assert detect_rename(known, set(), "abc") == ""


def test_nothing_known_and_nothing_missing():
    assert detect_rename({}, {"a.xlsx"}, "abc") == ""
    assert detect_rename({"a.xlsx": "abc"}, {"a.xlsx"}, "abc") == ""


def test_an_empty_hash_never_matches():
    assert detect_rename({"a.xlsx": ""}, set(), "") == ""


# --- over HTTP ------------------------------------------------------------


@pytest_asyncio.fixture
async def folder(client, admin_headers, qa_headers, tmp_path):
    """One workbook in the system layer, loaded once."""
    release_dir = tmp_path / "cellSens" / "v4.4"
    release_dir.mkdir(parents=True)
    (release_dir / "system.xlsx").write_bytes(build_workbook(SIMPLE))

    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.delete(f"{LAYERS}/system/records", headers=admin_headers)
    res = await client.post(SNAPSHOTS, headers=qa_headers, json={"period": SEPTEMBER})
    assert res.status_code == 200, res.text

    yield release_dir

    await client.delete(f"{LAYERS}/system/records", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


async def load(client, headers, files=None):
    body = {"period": SEPTEMBER}
    if files is not None:
        body["layers"] = [{"layerId": "system", "files": files}]
    res = await client.post(SNAPSHOTS, headers=headers, json=body)
    assert res.status_code == 200, res.text
    return res.json()


async def system_layer(client, headers):
    layers = (await client.get(LAYERS, headers=headers)).json()
    return next(l for l in layers if l["id"] == "system")


async def test_the_layer_count_does_not_double(client, qa_headers, folder):
    """The assertion the old design never made, and the reason it was wrong."""
    before = await system_layer(client, qa_headers)
    assert before["recordCount"] == 3 and before["fileCount"] == 1

    (folder / "system.xlsx").rename(folder / "system_test.xlsx")
    await load(client, qa_headers, ["system_test.xlsx"])

    after = await system_layer(client, qa_headers)
    assert after["recordCount"] == 3        # was 6 before this change
    assert after["fileCount"] == 1


async def test_the_history_comes_across(client, qa_headers, folder):
    (folder / "system.xlsx").rename(folder / "system_test.xlsx")
    await load(client, qa_headers, ["system_test.xlsx"])

    files = {f["file"]: f for f in (await client.get(FILES, headers=qa_headers)).json()}
    assert set(files) == {"system_test.xlsx"}          # the old name is gone
    assert files["system_test.xlsx"]["sequence"] == 1  # not restarted, carried
    assert files["system_test.xlsx"]["rowCount"] == 3

    history = (await client.get(f"{LAYERS}/system/snapshots",
                                headers=qa_headers)).json()
    assert [h["file"] for h in history] == ["system_test.xlsx"]
    assert history[0]["isCurrent"] is True


async def test_the_load_says_it_recognised_a_rename(client, qa_headers, folder):
    (folder / "system.xlsx").rename(folder / "system_test.xlsx")
    result = next(f for f in (await load(client, qa_headers, ["system_test.xlsx"]))["files"]
                  if f["file"] == "system_test.xlsx")
    assert result["renamedFrom"] == "system.xlsx"
    assert result["created"] is False                  # nothing new was written
    assert "Renamed from system.xlsx" in result["reason"]
    assert "No change since snapshot #1" in result["reason"]


async def test_the_rows_move_with_the_snapshot(client, qa_headers, folder):
    """Records carry the file name too, so a half-moved series would read as
    empty from one side."""
    (folder / "system.xlsx").rename(folder / "system_test.xlsx")
    await load(client, qa_headers, ["system_test.xlsx"])
    records = (await client.get(f"{LAYERS}/system/records?file=system_test.xlsx",
                                headers=qa_headers)).json()
    assert records["total"] == 3


async def test_loading_again_after_a_rename_is_an_ordinary_no_op(
        client, qa_headers, folder):
    (folder / "system.xlsx").rename(folder / "system_test.xlsx")
    await load(client, qa_headers, ["system_test.xlsx"])
    again = next(f for f in (await load(client, qa_headers, ["system_test.xlsx"]))["files"]
                 if f["file"] == "system_test.xlsx")
    assert again["created"] is False
    assert again["renamedFrom"] == ""       # it has its own history now
    assert (await system_layer(client, qa_headers))["recordCount"] == 3


async def test_a_renamed_and_edited_workbook_is_a_new_series(
        client, qa_headers, folder):
    """The content hash differs, so nothing links the two."""
    (folder / "system.xlsx").unlink()
    (folder / "system_test.xlsx").write_bytes(build_workbook(OTHER))
    result = next(f for f in (await load(client, qa_headers, ["system_test.xlsx"]))["files"]
                  if f["file"] == "system_test.xlsx")
    assert result["renamedFrom"] == ""
    assert result["created"] is True and result["sequence"] == 1


# --- a copy is not a rename -----------------------------------------------


async def test_a_copy_stays_two_datasets_and_is_reported(
        client, qa_headers, folder):
    """Both files are on disk, so both are real datasets by the rule this
    system runs on — and their rows genuinely are counted twice."""
    (folder / "system_copy.xlsx").write_bytes(build_workbook(SIMPLE))
    run = await load(client, qa_headers, ["system.xlsx", "system_copy.xlsx"])

    copy = next(f for f in run["files"] if f["file"] == "system_copy.xlsx")
    assert copy["renamedFrom"] == ""
    assert copy["created"] is True

    layer = await system_layer(client, qa_headers)
    assert layer["fileCount"] == 2 and layer["recordCount"] == 6

    warning = next(w for w in run["warnings"] if w["code"] == "identical_workbooks")
    assert warning["severity"] == "problem"
    assert "system.xlsx and system_copy.xlsx" in warning["message"]
    assert "counted twice" in warning["message"]


async def test_a_clean_load_reports_no_duplicates(client, qa_headers, folder):
    assert (await load(client, qa_headers, ["system.xlsx"]))["warnings"] == []
