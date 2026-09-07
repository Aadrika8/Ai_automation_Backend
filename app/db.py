"""Async MongoDB client lifecycle. Connected in the FastAPI lifespan."""
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None

# Indexes from before releases existed. The unique one is actively harmful now:
# the same row may legitimately appear in two releases of the same layer, and
# the old key would reject the second one.
LEGACY_INDEX_KEYS: list[list[tuple[str, int]]] = [
    [("appId", ASCENDING), ("layerId", ASCENDING), ("rowKey", ASCENDING)],
    # one row per identity per layer — replaced by one row per identity per
    # *snapshot*, so that a row's earlier versions can sit beside its current one
    [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING),
     ("rowKey", ASCENDING)],
    [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING),
     ("section", ASCENDING), ("rowIndex", ASCENDING)],
    [("appId", ASCENDING), ("layerId", ASCENDING), ("section", ASCENDING),
     ("rowIndex", ASCENDING)],
    [("appId", ASCENDING), ("layerId", ASCENDING), ("uploadedAt", DESCENDING)],
    [("appId", ASCENDING), ("order", ASCENDING)],
]


async def connect() -> None:
    global _client
    # tz_aware so datetimes round-trip as UTC and serialize with an offset,
    # instead of naive strings the browser would parse as local time
    _client = AsyncMongoClient(get_settings().mongo_uri, tz_aware=True)
    await _client.admin.command("ping")
    await ensure_indexes(get_db())


async def _drop_legacy(collection) -> None:
    """Remove pre-release indexes, matched by key pattern rather than name."""
    legacy = [dict(keys) for keys in LEGACY_INDEX_KEYS]
    try:
        existing = await collection.index_information()
    except Exception:  # collection does not exist yet
        return
    for name, info in existing.items():
        if name == "_id_":
            continue
        if dict(info.get("key", [])) in legacy:
            await collection.drop_index(name)


async def ensure_indexes(db: AsyncDatabase) -> None:
    """Idempotent — create_index is a no-op when the index already exists.

    Runs on every boot so a fresh production database gets its indexes
    without depending on the demo seed script.
    """
    for name in ("layers", "layer_records", "layer_uploads"):
        await _drop_legacy(db[name])

    await db.users.create_index("username", unique=True)
    await db.releases.create_index([("appId", ASCENDING), ("order", ASCENDING)])
    await db.layers.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("order", ASCENDING)])
    # snapshots are ordered within a testing type, and looked up by content
    # when deciding whether a load changed anything
    # a snapshot belongs to one file, and sequences run per file
    await db.snapshots.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING),
         ("file", ASCENDING), ("sequence", DESCENDING)])
    await db.snapshots.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("period.year", DESCENDING),
         ("period.month", DESCENDING)])
    # unique rowKey per snapshot: the same row may appear in many snapshots —
    # that is the history — but never twice within one
    await db.layer_records.create_index(
        [("snapshotId", ASCENDING), ("rowKey", ASCENDING)], unique=True)
    await db.layer_records.create_index(
        [("snapshotId", ASCENDING), ("section", ASCENDING), ("rowIndex", ASCENDING)])
    await db.layer_records.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING)])
    # how a release matches Feature rows to System rows — one document per
    # release, since the columns it names belong to that release's workbooks
    await db.trace_configs.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING)], unique=True)


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    if _client is None:
        raise RuntimeError("Mongo client not initialized — app lifespan did not run")
    return _client[get_settings().mongo_db]
