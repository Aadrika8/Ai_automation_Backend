"""Thin async Mongo queries. Documents are stored camelCase (= wire format),
so repositories only reshape ids and project fields.

Everything below the application level is scoped by (appId, releaseId):
layers, snapshots and records all belong to one release, so loading a new
release can never touch what an earlier one holds.

Records are written once, as part of a snapshot, and never updated in place —
which is what lets an earlier snapshot keep the numbers it was taken with.

A snapshot covers exactly one workbook. Two files feeding the same testing
type are two independent datasets with their own sequence, columns, records
and history; nothing in here ever puts their rows together.
"""
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pymongo import UpdateOne

from app.db import get_db
from app.services import profile as prof
from app.layer_defaults import layer_id as layer_key


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def find_user(username: str) -> dict | None:
    return await get_db().users.find_one({"username": username})


# --- apps ---------------------------------------------------------------


async def _counts_by(collection, field: str, match: dict | None = None) -> dict[str, int]:
    pipeline: list[dict] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": f"${field}", "n": {"$sum": 1}}})
    cursor = await collection.aggregate(pipeline)
    return {r["_id"]: r["n"] async for r in cursor}


async def list_apps() -> list[dict]:
    db = get_db()
    docs = await db.apps.find().sort("_id", 1).to_list(None)
    release_counts = await _counts_by(db.releases, "appId")
    # rows in each testing type's current snapshot, not every row ever stored
    record_counts = {d["_id"]: sum((await current_row_counts(d["_id"])).values())
                     for d in docs}
    # the release each application opens on, so its card can name it
    releases = await db.releases.find(
        projection={"appId": 1, "releaseId": 1, "name": 1, "current": 1, "order": 1}
    ).sort("order", -1).to_list(None)
    current: dict[str, dict] = {}
    for r in releases:  # newest first; an explicit current flag overrides it
        current.setdefault(r["appId"], r)
        if r.get("current"):
            current[r["appId"]] = r
    for d in docs:
        d["id"] = d.pop("_id")
        d["releaseCount"] = release_counts.get(d["id"], 0)
        d["recordCount"] = record_counts.get(d["id"], 0)
        d["currentRelease"] = current.get(d["id"], {}).get("name", "")
        d["currentReleaseId"] = current.get(d["id"], {}).get("releaseId", "")
    return docs


async def get_app(app_id: str) -> dict | None:
    return await get_db().apps.find_one({"_id": app_id})


async def create_app(doc: dict) -> None:
    await get_db().apps.insert_one(doc)


async def update_app(app_id: str, patch: dict) -> None:
    await get_db().apps.update_one({"_id": app_id}, {"$set": {**patch, "updatedAt": _now()}})


async def delete_app_cascade(app_id: str) -> None:
    db = get_db()
    await db.layer_records.delete_many({"appId": app_id})
    await db.snapshots.delete_many({"appId": app_id})
    await db.layers.delete_many({"appId": app_id})
    await db.trace_configs.delete_many({"appId": app_id})
    await db.releases.delete_many({"appId": app_id})
    await db.apps.delete_one({"_id": app_id})


# --- releases -----------------------------------------------------------
# `order` increases with every new release, so the newest carries the highest
# value. Lists come back newest-first, the order the release switcher shows.


async def list_releases(app_id: str) -> list[dict]:
    db = get_db()
    docs = await db.releases.find({"appId": app_id}).sort("order", -1).to_list(None)
    layer_counts = await _counts_by(db.layers, "releaseId", {"appId": app_id})
    record_counts = await current_row_counts(app_id)
    snapshot_cursor = await db.snapshots.aggregate([
        {"$match": {"appId": app_id}},
        {"$group": {"_id": "$releaseId", "at": {"$max": "$createdAt"}}},
    ])
    latest = {u["_id"]: u["at"] async for u in snapshot_cursor}
    return [
        {
            "id": d["releaseId"],
            "name": d["name"],
            "desc": d.get("desc", ""),
            "excelPath": d.get("excelPath", ""),
            "current": bool(d.get("current")),
            "order": d.get("order", 0),
            "layerCount": layer_counts.get(d["releaseId"], 0),
            "recordCount": record_counts.get(d["releaseId"], 0),
            "createdAt": d.get("createdAt"),
            "lastUploadAt": latest.get(d["releaseId"]),  # newest snapshot
        }
        for d in docs
    ]


async def get_release(app_id: str, release_id: str) -> dict | None:
    return await get_db().releases.find_one({"_id": f"{app_id}:{release_id}"})


async def get_current_release(app_id: str) -> dict | None:
    """The release the UI opens on: the flagged one, else the newest."""
    db = get_db()
    flagged = await db.releases.find_one({"appId": app_id, "current": True})
    if flagged is not None:
        return flagged
    newest = await db.releases.find({"appId": app_id}).sort("order", -1).limit(1).to_list(1)
    return newest[0] if newest else None


async def next_release_order(app_id: str) -> int:
    newest = await get_db().releases.find({"appId": app_id}, projection={"order": 1}) \
        .sort("order", -1).limit(1).to_list(1)
    return (newest[0].get("order", 0) + 1) if newest else 0


async def create_release(doc: dict) -> None:
    await get_db().releases.insert_one(doc)


async def update_release(app_id: str, release_id: str, patch: dict) -> None:
    await get_db().releases.update_one(
        {"_id": f"{app_id}:{release_id}"}, {"$set": {**patch, "updatedAt": _now()}})


async def set_current_release(app_id: str, release_id: str) -> None:
    """Promote one release, demoting whichever held the flag before."""
    db = get_db()
    await db.releases.update_many(
        {"appId": app_id, "releaseId": {"$ne": release_id}}, {"$set": {"current": False}})
    await db.releases.update_one(
        {"_id": f"{app_id}:{release_id}"}, {"$set": {"current": True, "updatedAt": _now()}})


async def delete_release_cascade(app_id: str, release_id: str) -> None:
    db = get_db()
    scope = {"appId": app_id, "releaseId": release_id}
    await db.layer_records.delete_many(scope)
    await db.snapshots.delete_many(scope)
    await db.layers.delete_many(scope)
    await db.trace_configs.delete_many(scope)
    await db.releases.delete_one({"_id": f"{app_id}:{release_id}"})
    # never leave an application without a release to open
    if await db.releases.count_documents({"appId": app_id, "current": True}) == 0:
        newest = await get_current_release(app_id)
        if newest is not None:
            await set_current_release(app_id, newest["releaseId"])


# --- layers -------------------------------------------------------------


async def list_layers(app_id: str, release_id: str) -> list[dict]:
    """Testing types with the state of their current snapshot.

    Row counts describe the newest snapshot only — the pyramid check and the
    layer cards are about what the release holds now, not everything ever
    loaded into it.
    """
    db = get_db()
    scope = {"appId": app_id, "releaseId": release_id}
    docs = await db.layers.find(scope).sort("order", 1).to_list(None)
    # current snapshot per file, then rolled up per testing type. The row count
    # is summed across files purely so the testing pyramid has a figure to
    # compare — no combined dataset is created or stored.
    cursor = await db.snapshots.aggregate([
        {"$match": scope},
        {"$sort": {"sequence": -1}},
        {"$group": {"_id": {"layer": "$layerId", "file": "$file"},
                    "loads": {"$sum": 1},
                    "at": {"$first": "$createdAt"},
                    "rowCount": {"$first": "$rowCount"},
                    "period": {"$first": "$period"}}},
        {"$sort": {"at": -1}},
        {"$group": {"_id": "$_id.layer",
                    "fileCount": {"$sum": 1},
                    "snapshotCount": {"$sum": "$loads"},
                    "recordCount": {"$sum": "$rowCount"},
                    "at": {"$first": "$at"},
                    "period": {"$first": "$period"},
                    "files": {"$push": "$_id.file"}}},
    ])
    latest = {c["_id"]: c async for c in cursor}
    out = []
    for d in docs:
        snap = latest.get(d["layerId"], {})
        out.append({
            "id": d["layerId"],
            "name": d["name"],
            "short": d.get("short", ""),
            "desc": d.get("desc", ""),
            "order": d.get("order", 0),
            "recordCount": snap.get("recordCount", 0),
            "fileCount": snap.get("fileCount", 0),
            "snapshotCount": snap.get("snapshotCount", 0),
            "latestSnapshotAt": snap.get("at"),
            "latestPeriod": snap.get("period"),
            "latestSources": snap.get("files", []),
        })
    return out


async def list_layer_docs(app_id: str, release_id: str) -> list[dict]:
    """Raw layer documents — used when copying a release's structure."""
    return await get_db().layers.find({"appId": app_id, "releaseId": release_id}) \
        .sort("order", 1).to_list(None)


async def get_layer(app_id: str, release_id: str, layer_id: str) -> dict | None:
    return await get_db().layers.find_one({"_id": layer_key(app_id, release_id, layer_id)})


async def create_layer(doc: dict) -> None:
    await get_db().layers.insert_one(doc)


async def create_layers(docs: list[dict]) -> None:
    """Insert several layers at once — used to lay down a release's defaults."""
    if docs:
        await get_db().layers.insert_many(docs)


async def set_layer_orders(app_id: str, release_id: str, orders: dict[str, int]) -> None:
    """Renumber layers in one round trip. Used when a layer is inserted into
    the middle of the pyramid and everything above it shifts up a slot."""
    if not orders:
        return
    now = _now()
    await get_db().layers.bulk_write([
        UpdateOne({"_id": layer_key(app_id, release_id, lid)},
                  {"$set": {"order": order, "updatedAt": now}})
        for lid, order in orders.items()
    ])


async def update_layer(app_id: str, release_id: str, layer_id: str, patch: dict) -> None:
    await get_db().layers.update_one(
        {"_id": layer_key(app_id, release_id, layer_id)},
        {"$set": {**patch, "updatedAt": _now()}},
    )


async def delete_layer_cascade(app_id: str, release_id: str, layer_id: str) -> None:
    db = get_db()
    scope = {"appId": app_id, "releaseId": release_id, "layerId": layer_id}
    await db.layer_records.delete_many(scope)
    await db.snapshots.delete_many(scope)
    await db.layers.delete_one({"_id": layer_key(app_id, release_id, layer_id)})


# --- snapshots / records ------------------------------------------------
# A snapshot is one complete, read-only reading of ONE workbook. Rows are only
# ever inserted: an earlier snapshot's rows stay exactly as they were written,
# which is what makes history durable rather than a convention.
#
# `file` — the workbook's path relative to the release folder — is what makes
# one dataset distinct from another. Sequence numbers run per file, so
# part-a.xlsx and part-b.xlsx each have their own #1, #2, #3.


def _scope(app_id: str, release_id: str, layer_id: str, file: str | None = None) -> dict:
    scope = {"appId": app_id, "releaseId": release_id, "layerId": layer_id}
    if file is not None:
        scope["file"] = file
    return scope


async def next_snapshot_sequence(app_id: str, release_id: str, layer_id: str,
                                 file: str) -> int:
    """One past the highest sequence this file has used.

    Deleting a snapshot frees its number again. That is deliberate: a snapshot
    is deleted when the load was a mistake, and the corrected load genuinely is
    the nth reading of that file. Links use the snapshot id, not the number, so
    nothing points at the freed slot.
    """
    newest = await get_db().snapshots.find(
        _scope(app_id, release_id, layer_id, file), projection={"sequence": 1}
    ).sort("sequence", -1).limit(1).to_list(1)
    return (newest[0].get("sequence", 0) + 1) if newest else 1


async def latest_snapshot(app_id: str, release_id: str, layer_id: str,
                          file: str | None = None) -> dict | None:
    """The current snapshot of one file, or — with no file — of the file that
    was loaded most recently, which is what a testing type opens on."""
    db = get_db()
    if file is not None:
        found = await db.snapshots.find(
            _scope(app_id, release_id, layer_id, file)
        ).sort("sequence", -1).limit(1).to_list(1)
        return found[0] if found else None
    current = await list_current_snapshots(app_id, release_id, layer_id)
    return current[0] if current else None


async def list_current_snapshots(app_id: str, release_id: str,
                                 layer_id: str) -> list[dict]:
    """The current snapshot of every file feeding this testing type, most
    recently loaded first. Each is a separate dataset — they are listed side
    by side, never added together."""
    cursor = await get_db().snapshots.aggregate([
        {"$match": _scope(app_id, release_id, layer_id)},
        {"$sort": {"sequence": -1}},
        {"$group": {"_id": "$file", "doc": {"$first": "$$ROOT"},
                    "snapshotCount": {"$sum": 1}}},
        {"$sort": {"doc.createdAt": -1}},
    ])
    out = []
    async for row in cursor:
        doc = row["doc"]
        doc["snapshotCount"] = row["snapshotCount"]
        out.append(doc)
    return out


async def list_snapshots(app_id: str, release_id: str, layer_id: str,
                         file: str | None = None) -> list[dict]:
    """History for this testing type — every file's snapshots together, newest
    load first, or just one file's when a file is named.

    `isCurrent` always means "current for its own file", so in a combined list
    one snapshot per file carries it.
    """
    docs = await get_db().snapshots.find(
        _scope(app_id, release_id, layer_id, file)
    ).sort("createdAt", -1).to_list(None)
    seen: set[str] = set()
    for doc in docs:
        doc["id"] = doc.pop("_id")
        # the first time a file appears in this newest-first list is its current
        doc["isCurrent"] = doc["file"] not in seen
        seen.add(doc["file"])
    return docs


async def get_snapshot(app_id: str, release_id: str, snapshot_id: str) -> dict | None:
    doc = await get_db().snapshots.find_one(
        {"_id": snapshot_id, "appId": app_id, "releaseId": release_id})
    if doc is not None:
        doc["id"] = doc.pop("_id")
    return doc


async def resolve_snapshot(
    app_id: str, release_id: str, layer_id: str, file: str | None = None,
    snapshot_id: str | None = None, year: int | None = None, month: int | None = None,
) -> dict | None:
    """Which snapshot a read is about.

    A snapshot named outright wins. Otherwise the file decides the dataset —
    the most recently loaded one when none is named — and the month, or the
    absence of one, decides which of that file's loads to show.
    """
    db = get_db()
    if snapshot_id:
        doc = await db.snapshots.find_one(
            {"_id": snapshot_id, **_scope(app_id, release_id, layer_id)})
    else:
        if file is None:
            newest_file = await latest_snapshot(app_id, release_id, layer_id)
            if newest_file is None:
                return None
            file = newest_file["file"]
        if year and month:
            # a month can hold several loads of one file; the last one is what
            # that month means for it
            found = await db.snapshots.find({
                **_scope(app_id, release_id, layer_id, file),
                "period.year": year, "period.month": month,
            }).sort("sequence", -1).limit(1).to_list(1)
            doc = found[0] if found else None
        else:
            doc = await latest_snapshot(app_id, release_id, layer_id, file)
    if doc is None:
        return None
    current = await latest_snapshot(app_id, release_id, layer_id, doc["file"])
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    doc["isCurrent"] = current is not None and current["_id"] == doc["id"]
    return doc


async def snapshot_rows(snapshot_id: str, limit: int | None = None) -> list[dict]:
    """Row keys and values of a snapshot — what a diff is computed against.

    A diff needs every row and passes no limit. The inventory dashboard reads
    identifiers out of the values instead, which cannot be done in the query,
    so it caps what it pulls back.
    """
    cursor = get_db().layer_records.find(
        {"snapshotId": snapshot_id}, projection={"rowKey": 1, "data": 1, "_id": 0}
    ).sort([("section", 1), ("rowIndex", 1)])
    if limit is not None:
        cursor = cursor.limit(limit)
    return await cursor.to_list(None)


async def create_snapshot(doc: dict, rows: list) -> dict:
    """Write one file's snapshot and its rows. Nothing existing is touched."""
    db = get_db()
    now = _now()
    await db.snapshots.insert_one(doc)
    if rows:
        await db.layer_records.insert_many([
            {
                "appId": doc["appId"], "releaseId": doc["releaseId"],
                "layerId": doc["layerId"], "snapshotId": doc["_id"],
                "file": doc["file"], "period": doc["period"],
                "section": row.section, "rowIndex": index,
                "data": row.values, "rowKey": row.row_key,
                "createdAt": now,
            }
            for index, row in enumerate(rows)
        ])
    stored = dict(doc)
    stored["id"] = stored.pop("_id")
    stored["isCurrent"] = True
    return stored


async def move_file_series(app_id: str, release_id: str, layer_id: str,
                           old_file: str, new_file: str) -> int:
    """Carry one workbook's whole history onto its new name.

    A rename does not make new data, so it must not make a new dataset: the
    snapshots keep their sequence numbers, their rows and their diffs, and only
    the name they are filed under moves. Left to fork instead, the old series
    would keep being counted alongside the new one and the layer's record count
    would double.

    Values are never touched — this restamps an identity, the way
    `update_snapshot_period` restamps a month.
    """
    db = get_db()
    scope = _scope(app_id, release_id, layer_id, old_file)
    moved = await db.snapshots.update_many(scope, {"$set": {"file": new_file}})
    await db.layer_records.update_many(scope, {"$set": {"file": new_file}})
    return moved.modified_count


async def update_snapshot_period(app_id: str, release_id: str, snapshot_id: str,
                                 period: dict) -> None:
    """Correct the month a snapshot is filed under. Its data never changes."""
    db = get_db()
    await db.snapshots.update_one(
        {"_id": snapshot_id, "appId": app_id, "releaseId": release_id},
        {"$set": {"period": period, "updatedAt": _now()}})
    await db.layer_records.update_many(
        {"snapshotId": snapshot_id}, {"$set": {"period": period}})


async def delete_snapshot(app_id: str, release_id: str, snapshot_id: str) -> int:
    """Remove one snapshot and the rows it wrote — nothing else.

    Every other snapshot, testing type and release is untouched, and the one
    before it becomes current again simply by being the highest-numbered.
    """
    db = get_db()
    result = await db.layer_records.delete_many({"snapshotId": snapshot_id})
    await db.snapshots.delete_one(
        {"_id": snapshot_id, "appId": app_id, "releaseId": release_id})
    return result.deleted_count


async def delete_layer_snapshots(app_id: str, release_id: str, layer_id: str,
                                 file: str | None = None) -> int:
    """Every snapshot of one testing type, or of one of its files."""
    db = get_db()
    scope = _scope(app_id, release_id, layer_id, file)
    result = await db.layer_records.delete_many(scope)
    await db.snapshots.delete_many(scope)
    return result.deleted_count


async def list_records(
    snapshot_id: str, columns: list[dict],
    search: str | None, section: str | None, skip: int, limit: int,
) -> tuple[int, list[dict]]:
    query: dict = {"snapshotId": snapshot_id}
    if section:
        query["section"] = section
    if search:
        clauses: list[dict] = [{"section": {"$regex": re.escape(search), "$options": "i"}}]
        for col in columns:
            field = f"data.{col['key']}"
            if col["type"] == "string":
                clauses.append({field: {"$regex": re.escape(search), "$options": "i"}})
            else:
                try:
                    num = float(search)
                    clauses.append({field: int(num) if num.is_integer() else num})
                except ValueError:
                    pass
        query["$or"] = clauses
    coll = get_db().layer_records
    total = await coll.count_documents(query)
    docs = await coll.find(query, projection={"section": 1, "data": 1}) \
        .sort([("section", 1), ("rowIndex", 1)]).skip(skip).limit(limit).to_list(None)
    return total, docs


async def aggregate_dashboard(snapshot_id: str, columns: list[dict]) -> dict:
    db = get_db()
    scope = {"snapshotId": snapshot_id}
    numeric = [c for c in columns if c["type"] == "number"]
    group: dict = {"_id": "$section", "rowCount": {"$sum": 1}}
    for col in numeric:
        group[col["key"]] = {"$sum": f"$data.{col['key']}"}
    section_cursor = await db.layer_records.aggregate([
        {"$match": scope}, {"$group": group}, {"$sort": {"_id": 1}},
    ])
    per_section = await section_cursor.to_list(None)

    # One sheet has one name per measure, so there is nothing to fold here —
    # but the measure is reported under the same logical name the merged view
    # uses, so a card does not change its title when you switch between them.
    identity = {c["key"]: prof.measure_identity(c) for c in numeric}
    shown = [{"key": identity[c["key"]][0], "label": identity[c["key"]][1],
              "type": "number"} for c in numeric]

    by_section = [
        {"section": s["_id"], "rowCount": s["rowCount"],
         "sums": {identity[c["key"]][0]: s.get(c["key"]) or 0 for c in numeric}}
        for s in per_section
    ]
    totals = {m["key"]: sum(s["sums"][m["key"]] for s in by_section) for m in shown}
    total_rows = sum(s["rowCount"] for s in by_section)

    top_rows = []
    if numeric:
        primary = numeric[-1]["key"]  # rightmost numeric column is the measure
        string_keys = [c["key"] for c in columns if c["type"] == "string"]
        # A measure column may hold the odd cell that is not a number, and
        # MongoDB sorts a string above every number — so the largest rows would
        # open with "Not Decided". Rank only what can be ranked.
        docs = await db.layer_records.find(
            {**scope, f"data.{primary}": {"$type": "number"}},
            projection={"section": 1, "data": 1}
        ).sort(f"data.{primary}", -1).limit(10).to_list(None)
        for d in docs:
            label = " · ".join(str(d["data"].get(k)) for k in string_keys if d["data"].get(k))
            top_rows.append({"section": d["section"], "label": label or d["section"],
                             "value": d["data"].get(primary) or 0})
    return {"totalRows": total_rows, "sectionCount": len(by_section),
            "numericColumns": shown, "totals": totals,
            "bySection": by_section, "topRows": top_rows,
            # keyed by the sheet's own column names, for the callers that read
            # a specific column — the run-results profile looks up `Pass` and
            # `Fail`, not a logical measure. Not part of the wire shape.
            "rawTotals": {c["key"]: sum(s.get(c["key"]) or 0 for s in per_section)
                          for c in numeric}}


SAMPLE_VALUES = 25


async def column_stats(snapshot_id: str, columns: list[dict]) -> dict[str, dict]:
    """What each text column holds: how full it is, how many different values,
    and a sample of them.

    Three questions are answered from this. Whether a column can group anything
    — `OS ver.` has two values over thirty-nine rows and groups them usefully,
    `cellSens Edition` has twenty-five and groups nothing. Whether it records an
    outcome, which is decided by reading the values rather than the label.
    And which column best describes a row.

    One `$facet` asks for every column at once rather than a round trip each.
    """
    text = [c for c in columns if c.get("type") == "string"][:16]
    if not text:
        return {}
    facet = {
        col["key"]: [
            {"$match": {f'data.{col["key"]}': {"$ne": None}}},
            {"$group": {"_id": f'$data.{col["key"]}', "n": {"$sum": 1}}},
            {"$group": {"_id": None, "distinct": {"$sum": 1},
                        "filled": {"$sum": "$n"},
                        "values": {"$push": "$_id"}}},
            {"$project": {"distinct": 1, "filled": 1,
                          "values": {"$slice": ["$values", SAMPLE_VALUES]}}},
        ]
        for col in text
    }
    cursor = await get_db().layer_records.aggregate([
        {"$match": {"snapshotId": snapshot_id}},
        {"$facet": facet},
    ])
    result = await cursor.to_list(1)
    if not result:
        return {}
    out: dict[str, dict] = {}
    for key, buckets in result[0].items():
        found = buckets[0] if buckets else {}
        out[key] = {"distinct": found.get("distinct", 0),
                    "filled": found.get("filled", 0),
                    "values": found.get("values", [])}
    return out


async def status_breakdown(snapshot_id: str, status_key: str,
                           measure_key: str) -> list[dict]:
    """Each outcome word, how many rows carry it, and what those rows are worth.

    The measure travels with the count because a row is not a test: four rows
    carrying 12, 18, 9 and 14 test cases do not vote equally.
    """
    if not status_key:
        return []
    group: dict = {"_id": f"$data.{status_key}", "rowCount": {"$sum": 1}}
    if measure_key:
        group["measure"] = {"$sum": f"$data.{measure_key}"}
    cursor = await get_db().layer_records.aggregate([
        {"$match": {"snapshotId": snapshot_id, f"data.{status_key}": {"$ne": None}}},
        {"$group": group},
        {"$sort": {"rowCount": -1, "_id": 1}},
    ])
    return [{"value": doc["_id"], "rowCount": doc["rowCount"],
             "measure": doc.get("measure") or 0}
            async for doc in cursor]


async def rows_with_status(snapshot_id: str, status_key: str, values: list[str],
                           label_keys: list[str], measure_key: str,
                           limit: int = 20) -> list[dict]:
    """The rows sitting on the outcomes worth acting on — what failed, and what
    nobody has run yet."""
    if not status_key or not values:
        return []
    docs = await get_db().layer_records.find(
        {"snapshotId": snapshot_id, f"data.{status_key}": {"$in": values}},
        projection={"data": 1, "section": 1},
    ).limit(limit).to_list(None)

    out = []
    for doc in docs:
        data = doc.get("data") or {}
        label = " · ".join(str(data[k]) for k in label_keys if data.get(k) is not None)
        out.append({
            "label": label or doc.get("section", ""),
            "status": str(data.get(status_key, "")),
            "measure": float(data.get(measure_key) or 0) if measure_key else 0.0,
        })
    return out


async def dimension_breakdown(snapshot_id: str, dimension: str,
                              measures: list[str], limit: int = 12) -> list[dict]:
    """One snapshot's measures grouped by a column of its own — pass and fail
    per tester, per OS, per install type. Read-time only; nothing is stored."""
    if not dimension:
        return []
    group: dict = {"_id": f"$data.{dimension}", "rowCount": {"$sum": 1}}
    for key in measures:
        if key:
            group[key] = {"$sum": f"$data.{key}"}
    cursor = await get_db().layer_records.aggregate([
        {"$match": {"snapshotId": snapshot_id, f"data.{dimension}": {"$ne": None}}},
        {"$group": group},
        {"$sort": {"rowCount": -1, "_id": 1}},
        {"$limit": limit},
    ])
    return [{"value": str(doc["_id"]), "rowCount": doc["rowCount"],
             **{key: doc.get(key) or 0 for key in measures if key}}
            async for doc in cursor]


async def rows_with_failures(snapshot_id: str, fail_key: str, label_keys: list[str],
                             measures: dict[str, str], limit: int = 10) -> list[dict]:
    """The rows that failed, worst first — where a results sheet is actually read.

    Named by the columns that distinguish one run from another rather than by
    every text column the sheet carries.
    """
    if not fail_key:
        return []
    docs = await get_db().layer_records.find(
        {"snapshotId": snapshot_id, f"data.{fail_key}": {"$gt": 0}},
        projection={"data": 1, "section": 1},
    ).sort(f"data.{fail_key}", -1).limit(limit).to_list(None)

    out = []
    for doc in docs:
        data = doc.get("data") or {}
        label = " · ".join(str(data[k]) for k in label_keys if data.get(k) is not None)
        out.append({
            "label": label or doc.get("section", ""),
            "passed": float(data.get(measures.get("passed", "")) or 0),
            "failed": float(data.get(fail_key) or 0),
            "total": float(data.get(measures.get("total", "")) or 0),
        })
    return out


async def aggregate_merged_dashboard(app_id: str, release_id: str,
                                     layer_id: str) -> dict:
    """Metrics across every file's current snapshot, computed at read time.

    Nothing is written: the records stay exactly where they are, one file per
    snapshot. This only adds their numbers up for a combined view.

    Columns that mean the same quantity are one measure, whatever each sheet
    calls it. Two workbooks counting test cases under `Test Count` and
    `Test Cases` used to produce two separate totals, and then a section from
    the second file read 0 against the first file's column — a wrong number
    rather than a missing one. Which labels count as equivalent is a whitelist
    in `profile.measure_identity`, so nothing folds together on a guess.
    """
    db = get_db()
    current = await list_current_snapshots(app_id, release_id, layer_id)
    if not current:
        return {"totalRows": 0, "sectionCount": 0, "numericColumns": [],
                "totals": {}, "bySection": [], "topRows": []}

    ids = [doc["_id"] for doc in current]

    # Each logical measure and the source columns feeding it. `sources` is what
    # makes the fold possible: the rows still hold whatever their own sheet
    # called the column, so the sum is taken per column and added up after.
    measures: dict[str, dict] = {}
    per_file: list[set[str]] = []
    for doc in current:
        mine: set[str] = set()
        for col in doc.get("columns", []):
            if col.get("type") != "number":
                continue
            key, label = prof.measure_identity(col)
            entry = measures.setdefault(
                key, {"key": key, "label": label, "type": "number", "sources": []})
            if col["key"] not in entry["sources"]:
                entry["sources"].append(col["key"])
            mine.add(key)
        per_file.append(mine)

    # a measure every file carries — after folding, so the two names above
    # count as the same thing and largest-rows is offered again
    shared = set.intersection(*per_file) if per_file else set()
    ordered = list(measures.values())
    source_keys = [k for m in ordered for k in m["sources"]]

    scope = {"snapshotId": {"$in": ids}}
    group: dict = {"_id": "$section", "rowCount": {"$sum": 1}}
    for key in source_keys:
        group[key] = {"$sum": f"$data.{key}"}
    cursor = await db.layer_records.aggregate([
        {"$match": scope}, {"$group": group}, {"$sort": {"_id": 1}},
    ])
    per_section = await cursor.to_list(None)

    def folded(bucket: dict) -> dict:
        return {m["key"]: sum(bucket.get(k) or 0 for k in m["sources"])
                for m in ordered}

    by_section = [
        {"section": s["_id"], "rowCount": s["rowCount"], "sums": folded(s)}
        for s in per_section
    ]
    totals = {m["key"]: sum(s["sums"][m["key"]] for s in by_section) for m in ordered}
    total_rows = sum(s["rowCount"] for s in by_section)

    top_rows: list[dict] = []
    primary = next((m for m in reversed(ordered) if m["key"] in shared), None)
    if primary:
        string_keys = [c["key"] for doc in current
                       for c in doc.get("columns", []) if c["type"] == "string"]
        # ranked per source column and merged here, because one row carries the
        # measure under its own sheet's name and no other
        found: list[tuple[float, dict]] = []
        for key in primary["sources"]:
            docs = await db.layer_records.find(
                {**scope, f"data.{key}": {"$type": "number"}},
                projection={"section": 1, "data": 1, "file": 1}
            ).sort(f"data.{key}", -1).limit(10).to_list(None)
            found += [(float(d["data"][key]), d) for d in docs]
        found.sort(key=lambda pair: -pair[0])
        for value, d in found[:10]:
            label = " · ".join(
                str(d["data"].get(k)) for k in dict.fromkeys(string_keys)
                if d["data"].get(k))
            top_rows.append({"section": d["section"],
                             "label": label or d["section"], "value": value})

    # `sources` is internal bookkeeping; the wire shape stays a column list
    columns_out = [{"key": m["key"], "label": m["label"], "type": "number"}
                   for m in ordered]
    return {"totalRows": total_rows, "sectionCount": len(by_section),
            "numericColumns": columns_out, "totals": totals,
            "bySection": by_section, "topRows": top_rows}


async def current_row_counts(app_id: str, release_id: str | None = None) -> dict:
    """Rows held right now, keyed by release.

    Only the current snapshot of each file counts — totalling every snapshot
    ever taken would report history as if it were live data. Files are summed
    to give a release its headline figure; this is a count, not a merge, and no
    combined records exist anywhere.
    """
    match: dict = {"appId": app_id}
    if release_id is not None:
        match["releaseId"] = release_id
    cursor = await get_db().snapshots.aggregate([
        {"$match": match},
        {"$sort": {"sequence": -1}},
        {"$group": {"_id": {"release": "$releaseId", "layer": "$layerId",
                            "file": "$file"},
                    "rowCount": {"$first": "$rowCount"}}},
        {"$group": {"_id": "$_id.release", "n": {"$sum": "$rowCount"}}},
    ])
    return {r["_id"]: r["n"] async for r in cursor}


# --- settings / users ---------------------------------------------------


async def get_settings_doc() -> dict | None:
    d = await get_db().settings.find_one({"_id": "app"})
    if d is not None:
        d.pop("_id")
    return d


async def put_settings_doc(doc: dict) -> None:
    await get_db().settings.replace_one({"_id": "app"}, {"_id": "app", **doc}, upsert=True)


async def list_users() -> list[dict]:
    docs = await get_db().users.find(projection={"_id": 0, "passwordHash": 0}).to_list(None)
    for doc in docs:
        # Databases seeded before lastActive became a timestamp stored a display
        # string ("2 h ago", "Yesterday"), which carries no recoverable time and
        # fails response validation. Read it as "never seen" rather than letting
        # one stale document 500 the whole list. `python -m app.migrate --apply`
        # clears them for good.
        if not isinstance(doc.get("lastActive"), (datetime, type(None))):
            doc["lastActive"] = None
    return docs


async def create_user(doc: dict) -> None:
    await get_db().users.insert_one(doc)


async def delete_user(username: str) -> None:
    await get_db().users.delete_one({"username": username})


# --- traceability config ------------------------------------------------
# One document per release, keyed like a layer is: the columns it names belong
# to that release's workbooks and mean nothing in another.


def _trace_key(app_id: str, release_id: str) -> str:
    return f"{app_id}:{release_id}"


async def get_trace_config(app_id: str, release_id: str) -> dict | None:
    return await get_db().trace_configs.find_one({"_id": _trace_key(app_id, release_id)})


async def put_trace_config(app_id: str, release_id: str, doc: dict) -> None:
    await get_db().trace_configs.replace_one(
        {"_id": _trace_key(app_id, release_id)},
        {**doc, "_id": _trace_key(app_id, release_id),
         "appId": app_id, "releaseId": release_id},
        upsert=True,
    )


async def coverage_rows(app_id: str, release_id: str, layer_id: str) -> dict:
    """Every row a testing layer currently holds, with the shape it holds it in.

    The current snapshot of *every* file feeding the layer, unioned: a layer
    split across `part-a.xlsx` and `part-b.xlsx` covers what both of them
    cover. Reading only the newest file would silently halve the scope.

    Each row carries the key of *its own file's* first column, because that is
    where the traceability identifier lives and two workbooks feeding one layer
    need not be shaped alike. The union below is still returned for the admin
    picker, but nothing reads the identifier out of it.
    """
    snapshots = await list_current_snapshots(app_id, release_id, layer_id)
    if not snapshots:
        return {"rows": [], "columns": [], "files": [], "snapshotIds": [],
                "latestPeriod": None, "idColumns": {}, "fileNames": {}}

    by_id = {s["_id"]: s for s in snapshots}
    docs = await get_db().layer_records.find(
        {"snapshotId": {"$in": list(by_id)}},
        projection={"section": 1, "data": 1, "snapshotId": 1, "rowIndex": 1},
    ).sort([("snapshotId", 1), ("section", 1), ("rowIndex", 1)]).to_list(None)

    # the first column of each file, by snapshot: the identifier's home
    id_columns = {sid: ((snap.get("columns") or [{}])[0].get("key") or "")
                  for sid, snap in by_id.items()}

    rows = []
    for doc in docs:
        snap = by_id.get(doc["snapshotId"], {})
        rows.append({
            "section": doc.get("section", ""), "data": doc.get("data", {}),
            "file": snap.get("file", ""),
            "fileName": PurePosixPath(snap.get("file", "")).name,
            "snapshotId": doc["snapshotId"],
            "rowIndex": doc.get("rowIndex", 0),
            "idColumn": id_columns.get(doc["snapshotId"], ""),
        })

    # the union of the files' columns: two workbooks feeding one layer may be
    # shaped differently, and the id can sit in either shape
    columns, seen = [], set()
    for snap in snapshots:
        for col in snap.get("columns", []):
            if col["key"] not in seen:
                seen.add(col["key"])
                columns.append(col)

    newest = max(snapshots, key=lambda s: s.get("createdAt") or _EPOCH)
    return {"rows": rows, "columns": columns,
            "files": [s.get("file", "") for s in snapshots],
            "snapshotIds": list(by_id), "latestPeriod": newest.get("period"),
            # {snapshotId: first column key} — reported so the admin screen can
            # show which column each file is actually being read from
            "idColumns": id_columns,
            "fileNames": {s["_id"]: PurePosixPath(s.get("file", "")).name
                          for s in snapshots}}
