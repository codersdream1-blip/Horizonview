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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import auth
import db

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


PAGES = {
    "index.html",
    "login.html",
    "register.html",
    "dashboard.html",
    "packages.html",
    "start-project.html",
}


@app.get("/{page_name}")
def page(page_name: str):
    if page_name not in PAGES:
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(BASE_DIR / page_name, media_type="text/html")


# --- Stylesheets and scripts ------------------------------------------

app.mount("/css", StaticFiles(directory=BASE_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=BASE_DIR / "js"), name="js")
