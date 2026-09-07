"""One workbook, one dataset — the guarantee that files are never merged.

Two workbooks feeding the same testing type must keep separate records,
separate columns, separate metrics and separate history, in the same release
and the same month. Nothing anywhere may add them together.
"""
import pytest_asyncio

from tests.helpers_xlsx import build_workbook

RELEASE = "/api/apps/cellsens/releases/v4-4"
LAYER = f"{RELEASE}/layers/system"
SNAPSHOTS = f"{RELEASE}/snapshots"
FILES = f"{LAYER}/files"
RECORDS = f"{LAYER}/records"
DASHBOARD = f"{LAYER}/dashboard"
HISTORY = f"{LAYER}/snapshots"

SEPTEMBER = {"year": 2026, "month": 9}
A, B = "system/part-a.xlsx", "system/part-b.xlsx"

# deliberately different shapes: proof the two never share a schema
SHEET_A = [
    ["Camera testing"],
    ["Test spec name", "Test spec tab name", "Test Count"],
    ["DP23", "(TS) Color", 10],
    ["DP23", "(TS) Gray", 20],
]
SHEET_B = [
    ["Pattern No.", "OS ver.", "TC count"],
    [1, "Win11 Pro 64bit", 57],
    [2, "Win10 Pro 64bit", 43],
    [3, "Win11 Home 64bit", 12],
]


@pytest_asyncio.fixture
async def two_files(client, admin_headers, qa_headers, tmp_path):
    """A release folder whose `system` testing type is fed by two workbooks."""
    layer_dir = tmp_path / "cellSens" / "v4.4" / "system"
    layer_dir.mkdir(parents=True)
    (layer_dir / "part-a.xlsx").write_bytes(build_workbook(SHEET_A))
    (layer_dir / "part-b.xlsx").write_bytes(build_workbook(SHEET_B))
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.delete(RECORDS, headers=admin_headers)
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system", "files": [A, B]}]})
    yield layer_dir
    await client.delete(RECORDS, headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": ""})


# --- the datasets stay apart ---------------------------------------------


async def test_each_file_is_its_own_dataset(client, qa_headers, two_files):
    files = (await client.get(FILES, headers=qa_headers)).json()
    assert sorted(f["file"] for f in files) == [A, B]
    by_path = {f["file"]: f for f in files}
    assert by_path[A]["rowCount"] == 2
    assert by_path[B]["rowCount"] == 3
    assert by_path[A]["columnCount"] == 3
    assert by_path[B]["columnCount"] == 3
    # each carries the release, month and exact load time of its own snapshot
    assert all(f["period"] == SEPTEMBER and f["loadedAt"] for f in files)
    assert all(f["snapshotCount"] == 1 for f in files)


async def test_records_are_only_ever_one_file_s(client, qa_headers, two_files):
    a = (await client.get(f"{RECORDS}?file={A}", headers=qa_headers)).json()
    b = (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json()

    assert a["total"] == 2 and b["total"] == 3          # never 5
    assert [c["key"] for c in a["columns"]] == [
        "test_spec_name", "test_spec_tab_name", "test_count"]
    assert [c["key"] for c in b["columns"]] == ["pattern_no", "os_ver", "tc_count"]
    assert a["snapshot"]["file"] == A and b["snapshot"]["file"] == B

    # no row of one file appears in the other
    a_rows = [r["data"] for s in a["sections"] for r in s["rows"]]
    b_rows = [r["data"] for s in b["sections"] for r in s["rows"]]
    assert all("pattern_no" not in row for row in a_rows)
    assert all("test_spec_name" not in row for row in b_rows)


async def test_metrics_are_per_file_and_never_totalled(client, qa_headers, two_files):
    a = (await client.get(f"{DASHBOARD}?file={A}", headers=qa_headers)).json()
    b = (await client.get(f"{DASHBOARD}?file={B}", headers=qa_headers)).json()

    assert a["totals"] == {"test_count": 30}
    assert b["totals"] == {"pattern_no": 6, "tc_count": 112}
    assert a["totalRows"] == 2 and b["totalRows"] == 3
    # the two measure sets have nothing in common — there is no combined view
    assert set(a["totals"]) & set(b["totals"]) == set()


async def test_history_is_the_selected_file_s_timeline(
        client, qa_headers, two_files):
    a = (await client.get(f"{HISTORY}?file={A}", headers=qa_headers)).json()
    b = (await client.get(f"{HISTORY}?file={B}", headers=qa_headers)).json()
    assert [h["file"] for h in a] == [A]
    assert [h["file"] for h in b] == [B]
    assert a[0]["sequence"] == b[0]["sequence"] == 1  # numbered independently


# --- loading one file leaves the other alone ------------------------------


async def test_reloading_one_file_does_not_touch_the_other(
        client, qa_headers, two_files):
    grown = [row[:] for row in SHEET_A] + [["DP99", "(TS) New", 5]]
    (two_files / "part-a.xlsx").write_bytes(build_workbook(grown))

    res = await client.post(SNAPSHOTS, headers=qa_headers,
                            json={"period": SEPTEMBER,
                                  "layers": [{"layerId": "system", "files": [A, B]}]})
    results = {f["file"]: f for f in res.json()["files"]}
    assert results[A]["created"] is True and results[A]["sequence"] == 2
    assert results[B]["created"] is False               # untouched, unchanged

    assert [h["sequence"] for h in
            (await client.get(f"{HISTORY}?file={A}", headers=qa_headers)).json()] == [2, 1]
    assert [h["sequence"] for h in
            (await client.get(f"{HISTORY}?file={B}", headers=qa_headers)).json()] == [1]
    b_now = (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json()
    assert b_now["total"] == 3 and b_now["snapshot"]["sequence"] == 1


async def test_deleting_one_file_s_snapshot_leaves_the_other(
        client, qa_headers, admin_headers, two_files):
    history = (await client.get(f"{HISTORY}?file={A}", headers=qa_headers)).json()
    await client.delete(f"{RELEASE}/snapshots/{history[0]['id']}", headers=admin_headers)

    assert (await client.get(f"{HISTORY}?file={A}", headers=qa_headers)).json() == []
    b = (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json()
    assert b["total"] == 3                              # wholly unaffected


# --- defaults and roll-ups ------------------------------------------------


async def test_the_view_opens_on_the_most_recently_loaded_file(
        client, qa_headers, two_files):
    """With no file named, reads land on the newest load — B here, since the
    two were loaded in order and B went in last."""
    files = (await client.get(FILES, headers=qa_headers)).json()
    assert files[0]["file"] == B                        # listed newest first
    default = (await client.get(RECORDS, headers=qa_headers)).json()
    assert default["snapshot"]["file"] == B
    assert (await client.get(DASHBOARD, headers=qa_headers)).json()["snapshot"]["file"] == B
    # history is common to the testing type: every file's snapshots together,
    # newest load first, each still flagged current for its own file
    history = (await client.get(HISTORY, headers=qa_headers)).json()
    assert sorted(h["file"] for h in history) == [A, B]
    assert all(h["isCurrent"] for h in history)      # one load each so far
    # narrowing to one file is still possible
    assert [h["file"] for h in
            (await client.get(f"{HISTORY}?file={A}", headers=qa_headers)).json()] == [A]


async def test_the_layer_count_sums_files_for_the_pyramid_only(
        client, qa_headers, two_files):
    """The testing pyramid needs one number per testing type, so the layer's
    record count is the sum across its files. That is a count, not a merge —
    no combined dataset exists to read."""
    layers = (await client.get(f"{RELEASE}/layers", headers=qa_headers)).json()
    system = next(l for l in layers if l["id"] == "system")
    assert system["recordCount"] == 5                   # 2 + 3, for the pyramid
    assert system["fileCount"] == 2
    assert system["snapshotCount"] == 2
    assert sorted(system["latestSources"]) == [A, B]

    # but nothing serves those 5 rows together
    assert (await client.get(f"{RECORDS}?file={A}", headers=qa_headers)).json()["total"] == 2
    assert (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json()["total"] == 3
    assert (await client.get(RECORDS, headers=qa_headers)).json()["total"] == 3


async def test_a_renamed_file_starts_a_new_series(client, qa_headers, two_files):
    """Renaming a workbook makes it a new dataset; the old one keeps its
    history under its old name."""
    (two_files / "part-a.xlsx").rename(two_files / "part-a-renamed.xlsx")
    renamed = "system/part-a-renamed.xlsx"
    res = await client.post(SNAPSHOTS, headers=qa_headers,
                            json={"period": SEPTEMBER,
                                  "layers": [{"layerId": "system", "files": [renamed]}]})
    new = next(f for f in res.json()["files"] if f["file"] == renamed)
    assert new["created"] is True and new["sequence"] == 1

    files = {f["file"]: f for f in (await client.get(FILES, headers=qa_headers)).json()}
    assert set(files) == {A, B, renamed}
    assert files[A]["rowCount"] == 2                    # old history preserved
    assert files[renamed]["rowCount"] == 2


# --- the merged dashboard: a read-time view, never stored data ------------


async def test_merged_dashboard_adds_the_files_up(client, qa_headers, two_files):
    """The dashboard can total every file's current snapshot. The two sheets
    here share no measures, so the merged view carries both sets side by
    side — each summed over the files that actually have it."""
    merged = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert merged["merged"] is True
    assert merged["snapshot"] is None
    assert merged["totalRows"] == 5                      # 2 + 3
    assert merged["totals"] == {"test_count": 30, "pattern_no": 6, "tc_count": 112}
    assert sorted(f["file"] for f in merged["mergedFiles"]) == [A, B]


async def test_merged_measures_agree_with_the_files_read_singly(
        client, qa_headers, two_files):
    a = (await client.get(f"{DASHBOARD}?file={A}", headers=qa_headers)).json()
    b = (await client.get(f"{DASHBOARD}?file={B}", headers=qa_headers)).json()
    merged = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()

    assert merged["totalRows"] == a["totalRows"] + b["totalRows"]
    for key, total in {**a["totals"], **b["totals"]}.items():
        assert merged["totals"][key] == total


async def test_merging_writes_nothing(client, qa_headers, admin_headers, two_files):
    """The combined figures are computed on the way out. Nothing is stored, so
    reading the files singly afterwards gives exactly what it did before."""
    before = [
        (await client.get(f"{RECORDS}?file={A}", headers=qa_headers)).json(),
        (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json(),
    ]
    files_before = (await client.get(FILES, headers=qa_headers)).json()

    await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)

    after = [
        (await client.get(f"{RECORDS}?file={A}", headers=qa_headers)).json(),
        (await client.get(f"{RECORDS}?file={B}", headers=qa_headers)).json(),
    ]
    assert [b["total"] for b in before] == [a["total"] for a in after] == [2, 3]
    assert [b["columns"] for b in before] == [a["columns"] for a in after]
    assert (await client.get(FILES, headers=qa_headers)).json() == files_before
    # and no snapshot was invented for the merge
    assert len((await client.get(HISTORY, headers=qa_headers)).json()) == 2


async def test_merged_largest_rows_need_a_shared_measure(client, qa_headers, two_files):
    """Ranking a `test_count` against a `tc_count` would be meaningless, so
    largest-rows is offered only when the files share a measure."""
    unshared = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert unshared["topRows"] == []

    # give the second file the same shape as the first — now they can be ranked
    same_shape = [
        ["Microscope testing"],
        ["Test spec name", "Test spec tab name", "Test Count"],
        ["IX73", "(TS) Devices", 99],
    ]
    (two_files / "part-b.xlsx").write_bytes(build_workbook(same_shape))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system", "files": [A, B]}]})

    shared = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert shared["totals"] == {"test_count": 129}        # 10 + 20 + 99
    assert shared["topRows"][0]["value"] == 99
    assert len(shared["topRows"]) == 3
    # still two datasets underneath, still unmerged in storage
    assert {f["file"]: f["rowCount"]
            for f in (await client.get(FILES, headers=qa_headers)).json()} == {A: 2, B: 1}


async def test_merged_sections_span_the_files(client, qa_headers, two_files):
    merged = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    sections = {s["section"]: s["rowCount"] for s in merged["bySection"]}
    assert sections == {"Camera testing": 2, "General": 3}
    assert merged["sectionCount"] == 2


# --- history is common to the testing type --------------------------------


async def test_history_covers_every_file(client, qa_headers, two_files):
    grown = [row[:] for row in SHEET_A] + [["DP99", "(TS) New", 5]]
    (two_files / "part-a.xlsx").write_bytes(build_workbook(grown))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system", "files": [A, B]}]})

    history = (await client.get(HISTORY, headers=qa_headers)).json()
    assert len(history) == 3                              # A twice, B once
    assert [h["file"] for h in history][0] == A           # newest load first
    # exactly one snapshot per file is current, and it is that file's newest
    current = [h for h in history if h["isCurrent"]]
    assert sorted(h["file"] for h in current) == [A, B]
    assert next(h for h in current if h["file"] == A)["sequence"] == 2
