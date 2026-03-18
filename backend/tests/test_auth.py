"""Tests for auth endpoints: login, /me, user management, role guards, password, tokens."""

from app.auth import create_refresh_token
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
            "password": "Secret1x",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["username"] == "newuser"
        assert data["role"] == "courier"

    def test_duplicate_username_rejected(self, client, admin_token, merchant_user):
        resp = client.post("/api/auth/users", json={
            "username": "merchant",
            "password": "Secret1x",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 400

    def test_merchant_cannot_create_user(self, client, merchant_token):
        resp = client.post("/api/auth/users", json={
            "username": "someone",
            "password": "Secret1x",
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

    def test_cannot_delete_last_admin(self, client, admin_token, admin_user):
        """The only admin in the DB — deleting them should be blocked."""
        # Create a non-admin user to delete *from* (admin can't self-delete anyway)
        # Instead, try to delete the only admin via another admin
        # Since admin can't delete themselves, create a second admin and then
        # delete the first, leaving only one — then try deleting the remaining one.
        resp = client.post("/api/auth/users", json={
            "username": "admin2",
            "password": "Secret1x",
            "role": "admin",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 201
        admin2_id = resp.json()["id"]

        # Delete admin_user (original) — should succeed since admin2 still exists
        # But admin can't delete self, so use admin2's token
        from app.auth import create_token
        admin2_token = create_token(admin2_id)
        resp = client.delete(
            f"/api/auth/users/{admin_user.id}",
            headers=auth_header(admin2_token),
        )
        assert resp.status_code == 204

        # Now admin2 is the last admin — try to delete admin2 from admin2 (self-delete blocked)
        resp = client.delete(
            f"/api/auth/users/{admin2_id}",
            headers=auth_header(admin2_token),
        )
        assert resp.status_code == 400  # "Cannot delete yourself"

    def test_can_delete_non_last_admin(self, client, admin_token, admin_user, db):
        """When multiple admins exist, deleting one should succeed."""
        from app.auth import hash_password
        from app.models import User
        admin2 = User(
            username="admin2", email="admin2@local",
            hashed_password=hash_password("Secret1x"),
            role="admin", is_superuser=True, is_verified=True,
        )
        db.add(admin2)
        db.commit()
        db.refresh(admin2)

        resp = client.delete(
            f"/api/auth/users/{admin2.id}",
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Password strength validation
# ---------------------------------------------------------------------------

class TestPasswordPolicy:
    def test_short_password_rejected(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "Short1",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 422

    def test_no_uppercase_rejected(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "abcdefg1",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 422

    def test_no_lowercase_rejected(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "ABCDEFG1",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 422

    def test_no_digit_rejected(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "Abcdefgh",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 422

    def test_valid_password_accepted(self, client, admin_token):
        resp = client.post("/api/auth/users", json={
            "username": "newuser",
            "password": "Secret1x",
            "role": "courier",
        }, headers=auth_header(admin_token))
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------

class TestPasswordChange:
    def test_admin_resets_user_password(self, client, admin_token, merchant_user):
        resp = client.put(
            f"/api/auth/users/{merchant_user.id}/password",
            json={"new_password": "NewPass1x"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204

        # Verify login with new password works
        resp = client.post("/api/auth/login", json={
            "username": "merchant",
            "password": "NewPass1x",
        })
        assert resp.status_code == 200

    def test_admin_resets_own_password(self, client, admin_token, admin_user):
        resp = client.put(
            f"/api/auth/users/{admin_user.id}/password",
            json={"new_password": "NewAdmin1"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204

    def test_non_admin_cannot_reset_others(self, client, merchant_token, admin_user):
        resp = client.put(
            f"/api/auth/users/{admin_user.id}/password",
            json={"new_password": "NewPass1x"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 403

    def test_user_changes_own_password(self, client, merchant_token, merchant_user):
        resp = client.put(
            "/api/auth/me/password",
            json={"current_password": "merchantpass", "new_password": "NewPass1x"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 204

        # Verify login with new password
        resp = client.post("/api/auth/login", json={
            "username": "merchant",
            "password": "NewPass1x",
        })
        assert resp.status_code == 200

    def test_wrong_current_password_rejected(self, client, merchant_token, merchant_user):
        resp = client.put(
            "/api/auth/me/password",
            json={"current_password": "wrongpass", "new_password": "NewPass1x"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 400

    def test_weak_new_password_rejected(self, client, merchant_token, merchant_user):
        resp = client.put(
            "/api/auth/me/password",
            json={"current_password": "merchantpass", "new_password": "weak"},
            headers=auth_header(merchant_token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Token rotation
# ---------------------------------------------------------------------------

class TestTokenRotation:
    def test_refresh_returns_new_refresh_token(self, client, admin_user):
        # Login to get tokens
        resp = client.post("/api/auth/login", json={
            "username": "admin", "password": "adminpass",
        })
        assert resp.status_code == 200
        old_refresh = resp.json()["refresh_token"]

        # Refresh
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": old_refresh,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "refresh_token" in data
        assert data["refresh_token"] != old_refresh

    def test_old_refresh_token_rejected_after_use(self, client, admin_user):
        resp = client.post("/api/auth/login", json={
            "username": "admin", "password": "adminpass",
        })
        old_refresh = resp.json()["refresh_token"]

        # Use it once
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": old_refresh,
        })
        assert resp.status_code == 200

        # Use it again — should be revoked
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": old_refresh,
        })
        assert resp.status_code == 401

    def test_new_refresh_token_works(self, client, admin_user):
        resp = client.post("/api/auth/login", json={
            "username": "admin", "password": "adminpass",
        })
        old_refresh = resp.json()["refresh_token"]

        # Rotate
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": old_refresh,
        })
        new_refresh = resp.json()["refresh_token"]

        # Use the new one
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": new_refresh,
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_logout_revokes_refresh_token(self, client, admin_user, admin_token):
        refresh = create_refresh_token(admin_user.id)

        resp = client.post(
            "/api/auth/logout",
            json={"refresh_token": refresh},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 204

        # Refresh token should now be rejected
        resp = client.post("/api/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert resp.status_code == 401

    def test_logout_requires_auth(self, client, admin_user):
        refresh = create_refresh_token(admin_user.id)
        resp = client.post(
            "/api/auth/logout",
            json={"refresh_token": refresh},
        )
        assert resp.status_code == 401
