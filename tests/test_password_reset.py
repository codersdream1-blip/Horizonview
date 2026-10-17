"""
Password reset flow tests.

These use a PLAIN TestClient on purpose: no session, no CSRF cookie.
/forgot-password and /reset-password run only for unknown visitors —
that is the whole point of a restart-a-forgotten-account flow.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient

import auth
import db
import main

EMAIL = "reset.target@example.com"
PASSWORD = "old-old-old-1"
NEW_PASSWORD = "new-new-new-1"


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    return TestClient(main.app)


@pytest.fixture()
def registered(client):
    response = client.post("/api/register", json={
        "full_name": "Reset Target",
        "email": EMAIL,
        "phone": "+233 21 000 999",
        "password": PASSWORD,
    })
    assert response.status_code == 201, response.text
    return client


def mint_token(email: str = EMAIL) -> str:
    """Simulate what create_reset_link.py does on the server."""
    token = secrets.token_hex(32)
    db.create_password_reset(
        user_id=db.get_user_by_email(email)["id"],
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=60)
        ).isoformat(),
        requested_ip="tests",
    )
    return token


# ---------- /api/forgot-password ----------

def test_forgot_reply_is_generic_for_unknown_email(client):
    response = client.post("/api/forgot-password",
                           json={"email": "nobody@example.com"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


def test_forgot_reply_identical_for_real_account(registered):
    known = registered.post("/api/forgot-password",
                            json={"email": EMAIL}).json()
    unknown = registered.post("/api/forgot-password",
                              json={"email": "nobody@example.com"}).json()
    assert known["message"] == unknown["message"]


def test_forgot_records_request_for_real_account(registered):
    registered.post("/api/forgot-password", json={"email": EMAIL})
    requests = db.list_reset_requests()
    assert [r["email"] for r in requests] == [EMAIL]


def test_forgot_invalid_email_is_400(client):
    response = client.post("/api/forgot-password", json={"email": "not-an-email"})
    assert response.status_code == 400


def test_forgot_is_rate_limited(client):
    for _ in range(auth.MAX_FORGOT_REQUESTS_PER_WINDOW):
        client.post("/api/forgot-password", json={"email": "x@example.com"})
    response = client.post("/api/forgot-password", json={"email": "x@example.com"})
    assert response.status_code == 429


# ---------- /api/reset-password ----------

def test_reset_password_works_and_swaps_login(registered):
    token = mint_token()
    response = registered.post("/api/reset-password", json={
        "token": token, "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 200

    assert registered.post("/api/login", json={
        "email": EMAIL, "password": PASSWORD
    }).status_code == 401
    assert registered.post("/api/login", json={
        "email": EMAIL, "password": NEW_PASSWORD
    }).status_code == 200


def test_reset_token_is_single_use(registered):
    token = mint_token()
    first = registered.post("/api/reset-password", json={
        "token": token, "new_password": NEW_PASSWORD,
    })
    assert first.status_code == 200

    second = registered.post("/api/reset-password", json={
        "token": token, "new_password": "another-new-2",
    })
    assert second.status_code == 400
    # The reuse attempt must not have changed the working password.
    assert registered.post("/api/login", json={
        "email": EMAIL, "password": NEW_PASSWORD
    }).status_code == 200


def test_reset_rejects_unknown_token(registered):
    response = registered.post("/api/reset-password", json={
        "token": "0" * 64, "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 400


def test_reset_rejects_expired_token(registered):
    db.create_password_reset(
        user_id=db.get_user_by_email(EMAIL)["id"],
        token_hash=auth.hashlib.sha256(b"expired-token").hexdigest(),
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1))
        .isoformat(),
        requested_ip="tests",
    )
    response = registered.post("/api/reset-password", json={
        "token": "expired-token", "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 400
    # Old password still works.
    assert registered.post("/api/login", json={
        "email": EMAIL, "password": PASSWORD
    }).status_code == 200


def test_reset_requires_minimum_length(registered):
    token = mint_token()
    response = registered.post("/api/reset-password", json={
        "token": token, "new_password": "short",
    })
    assert response.status_code == 400
    # Token was not consumed by the validation failure.
    again = registered.post("/api/reset-password", json={
        "token": token, "new_password": NEW_PASSWORD,
    })
    assert again.status_code == 200


def test_failed_reset_attempts_are_rate_limited(registered):
    for _ in range(auth.MAX_RESET_ATTEMPTS_PER_WINDOW):
        registered.post("/api/reset-password", json={
            "token": "wrong-token", "new_password": NEW_PASSWORD,
        })
    token = mint_token()
    response = registered.post("/api/reset-password", json={
        "token": token, "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 429


# ---------- Server-side session revocation ----------

def login_as(client_obj, email=EMAIL, password=PASSWORD):
    response = client_obj.post("/api/login", json={
        "email": email, "password": password,
    })
    assert response.status_code == 200, response.text


def two_logged_in_devices(registered):
    """Simulate two physical devices by giving each a fresh cookie jar."""
    other = TestClient(main.app)  # device two
    login_as(registered)          # device one inside `registered`
    login_as(other)
    for device in (registered, other):
        assert device.get("/api/me").json()["logged_in"] is True
    return other


def test_password_reset_signs_out_every_device(registered, fresh_db):
    device_two = two_logged_in_devices(registered)
    token = mint_token()
    response = registered.post("/api/reset-password", json={
        "token": token, "new_password": NEW_PASSWORD,
    })
    assert response.status_code == 200

    for device in (registered, device_two):
        assert device.get("/api/me").json()["logged_in"] is False
    # And the new password yields a working (fresh) session.
    login_as(registered, password=NEW_PASSWORD)
    assert registered.get("/api/me").json()["logged_in"] is True


def test_logout_revokes_only_its_own_session(registered, fresh_db):
    device_two = two_logged_in_devices(registered)

    response = registered.post("/api/logout", json={})
    assert response.status_code == 200
    assert registered.get("/api/me").json()["logged_in"] is False
    # Device two is untouched: a logout must not sign other devices out.
    assert device_two.get("/api/me").json()["logged_in"] is True


def test_sessions_without_registry_row_are_rejected(registered, fresh_db):
    login_as(registered)
    user = db.get_user_by_email(EMAIL)

    class FakeSession(dict):
        pass

    class FakeRequest:
        pass

    # A cookie from before the registry existed: user_id set, no sid.
    stale = FakeRequest()
    stale.session = FakeSession({"user_id": user["id"]})
    assert auth.get_current_user(stale) is None

    # Same event if the sid exists but its row was revoked.
    stale.session = FakeSession({"user_id": user["id"], "sid": "dead-row"})
    assert auth.get_current_user(stale) is None


# ---------- Registry housekeeping ----------

def _insert_row_for(sid: str, user_id: int, age_days: int) -> None:
    created = (
        datetime.now(timezone.utc) - timedelta(days=age_days)
    ).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO user_sessions (sid, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (sid, user_id, created),
        )


def _sessions_for_user(user_id: int) -> list:
    with db.get_connection() as conn:
        return [
            r["sid"] for r in conn.execute(
                "SELECT sid FROM user_sessions WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]


def test_expired_registry_rows_are_swept_on_login(registered, client):
    user = db.get_user_by_email(EMAIL)
    _insert_row_for("ancient-cookie-row", user["id"], age_days=9)
    assert "ancient-cookie-row" in _sessions_for_user(user["id"])

    # Logging in sweeps rows that cannot belong to a live cookie...
    login_as(client)
    assert "ancient-cookie-row" not in _sessions_for_user(user["id"])

    # ...while the login's own fresh row survives the sweep.
    fresh = _sessions_for_user(user["id"])
    assert len(fresh) == 1
    assert db.session_is_active(fresh[0])


def test_valid_sessions_survive_someone_elses_login(registered, client,
                                                   fresh_db):
    user = db.get_user_by_email(EMAIL)
    login_as(registered)
    live_rows = _sessions_for_user(user["id"])
    assert len(live_rows) == 1

    # A second user logs in days' worth of nothing special later; the
    # first user's fresh row must NOT be caught by the sweep.
    client.post("/api/register", json={
        "full_name": "Second Person", "email": "second@example.com",
        "phone": "+233 21 010 010", "password": "second-pass-1",
    })
    login_as(client, email="second@example.com", password="second-pass-1")

    assert db.session_is_active(live_rows[0])
    assert db.session_is_active(_sessions_for_user(
        db.get_user_by_email("second@example.com")["id"]
    )[0])
