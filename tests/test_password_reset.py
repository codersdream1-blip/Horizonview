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
