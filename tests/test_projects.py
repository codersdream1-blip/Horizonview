"""
Offline tests for project records: creation, listing and access
control. Every test uses its own temporary database.
"""

import pytest
from fastapi.testclient import TestClient

import db
import main

# Import the tested helper; pytest keeps both test modules fresh.
import projects as projects_api

from test_auth import REGISTER_PAYLOAD


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    return TestClient(main.app)


def make_logged_in(client):
    """Register + log in; returns nothing, keeps the session cookie."""
    response = client.post("/api/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 201
    client.post(
        "/api/login",
        json={"email": REGISTER_PAYLOAD["email"],
              "password": REGISTER_PAYLOAD["password"]},
    )


def test_anonymous_cannot_create_or_list(client):
    assert client.post("/api/projects", json={"name": "x"}).status_code == 401
    assert client.get("/api/projects").status_code == 401


def test_projects_new_page_requires_login(client):
    response = client.get("/projects/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login.html"


def create_project(client, name="East Legon House Build", budget="450000"):
    return client.post("/api/projects", json={
        "name": name,
        "project_type": "construction",
        "location": "East Legon, Accra",
        "budget_ghs": budget,
        "package": "gold",
        "payment_arrangement": "stage-by-stage",
        "description": "Three-bedroom house.",
    })


def test_create_project_and_list(client):
    make_logged_in(client)

    response = create_project(client)
    assert response.status_code == 201
    body = response.json()
    assert body["project"]["budget_display"] == "GH₵450,000"
    assert body["project"]["type_display"] == "New Construction"

    listing = client.get("/api/projects").json()
    assert listing["ok"] is True
    assert len(listing["projects"]) == 1
    assert listing["projects"][0]["name"] == "East Legon House Build"
    assert listing["projects"][0]["status_display"] == "Planning"
    assert listing["projects"][0]["progress_percent"] == 0


def test_validation_rejects_bad_values(client):
    make_logged_in(client)

    response = client.post("/api/projects", json={
        "name": "",
        "project_type": "half-built",
        "location": "",
        "budget_ghs": "one million",
    })
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "name" in errors and "project_type" in errors
    assert "location" in errors and "budget_ghs" in errors

    assert db.get_projects_for_user(1) == []


def test_negative_budget_rejected(client):
    make_logged_in(client)
    response = create_project(client, budget="-5000")
    assert response.status_code == 400
    assert db.get_projects_for_user(1) == []


def test_project_owner_isolation(client, fresh_db, tmp_path, monkeypatch):
    """User B must not see or take over user A's project."""
    make_logged_in(client)
    create_project(client)

    # Register + log in as a second user
    client.post("/api/logout")
    second = dict(REGISTER_PAYLOAD,
                  full_name="Kofi Guar",
                  email="kofi@example.com")
    client.post("/api/register", json=second)
    client.post("/api/login",
                json={"email": second["email"],
                      "password": second["password"]})

    listing = client.get("/api/projects").json()
    assert listing["projects"] == []


def test_dashboard_shows_real_project(client):
    make_logged_in(client)
    assert "No projects yet" in client.get("/dashboard").text

    create_project(client)

    page = client.get("/dashboard").text
    assert "East Legon House Build" in page
    assert "GH₵450,000" in page
    assert "No projects yet" not in page
    assert "HV-DEMO-001" not in page  # the old sample data is gone


def test_project_detail_page_owner_only(client):
    make_logged_in(client)
    response = create_project(client)
    project_id = response.json()["project"]["id"]

    page = client.get(f"/projects/{project_id}")
    assert page.status_code == 200
    assert "East Legon House Build" in page.text
    assert "GH₵450,000" in page.text
    assert "East Legon, Accra" in page.text


def test_project_detail_other_users_get_404(client):
    # User A creates a project; user B must get a plain 404.
    client.post("/api/register", json=REGISTER_PAYLOAD)
    client.post("/api/login",
                json={"email": REGISTER_PAYLOAD["email"],
                      "password": REGISTER_PAYLOAD["password"]})
    project_id = create_project(client).json()["project"]["id"]
    client.post("/api/logout")

    other = dict(REGISTER_PAYLOAD, email="another@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})

    assert client.get(f"/projects/{project_id}").status_code == 404


def test_project_detail_anonymous_redirects(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    client.post("/api/login",
                json={"email": REGISTER_PAYLOAD["email"],
                      "password": REGISTER_PAYLOAD["password"]})
    project_id = create_project(client).json()["project"]["id"]
    client.post("/api/logout")

    response = client.get(f"/projects/{project_id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login.html"


def test_dashboard_links_to_project_detail(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    page = client.get("/dashboard").text
    assert f'href="/projects/{project_id}"' in page