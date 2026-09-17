import os

os.environ["MONGO_DB"] = "qi_test"  # must be set before app.config is imported
# The seed reads initial passwords from the environment; the tests pick theirs.
os.environ["SEED_ADMIN_PASSWORD"] = "admin123"
os.environ["SEED_QA_PASSWORD"] = "qa123"
os.environ["SEED_MANAGER_PASSWORD"] = "manager123"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pymongo import MongoClient

from app import db as db_module
from app.config import get_settings
from app.main import app as fastapi_app
from app.seed.__main__ import seed_db


@pytest.fixture(scope="session", autouse=True)
def seeded_test_db():
    assert get_settings().mongo_db == "qi_test"
    client = MongoClient(get_settings().mongo_uri)
    seed_db(client["qi_test"])
    yield
    client.drop_database("qi_test")
    client.close()


@pytest_asyncio.fixture
async def client():
    # ASGITransport does not run the lifespan; manage the Mongo client directly
    await db_module.connect()
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
        yield c
    await db_module.close()


async def login_token(client: AsyncClient, username: str, password: str) -> str:
    res = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["token"]


@pytest_asyncio.fixture
async def manager_headers(client):
    return {"Authorization": f"Bearer {await login_token(client, 'manager', 'manager123')}"}


@pytest_asyncio.fixture
async def qa_headers(client):
    return {"Authorization": f"Bearer {await login_token(client, 'qa', 'qa123')}"}


@pytest_asyncio.fixture
async def admin_headers(client):
    return {"Authorization": f"Bearer {await login_token(client, 'admin', 'admin123')}"}
