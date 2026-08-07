import pytest

PROTECTED = [
    ("GET", "/api/apps"),
    ("GET", "/api/apps/hrms/layers"),
    ("GET", "/api/apps/hrms/layers/unit/dashboard"),
    ("GET", "/api/apps/hrms/layers/unit/tests"),
    ("GET", "/api/tests/hrms~unit~0"),
    ("GET", "/api/runs"),
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/users"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
async def test_all_routes_require_auth(client, method, path):
    res = await client.request(method, path)
    assert res.status_code == 401


async def test_manager_blocked_from_qa_and_admin_routes(client, manager_headers):
    for path in ("/api/runs", "/api/apps/hrms/layers/unit/tests", "/api/tests/hrms~unit~0", "/api/users"):
        res = await client.get(path, headers=manager_headers)
        assert res.status_code == 403, path
    res = await client.put("/api/settings", headers=manager_headers, json={})
    assert res.status_code == 403


async def test_manager_allowed_dashboard_flow(client, manager_headers):
    for path in ("/api/apps", "/api/apps/hrms/layers", "/api/apps/hrms/layers/unit/dashboard", "/api/settings"):
        res = await client.get(path, headers=manager_headers)
        assert res.status_code == 200, path


async def test_qa_allowed_drilldown_blocked_from_admin(client, qa_headers):
    for path in ("/api/runs", "/api/apps/hrms/layers/unit/tests"):
        assert (await client.get(path, headers=qa_headers)).status_code == 200
    assert (await client.get("/api/users", headers=qa_headers)).status_code == 403
    assert (await client.put("/api/settings", headers=qa_headers, json={})).status_code == 403


async def test_admin_allowed_everything(client, admin_headers):
    for path in ("/api/apps", "/api/runs", "/api/apps/hrms/layers/unit/tests", "/api/users", "/api/settings"):
        assert (await client.get(path, headers=admin_headers)).status_code == 200, path
