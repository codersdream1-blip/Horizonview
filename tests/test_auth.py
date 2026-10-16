"""
Offline tests for the HorizonView account API.

Each test gets its own temporary database file, so running the tests
never touches the real horizonview.db used by the live server.
No network access is needed.
"""

import bcrypt
import pytest
from fastapi.testclient import TestClient

import db
import main


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Give every test its own empty temporary database."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    return TestClient(main.app)


REGISTER_PAYLOAD = {
    "full_name": "Akosua Mensah",
    "email": "akosua@example.com",
    "phone": "+233 24 555 0199",
    "password": "correct-horse-battery",
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "HorizonView"}


def test_private_files_are_not_served(client):
    for path in ("/main.py", "/requirements.txt", "/db.py", "/nonexistent.html"):
        assert client.get(path).status_code == 404, path


def test_register_creates_hashed_user(client):
    response = client.post("/api/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 201
    assert response.json()["ok"] is True

    user = db.get_user_by_email("akosua@example.com")
    assert user is not None
    assert user["full_name"] == "Akosua Mensah"

    # The hash must exist and must not be the plain password.
    stored = user["password_hash"]
    assert stored != REGISTER_PAYLOAD["password"]
    assert stored.startswith("$2")  # bcrypt signature

    assert bcrypt.checkpw(REGISTER_PAYLOAD["password"].encode(), stored.encode())


def test_register_rejects_short_password(client):
    payload = dict(REGISTER_PAYLOAD, password="short")
    response = client.post("/api/register", json=payload)
    assert response.status_code == 400
    assert "password" in response.json()["errors"]
    assert db.get_user_by_email(payload["email"]) is None


def test_register_rejects_bad_email(client):
    payload = dict(REGISTER_PAYLOAD, email="not-an-email")
    response = client.post("/api/register", json=payload)
    assert response.status_code == 400
    assert "email" in response.json()["errors"]


def test_register_rejects_duplicate_email(client):
    assert client.post("/api/register", json=REGISTER_PAYLOAD).status_code == 201
    response = client.post("/api/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 400
    assert "already registered" in response.json()["message"]


def test_login_with_correct_password_sets_session(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)

    response = client.post(
        "/api/login",
        json={"email": "Akosua@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Akosua Mensah"

    me = client.get("/api/me").json()
    assert me["logged_in"] is True
    assert me["email"] == "akosua@example.com"


def test_login_is_case_insensitive_on_email(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    response = client.post(
        "/api/login",
        json={"email": "AKOSUA@EXAMPLE.COM", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


def test_login_rejects_wrong_password(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    response = client.post(
        "/api/login",
        json={"email": "akosua@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["message"] == "Email or password is incorrect."
    assert client.get("/api/me").json()["logged_in"] is False


def test_login_unknown_email_uses_generic_message(client):
    response = client.post(
        "/api/login",
        json={"email": "ghost@example.com", "password": "whatever-value"},
    )
    assert response.status_code == 401
    assert response.json()["message"] == "Email or password is incorrect."


def test_logout_clears_session(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    client.post(
        "/api/login",
        json={"email": "akosua@example.com", "password": "correct-horse-battery"},
    )
    assert client.get("/api/me").json()["logged_in"] is True

    client.post("/api/logout")
    assert client.get("/api/me").json()["logged_in"] is False


def test_me_is_anonymous_without_login(client):
    assert client.get("/api/me").json() == {"logged_in": False}
