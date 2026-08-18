import pytest

PROTECTED = [
    ("GET", "/api/apps"),
    ("POST", "/api/apps"),
    ("PATCH", "/api/apps/cellsens"),
    ("DELETE", "/api/apps/cellsens"),
    ("GET", "/api/apps/cellsens/layers"),
    ("POST", "/api/apps/cellsens/layers"),
    ("PATCH", "/api/apps/cellsens/layers/regression"),
    ("DELETE", "/api/apps/cellsens/layers/regression"),
    ("POST", "/api/apps/cellsens/layers/regression/uploads"),
    ("GET", "/api/apps/cellsens/layers/regression/records"),
    ("DELETE", "/api/apps/cellsens/layers/regression/records"),
    ("GET", "/api/apps/cellsens/layers/regression/dashboard"),
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
    ("POST", "/api/apps/cellsens/layers"),
    ("PATCH", "/api/apps/cellsens/layers/regression"),
    ("DELETE", "/api/apps/cellsens/layers/regression"),
    ("DELETE", "/api/apps/cellsens/layers/regression/records"),
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
    for path in ("/api/apps", "/api/apps/cellsens/layers",
                 "/api/apps/cellsens/layers/regression/records",
                 "/api/apps/cellsens/layers/regression/dashboard", "/api/settings"):
        res = await client.get(path, headers=manager_headers)
        assert res.status_code == 200, path
    res = await client.post("/api/apps/cellsens/layers/regression/uploads",
                            headers=manager_headers,
                            files={"file": ("x.xlsx", b"x", "application/octet-stream")})
    assert res.status_code == 403
    for method, path in ADMIN_ONLY:
        res = await client.request(method, path, headers=manager_headers, json={})
        assert res.status_code == 403, f"{method} {path}"


async def test_qa_can_upload_but_not_administer(client, qa_headers):
    for method, path in ADMIN_ONLY:
        res = await client.request(method, path, headers=qa_headers, json={})
        assert res.status_code == 403, f"{method} {path}"


async def test_admin_allowed_everything(client, admin_headers):
    for path in ("/api/apps", "/api/apps/cellsens/layers",
                 "/api/apps/cellsens/layers/regression/records",
                 "/api/apps/cellsens/layers/regression/dashboard",
                 "/api/users", "/api/settings"):
        assert (await client.get(path, headers=admin_headers)).status_code == 200, path
