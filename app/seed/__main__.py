"""Seed MongoDB with the demo dataset. Idempotent (drop + recreate).

    python -m app.seed
"""
from pymongo import ASCENDING, MongoClient
from pymongo.synchronous.database import Database

from app.config import get_settings
from app.security import hash_password
from app.seed.generator import DEFAULT_SETTINGS, USERS, generate


def seed_db(db: Database) -> dict:
    data = generate()
    for name in ("users", "apps", "layers", "tests", "runs", "settings"):
        db[name].drop()

    db.users.insert_many([
        {
            "username": u["username"], "name": u["name"], "role": u["role"],
            "passwordHash": hash_password(u["password"]) if u["password"] else None,
            "lastActive": u["lastActive"],
        }
        for u in USERS
    ])
    db.users.create_index("username", unique=True)

    db.apps.insert_many(data["apps"])
    db.layers.insert_many(data["layers"])
    db.layers.create_index("appId")
    db.tests.insert_many(data["tests"])
    db.tests.create_index([("appId", ASCENDING), ("layerId", ASCENDING)])
    db.runs.insert_many([
        {"_id": run["id"], **{k: v for k, v in run.items() if k != "id"}} for run in data["runs"]
    ])
    db.settings.insert_one({"_id": "app", **DEFAULT_SETTINGS})

    return {
        "apps": len(data["apps"]), "layers": len(data["layers"]),
        "tests": len(data["tests"]), "runs": len(data["runs"]), "users": len(USERS),
    }


def main() -> None:
    settings = get_settings()
    client = MongoClient(settings.mongo_uri)
    counts = seed_db(client[settings.mongo_db])
    print(f"seeded {settings.mongo_db}: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    client.close()


if __name__ == "__main__":
    main()
