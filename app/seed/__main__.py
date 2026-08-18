"""Seed MongoDB with the base structure. Idempotent (drop + recreate).

    python -m app.seed

Seeds users, the cellSens application, its four testing layers and default
settings. Layer data is NOT seeded — it is ingested via Excel upload.
Also drops the legacy fake-data collections (tests, runs, reports).
"""
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.synchronous.database import Database

from app.config import get_settings
from app.security import hash_password
from app.seed.fixtures import APPS, DEFAULT_SETTINGS, LAYERS, USERS


def seed_db(db: Database) -> dict:
    now = datetime.now(timezone.utc)
    for name in ("users", "apps", "layers", "layer_uploads", "layer_records", "settings",
                 "tests", "runs", "reports"):  # last three: legacy fake-data cleanup
        db[name].drop()

    db.users.insert_many([
        {
            "username": u["username"], "name": u["name"], "role": u["role"],
            "passwordHash": hash_password(u["password"]) if u["password"] else None,
            "lastActive": now if u["password"] else None,
        }
        for u in USERS
    ])
    db.apps.insert_many([{**a, "createdAt": now, "updatedAt": now} for a in APPS])
    db.layers.insert_many([
        {"_id": f'{a["_id"]}:{l["layerId"]}', "appId": a["_id"], **l,
         "createdAt": now, "updatedAt": now}
        for a in APPS for l in LAYERS
    ])
    db.settings.insert_one({"_id": "app", **DEFAULT_SETTINGS})

    # mirror of app.db.ensure_indexes so a seeded DB is ready without a boot
    db.users.create_index("username", unique=True)
    db.layers.create_index([("appId", ASCENDING), ("order", ASCENDING)])
    db.layer_records.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("rowKey", ASCENDING)], unique=True)
    db.layer_records.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("section", ASCENDING), ("rowIndex", ASCENDING)])
    db.layer_uploads.create_index(
        [("appId", ASCENDING), ("layerId", ASCENDING), ("uploadedAt", DESCENDING)])

    return {"apps": len(APPS), "layers": len(APPS) * len(LAYERS),
            "users": len(USERS), "records": 0}


def main() -> None:
    settings = get_settings()
    client = MongoClient(settings.mongo_uri)
    counts = seed_db(client[settings.mongo_db])
    print(f"seeded {settings.mongo_db}: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    client.close()


if __name__ == "__main__":
    main()
