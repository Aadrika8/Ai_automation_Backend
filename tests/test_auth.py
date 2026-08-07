async def test_login_success_all_roles(client):
    for username, password, role in (
        ("admin", "admin123", "admin"),
        ("qa", "qa123", "qa"),
        ("manager", "manager123", "manager"),
    ):
        res = await client.post("/api/auth/login", json={"username": username, "password": password})
        assert res.status_code == 200
        body = res.json()
        assert body["token"]
        assert body["user"]["username"] == username
        assert body["user"]["role"] == role
        assert "name" in body["user"]


async def test_login_wrong_password(client):
    res = await client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid username or password"


async def test_login_unknown_user(client):
    res = await client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert res.status_code == 401


async def test_display_only_user_cannot_login(client):
    res = await client.post("/api/auth/login", json={"username": "qa2", "password": "anything"})
    assert res.status_code == 401


async def test_garbage_token_rejected(client):
    res = await client.get("/api/apps", headers={"Authorization": "Bearer not.a.jwt"})
    assert res.status_code == 401
