"""
HorizonView server.

Phase 3, step 1: a minimal FastAPI server that serves the existing
HorizonView pages with a whitelist. No accounts or database yet --
those come in the next task.

Run from the project folder:
    .\\venv\\Scripts\\python.exe -m uvicorn main:app --reload
Then open http://127.0.0.1:8000 in your browser.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="HorizonView",
    description="Construction project management platform for Ghana.",
)

# --- Health check -----------------------------------------------------
# Useful for monitoring and for verifying the server runs correctly.


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "HorizonView"}


# --- Page whitelist ---------------------------------------------------
# These are the HTML pages visitors may request. Serving a whitelist
# (instead of a whole directory) means private files such as main.py
# cannot be downloaded by anyone. This is a security boundary served
# by our own code; do not replace it with a catch-all directory mount.


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
# Only the public asset folders are exposed, not the project root.

app.mount("/css", StaticFiles(directory=BASE_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=BASE_DIR / "js"), name="js")
