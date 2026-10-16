"""
Offline tests for project records: creation, listing and access
control. Every test uses its own temporary database.
"""

import pytest
from datetime import datetime
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


def add_update(client, project_id, title="Foundation completed",
               progress="40", stage="foundation"):
    return client.post(f"/api/projects/{project_id}/updates", json={
        "title": title,
        "body": "Foundation works were completed this week.",
        "stage": stage,
        "progress_percent": progress,
    })


def test_add_update_sets_project_progress(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    assert add_update(client, project_id, progress="40").status_code == 201

    stored = db.get_project_by_id(project_id)
    assert stored["progress_percent"] == 40

    updates = db.get_updates_for_project(project_id)
    assert len(updates) == 1
    assert updates[0]["title"] == "Foundation completed"
    assert updates[0]["progress_percent"] == 40


def test_progress_only_moves_forward(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    add_update(client, project_id, progress="60")
    add_update(client, project_id, title="Prep works", progress="30")

    stored = db.get_project_by_id(project_id)
    assert stored["progress_percent"] == 60


def test_update_validation(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = add_update(client, project_id, title="x", progress="150")
    assert response.status_code == 400
    errors = response.json()["errors"]
    assert "title" in errors and "progress_percent" in errors
    assert db.get_updates_for_project(project_id) == []


def test_other_user_cannot_add_update(client):
    client.post("/api/register", json=REGISTER_PAYLOAD)
    client.post("/api/login",
                json={"email": REGISTER_PAYLOAD["email"],
                      "password": REGISTER_PAYLOAD["password"]})
    project_id = create_project(client).json()["project"]["id"]
    client.post("/api/logout")

    other = dict(REGISTER_PAYLOAD, email="stranger@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})

    assert add_update(client, project_id).status_code == 404


def test_detail_page_shows_timeline_and_form(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    add_update(client, project_id, title="Blockwork started",
               progress="25", stage="blockwork")

    page = client.get(f"/projects/{project_id}").text
    assert "Blockwork started" in page
    assert "Blockwork" in page
    assert "Progress set to 25%" in page
    assert "Record a progress note" in page
    # "form action-free fetch style": the form id is present
    assert 'id="addUpdateForm"' in page


def test_progress_updates_detail_percent(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    add_update(client, project_id, progress="45")

    page = client.get(f"/projects/{project_id}").text
    assert "45%" in page


def test_anonymous_cannot_add_or_list_updates(client):
    # 401 (not 404) for anonymous users: identity is checked first,
    # so nothing about project existence is revealed.
    assert client.get("/api/projects/1/updates").status_code == 401
    response = client.post(
        "/api/projects/1/updates",
        json={"title": "Note", "body": "Some body text here.",
              "progress_percent": 10},
    )
    assert response.status_code == 401


# ---------- Site photos ----------

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"x" * 2048
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"y" * 2048


def upload_photo(client, project_id, filename="site.jpg",
                 content=JPEG_BYTES, caption="Foundation work",
                 content_type="image/jpeg"):
    return client.post(
        f"/api/projects/{project_id}/photos",
        files={"photo": (filename, content, content_type)},
        data={"caption": caption},
    )


def test_upload_photo_stores_file_and_serves_it_back(client, tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = upload_photo(client, project_id)
    assert response.status_code == 201

    photos = db.get_photos_for_project(project_id)
    assert len(photos) == 1
    photo_id = photos[0]["id"]
    assert photos[0]["caption"] == "Foundation work"
    assert photos[0]["extension"] == "jpg"
    assert photos[0]["stored_path"].startswith(f"project_{project_id}/")

    served = client.get(f"/api/photos/{photo_id}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/jpeg")
    assert served.content == JPEG_BYTES


def test_upload_png_is_detected_by_content(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = upload_photo(client, project_id, filename="plan.png",
                            content=PNG_BYTES, content_type="image/png")
    assert response.status_code == 201
    assert db.get_photos_for_project(project_id)[0]["extension"] == "png"


def test_rejects_non_image_content_even_with_image_content_type(
        client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    # Declared as image/jpeg but the content is plain text: must be
    # caught, never stored.
    response = upload_photo(client, project_id, filename="fake.jpg",
                            content=b"this is not really a photo",
                            caption="", content_type="image/jpeg")
    assert response.status_code == 400
    assert "JPEG or PNG" in response.json()["message"]
    assert db.get_photos_for_project(project_id) == []


def test_rejects_oversize_photo(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    six_mb = JPEG_BYTES + b"z" * (6 * 1024 * 1024)
    response = upload_photo(client, project_id, content=six_mb)
    assert response.status_code == 400
    assert "5 MB" in response.json()["message"]
    assert db.get_photos_for_project(project_id) == []


def test_rejects_empty_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = upload_photo(client, project_id, content=b"")
    assert response.status_code == 400


def test_photo_page_shows_upload_form_and_thumbnails(client, tmp_path,
                                                     monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_photo(client, project_id, caption="Roof frame ready")
    photo_id = db.get_photos_for_project(project_id)[0]["id"]

    page = client.get(f"/projects/{project_id}").text
    assert "Upload a site photo" in page
    assert 'id="photoFile"' in page
    assert f'<img src="/api/photos/{photo_id}"' in page
    assert "Roof frame ready" in page


def test_other_user_cannot_view_photo_and_anonymous_gets_401(
        client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_photo(client, project_id)
    photo_id = db.get_photos_for_project(project_id)[0]["id"]

    client.post("/api/logout")

    other = dict(REGISTER_PAYLOAD, email="stranger2@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})
    # Another logged-in user: 404, never a leak that the photo exists.
    assert client.get(f"/api/photos/{photo_id}").status_code == 404

    client.post("/api/logout")
    assert client.get(f"/api/photos/{photo_id}").status_code == 401


def test_upload_photo_requires_ownership(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    client.post("/api/logout")

    other = dict(REGISTER_PAYLOAD, email="stranger3@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})

    assert upload_photo(client, project_id).status_code == 404


# ---------- Budget & expenses ----------

def log_expense(client, project_id, title="20 bags of cement",
                amount="1500", category="materials", spent_on=""):
    payload = {"title": title, "amount_ghs": amount, "category": category}
    if spent_on:
        payload["spent_on"] = spent_on
    return client.post(f"/api/projects/{project_id}/expenses", json=payload)


def test_log_expense_updates_running_totals(client):
    make_logged_in(client)
    project_id = create_project(client, budget="50000").json()["project"]["id"]

    assert log_expense(client, project_id, amount="1500").status_code == 201
    assert log_expense(client, project_id, title="Carpenter week 1",
                       amount="2,250.50", category="labour").status_code == 201

    expenses = db.get_expenses_for_project(project_id)
    assert len(expenses) == 2

    # The comma-format amount was parsed as a real number.
    response = client.get(f"/api/projects/{project_id}/expenses").json()
    summary = response["summary"]
    assert summary["total_spent"] == round(1500 + 2250.50, 2)
    assert summary["total_spent_display"] == "GH₵3,750.50"
    assert summary["remaining"] == round(50000 - 3750.50, 2)
    assert summary["spent_percent"] == 8


def test_expense_defaults_to_today(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    log_expense(client, project_id)
    stored = db.get_expenses_for_project(project_id)[0]
    assert stored["spent_on"] == datetime.now().strftime("%Y-%m-%d")


def test_expense_validation_rejects_bad_values(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = log_expense(client, project_id, amount="-40")
    assert response.status_code == 400
    assert "amount_ghs" in response.json()["errors"]

    response = log_expense(client, project_id, amount="12,ab")
    assert response.status_code == 400

    response = log_expense(client, project_id, category="suspicious")
    assert response.status_code == 400
    assert "category" in response.json()["errors"]

    response = log_expense(client, project_id, spent_on="16/10/2026")
    assert response.status_code == 400
    assert "spent_on" in response.json()["errors"]

    assert db.get_expenses_for_project(project_id) == []


def test_expense_access_control(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    log_expense(client, project_id)
    client.post("/api/logout")

    assert log_expense(TestClient(main.app), project_id).status_code == 401
    assert client.get(f"/api/projects/{project_id}/expenses").status_code == 401

    other = dict(REGISTER_PAYLOAD, email="stranger4@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})
    assert client.get(f"/api/projects/{project_id}/expenses").status_code == 404
    assert log_expense(client, project_id).status_code == 404


def test_budget_page_shows_totals_and_rows(client):
    make_logged_in(client)
    project_id = create_project(client, budget="50000").json()["project"]["id"]
    log_expense(client, project_id, title="20 bags of cement", amount="1500")
    log_expense(client, project_id, title="Welding machine hire",
                amount="800", category="equipment")

    page = client.get(f"/projects/{project_id}").text
    assert "GH₵50,000" in page                # budget box
    assert "GH₵2,300.00" in page              # spent-so-far box
    assert "GH₵47,700.00" in page             # remaining box
    assert "5% of the budget recorded as spent." in page
    assert "20 bags of cement" in page
    assert "Equipment Hire" in page
    assert "GH₵800.00" in page
    assert 'id="expenseForm"' in page


def test_over_budget_shows_red_state(client):
    make_logged_in(client)
    project_id = create_project(client, budget="1000").json()["project"]["id"]
    log_expense(client, project_id, amount="1250")

    page = client.get(f"/projects/{project_id}").text
    assert "GH₵-250.00" in page
    assert "over-budget" in page
    assert "#b3543a" in page                  # red bar fill
    # Bar width must be capped at 100%, never wider.
    assert 'width: 100%' in page


def test_project_without_budget_still_sums_spending(client):
    make_logged_in(client)
    # Create with no budget figure.
    client.post("/api/projects", json={
        "name": "Renovation Without Plan",
        "project_type": "renovation",
        "location": "Tema",
        "package": "silver",
        "payment_arrangement": "discuss-later",
    })
    project_id = client.get("/api/projects").json()["projects"][0]["id"]

    log_expense(client, project_id, amount="350")

    response = client.get(f"/api/projects/{project_id}/expenses").json()
    assert response["summary"]["total_spent_display"] == "GH₵350.00"
    assert "remaining" not in response["summary"]

    page = client.get(f"/projects/{project_id}").text
    assert "no budget recorded yet" in page
    assert "GH₵350.00" in page
def send_message(client, project_id, body="Hello, please share the schedule."):
    return client.post(
        f"/api/projects/{project_id}/messages", json={"body": body}
    )


def test_send_message_stores_and_lists_in_chat_order(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    assert send_message(client, project_id, "First message").status_code == 201
    send_message(client, project_id, "Second message")

    data = client.get(f"/api/projects/{project_id}/messages").json()
    bodies = [m["body"] for m in data["messages"]]
    # Chat order: oldest first, reading like a conversation.
    assert bodies == ["First message", "Second message"]
    assert data["messages"][0]["sender_display"] == "You"


def test_message_page_renders_thread(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    send_message(client, project_id, "Please confirm the roofing team size.")

    page = client.get(f"/projects/{project_id}").text
    assert "Please confirm the roofing team size." in page
    assert "Send a message" in page
    assert 'id="messageForm"' in page
    assert "msg-bubble" in page


def test_message_validation(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    assert send_message(client, project_id, body="   ").status_code == 400
    assert send_message(client, project_id, body="x" * 3000).status_code == 400
    assert db.get_messages_for_project(project_id) == []


def test_message_access_control(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    send_message(client, project_id)
    client.post("/api/logout")

    assert send_message(TestClient(main.app), project_id).status_code == 401
    assert client.get(f"/api/projects/{project_id}/messages").status_code == 401

    other = dict(REGISTER_PAYLOAD, email="stranger5@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})
    assert client.get(f"/api/projects/{project_id}/messages").status_code == 404
    assert send_message(client, project_id).status_code == 404
