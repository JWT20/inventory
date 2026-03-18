# Account Management Improvements — Implementation Plan

## 1. Password Change (Admin reset + Self-service)

### Backend

**Schema** (`schemas.py`):
- Add `AdminResetPassword(BaseModel)`: `new_password: str` (min 8, max 128) — validated with password policy (see point 7)
- Add `ChangeOwnPassword(BaseModel)`: `current_password: str`, `new_password: str` — validated with password policy

**Endpoints** (`routers/auth.py`):
- `PUT /auth/users/{user_id}/password` — admin-only, resets any user's password
  - Dependency: `require_admin`
  - Validate new password with `validate_password_strength()` (point 7)
  - Hash and save, publish `password_reset` audit event
  - Return 204
- `PUT /auth/me/password` — any authenticated user changes their own password
  - Dependency: `get_current_user`
  - Verify `current_password` against stored hash using `verify_password()`
  - Validate `new_password` with `validate_password_strength()`
  - Hash and save, publish `password_changed` audit event
  - Return 204

### Frontend

**API** (`api.ts`):
- Add `resetUserPassword(userId: number, newPassword: string)`
- Add `changeMyPassword(currentPassword: string, newPassword: string)`

**Accounts page** (`accounts.tsx`):
- Add a "Reset password" button per user row (key icon) → opens dialog with single password input
- Show password requirements hint below the input field

**Profile/settings area** — "Change my password":
- Form: current password, new password, confirm new password
- Client-side validation: match check + strength hint

### Tests (`test_auth.py`)

New class `TestPasswordChange`:
- `test_admin_resets_user_password` — reset works, user can login with new password
- `test_admin_resets_own_password` — admin can reset their own
- `test_non_admin_cannot_reset_others` — merchant/courier get 403
- `test_user_changes_own_password` — correct current password → success
- `test_wrong_current_password_rejected` — returns 400
- `test_weak_password_rejected` — returns 422 (ties into point 7)

---

## 4. Refresh Token Rotation

### Backend

**Auth** (`auth.py`):
- Add in-memory token blocklist: `_revoked_refresh_tokens: dict[str, float]` mapping `jti → expiry`
  - Prune expired entries periodically (on each check) to prevent memory growth
- Modify `create_refresh_token()`: add `jti` (UUID4) claim to JWT payload
- Modify `decode_refresh_token()`: check `jti` not in revoked set; return `(user_id, jti, exp)` tuple
- Add `revoke_refresh_token(jti: str, exp: float)`: add to revoked dict

**Router** (`routers/auth.py`):
- Modify `POST /auth/refresh`:
  1. Decode old refresh token → get `user_id`, `jti`, `exp`
  2. Revoke the old token's `jti`
  3. Issue new access token AND new refresh token
  4. Return both in response

**Schema** (`schemas.py`):
- Update `RefreshResponse` to include `refresh_token: str` field

### Frontend

**API** (`api.ts`):
- Update `tryRefresh()`: read `refresh_token` from response and call `setRefreshToken()` to store the rotated token

### Tests

New class `TestTokenRotation`:
- `test_refresh_returns_new_refresh_token` — response includes a new `refresh_token`
- `test_old_refresh_token_rejected_after_use` — reusing old token returns 401
- `test_new_refresh_token_works` — the rotated refresh token produces a valid access token

---

## 5. Server-side Logout (Token Revocation)

### Backend

**Schema** (`schemas.py`):
- Add `LogoutRequest(BaseModel)`: `refresh_token: str`

**Router** (`routers/auth.py`):
- Add `POST /auth/logout`:
  - Dependency: `get_current_user`
  - Accept `refresh_token` in request body
  - Decode and revoke the refresh token's `jti` (reuses blocklist from point 4)
  - Publish `user_logout` audit event
  - Return 204

### Frontend

**Auth** (`auth.tsx`):
- Update `logout()`: call `api.logout(refreshToken)` before clearing localStorage
- Gracefully handle failure (still clear local state regardless)

**API** (`api.ts`):
- Add `logout(refreshToken: string)` method

### Tests

New class `TestLogout`:
- `test_logout_revokes_refresh_token` — after logout, the refresh token is rejected
- `test_logout_requires_auth` — unauthenticated request returns 401

---

## 6. Redis-backed Rate Limiting

### Backend

**Config** (`config.py`):
- Add `redis_url: str = ""` setting (empty = fall back to in-memory)

**Auth** (`auth.py`):
- Define `RateLimiter` ABC with methods: `check(key)`, `record_failure(key)`, `clear(key)`
- `InMemoryRateLimiter` — refactor current `_failed_attempts` dict into this class
- `RedisRateLimiter` — uses Redis INCR + EXPIRE:
  - Key pattern: `rate_limit:{key}`
  - INCR key, set EXPIRE to lockout window on first increment
  - If count > MAX_LOGIN_ATTEMPTS → raise 429
  - On success → DEL key
- Factory: `get_rate_limiter()` → returns Redis if `redis_url` configured, else in-memory
- Module-level singleton so the limiter is created once

**Dependencies**: Add `redis` to `requirements.txt` (optional — only imported if `redis_url` is set)

### Tests

- Existing rate-limit tests continue working (in-memory limiter)
- Add `TestRedisRateLimiter` with `@pytest.mark.skipif(not redis_available)` guard

---

## 7. Password Strength Validation

### Backend

**New function in `auth.py`**: `validate_password_strength(password: str) -> list[str]`
- Rules:
  - Minimum 8 characters
  - At least 1 uppercase letter
  - At least 1 lowercase letter
  - At least 1 digit
- Returns list of violation messages (empty = valid)

**Schema** (`schemas.py`):
- Add Pydantic `field_validator` on `password` / `new_password` fields in `UserCreate`, `AdminResetPassword`, `ChangeOwnPassword`
- Call `validate_password_strength()` and raise `ValueError` if violations
- Update `min_length` from 6 to 8

### Frontend

- Show password requirements below input fields: "Min. 8 tekens, 1 hoofdletter, 1 kleine letter, 1 cijfer"

### Tests

New class `TestPasswordPolicy`:
- `test_short_password_rejected` — <8 chars → 422
- `test_no_uppercase_rejected` — "abcdefg1" → 422
- `test_no_lowercase_rejected` — "ABCDEFG1" → 422
- `test_no_digit_rejected` — "Abcdefgh" → 422
- `test_valid_password_accepted` — "Secret1x" → 201
- Update existing `test_short_password_rejected` fixture to use 7-char password

---

## 10. Last-Admin Protection (Self-demotion Prevention Foundation)

### Backend

**Router** (`routers/auth.py`):
- In `DELETE /auth/users/{user_id}`, before deleting, check:
  - If target user is admin → count active admins in DB
  - If count == 1 → return 400 "Cannot delete the last admin account"
- This prevents accidentally locking out the entire system

### Tests

Add to `TestDeleteUser`:
- `test_cannot_delete_last_admin` — single admin exists, another admin tries to delete via a second admin (or self-delete check already exists). Create scenario: admin1 tries to delete admin2 who is the only *other* admin — succeeds. But if admin2 is the last admin — blocked.
- `test_can_delete_non_last_admin` — 2 admins exist, deleting one succeeds

---

## File Change Summary

| File | Changes |
|------|---------|
| `backend/app/schemas.py` | Add `AdminResetPassword`, `ChangeOwnPassword`, `LogoutRequest`; update `RefreshResponse`; add password validators; bump min_length to 8 |
| `backend/app/auth.py` | Add `validate_password_strength()`, `RateLimiter` ABC + implementations, refresh token `jti` + blocklist, `revoke_refresh_token()` |
| `backend/app/config.py` | Add `redis_url` setting |
| `backend/app/routers/auth.py` | Add `PUT /users/{id}/password`, `PUT /me/password`, `POST /logout`; modify `POST /refresh` for rotation; add last-admin guard on delete |
| `backend/requirements.txt` | Add `redis` (optional dependency) |
| `backend/tests/test_auth.py` | Add `TestPasswordChange`, `TestTokenRotation`, `TestLogout`, `TestPasswordPolicy`, last-admin tests |
| `frontend/src/lib/api.ts` | Add `resetUserPassword`, `changeMyPassword`, `logout`; update `tryRefresh` |
| `frontend/src/components/accounts.tsx` | Add reset-password dialog; show password requirements |
| `frontend/src/lib/auth.tsx` | Update `logout()` to call server-side endpoint |

## Implementation Order

1. **Password strength validation** (point 7) — foundation used by all password endpoints
2. **Password change endpoints** (point 1) — depends on point 7
3. **Refresh token rotation** (point 4) — adds `jti` + blocklist infrastructure
4. **Server-side logout** (point 5) — reuses blocklist from point 4
5. **Last-admin guard** (point 10) — small, independent change
6. **Redis rate limiting** (point 6) — independent, can be done last
7. **Frontend updates** — after all backend endpoints are stable
8. **Tests** — written alongside each backend change
