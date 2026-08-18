"""Admin CRUD for applications and layers, including cascades."""
from tests.helpers_xlsx import SIMPLE, build_workbook


def xlsx(payload: bytes):
    return {"file": ("data.xlsx", payload,
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


async def test_seeded_structure(client, manager_headers):
    apps = (await client.get("/api/apps", headers=manager_headers)).json()
    assert [a["id"] for a in apps] == ["cellsens"]
    assert apps[0]["layerCount"] == 4
    layers = (await client.get("/api/apps/cellsens/layers", headers=manager_headers)).json()
    assert [l["id"] for l in layers] == ["regression", "system", "feature", "acceptance"]
    assert [l["name"] for l in layers] == [
        "Regression testing", "System testing", "Feature testing", "Acceptance testing"]


async def test_app_lifecycle(client, admin_headers):
    res = await client.post("/api/apps", headers=admin_headers,
                            json={"name": "PRECiV 2.0", "tag": "Industrial"})
    assert res.status_code == 201
    assert res.json()["id"] == "preciv-2-0"

    dup = await client.post("/api/apps", headers=admin_headers, json={"name": "PRECiV 2.0"})
    assert dup.status_code == 409

    res = await client.patch("/api/apps/preciv-2-0", headers=admin_headers,
                             json={"desc": "Industrial measurement"})
    assert res.json()["desc"] == "Industrial measurement"

    assert (await client.delete("/api/apps/preciv-2-0", headers=admin_headers)).status_code == 204
    apps = (await client.get("/api/apps", headers=admin_headers)).json()
    assert "preciv-2-0" not in [a["id"] for a in apps]


async def test_layer_lifecycle_and_cascade(client, admin_headers, qa_headers):
    res = await client.post("/api/apps/cellsens/layers", headers=admin_headers,
                            json={"name": "Smoke testing", "short": "SMK"})
    assert res.status_code == 201
    layer = res.json()
    assert layer["id"] == "smoke-testing"
    assert layer["order"] == 4  # appended after the four seeded layers

    up = await client.post("/api/apps/cellsens/layers/smoke-testing/uploads",
                           headers=qa_headers, files=xlsx(build_workbook(SIMPLE)))
    assert up.status_code == 200

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
