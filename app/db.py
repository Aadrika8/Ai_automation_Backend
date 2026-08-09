"""Async MongoDB client lifecycle. Connected in the FastAPI lifespan."""
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None


async def connect() -> None:
    global _client
    _client = AsyncMongoClient(get_settings().mongo_uri)
    await _client.admin.command("ping")
    await ensure_indexes(get_db())


async def ensure_indexes(db: AsyncDatabase) -> None:
    """Idempotent — create_index is a no-op when the index already exists.

    Runs on every boot so a fresh production database gets its indexes
    without depending on the demo seed script.
    """
    await db.users.create_index("username", unique=True)
    await db.layers.create_index("appId")
    await db.tests.create_index([("appId", ASCENDING), ("layerId", ASCENDING)])


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    if _client is None:
        raise RuntimeError("Mongo client not initialized — app lifespan did not run")
    return _client[get_settings().mongo_db]
