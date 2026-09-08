"""Bring an older database onto the current schema.

    python -m app.migrate                      # dry run: prints what it would do
    python -m app.migrate --apply
    python -m app.migrate --apply --release "v4.3"

Two things are fixed, both without deleting a row:

**Releases.** Before releases existed, layers were keyed `<app>:<layer>` and
records carried no release. This walks every application, puts the data it
already holds into one release, and re-keys the layers to
`<app>:<release>:<layer>`. No row's contents change — the existing data becomes
the first release's history, which later releases are then loaded alongside.

**Legacy `lastActive`.** Very early seeds wrote a display string ("2 h ago",
"Yesterday") where a timestamp belongs. Those strings carry no recoverable
time, so `GET /api/users` could not serialize them; they are cleared to null,
which reads as "never seen".

**Split Excel paths.** The catalog folder used to be assembled from three
values (root + an application path + a release path). It is now one path per
release, defaulting to "<application name>/<release name>". A release that had
actually been synced keeps the exact folder it was reading, spelled out in
full; one that never synced is left blank so it picks up the new default.

**Snapshots.** Rows used to be merged in place, one per identity per testing
type. They now belong to a snapshot. Rows that predate snapshots were merged
together over many loads and cannot be split back into the loads they came
from, so each testing type's rows are attached to a single "imported"
snapshot dated from its last load. Nothing is deleted: the rows keep their
values, and the old `layer_uploads` documents are left in place as a record of
what was loaded and when.

**One file per snapshot.** A snapshot used to be able to cover several
workbooks at once. Each one is now tied to a single file, and sequence numbers
run per file. Snapshots built from one workbook are simply tagged with it;
those built from several cannot be split apart after the fact — their rows
were combined when they were written — so they are tagged with the first and
flagged `combined`, kept as readable history beside the per-file series that
later loads create.

Safe to run twice: documents that already carry a `releaseId` or a `file`, and
users whose `lastActive` is already a timestamp or null, are left alone.
"""
import argparse
import re
import uuid
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.synchronous.database import Database

from app.config import get_settings
from app.db import LEGACY_INDEX_KEYS
from app.services.excel_ingest import ParsedRow, content_fingerprint

DEFAULT_RELEASE_NAME = "Initial release"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64] or "release"


def _target_release(db: Database, app_id: str, name: str, now: datetime,
                    apply: bool, log: list[str]) -> tuple[str, str]:
    """The release legacy data belongs to: the application's current release
    if it already has one, otherwise a new release created for it."""
    existing = db.releases.find_one({"appId": app_id, "current": True}) \
        or db.releases.find_one({"appId": app_id})
    if existing is not None:
        return existing["releaseId"], existing["name"]

    release_id = slugify(name)
    log.append(f'  create release "{name}" ({release_id})')
    if apply:
        db.releases.insert_one({
            "_id": f"{app_id}:{release_id}", "appId": app_id, "releaseId": release_id,
            "name": name, "desc": "Data ingested before releases were introduced.",
            "excelPath": "", "current": True, "order": 0,
            "createdAt": now, "updatedAt": now,
        })
    return release_id, name


def repair_legacy_users(db: Database, apply: bool) -> list[str]:
    """Clear `lastActive` values that are not timestamps.

    Early seeds wrote the rendered label ("2 h ago") instead of the time it was
    rendered from. The label cannot be turned back into an instant, and
    inventing one would be worse than admitting the time is unknown, so it
    becomes null — which the users table shows as "never".
    """
    log: list[str] = []
    stale = [
        u for u in db.users.find(projection={"username": 1, "lastActive": 1})
        if not isinstance(u.get("lastActive"), (datetime, type(None)))
    ]
    for user in stale:
        log.append(f'user {user["username"]}: clear lastActive '
                   f'({user["lastActive"]!r} is not a timestamp)')
    if stale and apply:
        db.users.update_many(
            {"username": {"$in": [u["username"] for u in stale]}},
            {"$set": {"lastActive": None}})
    return log


def fold_excel_paths(db: Database, apply: bool) -> list[str]:
    """Collapse the application path + release path pair into one per release.

    Only a release that has actually read from disk has a path worth
    preserving; the rest are blanked so they follow the new default rather
    than carrying a folder nobody ever used.
    """
    log: list[str] = []
    for app in db.apps.find({"excelPath": {"$exists": True}}):
        app_path = (app.get("excelPath") or "").strip("/ ")
        for release in db.releases.find({"appId": app["_id"]}):
            synced = db.layer_uploads.count_documents(
                {"appId": app["_id"], "releaseId": release["releaseId"],
                 "sourcePath": {"$exists": True}})
            release_path = (release.get("excelPath") or "").strip("/ ")
            combined = "/".join(part for part in (app_path, release_path) if part)
            folded = combined if synced and combined else ""
            if folded == (release.get("excelPath") or ""):
                continue
            log.append(
                f'release {release["_id"]}: excelPath '
                f'{release.get("excelPath")!r} -> {folded!r}'
                + ("" if folded else "  (never synced — uses <app>/<release>)"))
            if apply:
                db.releases.update_one({"_id": release["_id"]},
                                       {"$set": {"excelPath": folded}})
        log.append(f'application {app["_id"]}: drop excelPath {app_path!r}')
        if apply:
            db.apps.update_one({"_id": app["_id"]}, {"$unset": {"excelPath": ""}})
    return log


def import_snapshots(db: Database, apply: bool,
                     releases: dict[str, str] | None = None) -> list[str]:
    """Attach rows that predate snapshots to one imported snapshot each.

    Read-only with respect to the values: rows are stamped with a snapshotId
    and a period, and nothing is removed. `layer_uploads` is left untouched —
    it is the only record of what was loaded before snapshots existed.

    `releases` maps an application to the release its untagged rows are moving
    into. It is what makes a dry run possible: the tagging step writes nothing
    without `--apply`, so without this the rows would still have no release by
    the time they reach here.
    """
    log: list[str] = []
    releases = releases or {}
    orphans = db.layer_records.aggregate([
        {"$match": {"snapshotId": {"$exists": False}}},
        {"$group": {"_id": {"appId": "$appId", "releaseId": "$releaseId",
                            "layerId": "$layerId"},
                    "n": {"$sum": 1}}},
        {"$sort": {"_id.appId": 1, "_id.releaseId": 1, "_id.layerId": 1}},
    ])
    for group in orphans:
        key = group["_id"]
        app_id, layer_id = key.get("appId"), key.get("layerId")
        # Rows written before releases existed carry no releaseId until the
        # tagging step in migrate_db sets one — and a dry run does not write, so
        # in a dry run they arrive here still untagged. Mongo omits a missing
        # field from a $group key entirely, so this has to be read with .get()
        # and the release the rows are heading for resolved from the caller.
        tagged = key.get("releaseId")
        release_id = tagged or releases.get(app_id)
        if not (app_id and layer_id and release_id):
            log.append(f'{app_id}/{layer_id}: skipped {group["n"]} rows — they '
                       f'belong to no application this database still has, so '
                       f'there is no release to attach them to')
            continue

        resolved = {"appId": app_id, "releaseId": release_id, "layerId": layer_id}
        # match the rows as they are *now*, which in a dry run is still untagged
        scope = dict(resolved) if tagged else {
            "appId": app_id, "layerId": layer_id, "releaseId": {"$exists": False}}
        rows = list(db.layer_records.find(scope).sort("rowIndex", 1))
        uploads = list(db.layer_uploads.find(scope).sort("uploadedAt", 1))
        newest = uploads[-1] if uploads else {}
        layer = db.layers.find_one(
            {"_id": f"{app_id}:{release_id}:{layer_id}"}) or {}
        loaded_at = newest.get("uploadedAt") or datetime.now(timezone.utc)
        columns = newest.get("columns") or layer.get("columns") or []
        identity = [c["key"] for c in columns if c.get("type") == "string"] \
            or [c["key"] for c in columns]

        snapshot_id = uuid.uuid4().hex
        log.append(
            f'{app_id}/{release_id}/{layer_id}: '
            f'{group["n"]} rows -> imported snapshot #1 '
            f'({loaded_at:%b %Y}), from {len(uploads)} earlier load(s)')
        if not apply:
            continue

        db.snapshots.insert_one({
            "_id": snapshot_id, **resolved,
            "sequence": 1,
            "period": {"year": loaded_at.year, "month": loaded_at.month},
            "contentHash": _content_hash(columns, rows),
            "identityKeys": identity,
            "columns": columns,
            "sections": sorted({r.get("section", "General") for r in rows}),
            "sources": [
                {"relativePath": u.get("fileName", ""),
                 "fileName": u.get("fileName", ""),
                 "fileHash": u.get("fileHash", ""),
                 "sizeBytes": u.get("fileSize", 0),
                 "modifiedAt": u.get("uploadedAt"),
                 "rowsRead": u.get("totalRows", 0),
                 "duplicatesSkipped": u.get("duplicatesSkipped", 0)}
                for u in uploads
            ],
            "file": (uploads[-1].get("fileName") if uploads else "(imported)"),
            "combined": len({u.get("fileName") for u in uploads if u.get("fileName")}) > 1,
            "rowCount": len(rows),
            "totalRows": sum(u.get("totalRows", 0) for u in uploads) or len(rows),
            "duplicatesSkipped": sum(u.get("duplicatesSkipped", 0) for u in uploads),
            "diff": {"comparable": True, "comparedTo": None,
                     "added": len(rows), "changed": 0, "removed": 0},
            "createdAt": loaded_at,
            "createdBy": newest.get("uploadedBy", "import"),
            "imported": True,
        })
        db.layer_records.update_many(
            {**scope, "snapshotId": {"$exists": False}},
            {"$set": {"snapshotId": snapshot_id,
                      "file": (uploads[-1].get("fileName") if uploads else "(imported)"),
                      "period": {"year": loaded_at.year, "month": loaded_at.month}}})
    return log


def _content_hash(columns: list[dict], rows: list[dict]) -> str:
    """The fingerprint the rows would have had, so the next load compares."""
    parsed = [ParsedRow(section=r.get("section", "General"),
                        values=r.get("data", {}), row_key=r.get("rowKey", ""))
              for r in rows]
    return content_fingerprint(columns, parsed)


def assign_snapshot_files(db: Database, apply: bool) -> list[str]:
    """Tie every snapshot to the one workbook it came from.

    Nothing is deleted and no row's values change: snapshots and their records
    gain a `file`, and sequences are renumbered per file so each workbook has
    its own #1, #2, #3.
    """
    log: list[str] = []
    stale = list(db.snapshots.find({"file": {"$exists": False}}).sort("createdAt", 1))
    for snapshot in stale:
        paths = []
        for src in snapshot.get("sources", []):
            path = src.get("relativePath") or src.get("fileName") or ""
            if path and path not in paths:
                paths.append(path)
        file = paths[0] if paths else "(unknown)"
        combined = len(paths) > 1
        note = f" (built from {len(paths)} files — kept as combined history)" if combined else ""
        log.append(f'snapshot {snapshot["_id"][:8]} '
                   f'{snapshot["releaseId"]}/{snapshot["layerId"]} -> file {file!r}{note}')
        if not apply:
            continue
        db.snapshots.update_one({"_id": snapshot["_id"]},
                                {"$set": {"file": file, "combined": combined}})
        db.layer_records.update_many({"snapshotId": snapshot["_id"]},
                                     {"$set": {"file": file}})

    if not apply:
        return log

    # sequences now run per file, so renumber wherever a testing type turns out
    # to hold more than one
    groups: dict[tuple, list] = {}
    for snapshot in db.snapshots.find().sort("createdAt", 1):
        key = (snapshot["appId"], snapshot["releaseId"], snapshot["layerId"],
               snapshot.get("file", ""))
        groups.setdefault(key, []).append(snapshot)
    for key, snapshots in groups.items():
        for index, snapshot in enumerate(snapshots, start=1):
            if snapshot.get("sequence") != index:
                log.append(f'snapshot {snapshot["_id"][:8]} {key[3]}: '
                           f'sequence {snapshot.get("sequence")} -> {index}')
                db.snapshots.update_one({"_id": snapshot["_id"]},
                                        {"$set": {"sequence": index}})
    return log


def migrate_db(db: Database, release_name: str, apply: bool) -> list[str]:
    now = datetime.now(timezone.utc)
    log: list[str] = repair_legacy_users(db, apply)
    log += fold_excel_paths(db, apply)

    # where each application's untagged rows are heading. Carried to
    # import_snapshots because the tagging below only writes under --apply,
    # so a dry run has to be told what it would have written.
    targets: dict[str, str] = {}

    for app in db.apps.find():
        app_id = app["_id"]
        log.append(f"application {app_id}:")

        release_id, name = _target_release(db, app_id, release_name, now, apply, log)
        targets[app_id] = release_id

        # layers: re-key <app>:<layer> -> <app>:<release>:<layer>. _id is
        # immutable, so the document is rewritten under its new key.
        stale = list(db.layers.find({"appId": app_id, "releaseId": {"$exists": False}}))
        for layer in stale:
            new_id = f'{app_id}:{release_id}:{layer["layerId"]}'
            log.append(f'  layer {layer["_id"]} -> {new_id}')
            if apply:
                doc = {**layer, "_id": new_id, "releaseId": release_id, "updatedAt": now}
                db.layers.insert_one(doc)
                db.layers.delete_one({"_id": layer["_id"]})

        for coll in ("layer_records", "layer_uploads"):
            query = {"appId": app_id, "releaseId": {"$exists": False}}
            n = db[coll].count_documents(query)
            if n:
                log.append(f"  {coll}: tag {n} documents with releaseId={release_id}")
                if apply:
                    db[coll].update_many(query, {"$set": {"releaseId": release_id}})

        if not stale and not db.layers.count_documents({"appId": app_id}):
            log.append("  no layers found — release created empty")
        log.append(f'  data now lives under release "{name}"')

    # indexes: the old unique key would reject a row that legitimately appears
    # in two releases, so it has to go before anything is loaded
    legacy = [dict(keys) for keys in LEGACY_INDEX_KEYS]
    for coll in ("layers", "layer_records", "layer_uploads"):
        for index_name, info in db[coll].index_information().items():
            if index_name != "_id_" and dict(info.get("key", [])) in legacy:
                log.append(f"index: drop {coll}.{index_name}")
                if apply:
                    db[coll].drop_index(index_name)
    # rows first get their release, then the snapshot that release loaded them
    # in, and finally the one workbook that snapshot came from
    log += import_snapshots(db, apply, targets)
    log += assign_snapshot_files(db, apply)

    if apply:
        db.releases.create_index([("appId", ASCENDING), ("order", ASCENDING)])
        db.layers.create_index(
            [("appId", ASCENDING), ("releaseId", ASCENDING), ("order", ASCENDING)])
        db.snapshots.create_index(
            [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING),
             ("file", ASCENDING), ("sequence", DESCENDING)])
        db.layer_records.create_index(
            [("snapshotId", ASCENDING), ("rowKey", ASCENDING)], unique=True)
        db.layer_records.create_index(
            [("snapshotId", ASCENDING), ("section", ASCENDING), ("rowIndex", ASCENDING)])
        log.append("index: release-scoped indexes created")
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (without it, only prints them)")
    parser.add_argument("--release", default=DEFAULT_RELEASE_NAME,
                        help=f"name for the release existing data moves into "
                             f"(default: {DEFAULT_RELEASE_NAME!r})")
    args = parser.parse_args()

    settings = get_settings()
    client = MongoClient(settings.mongo_uri)
    log = migrate_db(client[settings.mongo_db], args.release, args.apply)
    client.close()

    print(f"{'migrating' if args.apply else 'dry run —'} {settings.mongo_db}")
    for line in log:
        print(line)
    if not args.apply:
        print("\nnothing was written. re-run with --apply to perform the migration.")


if __name__ == "__main__":
    main()
