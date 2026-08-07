"""Thin async Mongo queries. Documents are stored camelCase (= wire format),
so repositories only reshape ids and project fields."""
from app.db import get_db


async def find_user(username: str) -> dict | None:
    return await get_db().users.find_one({"username": username})


async def list_apps() -> list[dict]:
    docs = await get_db().apps.find().to_list(None)
    for d in docs:
        d["id"] = d.pop("_id")
    return docs


async def list_layers(app_id: str) -> list[dict]:
    docs = await get_db().layers.find({"appId": app_id}).sort("order", 1).to_list(None)
    return [
        {
            "id": d["layerId"],
            "name": d["name"],
            "short": d["short"],
            "share": d["share"],
            "desc": d["desc"],
            "colorVar": d["colorVar"],
            "testCount": d["testCount"],
            "passRate": d["passRate"],
        }
        for d in docs
    ]


async def get_dashboard(app_id: str, layer_id: str) -> dict | None:
    d = await get_db().layers.find_one({"_id": f"{app_id}:{layer_id}"})
    if d is None:
        return None
    return {
        "snapshot": d["snapshot"],
        "history": d["history"],
        "hourly": d["hourly"],
        "version": d["version"],
        "recentRuns": d["recentRuns"],
    }


async def list_tests(app_id: str, layer_id: str) -> list[dict]:
    docs = await get_db().tests.find(
        {"appId": app_id, "layerId": layer_id},
        projection={"appId": 0, "layerId": 0, "path": 0, "history": 0, "failure": 0},
    ).to_list(None)
    for d in docs:
        d["id"] = d.pop("_id")
    return docs


async def get_test(test_id: str) -> dict | None:
    d = await get_db().tests.find_one({"_id": test_id})
    if d is not None:
        d["id"] = d.pop("_id")
    return d


async def list_runs() -> list[dict]:
    docs = await get_db().runs.find().sort([("whenOrder", 1), ("_id", 1)]).to_list(None)
    for d in docs:
        d["id"] = d.pop("_id")
        d.pop("whenOrder", None)
    return docs


async def get_settings_doc() -> dict | None:
    d = await get_db().settings.find_one({"_id": "app"})
    if d is not None:
        d.pop("_id")
    return d


async def put_settings_doc(doc: dict) -> None:
    await get_db().settings.replace_one({"_id": "app"}, {"_id": "app", **doc}, upsert=True)


async def list_users() -> list[dict]:
    return await get_db().users.find(projection={"_id": 0, "passwordHash": 0}).to_list(None)
