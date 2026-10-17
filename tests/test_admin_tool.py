"""
Administrator toolkit tests.

Everything here is local-CLI territory by design: the toolkit's
handlers are called as functions against a temporary database. No
HTTP endpoint participates in admin actions, and one test pins that
the web API never starts publishing administrative state.
"""

import hashlib
import sys

import pytest
from fastapi.testclient import TestClient

import admin_tool
import db
import main

A_EMAIL = "ada@example.com"
B_EMAIL = "ben@example.com"
PASSWORD = "playbook-pass-3"


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    return TestClient(main.app)


def register(client, email, name="Ada Boakye"):
    reply = client.post("/api/register", json={
        "full_name": name, "email": email,
        "phone": "+233 20 600 0000", "password": PASSWORD,
    })
    assert reply.status_code == 201, reply.text


def login(client, email=A_EMAIL):
    reply = client.post("/api/login", json={"email": email,
                                            "password": PASSWORD})
    assert reply.status_code == 200, reply.text


# ---------- list-users / admin flag ----------

def test_new_accounts_are_never_admins(client):
    register(client, A_EMAIL)
    assert db.get_user_by_email(A_EMAIL)["is_admin"] == 0


def test_list_users_shows_counts_and_admin_tag(client, capsys):
    register(client, A_EMAIL)
    login(client)
    register(client, B_EMAIL, name="Ben Owusu")

    admin_tool.main(["list-users"])
    out = capsys.readouterr().out
    assert A_EMAIL in out and B_EMAIL in out
    assert "projects: 0" in out
    assert "active sessions: 1" in out
    assert "[customer]" in out


def test_promote_and_demote_switch_the_flag(client, capsys):
    register(client, A_EMAIL)
    assert db.get_user_by_email(A_EMAIL)["is_admin"] == 0

    assert admin_tool.main(["promote-admin", A_EMAIL]) == 0
    assert db.get_user_by_email(A_EMAIL)["is_admin"] == 1
    assert "PROMOTED" in capsys.readouterr().out

    assert admin_tool.main(["demote-admin", A_EMAIL]) == 0
    assert db.get_user_by_email(A_EMAIL)["is_admin"] == 0


def test_admin_identity_publishes_nothing_through_the_web(client):
    """The site exposes no admin action and even the account API
    leaks no admin marker: an administrator looks like anyone else
    over HTTP. That is the property that must hold until a properly
    protected admin interface is a deliberate, test-covered build."""
    register(client, A_EMAIL)
    user = db.get_user_by_email(A_EMAIL)
    db.set_admin_flag(user["id"], True)
    login(client)

    me = client.get("/api/me").json()
    assert me["logged_in"] is True
    assert "is_admin" not in me


# ---------- reset links (round-up keeps the old behaviour) ----------

def test_reset_link_rejects_unknown_email(capsys):
    assert admin_tool.main(["reset-link", "ghost@example.com"]) == 1
    assert "No account" in capsys.readouterr().out


def test_reset_link_mints_single_use_token(client):
    register(client, A_EMAIL)
    token, link = admin_tool.mint_reset_token(A_EMAIL)

    stored = db.get_password_reset_by_hash(
        hashlib.sha256(token.encode()).hexdigest())
    assert stored is not None
    assert stored["used_at"] is None
    assert f"reset-password.html?token={token}" in link


def test_legacy_create_reset_link_alias_still_works(client, monkeypatch,
                                                    capsys):
    import create_reset_link
    register(client, A_EMAIL)
    monkeypatch.setattr(sys, "argv", ["create_reset_link.py", A_EMAIL])

    assert create_reset_link.main() == 0
    assert "reset-password.html?token=" in capsys.readouterr().out


# ---------- session revocation by the admin ----------

def test_revoke_sessions_signs_every_device_out(client):
    register(client, A_EMAIL)
    device_a = TestClient(main.app)
    device_b = TestClient(main.app)
    for device in (device_a, device_b):
        login(device)
        assert device.get("/api/me").json()["logged_in"] is True

    assert admin_tool.main(["revoke-sessions", A_EMAIL]) == 0

    # Both cookies stop working even though no password changed.
    for device in (device_a, device_b):
        assert device.get("/api/me").json()["logged_in"] is False


def test_sessions_listing_reports_devices(client, capsys):
    register(client, A_EMAIL)
    device = TestClient(main.app)
    login(device)
    admin_tool.main(["sessions", A_EMAIL])
    assert "1 registered device session(s)" in capsys.readouterr().out
