"""Admin CRUD for applications and layers, including cascades."""
from app.layer_defaults import DEFAULT_LAYERS
from tests.helpers_xlsx import SIMPLE, build_workbook


async def test_seeded_structure(client, manager_headers):
    apps = (await client.get("/api/apps", headers=manager_headers)).json()
    assert [a["id"] for a in apps] == ["cellsens"]
    assert apps[0]["layerCount"] == 5
    layers = (await client.get("/api/apps/cellsens/layers", headers=manager_headers)).json()
    # bottom-first: the seeded order is the testing pyramid itself
    assert [l["id"] for l in layers] == [
        "unit", "regression", "feature", "system", "acceptance"]
    assert [l["name"] for l in layers] == [
        "Unit Testing", "Regression Testing", "Feature Testing", "System Testing",
        "Acceptance Testing"]
    assert [l["order"] for l in layers] == [0, 1, 2, 3, 4]


async def test_app_lifecycle(client, admin_headers):
    res = await client.post("/api/apps", headers=admin_headers,
                            json={"name": "PRECiV 2.0", "tag": "Industrial"})
    assert res.status_code == 201
    assert res.json()["id"] == "preciv-2-0"
    assert res.json()["layerCount"] == 5

    # the global pyramid is laid down for every new application
    layers = (await client.get("/api/apps/preciv-2-0/layers", headers=admin_headers)).json()
    assert [l["id"] for l in layers] == [d["layerId"] for d in DEFAULT_LAYERS]
    assert [l["order"] for l in layers] == [0, 1, 2, 3, 4]

    dup = await client.post("/api/apps", headers=admin_headers, json={"name": "PRECiV 2.0"})
    assert dup.status_code == 409

    res = await client.patch("/api/apps/preciv-2-0", headers=admin_headers,
                             json={"desc": "Industrial measurement"})
    assert res.json()["desc"] == "Industrial measurement"

    assert (await client.delete("/api/apps/preciv-2-0", headers=admin_headers)).status_code == 204
    apps = (await client.get("/api/apps", headers=admin_headers)).json()
    assert "preciv-2-0" not in [a["id"] for a in apps]


async def test_layer_lifecycle_and_cascade(client, admin_headers, qa_headers, tmp_path):
    res = await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                            json={"name": "Smoke testing", "short": "SMK"})
    assert res.status_code == 201
    layer = res.json()
    assert layer["id"] == "smoke-testing"
    assert layer["order"] == 5  # appended after the five default layers

    # feed it from the configured Excel folder — the file name picks the layer
    (tmp_path / "cellsens").mkdir()
    (tmp_path / "cellsens" / "smoke-testing.xlsx").write_bytes(build_workbook(SIMPLE))
    settings = (await client.get("/api/settings", headers=admin_headers)).json()
    await client.put("/api/settings", headers=admin_headers,
                     json={**settings, "excelRoot": str(tmp_path)})
    await client.patch("/api/apps/cellsens", headers=admin_headers,
                       json={"excelPath": "cellsens"})
    up = await client.post("/api/apps/cellsens/sync", headers=qa_headers, json={
        "mode": "merge",
        "layers": [{"layerId": "smoke-testing", "files": ["smoke-testing.xlsx"]}]})
    assert up.status_code == 200, up.text

    res = await client.patch("/api/apps/cellsens/layers/smoke-testing",
                             headers=admin_headers, json={"desc": "Quick smoke pass"})
    assert res.json()["desc"] == "Quick smoke pass"
    assert res.json()["recordCount"] == 3

    assert (await client.delete("/api/apps/cellsens/layers/smoke-testing",
                                headers=admin_headers)).status_code == 204
    layers = (await client.get("/api/apps/cellsens/layers", headers=admin_headers)).json()
    assert "smoke-testing" not in [l["id"] for l in layers]
    # cascade removed the layer's records: recreating it starts empty
    await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                      json={"name": "Smoke testing"})
    body = (await client.get("/api/apps/cellsens/layers/smoke-testing/records",
                             headers=admin_headers)).json()
    assert body["total"] == 0
    await client.delete("/api/apps/cellsens/layers/smoke-testing", headers=admin_headers)
    await client.put("/api/settings", headers=admin_headers, json={**settings, "excelRoot": ""})


async def test_layer_inserted_at_requested_pyramid_position(client, admin_headers):
    res = await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                            json={"name": "Component testing", "order": 1})
    assert res.status_code == 201
    assert res.json()["order"] == 1

    layers = (await client.get("/api/apps/cellsens/layers", headers=admin_headers)).json()
    # slotted between unit and regression; everything above it shifted up one
    assert [l["id"] for l in layers] == [
        "unit", "component-testing", "regression", "feature", "system", "acceptance"]
    assert [l["order"] for l in layers] == [0, 1, 2, 3, 4, 5]

    # an out-of-range position lands at the tip rather than erroring
    res = await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                            json={"name": "Exploratory testing", "order": 99})
    assert res.json()["order"] == 6
    # a negative position is rejected by the schema
    res = await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                            json={"name": "Bad testing", "order": -1})
    assert res.status_code == 422

    for layer_id in ("component-testing", "exploratory-testing"):
        await client.delete(f"/api/apps/cellsens/layers/{layer_id}", headers=admin_headers)
    layers = (await client.get("/api/apps/cellsens/layers", headers=admin_headers)).json()
    assert [l["id"] for l in layers] == [d["layerId"] for d in DEFAULT_LAYERS]
    assert [l["order"] for l in layers] == [0, 1, 2, 3, 4]  # gaps closed on delete


async def test_user_lifecycle(client, admin_headers):
    res = await client.post("/api/users", headers=admin_headers, json={
        "username": "Tester1", "name": "Test Person", "password": "secret123", "role": "qa"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["username"] == "tester1"  # lowercased
    assert "passwordHash" not in body

    dup = await client.post("/api/users", headers=admin_headers, json={
        "username": "tester1", "name": "Someone Else", "password": "secret123", "role": "qa"})
    assert dup.status_code == 409

    # the new user can actually log in
    login = await client.post("/api/auth/login",
                              json={"username": "tester1", "password": "secret123"})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "qa"

    assert (await client.delete("/api/users/tester1", headers=admin_headers)).status_code == 204
    users = (await client.get("/api/users", headers=admin_headers)).json()
    assert "tester1" not in [u["username"] for u in users]
    assert (await client.delete("/api/users/tester1", headers=admin_headers)).status_code == 404


async def test_admin_cannot_delete_self(client, admin_headers):
    res = await client.delete("/api/users/admin", headers=admin_headers)
    assert res.status_code == 400
    users = (await client.get("/api/users", headers=admin_headers)).json()
    assert "admin" in [u["username"] for u in users]


async def test_user_create_validation(client, admin_headers):
    for bad in (
        {"username": "ab", "name": "X", "password": "secret123", "role": "qa"},  # too short
        {"username": "ok-user", "name": "X", "password": "12345", "role": "qa"},  # weak password
        {"username": "ok-user", "name": "X", "password": "secret123", "role": "root"},  # bad role
    ):
        res = await client.post("/api/users", headers=admin_headers, json=bad)
        assert res.status_code == 422, bad


async def test_unknown_ids_404(client, manager_headers):
    assert (await client.get("/api/apps/nope/layers", headers=manager_headers)).status_code == 404
    assert (await client.get("/api/apps/cellsens/layers/nope/records",
                             headers=manager_headers)).status_code == 404
    assert (await client.get("/api/apps/cellsens/layers/nope/dashboard",
                             headers=manager_headers)).status_code == 404
