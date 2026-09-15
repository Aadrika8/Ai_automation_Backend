"""Settings: the Excel root, and nothing else.

An earlier git-runner design kept a repository URL, branch, timeout, cache
directory, hierarchy root and levels, and test-file extensions here. Nothing
read them. What is pinned: the API answers with the one setting the application
uses, a save needs nothing more, and a database written under the old design
keeps working \u2014 its extra keys are ignored on read and gone after the next save.
"""
import pytest_asyncio
from pymongo import MongoClient

from app.config import get_settings
from app.seed.fixtures import DEFAULT_SETTINGS

ROOT = r"D:\New folder"
LEGACY = {
    "repoUrl": "https://github.com/Aadrika8/test_suites.git",
    "branch": "main",
    "cacheDir": "~/TestRunner/cache",
    "timeoutSeconds": 60,
    "rootFolder": "test_suites",
    "levels": ["application", "suite", "release"],
    "extensions": [".py"],
}


def stored() -> dict:
    """The settings document exactly as MongoDB holds it."""
    client = MongoClient(get_settings().mongo_uri)
    try:
        return client[get_settings().mongo_db].settings.find_one({"_id": "app"}) or {}
    finally:
        client.close()


def store(doc: dict) -> None:
    client = MongoClient(get_settings().mongo_uri)
    try:
        client[get_settings().mongo_db].settings.replace_one(
            {"_id": "app"}, {"_id": "app", **doc}, upsert=True)
    finally:
        client.close()


@pytest_asyncio.fixture(autouse=True)
async def restore_settings():
    """Every other suite points the Excel root at its own temp folder, so the
    document goes back exactly as it was found."""
    before = {k: v for k, v in stored().items() if k != "_id"}
    yield
    store(before)


async def test_settings_expose_only_the_excel_root(client, admin_headers):
    body = (await client.get("/api/settings", headers=admin_headers)).json()
    assert set(body) == {"excelRoot"}


async def test_a_save_needs_only_the_excel_root(client, admin_headers):
    """The page used to refuse a save with no test-file extension \u2014 a field
    nothing read, blocking the one that matters."""
    res = await client.put("/api/settings", headers=admin_headers, json={"excelRoot": ROOT})
    assert res.status_code == 200, res.text
    assert res.json() == {"excelRoot": ROOT}
    assert (await client.get("/api/settings", headers=admin_headers)).json() == {"excelRoot": ROOT}


async def test_a_database_from_the_old_design_still_reads(client, admin_headers):
    """The real database holds all eight keys. The seven unknown ones are
    ignored, so nothing needs migrating before the new version runs."""
    store({"excelRoot": ROOT, **LEGACY})
    res = await client.get("/api/settings", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"excelRoot": ROOT}


async def test_the_next_save_clears_the_old_keys(client, admin_headers):
    """A save replaces the document, so the leftovers go without a migration."""
    store({"excelRoot": ROOT, **LEGACY})
    body = (await client.get("/api/settings", headers=admin_headers)).json()
    assert (await client.put("/api/settings", headers=admin_headers,
                             json=body)).status_code == 200
    doc = stored()
    assert doc["excelRoot"] == ROOT
    assert set(LEGACY).isdisjoint(doc)


async def test_an_old_client_sending_the_old_fields_is_not_refused(client, admin_headers):
    """A frontend from before the change still posts all eight. The extra seven
    are dropped rather than rejected, so a laptop running the older page can
    still save while the two versions are out of step."""
    res = await client.put("/api/settings", headers=admin_headers,
                           json={"excelRoot": ROOT, **LEGACY})
    assert res.status_code == 200, res.text
    assert res.json() == {"excelRoot": ROOT}
    assert set(LEGACY).isdisjoint(stored())


def test_a_fresh_seed_carries_no_legacy_settings():
    """Including the GitHub URL the old default pointed at."""
    assert DEFAULT_SETTINGS == {"excelRoot": ""}
