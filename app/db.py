"""Async MongoDB client lifecycle. Connected in the FastAPI lifespan."""
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None


async def connect() -> None:
    global _client
    # tz_aware so datetimes round-trip as UTC and serialize with an offset,
    # instead of naive strings the browser would parse as local time
    _client = AsyncMongoClient(get_settings().mongo_uri, tz_aware=True)
    await _client.admin.command("ping")
    await ensure_indexes(get_db())


async def ensure_indexes(db: AsyncDatabase) -> None:
    """Idempotent — create_index is a no-op when the index already exists.

    Runs on every boot so a fresh production database gets its indexes
    without depending on the demo seed script.
    """
    await db.users.create_index("username", unique=True)
    await db.layers.create_index([("appId", ASCENDING), ("order", ASCENDING)])
    # unique rowKey per layer is the DB-level duplicate guarantee for uploads
    await db.layer_records.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("rowKey", ASCENDING)], unique=True
    )
    await db.layer_records.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("section", ASCENDING), ("rowIndex", ASCENDING)]
    )
    await db.layer_uploads.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("uploadedAt", DESCENDING)]
    )


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    if _client is None:
        raise RuntimeError("Mongo client not initialized — app lifespan did not run")
    return _client[get_settings().mongo_db]
