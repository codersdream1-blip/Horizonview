"""
HorizonView server.

Serves the website pages (through a whitelist) and the account API:
register / login / logout with hashed passwords in SQLite.

Run from the project folder:
    .\\venv\\Scripts\\python.exe -m uvicorn main:app --reload --port 8300
Then open http://127.0.0.1:8300 in your browser.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import auth
import db
import projects

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    # Development fallback: a fresh random key on every restart.
    # For a stable key, copy .env.example to .env and set your own.
    # Never share the real SESSION_SECRET with anyone.
    SESSION_SECRET = secrets.token_hex(32)
    print(
        "WARNING: SESSION_SECRET is not set. Sessions will reset on "
        "every server restart. Copy .env.example to .env and set one."
    )

app = FastAPI(
    title="HorizonView",
    description="Construction project management platform for Ghana.",
)

# Session middleware signs the browser cookie cryptographically.
# A visitor cannot forge it, so the user_id inside is trusted.
# max_age in seconds: one week.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=7 * 24 * 3600,
)

db.init_db()

app.include_router(auth.router)
app.include_router(projects.router)

templates = Jinja2Templates(directory=BASE_DIR / "templates")


# --- Health check -----------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "HorizonView"}


# --- Page whitelist ---------------------------------------------------
# Explicit list of HTML pages visitors may request. Private files
# such as main.py can never be downloaded. Do not replace this with
# a catch-all directory mount.


@app.get("/")
def home():
    return FileResponse(BASE_DIR / "index.html", media_type="text/html")


# /dashboard is user-specific: it must never be served without a
# logged-in session, so it is rendered from a template instead of
# the static-page whitelist.


@app.get("/logout")
def logout_page(request: Request):
    """Full-page logout used by the dashboard's Log Out link."""
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    user = auth.get_current_user(request)
    if not user:
        return RedirectResponse("/login.html", status_code=303)

    name_parts = [p for p in user["full_name"].split() if p]
    initials = "".join(p[0].upper() for p in name_parts[:2]) or "HV"

    user_projects = [
        projects.display_fields(p)
        for p in db.get_projects_for_user(user["id"])
    ]

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "full_name": user["full_name"],
            "email": user["email"],
            "initials": initials,
            "projects": user_projects,
        },
    )


# The project creation page. Like /dashboard, it is personalised and
# must never be viewable without a logged-in session.
# NOTE: this route is registered BEFORE "/projects/{project_id}",
# otherwise FastAPI would try to read the word "new" as an integer.


@app.get("/projects/new")
def new_project_page(request: Request):
    user = auth.get_current_user(request)
    if not user:
        return RedirectResponse("/login.html", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="project_new.html",
        context={
            "full_name": user["full_name"],
        },
    )


@app.get("/projects/{project_id}")
def project_detail(project_id: int, request: Request):
    user = auth.get_current_user(request)
    if not user:
        return RedirectResponse("/login.html", status_code=303)

    project = db.get_project_by_id(project_id)

    # 404 for missing pages AND other people's projects: the URL never
    # confirms that someone else's project even exists.
    if not project or project["owner_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    shown = projects.display_fields(project)
    shown["created_display"] = shown["created_at"][:10]
    shown["id"] = project_id

    updates = [
        projects.display_update(u, owner_id=user["id"])
        for u in db.get_updates_for_project(project_id)
    ]

    photos = [
        projects.display_photo(p)
        for p in db.get_photos_for_project(project_id)
    ]

    return templates.TemplateResponse(
        request=request,
        name="project_detail.html",
        context={
            "project": shown,
            "updates": updates,
            "photos": photos,
            "stages": projects.STAGES,
        },
    )


PAGES = {
    "index.html",
    "login.html",
    "register.html",
    "packages.html",
    "start-project.html",
}


@app.get("/{page_name}")
def page(page_name: str):
    if page_name not in PAGES:
        if page_name == "dashboard.html":
            # Friendly redirect for old bookmarks.
            return RedirectResponse("/dashboard", status_code=303)
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(BASE_DIR / page_name, media_type="text/html")


# --- Stylesheets and scripts ------------------------------------------

app.mount("/css", StaticFiles(directory=BASE_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=BASE_DIR / "js"), name="js")
