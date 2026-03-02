"""Tests for auth endpoints: login, /me, user management, role guards."""

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, client, admin_user):
        resp = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "adminpass",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_login_wrong_password(self, client, admin_user):
        resp = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong",
        })
        assert resp.status_code == 401

    def test_login_unknown_user(self, client):
        resp = client.post("/api/auth/login", json={
            "username": "nobody",
            "password": "whatever",
        })
        assert resp.status_code == 401

    def test_login_inactive_user(self, client, db, admin_user):
        admin_user.is_active = False
        db.commit()
        resp = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "adminpass",
        })
        assert resp.status_code == 403

    def test_login_returns_correct_role(self, client, merchant_user):
        resp = client.post("/api/auth/login", json={
            "username": "merchant",
            "password": "merchantpass",
        })
        assert resp.status_code == 200
        assert resp.json()["role"] == "merchant"


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------

class TestMe:
    def test_get_me_authenticated(self, client, admin_token):
        resp = client.get("/api/auth/me", headers=auth_header(admin_token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert data["role"] == "admin"

    def test_get_me_no_token(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_get_me_invalid_token(self, client):
        resp = client.get("/api/auth/me", headers=auth_header("bad.token.here"))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/auth/users  (admin only)
# ---------------------------------------------------------------------------

class TestListUsers:
    def test_admin_can_list_users(self, client, admin_token, merchant_user):
        resp = client.get("/api/auth/users", headers=auth_header(admin_token))
        assert resp.status_code == 200
        usernames = [u["username"] for u in resp.json()]
        assert "admin" in usernames
        assert "merchant" in usernames

    def test_merchant_cannot_list_users(self, client, merchant_token):
        resp = client.get("/api/auth/users", headers=auth_header(merchant_token))
        assert resp.status_code == 403

    def test_courier_cannot_list_users(self, client, courier_token):
        resp = client.get("/api/auth/users", headers=auth_header(courier_token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/auth/users  (admin only)
# ---------------------------------------------------------------------------

class TestCreateUser:
    def test_admin_creates_user(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "secret123",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["role"] == "courier"

    def test_duplicate_username_rejected(self, client, admin_token, merchant_user):
        resp = client.post("/api/auth/users", json={
            "username": "merchant",
            "password": "secret123",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 400

    def test_merchant_cannot_create_user(self, client, merchant_token):
        resp = client.post("/api/auth/users", json={
            "username": "someone",
            "password": "secret123",
            "role": "courier",
        }, headers=auth_header(merchant_token))
        assert resp.status_code == 403

    def test_short_password_rejected(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "short",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 422  # Pydantic validation


# ---------------------------------------------------------------------------
# DELETE /api/auth/users/{id}  (admin only)
# ---------------------------------------------------------------------------

class TestDeleteUser:
    def test_admin_deletes_user(self, client, admin_token, merchant_user):
        resp = client.delete(
            f"/api/auth/users/{merchant_user.id}",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204

    def test_admin_cannot_delete_self(self, client, admin_token, admin_user):
        resp = client.delete(
            f"/api/auth/users/{admin_user.id}",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_delete_nonexistent_user(self, client, admin_token):
        resp = client.delete(
            "/api/auth/users/9999",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 404

    def test_merchant_cannot_delete_user(self, client, merchant_token, courier_user):
        resp = client.delete(
            f"/api/auth/users/{courier_user.id}",
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 403
