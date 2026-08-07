"""Shape checks: the JSON the API serves must match Frontend/src/api/types.ts."""


async def test_apps_shape(client, manager_headers):
    apps = (await client.get("/api/apps", headers=manager_headers)).json()
    assert len(apps) == 3
    assert {a["id"] for a in apps} == {"hrms", "cellsens", "preciv"}
    assert set(apps[0]) == {"id", "name", "tag", "desc", "icon", "totalTests", "passRate"}


async def test_layers_shape_and_order(client, manager_headers):
    layers = (await client.get("/api/apps/cellsens/layers", headers=manager_headers)).json()
    assert [l["id"] for l in layers] == ["unit", "integration", "system", "e2e"]
    assert set(layers[0]) == {"id", "name", "short", "share", "desc", "colorVar", "testCount", "passRate"}
    assert layers[0]["colorVar"] == "--tier-unit"


async def test_dashboard_shape(client, manager_headers):
    d = (await client.get("/api/apps/hrms/layers/unit/dashboard", headers=manager_headers)).json()
    assert set(d) == {"snapshot", "history", "hourly", "version", "recentRuns"}
    snap = d["snapshot"]
    assert set(snap) == {"total", "passed", "failed", "knownBugs", "running", "skipped", "passRate", "deltaVsLastWeek"}
    assert snap["passed"] + snap["failed"] + snap["running"] + snap["skipped"] == snap["total"]
    assert snap["knownBugs"] <= snap["failed"]
    assert len(d["history"]) == 90 and len(d["hourly"]) == 24 and len(d["recentRuns"]) == 6
    assert set(d["history"][0]) == {"daysAgo", "passRate", "runs"}
    assert set(d["hourly"][0]) == {"hoursAgo", "passRate", "runs"}
    assert set(d["version"]) == {"current", "lastUpdated"}


async def test_dashboard_404_on_unknown_layer_combo(client, manager_headers):
    res = await client.get("/api/apps/hrms/layers/nope/dashboard", headers=manager_headers)
    assert res.status_code == 422  # Literal path validation rejects unknown layer ids


async def test_tests_list_matches_snapshot_total(client, qa_headers):
    dash = (await client.get("/api/apps/preciv/layers/e2e/dashboard", headers=qa_headers)).json()
    rows = (await client.get("/api/apps/preciv/layers/e2e/tests", headers=qa_headers)).json()
    assert len(rows) == dash["snapshot"]["total"]
    assert set(rows[0]) == {"id", "name", "suite", "release", "status", "durationS", "lastRun"}
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in ("Passed", "Failed", "Running", "Skipped")}
    assert by_status["Failed"] == dash["snapshot"]["failed"]
    assert by_status["Passed"] == dash["snapshot"]["passed"]


async def test_failed_test_detail_has_failure(client, qa_headers):
    # hrms:unit is guaranteed failures in the deterministic seed (preciv:e2e has zero)
    rows = (await client.get("/api/apps/hrms/layers/unit/tests", headers=qa_headers)).json()
    failed = next(r for r in rows if r["status"] == "Failed")
    detail = (await client.get(f"/api/tests/{failed['id']}", headers=qa_headers)).json()
    assert detail["appId"] == "hrms" and detail["layerId"] == "unit"
    assert detail["path"].startswith("test_suites/HRMS/")
    assert len(detail["history"]) == 7
    assert set(detail["failure"]) == {"errorMessage", "failingStep", "stackTrace"}
    assert detail["failure"]["stackTrace"].startswith("Traceback (most recent call last):")
    assert detail["history"][0]["status"] == "Failed"


async def test_passed_test_detail_has_no_failure(client, qa_headers):
    rows = (await client.get("/api/apps/preciv/layers/e2e/tests", headers=qa_headers)).json()
    passed = next(r for r in rows if r["status"] == "Passed")
    detail = (await client.get(f"/api/tests/{passed['id']}", headers=qa_headers)).json()
    assert detail["failure"] is None


async def test_test_detail_404(client, qa_headers):
    res = await client.get("/api/tests/hrms~unit~999999", headers=qa_headers)
    assert res.status_code == 404
    assert res.json()["detail"] == "Test case not found"


async def test_runs_shape(client, qa_headers):
    runs = (await client.get("/api/runs", headers=qa_headers)).json()
    assert len(runs) == 72
    assert set(runs[0]) == {"id", "appId", "layerId", "name", "when", "total", "passed", "durationMin", "status"}
    assert runs[0]["when"] == "38 min ago"  # sorted by recency order


async def test_settings_roundtrip(client, admin_headers):
    original = (await client.get("/api/settings", headers=admin_headers)).json()
    assert set(original) == {"repoUrl", "branch", "cacheDir", "timeoutSeconds", "rootFolder", "levels", "extensions"}
    updated = {**original, "branch": "develop", "extensions": [".py", ".robot"]}
    res = await client.put("/api/settings", headers=admin_headers, json=updated)
    assert res.status_code == 200
    after = (await client.get("/api/settings", headers=admin_headers)).json()
    assert after["branch"] == "develop" and after["extensions"] == [".py", ".robot"]
    # restore so repeated test runs stay stable
    await client.put("/api/settings", headers=admin_headers, json=original)


async def test_users_shape(client, admin_headers):
    users = (await client.get("/api/users", headers=admin_headers)).json()
    assert len(users) == 5
    assert set(users[0]) == {"username", "name", "role", "lastActive"}
    assert not any("passwordHash" in u for u in users)
