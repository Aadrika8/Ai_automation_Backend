"""Async MongoDB client lifecycle. Connected in the FastAPI lifespan."""
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None


async def connect() -> None:
    global _client
    _client = AsyncMongoClient(get_settings().mongo_uri)
    await _client.admin.command("ping")


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def get_db() -> AsyncDatabase:
    if _client is None:
        raise RuntimeError("Mongo client not initialized — app lifespan did not run")
    return _client[get_settings().mongo_db]
