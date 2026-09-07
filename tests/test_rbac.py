import pytest

PROTECTED = [
    ("GET", "/api/apps"),
    ("POST", "/api/apps"),
    ("PATCH", "/api/apps/cellsens"),
    ("DELETE", "/api/apps/cellsens"),
    ("GET", "/api/apps/cellsens/releases"),
    ("POST", "/api/apps/cellsens/releases"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4"),
    ("DELETE", "/api/apps/cellsens/releases/v4-3"),
    ("GET", "/api/apps/cellsens/releases/v4-4/layers"),
    ("POST", "/api/apps/cellsens/releases/v4-4/layers"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4/layers/unit"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/layers/unit"),
    ("POST", "/api/apps/cellsens/releases/v4-4/snapshots"),
    ("GET", "/api/apps/cellsens/releases/v4-4/layers/unit/snapshots"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4/snapshots/nope"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/snapshots/nope"),
    ("GET", "/api/apps/cellsens/releases/v4-4/layers/unit/records"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/layers/unit/records"),
    ("GET", "/api/apps/cellsens/releases/v4-4/layers/unit/dashboard"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("DELETE", "/api/users/qa2"),
]

ADMIN_ONLY = [
    ("POST", "/api/apps"),
    ("PATCH", "/api/apps/cellsens"),
    ("DELETE", "/api/apps/cellsens"),
    ("POST", "/api/apps/cellsens/releases"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4"),
    ("DELETE", "/api/apps/cellsens/releases/v4-3"),
    ("POST", "/api/apps/cellsens/releases/v4-4/layers"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4/layers/unit"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/layers/unit"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/layers/unit/records"),
    ("PATCH", "/api/apps/cellsens/releases/v4-4/snapshots/nope"),
    ("DELETE", "/api/apps/cellsens/releases/v4-4/snapshots/nope"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("DELETE", "/api/users/qa2"),
    ("PUT", "/api/settings"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
async def test_all_routes_require_auth(client, method, path):
    res = await client.request(method, path)
    assert res.status_code == 401


async def test_manager_read_only(client, manager_headers):
    for path in ("/api/apps", "/api/apps/cellsens/releases",
                 "/api/apps/cellsens/releases/v4-4/layers",
                 "/api/apps/cellsens/releases/v4-4/source",
                 "/api/apps/cellsens/releases/v4-4/layers/unit/records",
                 "/api/apps/cellsens/releases/v4-4/layers/unit/snapshots",
                 "/api/apps/cellsens/releases/v4-4/layers/unit/dashboard", "/api/settings"):
        res = await client.get(path, headers=manager_headers)
        assert res.status_code == 200, path
    # loading from the Excel folder needs qa+, reading its status does not
    res = await client.post("/api/apps/cellsens/releases/v4-4/snapshots",
                            headers=manager_headers, json={})
    assert res.status_code == 403
    for method, path in ADMIN_ONLY:
        res = await client.request(method, path, headers=manager_headers, json={})
        assert res.status_code == 403, f"{method} {path}"


async def test_qa_can_upload_but_not_administer(client, qa_headers):
    for method, path in ADMIN_ONLY:
        res = await client.request(method, path, headers=qa_headers, json={})
        assert res.status_code == 403, f"{method} {path}"


async def test_admin_allowed_everything(client, admin_headers):
    for path in ("/api/apps", "/api/apps/cellsens/releases",
                 "/api/apps/cellsens/releases/v4-4/layers",
                 "/api/apps/cellsens/releases/v4-4/layers/unit/records",
                 "/api/apps/cellsens/releases/v4-4/layers/unit/dashboard",
                 "/api/users", "/api/settings"):
        assert (await client.get(path, headers=admin_headers)).status_code == 200, path
