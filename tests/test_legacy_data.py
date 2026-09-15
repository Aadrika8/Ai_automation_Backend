"""Databases that predate the current schema must not break the API.

The oldest seeds wrote `lastActive` as the rendered label ("2 h ago") rather
than the instant behind it. Response validation expects a timestamp, so one
such document used to fail the whole user list with a 500.
"""
import pytest
import pytest_asyncio
from pymongo import MongoClient

from app.config import get_settings
from app.migrate import repair_legacy_users

LEGACY = {"admin": "Now", "qa": "2 h ago", "qa2": "3 days ago"}


@pytest.fixture
def users_collection():
    client = MongoClient(get_settings().mongo_uri)
    yield client[get_settings().mongo_db].users
    client.close()


@pytest_asyncio.fixture
async def legacy_users(users_collection):
    """Plant the old string values, and put real ones back afterwards."""
    original = {u["username"]: u.get("lastActive")
                for u in users_collection.find(projection={"username": 1, "lastActive": 1})}
    for username, label in LEGACY.items():
        users_collection.update_one({"username": username}, {"$set": {"lastActive": label}})
    yield users_collection
    for username, value in original.items():
        users_collection.update_one({"username": username}, {"$set": {"lastActive": value}})


async def test_legacy_last_active_does_not_break_the_user_list(
        client, admin_headers, legacy_users):
    res = await client.get("/api/users", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = {u["username"]: u["lastActive"] for u in res.json()}
    # unreadable timestamps read as "never", and the rest of the row survives
    assert all(body[username] is None for username in LEGACY)
    assert len(body) >= len(LEGACY)
    roles = {u["username"]: u["role"] for u in res.json()}
    assert roles["admin"] == "admin" and roles["qa"] == "qa"


async def test_migration_clears_legacy_last_active(legacy_users):
    planted = {u["username"]: u.get("lastActive") for u in legacy_users.find()}
    assert planted["qa"] == "2 h ago"  # the fixture really did plant them

    dry = repair_legacy_users(legacy_users.database, apply=False)
    assert len(dry) == len(LEGACY)
    assert legacy_users.find_one({"username": "qa"})["lastActive"] == "2 h ago"  # untouched

    applied = repair_legacy_users(legacy_users.database, apply=True)
    assert len(applied) == len(LEGACY)
    assert all(
        legacy_users.find_one({"username": username})["lastActive"] is None
        for username in LEGACY
    )
    # idempotent: a repaired database reports nothing left to do
    assert repair_legacy_users(legacy_users.database, apply=True) == []


async def test_real_timestamps_are_left_alone(users_collection):
    before = {u["username"]: u.get("lastActive") for u in users_collection.find()}
    assert repair_legacy_users(users_collection.database, apply=True) == []
    after = {u["username"]: u.get("lastActive") for u in users_collection.find()}
    assert before == after
