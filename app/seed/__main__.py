"""Seed MongoDB with the base structure. Idempotent (drop + recreate).

    python -m app.seed

Seeds users, the cellSens application, its releases (v4.3 shipped, v4.4
current), the global default testing pyramid inside each release
(Unit → Regression → Feature → System → Acceptance, bottom-first) and default
settings. Layer data is NOT seeded — it is ingested per release via Excel
upload or a folder sync.

To keep the data already in a database, migrate it instead:
`python -m app.migrate` moves a pre-release database onto this schema.
Also drops the legacy fake-data collections (tests, runs, reports).
"""
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.synchronous.database import Database

from app.config import get_settings
from app.security import hash_password
from app.layer_defaults import DEFAULT_LAYERS, default_layer_docs
from app.seed.fixtures import APPS, DEFAULT_SETTINGS, RELEASES, USERS


def seed_db(db: Database) -> dict:
    now = datetime.now(timezone.utc)
    for name in ("users", "apps", "releases", "layers", "snapshots", "layer_records",
                 "settings", "layer_uploads", "tests", "runs",
                 "reports", "qa_reports"):  # tests/runs/reports: pre-snapshot
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
    db.releases.insert_many([
        {"_id": f'{r["appId"]}:{r["releaseId"]}', **r, "order": i,
         "createdAt": now, "updatedAt": now}
        for i, r in enumerate(RELEASES)
    ])
    # same defaults the API applies when a release is created
    db.layers.insert_many([
        doc for r in RELEASES
        for doc in default_layer_docs(r["appId"], r["releaseId"], now)
    ])
    db.settings.insert_one({"_id": "app", **DEFAULT_SETTINGS})

    # mirror of app.db.ensure_indexes so a seeded DB is ready without a boot
    db.users.create_index("username", unique=True)
    db.releases.create_index([("appId", ASCENDING), ("order", ASCENDING)])
    db.layers.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("order", ASCENDING)])
    db.snapshots.create_index(
        [("appId", ASCENDING), ("releaseId", ASCENDING), ("layerId", ASCENDING),
         ("sequence", DESCENDING)])
    db.layer_records.create_index(
        [("snapshotId", ASCENDING), ("rowKey", ASCENDING)], unique=True)
    db.layer_records.create_index(
        [("snapshotId", ASCENDING), ("section", ASCENDING), ("rowIndex", ASCENDING)])

    return {"apps": len(APPS), "releases": len(RELEASES),
            "layers": len(RELEASES) * len(DEFAULT_LAYERS),
            "users": len(USERS), "records": 0}


def main() -> None:
    settings = get_settings()
    client = MongoClient(settings.mongo_uri)
    counts = seed_db(client[settings.mongo_db])
    print(f"seeded {settings.mongo_db}: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    client.close()


if __name__ == "__main__":
    main()
