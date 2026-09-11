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

    # both sheets count test cases, so both report the same logical measure —
    # under each sheet's own numbers, because they are separate datasets
    assert a["totals"] == {"total_tests": 30}
    assert b["totals"] == {"pattern_no": 6, "total_tests": 112}
    assert a["totalRows"] == 2 and b["totalRows"] == 3
    # neither has been told about the other: no figure here is a sum of both
    assert a["totals"]["total_tests"] != b["totals"]["total_tests"]
    assert "pattern_no" not in a["totals"]


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


async def test_a_renamed_file_moves_its_history(client, qa_headers, two_files):
    """Renaming a workbook is not new data, so it must not make a new dataset.

    Left to fork, the old name would keep a current snapshot of its own and the
    layer would count the same rows twice — which then feeds the pyramid check.
    """
    (two_files / "part-a.xlsx").rename(two_files / "part-a-renamed.xlsx")
    renamed = "system/part-a-renamed.xlsx"
    res = await client.post(SNAPSHOTS, headers=qa_headers,
                            json={"period": SEPTEMBER,
                                  "layers": [{"layerId": "system", "files": [renamed]}]})
    new = next(f for f in res.json()["files"] if f["file"] == renamed)
    assert new["created"] is False                      # nothing was written
    assert new["renamedFrom"] == A
    assert "Renamed from part-a.xlsx" in new["reason"]

    files = {f["file"]: f for f in (await client.get(FILES, headers=qa_headers)).json()}
    assert set(files) == {B, renamed}                   # the old name is gone
    assert files[renamed]["rowCount"] == 2              # its history came across
    assert files[renamed]["sequence"] == 1


# --- the merged dashboard: a read-time view, never stored data ------------


async def test_merged_dashboard_adds_the_files_up(client, qa_headers, two_files):
    """The dashboard can total every file's current snapshot.

    `Test Count` in one sheet and `TC count` in the other are the same
    quantity under two headers, so they fold into one measure. `Pattern No.`
    is nothing of the kind and keeps its own identity.
    """
    merged = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert merged["merged"] is True
    assert merged["snapshot"] is None
    assert merged["totalRows"] == 5                      # 2 + 3
    assert merged["totals"] == {"total_tests": 142, "pattern_no": 6}  # 30 + 112
    assert sorted(f["file"] for f in merged["mergedFiles"]) == [A, B]


async def test_merged_measures_agree_with_the_files_read_singly(
        client, qa_headers, two_files):
    a = (await client.get(f"{DASHBOARD}?file={A}", headers=qa_headers)).json()
    b = (await client.get(f"{DASHBOARD}?file={B}", headers=qa_headers)).json()
    merged = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()

    assert merged["totalRows"] == a["totalRows"] + b["totalRows"]
    # every measure, merged, is exactly the sum of the files that carry it —
    # including one the two sheets name differently
    for key in set(a["totals"]) | set(b["totals"]):
        assert merged["totals"][key] == a["totals"].get(key, 0) + b["totals"].get(key, 0)


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


async def test_two_files_counting_the_same_thing_make_one_total(
        client, admin_headers, qa_headers, two_files):
    """`Test Count` in one sheet and `Test Cases` in the other are the same
    quantity. Keyed on the column name they made two separate totals, and then
    a section from the second file reported 0 against the first file's column
    — a wrong number rather than a missing one.
    """
    (two_files / "part-c.xlsx").write_bytes(build_workbook([
        ["Suite", "Test Cases"],          # a different header for the same thing
        ["licensing", 7],
    ]))
    res = await client.post(SNAPSHOTS, headers=qa_headers,
                            json={"period": SEPTEMBER,
                                  "layers": [{"layerId": "system",
                                              "files": ["system/part-c.xlsx"]}]})
    assert res.status_code == 200, res.text

    body = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()

    # three headers for test cases across three files, one measure
    shown = {c["key"]: c["label"] for c in body["numericColumns"]}
    assert shown["total_tests"] == "Test count"
    assert body["totals"]["total_tests"] == 149     # 30 + 112 + 7

    # and no section reads zero for using another file's name for it
    per_section = {s["section"]: s["sums"]["total_tests"] for s in body["bySection"]}
    assert per_section["Camera testing"] == 30      # part-a, `Test Count`
    assert per_section["General"] == 119            # part-b `TC count` + part-c `Test Cases`


async def test_a_section_is_never_zero_for_using_the_other_name(
        client, qa_headers, two_files):
    """The bug this closes: part-c's rows measure `Test Cases`, so against a
    `Test Count` key they summed to nothing and the chart drew a zero bar."""
    (two_files / "part-c.xlsx").write_bytes(build_workbook([
        ["Suite", "Total Cases"], ["licensing", 7]]))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system",
                                        "files": ["system/part-c.xlsx"]}]})
    body = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert all(s["sums"]["total_tests"] > 0 for s in body["bySection"])


async def test_folding_restores_the_largest_rows_panel(
        client, qa_headers, two_files):
    """Largest-rows needs a measure every file carries. Before the fold these
    two counted the same thing under different names and shared none, so the
    panel was withheld."""
    (two_files / "part-c.xlsx").write_bytes(build_workbook([
        ["Suite", "Test Cases"], ["licensing", 7]]))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system",
                                        "files": ["system/part-c.xlsx"]}]})
    body = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert body["topRows"], "the panel should be offered once a measure is shared"
    # ranked across every file, so the biggest row wins whichever sheet it is in
    assert body["topRows"][0]["value"] == 57        # part-b, under `TC count`
    assert [r["value"] for r in body["topRows"]][:4] == [57, 43, 20, 12]


async def test_an_automated_count_is_not_folded_into_the_total(
        client, qa_headers, two_files):
    """It contains "Test Count" and is a different quantity — the automated
    subset, which the benchmark reads on its own."""
    (two_files / "part-c.xlsx").write_bytes(build_workbook([
        ["Suite", "Test Count", "Automated Test Count"],
        ["licensing", 10, 4]]))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system",
                                        "files": ["system/part-c.xlsx"]}]})
    body = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    keys = {c["key"] for c in body["numericColumns"]}
    assert "total_tests" in keys
    assert "automated_test_count" in keys       # kept as its own measure
    assert body["totals"]["automated_test_count"] == 4


async def test_merged_largest_rows_need_a_shared_measure(client, qa_headers, two_files):
    """Ranking a test count against a defect count would be meaningless, so
    largest-rows is offered only when the files share a measure.

    It takes two genuinely different quantities to show this: `Test Count` and
    `TC count` are the same measure under two names and do share.
    """
    (two_files / "part-b.xlsx").write_bytes(build_workbook([
        ["Area", "Defects found"], ["licensing", 4]]))
    await client.post(SNAPSHOTS, headers=qa_headers,
                      json={"period": SEPTEMBER,
                            "layers": [{"layerId": "system", "files": [A, B]}]})
    unshared = (await client.get(f"{DASHBOARD}?merged=true", headers=qa_headers)).json()
    assert {c["key"] for c in unshared["numericColumns"]} == {"total_tests", "defects_found"}
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
    assert shared["totals"] == {"total_tests": 129}       # 10 + 20 + 99
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
