"""
CSRF protection tests. These use a plain TestClient deliberately —
echoing the token is the browser's job, and these tests prove the
server enforces it even when nobody echoes it.
"""

import pytest
from fastapi.testclient import TestClient

import auth
import db
import main
from test_auth import REGISTER_PAYLOAD


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    return TestClient(main.app)


@pytest.fixture()
def logged_in(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    client.post("/api/login", json={
        "email": REGISTER_PAYLOAD["email"],
        "password": REGISTER_PAYLOAD["password"],
    })
    return client


def get_session_token(client) -> str:
    # The token lives inside the signed session; the test reads the
    # readable mirror the browser would use.
    return client.cookies.get(auth.CSRF_COOKIE_NAME)


def test_anonymous_mutation_gets_401_not_403(client):
    # Identity is checked first; CSRF is a logged-in-session concern.
    assert client.post("/api/projects", json={"name": "x"}).status_code == 401


def test_logged_in_mutation_without_token_is_403(logged_in):
    response = logged_in.post("/api/projects", json={
        "name": "Legitimate Looking", "project_type": "construction",
        "location": "Accra", "package": "silver",
        "payment_arrangement": "discuss-later",
    })
    assert response.status_code == 403
    assert "Security check failed" in response.json()["message"]
    # Nothing was created.
    user = db.get_user_by_email(REGISTER_PAYLOAD["email"])
    assert db.get_projects_for_user(user["id"]) == []


def test_logged_in_mutation_with_wrong_token_is_403(logged_in):
    response = logged_in.post("/api/projects", json={
        "name": "Forged From Evil Site", "project_type": "construction",
        "location": "Accra", "package": "silver",
        "payment_arrangement": "discuss-later",
    }, headers={"X-CSRF-Token": "attacker-guessed-value"})
    assert response.status_code == 403


def test_correct_token_header_passes(logged_in):
    token = get_session_token(logged_in)
    assert token, "login must set the readable csrf_token cookie"

    response = logged_in.post("/api/projects", json={
        "name": "Honest Request", "project_type": "construction",
        "location": "Accra", "package": "silver",
        "payment_arrangement": "discuss-later",
    }, headers={"X-CSRF-Token": token})
    assert response.status_code == 201


def test_reads_are_exempt(logged_in):
    # Listing projects needs no CSRF header: reads do not change state.
    assert logged_in.get("/api/projects").status_code == 200


def test_login_and_register_are_exempt(client):
    # No session exists at those points; cross-site form POSTs are
    # already refused by the session cookie's SameSite=Lax.
    assert client.post("/api/register", json=REGISTER_PAYLOAD).status_code == 201
    assert client.post("/api/login", json={
        "email": REGISTER_PAYLOAD["email"],
        "password": REGISTER_PAYLOAD["password"],
    }).status_code == 200


def test_delete_also_requires_token(logged_in, tmp_path, monkeypatch):
    import projects
    monkeypatch.setattr(projects, "UPLOAD_DIR", tmp_path)

    token = get_session_token(logged_in)
    project_id = logged_in.post("/api/projects", json={
        "name": "Delete Me", "project_type": "construction",
        "location": "Accra", "package": "silver",
        "payment_arrangement": "discuss-later",
    }, headers={"X-CSRF-Token": token}).json()["project"]["id"]

    logged_in.post(f"/api/projects/{project_id}/expenses", json={
        "title": "Cement bags", "amount_ghs": "60", "category": "materials"},
        headers={"X-CSRF-Token": token})
    expense_id = logged_in.get(
        f"/api/projects/{project_id}/expenses").json()["expenses"][0]["id"]

    # Without the token: 403, and the record survives.
    assert logged_in.delete(f"/api/expenses/{expense_id}").status_code == 403
    assert len(logged_in.get(
        f"/api/projects/{project_id}/expenses").json()["expenses"]) == 1

    # With the token: the delete goes through.
    assert logged_in.delete(
        f"/api/expenses/{expense_id}",
        headers={"X-CSRF-Token": token},
    ).status_code == 200
