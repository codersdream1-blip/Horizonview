"""
Offline tests for project records: creation, listing and access
control. Every test uses its own temporary database.
"""

import re

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
    from conftest import CsrfClient
    return CsrfClient(main.app)


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


def test_comma_formatted_budget_is_accepted(client):
    """Ghanaians write 450,000.25 with commas; the expense ledger
    already accepted that habit, so the budget field must too."""
    make_logged_in(client)
    response = create_project(client, budget="450,000.25")
    assert response.status_code == 201, response.text
    assert response.json()["project"]["budget_display"] == "GH₵450,000"
    assert db.get_projects_for_user(1)[0]["budget_ghs"] == 450000.25


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


# ---------- Dashboard wired to real data ----------

def _squash(html: str) -> str:
    """Collapse all whitespace so assertions survive template line breaks."""
    return re.sub(r"\s+", " ", html)


def test_dashboard_shows_real_activity_and_totals(client, tmp_path,
                                                 monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client, budget="50000").json()["project"]["id"]

    client.post(f"/api/projects/{project_id}/updates", json={
        "title": "Foundation cast", "body":
            "Foundation works were completed this week without problems.",
        "stage": "foundation", "progress_percent": "35",
    })
    log_expense(client, project_id, amount="5000")
    log_expense(client, project_id, title="Welder day rate",
                amount="750.50", category="labour")
    send_message(client, project_id, "Please confirm next delivery date.")

    page = _squash(client.get("/dashboard").text)

    # Project card shows the latest note.
    assert "Latest note:" in page
    assert "Foundation cast" in page

    # Budget card shows combined totals (5000 + 750.50 = 5750.50).
    assert "GH₵50,000" in page          # total budget
    assert "GH₵5,750.50" in page        # total spent
    assert "GH₵44,249.50" in page       # remaining
    assert "2 expense entries" in page

    # Activity feed lists the real events.
    assert "Progress note: Foundation cast — 35%" in page
    assert "Welder day rate" in page
    assert "Message: Please confirm next delivery date." in page

    # Messages card counts and previews the thread.
    assert "1 message in your project threads." in page
    assert "Please confirm next delivery date." in page

    # The real panels link to the project detail page.
    assert page.count(f'href="/projects/{project_id}"') >= 3


def test_dashboard_photo_thumbnails(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_photo(client, project_id, caption="Roofing steel ready")

    page = _squash(client.get("/dashboard").text)
    assert 'class="dash-photo-thumb"' in page
    assert "/api/photos/1" in page
    assert "1 photo uploaded across your projects." in page


def test_dashboard_empty_states_stay_honest(client):
    make_logged_in(client)
    create_project(client)              # no notes/photos/expenses yet

    page = _squash(client.get("/dashboard").text)
    assert "No progress notes yet" in page
    assert "No site photos yet" in page
    assert "No expenses recorded yet" in page
    assert "No activity yet" in page
    assert "No messages yet" in page
    # Planned panels keep their own clearly-labelled section, with no
    # pretend buttons anywhere near them.
    assert "Coming Later" in page
    assert "PLANNED — NOT CONNECTED YET" in page
    assert "Live Video Monitoring" in page


# ---------- Documents ----------

PDF_BYTES = b"%PDF-1.4\n" + b"1 0 obj\n<< /Type /Catalog >>\nendobj\n" \
            b"%%EOF\n" + b"\x00" * 1024


def upload_document(client, project_id, filename="contract.pdf",
                    content=PDF_BYTES, description="Signed contract",
                    content_type="application/pdf"):
    return client.post(
        f"/api/projects/{project_id}/documents",
        files={"document": (filename, content, content_type)},
        data={"description": description},
    )


def test_upload_document_stores_and_serves(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    response = upload_document(client, project_id)
    assert response.status_code == 201

    docs = db.get_documents_for_project(project_id)
    assert len(docs) == 1
    assert docs[0]["extension"] == "pdf"
    assert docs[0]["description"] == "Signed contract"
    assert docs[0]["stored_path"].startswith(f"project_{project_id}/")

    served = client.get(f"/api/documents/{docs[0]['id']}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/pdf")
    assert served.content == PDF_BYTES


def test_document_rejects_unknown_type_and_christened_exe(client, tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    # A .pdf name whose content is an executable signature.
    response = upload_document(client, project_id, filename="trojan.pdf",
                               content=b"MZ" + b"\x00" * 512, description="")
    assert response.status_code == 400
    assert "PDF, PNG or JPEG" in response.json()["message"]

    # A plain Word document by content, not just by name.
    response = upload_document(client, project_id, filename="notes.docx",
                               content=b"P" * 64, description="",
                               content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert response.status_code == 400

    assert db.get_documents_for_project(project_id) == []


def test_document_size_and_ownership(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    eleven_mb = PDF_BYTES + b"\x00" * (11 * 1024 * 1024)
    response = upload_document(client, project_id, content=eleven_mb)
    assert response.status_code == 400
    assert "10 MB" in response.json()["message"]

    client.post("/api/logout")
    other = dict(REGISTER_PAYLOAD, email="stranger6@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})

    assert upload_document(client, project_id).status_code == 404
    assert client.get("/api/documents/1").status_code == 404

    client.post("/api/logout")
    assert client.get("/api/documents/1").status_code == 401


def test_document_page_and_dashboard_wiring(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_document(client, project_id)
    doc_id = db.get_documents_for_project(project_id)[0]["id"]

    detail = _squash(client.get(f"/projects/{project_id}").text)
    assert "Project documents" in detail
    assert "Signed contract" in detail
    assert f'href="/api/documents/{doc_id}"' in detail
    assert "Upload a document" in detail

    dash = _squash(client.get("/dashboard").text)
    assert "1 document stored." in dash
    assert "Signed contract" in dash
    # The prototype-notice entry for documents is retired.
    assert "Document storage is not connected" not in dash


# ---------- Correcting records (delete / re-enter) ----------

def test_delete_expense_falls_totals_back(client):
    make_logged_in(client)
    project_id = create_project(client, budget="50000").json()["project"]["id"]
    log_expense(client, project_id, amount="5000")
    log_expense(client, project_id, title="Welder day rate",
                amount="750.50", category="labour")

    # Newest entry first in the ledger order (same date).
    newest_id = db.get_expenses_for_project(project_id)[0]["id"]

    # Sanity: totals before delete.
    spent_before = client.get(f"/api/projects/{project_id}/expenses") \
        .json()["summary"]["total_spent"]
    assert spent_before == 5750.50

    response = client.delete(f"/api/expenses/{newest_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["total_spent"] == 5000
    assert body["remaining"] == 45000

    assert len(db.get_expenses_for_project(project_id)) == 1


def test_delete_expense_edit_amount_via_put(client):
    make_logged_in(client)
    project_id = create_project(client, budget="50000").json()["project"]["id"]
    log_expense(client, project_id, amount="5000")
    expense_id = db.get_expenses_for_project(project_id)[0]["id"]

    response = client.put(f"/api/expenses/{expense_id}", json={
        "title": "20 bags of cement", "amount_ghs": "4,375.25",
        "category": "materials",
    })
    assert response.status_code == 200
    assert response.json()["total_spent"] == 4375.25

    stored = db.get_expenses_for_project(project_id)[0]
    assert stored["amount_ghs"] == 4375.25
    assert stored["title"] == "20 bags of cement"


def test_delete_note_recalculates_progress(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]

    client.post(f"/api/projects/{project_id}/updates", json={
        "title": "Roof at 60", "body": "Roofing structure now fully in place.",
        "stage": "roofing", "progress_percent": "60"})
    client.post(f"/api/projects/{project_id}/updates", json={
        "title": "Prep at 30", "body": "Site preparation work was started.",
        "stage": "site-clearing", "progress_percent": "30"})
    assert db.get_project_by_id(project_id)["progress_percent"] == 60

    note60 = [u for u in db.get_updates_for_project(project_id)
              if u["progress_percent"] == 60][0]
    response = client.delete(f"/api/updates/{note60['id']}")
    assert response.status_code == 200
    assert response.json()["project_progress"] == 30
    assert db.get_project_by_id(project_id)["progress_percent"] == 30

    # Deleting the remaining note resets the bar to 0.
    last_id = db.get_updates_for_project(project_id)[0]["id"]
    client.delete(f"/api/updates/{last_id}")
    assert db.get_project_by_id(project_id)["progress_percent"] == 0


def test_delete_photo_removes_file_and_blocks_serving(client, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_photo(client, project_id)
    photo = db.get_photos_for_project(project_id)[0]
    stored_file = tmp_path / photo["stored_path"]
    assert stored_file.is_file()

    assert client.delete(f"/api/photos/{photo['id']}").status_code == 200
    assert not stored_file.is_file()
    assert db.get_photos_for_project(project_id) == []
    assert client.get(f"/api/photos/{photo['id']}").status_code == 404


def test_delete_document_removes_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(projects_api, "UPLOAD_DIR", tmp_path)
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    upload_document(client, project_id)
    doc = db.get_documents_for_project(project_id)[0]
    stored_file = tmp_path / doc["stored_path"]
    assert stored_file.is_file()

    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert not stored_file.is_file()
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


def test_delete_ownership_strict_and_hidden(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    log_expense(client, project_id, amount="50")
    expense_id = db.get_expenses_for_project(project_id)[0]["id"]

    client.post("/api/logout")
    assert client.delete(f"/api/expenses/{expense_id}").status_code == 401

    other = dict(REGISTER_PAYLOAD, email="stranger7@example.com")
    client.post("/api/register", json=other)
    client.post("/api/login",
                json={"email": other["email"],
                      "password": other["password"]})
    # 404 for a stranger: existence is never confirmed.
    assert client.delete(f"/api/expenses/{expense_id}").status_code == 404
    assert client.put(f"/api/expenses/{expense_id}", json={
        "title": "Tampered", "amount_ghs": "1", "category": "other"
    }).status_code == 404
    assert client.delete("/api/updates/999").status_code == 404
    assert db.get_expenses_for_project(project_id)[0]["amount_ghs"] == 50


def test_detail_page_shows_delete_controls(client):
    make_logged_in(client)
    project_id = create_project(client).json()["project"]["id"]
    log_expense(client, project_id, amount="50")

    page = client.get(f"/projects/{project_id}").text
    assert page.count('class="row-x"') == 1
    assert 'data-kind="expenses"' in page
    # The two-step confirm lives in the page script, not a browser popup.
    assert "first click arms the button" in page
