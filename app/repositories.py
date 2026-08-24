"""Thin async Mongo queries. Documents are stored camelCase (= wire format),
so repositories only reshape ids and project fields."""
import re
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.db import get_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def find_user(username: str) -> dict | None:
    return await get_db().users.find_one({"username": username})


# --- apps ---------------------------------------------------------------


async def _counts_by(collection, field: str) -> dict[str, int]:
    cursor = await collection.aggregate([{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}])
    return {r["_id"]: r["n"] async for r in cursor}


async def list_apps() -> list[dict]:
    db = get_db()
    docs = await db.apps.find().sort("_id", 1).to_list(None)
    layer_counts = await _counts_by(db.layers, "appId")
    record_counts = await _counts_by(db.layer_records, "appId")
    for d in docs:
        d["id"] = d.pop("_id")
        d["layerCount"] = layer_counts.get(d["id"], 0)
        d["recordCount"] = record_counts.get(d["id"], 0)
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
    await db.layer_uploads.delete_many({"appId": app_id})
    await db.layers.delete_many({"appId": app_id})
    await db.apps.delete_one({"_id": app_id})


# --- layers -------------------------------------------------------------


async def list_layers(app_id: str) -> list[dict]:
    db = get_db()
    docs = await db.layers.find({"appId": app_id}).sort("order", 1).to_list(None)
    uploads_cursor = await db.layer_uploads.aggregate([
        {"$match": {"appId": app_id}},
        {"$sort": {"uploadedAt": -1}},
        {"$group": {"_id": "$layerId", "at": {"$first": "$uploadedAt"}, "file": {"$first": "$fileName"}}},
    ])
    latest = {u["_id"]: u async for u in uploads_cursor}
    counts_cursor = await db.layer_records.aggregate([
        {"$match": {"appId": app_id}},
        {"$group": {"_id": "$layerId", "n": {"$sum": 1}}},
    ])
    record_counts = {c["_id"]: c["n"] async for c in counts_cursor}
    return [
        {
            "id": d["layerId"],
            "name": d["name"],
            "short": d.get("short", ""),
            "desc": d.get("desc", ""),
            "order": d.get("order", 0),
            "recordCount": record_counts.get(d["layerId"], 0),
            "lastUploadAt": latest.get(d["layerId"], {}).get("at"),
            "lastUploadFile": latest.get(d["layerId"], {}).get("file"),
        }
        for d in docs
    ]


async def get_layer(app_id: str, layer_id: str) -> dict | None:
    return await get_db().layers.find_one({"_id": f"{app_id}:{layer_id}"})


async def create_layer(doc: dict) -> None:
    await get_db().layers.insert_one(doc)


async def create_layers(docs: list[dict]) -> None:
    """Insert several layers at once — used to lay down an application's defaults."""
    if docs:
        await get_db().layers.insert_many(docs)


async def set_layer_orders(app_id: str, orders: dict[str, int]) -> None:
    """Renumber layers in one round trip. Used when a layer is inserted into
    the middle of the pyramid and everything above it shifts up a slot."""
    if not orders:
        return
    now = _now()
    await get_db().layers.bulk_write([
        UpdateOne({"_id": f"{app_id}:{layer_id}"}, {"$set": {"order": order, "updatedAt": now}})
        for layer_id, order in orders.items()
    ])


async def update_layer(app_id: str, layer_id: str, patch: dict) -> None:
    await get_db().layers.update_one(
        {"_id": f"{app_id}:{layer_id}"}, {"$set": {**patch, "updatedAt": _now()}}
    )


async def delete_layer_cascade(app_id: str, layer_id: str) -> None:
    db = get_db()
    await db.layer_records.delete_many({"appId": app_id, "layerId": layer_id})
    await db.layer_uploads.delete_many({"appId": app_id, "layerId": layer_id})
    await db.layers.delete_one({"_id": f"{app_id}:{layer_id}"})


# --- uploads / records --------------------------------------------------


async def get_layer_file_hashes(app_id: str, layer_id: str) -> dict[str, str]:
    """Latest ingested fingerprint per source workbook, keyed by absolute path.

    Keyed per file rather than per layer so a layer fed by several workbooks
    reports only the ones that actually changed.
    """
    docs = await get_db().layer_uploads.find(
        {"appId": app_id, "layerId": layer_id},
        projection={"sourcePath": 1, "fileHash": 1, "uploadedAt": 1},
    ).sort("uploadedAt", 1).to_list(None)
    return {d["sourcePath"]: d.get("fileHash", "")
            for d in docs if d.get("sourcePath")}  # later uploads win


async def get_latest_upload(app_id: str, layer_id: str) -> dict | None:
    d = await get_db().layer_uploads.find({"appId": app_id, "layerId": layer_id}) \
        .sort("uploadedAt", -1).limit(1).to_list(1)
    if not d:
        return None
    doc = d[0]
    doc["uploadId"] = doc.pop("_id")
    return doc


async def merge_layer_data(
    app_id: str, layer_id: str, upload_doc: dict, rows: list, replace: bool = False,
) -> dict:
    """Store parsed rows by rowKey. Merge (default): upsert against existing
    rows, keeping rows absent from the new file. Replace: the layer's records
    are wiped first, so the file becomes the whole dataset.
    Returns {"inserted": n, "updated": n, "unchanged": n}."""
    db = get_db()
    scope = {"appId": app_id, "layerId": layer_id}
    if replace:
        await db.layer_records.delete_many(scope)
        existing: dict = {}
    else:
        existing = {
            d["rowKey"]: d
            async for d in db.layer_records.find(scope, projection={"rowKey": 1, "data": 1, "section": 1})
        }
    now = _now()
    inserted = updated = unchanged = 0
    to_insert, update_ops = [], []
    for index, row in enumerate(rows):
        prev = existing.get(row.row_key)
        if prev is None:
            inserted += 1
            to_insert.append({
                **scope, "uploadId": upload_doc["_id"], "section": row.section,
                "rowIndex": index, "data": row.values, "rowKey": row.row_key,
                "createdAt": now, "updatedAt": now,
            })
        elif prev["data"] == row.values and prev["section"] == row.section:
            unchanged += 1
        else:
            updated += 1
            update_ops.append((prev["_id"], {
                "section": row.section, "rowIndex": index, "data": row.values,
                "uploadId": upload_doc["_id"], "updatedAt": now,
            }))
    if to_insert:
        await db.layer_records.insert_many(to_insert)
    for _id, patch in update_ops:
        await db.layer_records.update_one({"_id": _id}, {"$set": patch})
    counts = {"inserted": inserted, "updated": updated, "unchanged": unchanged}
    await db.layer_uploads.insert_one({**upload_doc, **counts})
    await db.layers.update_one(
        {"_id": f"{app_id}:{layer_id}"},
        {"$set": {"columns": upload_doc["columns"], "updatedAt": now}},
    )
    return counts


async def clear_layer_records(app_id: str, layer_id: str) -> int:
    db = get_db()
    result = await db.layer_records.delete_many({"appId": app_id, "layerId": layer_id})
    await db.layer_uploads.delete_many({"appId": app_id, "layerId": layer_id})
    await db.layers.update_one({"_id": f"{app_id}:{layer_id}"},
                               {"$unset": {"columns": ""}, "$set": {"updatedAt": _now()}})
    return result.deleted_count


async def clear_last_upload(app_id: str, layer_id: str) -> int:
    """Remove the most recent upload: its upload record plus every row it
    inserted or last touched (rows merged-over by it carry its uploadId)."""
    db = get_db()
    latest = await get_latest_upload(app_id, layer_id)
    if latest is None:
        return 0
    result = await db.layer_records.delete_many(
        {"appId": app_id, "layerId": layer_id, "uploadId": latest["uploadId"]})
    await db.layer_uploads.delete_one({"_id": latest["uploadId"]})
    # the previous upload's columns become the active ones again
    previous = await get_latest_upload(app_id, layer_id)
    if previous is not None:
        await db.layers.update_one({"_id": f"{app_id}:{layer_id}"},
                                   {"$set": {"columns": previous["columns"], "updatedAt": _now()}})
    else:
        await db.layers.update_one({"_id": f"{app_id}:{layer_id}"},
                                   {"$unset": {"columns": ""}, "$set": {"updatedAt": _now()}})
    return result.deleted_count


async def list_records(
    app_id: str, layer_id: str, columns: list[dict],
    search: str | None, section: str | None, skip: int, limit: int,
) -> tuple[int, list[dict]]:
    query: dict = {"appId": app_id, "layerId": layer_id}
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


async def aggregate_dashboard(app_id: str, layer_id: str, columns: list[dict]) -> dict:
    db = get_db()
    scope = {"appId": app_id, "layerId": layer_id}
    numeric = [c for c in columns if c["type"] == "number"]
    group: dict = {"_id": "$section", "rowCount": {"$sum": 1}}
    for col in numeric:
        group[col["key"]] = {"$sum": f"$data.{col['key']}"}
    section_cursor = await db.layer_records.aggregate([
        {"$match": scope}, {"$group": group}, {"$sort": {"_id": 1}},
    ])
    per_section = await section_cursor.to_list(None)
    by_section = [
        {"section": s["_id"], "rowCount": s["rowCount"],
         "sums": {c["key"]: s.get(c["key"]) or 0 for c in numeric}}
        for s in per_section
    ]
    totals = {c["key"]: sum(s["sums"][c["key"]] for s in by_section) for c in numeric}
    total_rows = sum(s["rowCount"] for s in by_section)

    top_rows = []
    if numeric:
        primary = numeric[-1]["key"]  # rightmost numeric column is the measure
        string_keys = [c["key"] for c in columns if c["type"] == "string"]
        docs = await db.layer_records.find(scope, projection={"section": 1, "data": 1}) \
            .sort(f"data.{primary}", -1).limit(10).to_list(None)
        for d in docs:
            label = " · ".join(str(d["data"].get(k)) for k in string_keys if d["data"].get(k))
            top_rows.append({"section": d["section"], "label": label or d["section"],
                             "value": d["data"].get(primary) or 0})
    return {"totalRows": total_rows, "sectionCount": len(by_section),
            "numericColumns": numeric, "totals": totals,
            "bySection": by_section, "topRows": top_rows}


# --- settings / users ---------------------------------------------------


async def get_settings_doc() -> dict | None:
    d = await get_db().settings.find_one({"_id": "app"})
    if d is not None:
        d.pop("_id")
    return d


async def put_settings_doc(doc: dict) -> None:
    await get_db().settings.replace_one({"_id": "app"}, {"_id": "app", **doc}, upsert=True)


async def list_users() -> list[dict]:
    return await get_db().users.find(projection={"_id": 0, "passwordHash": 0}).to_list(None)


async def create_user(doc: dict) -> None:
    await get_db().users.insert_one(doc)


async def delete_user(username: str) -> None:
    await get_db().users.delete_one({"username": username})
