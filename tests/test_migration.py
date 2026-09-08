"""Bringing a pre-snapshot database forward without losing a row.

Rows written before snapshots existed were merged in place over many loads, so
they cannot be split back into the loads they came from. The migration attaches
each testing type's rows to a single imported snapshot instead — stamping them,
never rewriting them — and leaves the old `layer_uploads` documents alone as
the only record of what was loaded and when.
"""
import pytest
from pymongo import MongoClient

from app.config import get_settings
from app.migrate import import_snapshots, migrate_db

DB_NAME = "qi_migration_test"

LEGACY_ROWS = [
    {"section": "Camera testing", "rowIndex": 0, "rowKey": "k1",
     "data": {"test_spec_name": "DP23", "test_spec_tab_name": "(TS) Color",
              "test_count": 1294}},
    {"section": "Camera testing", "rowIndex": 1, "rowKey": "k2",
     "data": {"test_spec_name": "DP23", "test_spec_tab_name": "(TS) Gray",
              "test_count": 1585}},
    {"section": "Microscope testing", "rowIndex": 2, "rowKey": "k3",
     "data": {"test_spec_name": "IX73", "test_spec_tab_name": "(TS) Devices",
              "test_count": 42}},
]
COLUMNS = [
    {"key": "test_spec_name", "label": "Test spec name", "type": "string"},
    {"key": "test_spec_tab_name", "label": "Test spec tab name", "type": "string"},
    {"key": "test_count", "label": "Test Count", "type": "number"},
]


@pytest.fixture
def legacy_db():
    """A database shaped the way one looked before snapshots: rows carrying a
    release but no snapshot, and two upload documents behind them."""
    client = MongoClient(get_settings().mongo_uri)
    client.drop_database(DB_NAME)
    db = client[DB_NAME]

    db.apps.insert_one({"_id": "cellsens", "name": "cellSens", "excelPath": "cellsens"})
    db.releases.insert_one({
        "_id": "cellsens:v4-2", "appId": "cellsens", "releaseId": "v4-2",
        "name": "4.2", "excelPath": "v4.2", "current": True, "order": 0})
    db.layers.insert_one({
        "_id": "cellsens:v4-2:regression", "appId": "cellsens", "releaseId": "v4-2",
        "layerId": "regression", "name": "Regression Testing", "order": 1,
        "columns": COLUMNS})
    db.layer_records.insert_many([
        {**row, "appId": "cellsens", "releaseId": "v4-2", "layerId": "regression",
         "uploadId": "upload-2"}
        for row in LEGACY_ROWS
    ])
    db.layer_uploads.insert_many([
        {"_id": "upload-1", "appId": "cellsens", "releaseId": "v4-2",
         "layerId": "regression", "fileName": "regression-jan.xlsx", "fileSize": 900,
         "columns": COLUMNS, "totalRows": 2, "duplicatesSkipped": 0,
         "uploadedBy": "qa", "uploadedAt": _dt(2026, 1, 14)},
        {"_id": "upload-2", "appId": "cellsens", "releaseId": "v4-2",
         "layerId": "regression", "fileName": "regression-feb.xlsx", "fileSize": 1200,
         "columns": COLUMNS, "totalRows": 4, "duplicatesSkipped": 1,
         "uploadedBy": "qa", "uploadedAt": _dt(2026, 2, 3)},
    ])
    yield db
    client.drop_database(DB_NAME)
    client.close()


def _dt(year, month, day):
    from datetime import datetime, timezone
    return datetime(year, month, day, tzinfo=timezone.utc)


def _rows(db):
    return sorted(db.layer_records.find(projection={"_id": 0}),
                  key=lambda r: r["rowKey"])


def test_dry_run_writes_nothing(legacy_db):
    before = _rows(legacy_db)
    log = import_snapshots(legacy_db, apply=False)
    assert any("imported snapshot" in line for line in log)
    assert legacy_db.snapshots.count_documents({}) == 0
    assert _rows(legacy_db) == before


def test_rows_keep_their_values_and_gain_a_snapshot(legacy_db):
    before = {r["rowKey"]: r["data"] for r in _rows(legacy_db)}
    import_snapshots(legacy_db, apply=True)

    after = _rows(legacy_db)
    assert len(after) == len(LEGACY_ROWS)          # nothing dropped
    assert {r["rowKey"]: r["data"] for r in after} == before  # nothing altered
    assert all(r["snapshotId"] for r in after)     # every row placed
    assert {r["snapshotId"] for r in after} == {
        legacy_db.snapshots.find_one()["_id"]}     # all in the one snapshot


def test_the_imported_snapshot_describes_what_was_there(legacy_db):
    import_snapshots(legacy_db, apply=True)
    snapshot = legacy_db.snapshots.find_one()

    assert snapshot["sequence"] == 1
    assert snapshot["rowCount"] == 3
    assert snapshot["columns"] == COLUMNS
    assert snapshot["identityKeys"] == ["test_spec_name", "test_spec_tab_name"]
    assert sorted(snapshot["sections"]) == ["Camera testing", "Microscope testing"]
    assert snapshot["imported"] is True
    # dated from the last load, and filed under that month
    assert snapshot["period"] == {"year": 2026, "month": 2}
    # every workbook it was ever built from is preserved
    assert [s["fileName"] for s in snapshot["sources"]] == [
        "regression-jan.xlsx", "regression-feb.xlsx"]
    assert snapshot["contentHash"]


def test_the_old_upload_history_is_left_alone(legacy_db):
    import_snapshots(legacy_db, apply=True)
    assert legacy_db.layer_uploads.count_documents({}) == 2
    assert legacy_db.layer_uploads.find_one({"_id": "upload-1"})["fileName"] == \
        "regression-jan.xlsx"


def test_running_it_twice_changes_nothing(legacy_db):
    import_snapshots(legacy_db, apply=True)
    snapshot_id = legacy_db.snapshots.find_one()["_id"]
    rows = _rows(legacy_db)

    assert import_snapshots(legacy_db, apply=True) == []
    assert legacy_db.snapshots.count_documents({}) == 1
    assert legacy_db.snapshots.find_one()["_id"] == snapshot_id
    assert _rows(legacy_db) == rows


def test_the_whole_migration_runs_end_to_end(legacy_db):
    """The release, path and snapshot steps together, on one old database."""
    log = migrate_db(legacy_db, "Initial release", apply=True)
    assert any("imported snapshot" in line for line in log)

    # the application no longer carries a path of its own
    assert "excelPath" not in legacy_db.apps.find_one()
    # these rows arrived by browser upload — no folder was ever read for them,
    # so there is no path worth preserving and the release falls back to the
    # derived <application>/<release>
    assert legacy_db.releases.find_one()["excelPath"] == ""
    # and the rows are intact under one snapshot
    rows = _rows(legacy_db)
    assert len(rows) == 3
    assert {r["rowKey"] for r in rows} == {"k1", "k2", "k3"}
    assert legacy_db.snapshots.count_documents({}) == 1


# --- rows from before releases existed ------------------------------------
# The oldest databases predate releases as well as snapshots, so their rows
# carry neither. Mongo omits a missing field from a $group key entirely, and a
# dry run writes nothing — so the release tagging has not happened by the time
# the snapshot import reads them, and it has to cope.


@pytest.fixture
def pre_release_db(legacy_db):
    """The same database with the releaseId stripped off every row, which is
    how a database that predates releases actually looks."""
    legacy_db.layer_records.update_many({}, {"$unset": {"releaseId": ""}})
    legacy_db.layer_uploads.update_many({}, {"$unset": {"releaseId": ""}})
    return legacy_db


def test_a_dry_run_survives_rows_that_have_no_release_yet(pre_release_db):
    """This crashed with KeyError: 'releaseId' — the dry run was unusable on
    exactly the databases the migration exists for."""
    log = migrate_db(pre_release_db, "Initial release", apply=False)
    assert any("imported snapshot" in line for line in log)
    assert pre_release_db.snapshots.count_documents({}) == 0   # still a dry run


def test_the_dry_run_names_the_release_the_rows_will_move_into(pre_release_db):
    log = migrate_db(pre_release_db, "Initial release", apply=False)
    imported = next(line for line in log if "imported snapshot" in line)
    assert "cellsens/v4-2/regression" in imported
    assert "3 rows" in imported


def test_applying_it_places_every_row(pre_release_db):
    migrate_db(pre_release_db, "Initial release", apply=True)
    rows = list(pre_release_db.layer_records.find())
    assert len(rows) == 3
    assert all(r["snapshotId"] for r in rows)
    assert all(r["releaseId"] == "v4-2" for r in rows)
    snapshot = pre_release_db.snapshots.find_one()
    assert snapshot["releaseId"] == "v4-2" and snapshot["appId"] == "cellsens"


def test_rows_belonging_to_no_application_are_skipped_not_crashed_on(legacy_db):
    """An appId with no application document has no release to move into.
    Reporting that beats failing the whole migration for everyone else."""
    legacy_db.layer_records.insert_one({
        "appId": "deleted-app", "layerId": "unit", "rowKey": "orphan",
        "section": "General", "rowIndex": 0, "data": {"name": "x"}})
    log = migrate_db(legacy_db, "Initial release", apply=True)
    assert any("belong to no application" in line for line in log)
    # the real rows still migrated
    assert legacy_db.snapshots.count_documents({}) == 1
    orphan = legacy_db.layer_records.find_one({"appId": "deleted-app"})
    assert "snapshotId" not in orphan          # left exactly as it was


def test_a_release_that_read_from_a_folder_keeps_its_exact_path(legacy_db):
    """The other branch: rows loaded from disk carry a sourcePath, so the
    folder that produced them is spelled out in full rather than guessed."""
    legacy_db.layer_uploads.update_one(
        {"_id": "upload-2"},
        {"$set": {"sourcePath": r"A:\office excel files\cellsens\v4.2\regression.xlsx"}})
    migrate_db(legacy_db, "Initial release", apply=True)
    assert legacy_db.releases.find_one()["excelPath"] == "cellsens/v4.2"
