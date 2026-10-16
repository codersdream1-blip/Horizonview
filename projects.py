"""
Project records API for HorizonView.

Every project belongs to exactly one owner. Routes check the session
before touching the database, so a user can only ever see and create
their own records.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from auth import get_current_user
import db

router = APIRouter(prefix="/api", tags=["projects"])

PROJECT_TYPES = {
    "construction": "New Construction",
    "renovation": "Renovation",
    "property-development": "Property Development",
    "site-monitoring": "Site Monitoring",
    "other": "Other",
}

PACKAGES = {"silver", "gold", "platinum"}

PAYMENT_ARRANGEMENTS = {
    "pay-all-at-once": "Pay all at once",
    "stage-by-stage": "Pay stage by stage",
    "save-toward-target": "Save toward a target",
    "discuss-later": "To be discussed",
}

STAGES = {
    "site-clearing": "Site clearing",
    "foundation": "Foundation",
    "blockwork": "Blockwork",
    "roofing": "Roofing",
    "services": "Plumbing & Electrical",
    "rendering": "Rendering",
    "finishing": "Finishing",
    "handover": "Handover",
    "other": "Other stage",
}

# Sanity ceiling so a typo cannot store absurd money values.
MAX_BUDGET_GHS = 1_000_000_000


def bad_request(message: str, errors: dict | None = None) -> JSONResponse:
    payload: dict = {"ok": False, "message": message}
    if errors:
        payload["errors"] = errors
    return JSONResponse(payload, status_code=400)


def validate_project(data: dict) -> tuple[dict, dict]:
    """Return (errors, stored_values)."""
    errors: dict = {}

    name = str(data.get("name", "")).strip()
    project_type = str(data.get("project_type", "")).strip()
    location = str(data.get("location", "")).strip()
    description = str(data.get("description", "") or "").strip()

    if not 3 <= len(name) <= 200:
        errors["name"] = "Project name must be between 3 and 200 characters."

    if project_type not in PROJECT_TYPES:
        errors["project_type"] = "Choose a project type from the list."

    if not 3 <= len(location) <= 200:
        errors["location"] = "Project location must be between 3 and 200 characters."

    if len(description) > 5000:
        errors["description"] = "Description is too long (maximum 5000 characters)."

    budget_raw = str(data.get("budget_ghs", "") or "").strip()
    budget = None
    if budget_raw:
        try:
            budget = float(budget_raw)
        except ValueError:
            errors["budget_ghs"] = "Enter the budget as a number, for example 450000."
        else:
            if not 0 < budget <= MAX_BUDGET_GHS:
                errors["budget_ghs"] = "Budget must be greater than 0."

    package = str(data.get("package", "") or "").strip().lower()
    package = package if package in PACKAGES else None

    payment = str(data.get("payment_arrangement", "") or "").strip()
    if payment and payment not in PAYMENT_ARRANGEMENTS:
        errors["payment_arrangement"] = "Choose a payment arrangement from the list."

    stored = {
        "name": name,
        "project_type": project_type,
        "location": location,
        "description": description or None,
        "budget_ghs": budget,
        "package": package,
        "payment_arrangement": payment or None,
    }
    return errors, stored


def display_fields(project: dict) -> dict:
    """Human-readable additions used by templates and JSON output."""
    project = dict(project)
    project["type_display"] = PROJECT_TYPES.get(project["project_type"], project["project_type"])
    project["package_display"] = (project["package"] or "").capitalize() if project["package"] else "Not chosen"
    project["payment_display"] = PAYMENT_ARRANGEMENTS.get(
        project["payment_arrangement"], "To be discussed"
    )
    if project["budget_ghs"] is None:
        project["budget_display"] = "Not set yet"
    else:
        project["budget_display"] = f"GH₵{project['budget_ghs']:,.0f}"
    project["status_display"] = project["status"].capitalize()
    # progress_percent kept as-is (0 for now; updated by the team later)
    return project


def display_update(update: dict, owner_id: int) -> dict:
    """Template-ready version of a progress note."""
    update = dict(update)
    if update["stage"]:
        update["stage_display"] = STAGES.get(update["stage"], update["stage"])
    else:
        update["stage_display"] = None
    update["created_display"] = update["created_at"][:10]
    update["author_display"] = "You" if update["author_id"] == owner_id else "HorizonView team"
    return update


@router.post("/projects/{project_id}/updates")
async def add_project_update(project_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    project = db.get_project_by_id(project_id)
    if not project or project["owner_id"] != user["id"]:
        # Same stance as project detail: no existence confirmation.
        return JSONResponse(
            {"ok": False, "message": "Project not found."}, status_code=404
        )

    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    errors: dict = {}

    title = str(data.get("title", "")).strip()
    body = str(data.get("body", "")).strip()
    stage = str(data.get("stage", "") or "").strip().lower()
    stage = stage if stage in STAGES else None

    if not 3 <= len(title) <= 150:
        errors["title"] = "Title must be between 3 and 150 characters."

    if not 10 <= len(body) <= 4000:
        errors["body"] = "Describe the progress in at least 10 characters (max 4000)."

    progress_raw = str(data.get("progress_percent", "") or "").strip()
    try:
        progress = int(float(progress_raw))
    except ValueError:
        progress = -1
    if not 0 <= progress <= 100:
        errors["progress_percent"] = "Progress must be a number from 0 to 100."

    if errors:
        return bad_request("Please fix the highlighted fields.", errors)

    db.create_project_update(
        project_id=project_id,
        author_id=user["id"],
        title=title,
        body=body,
        stage=stage,
        progress_percent=progress,
    )

    # Overall progress only ever moves forward (max).
    new_progress = max(project["progress_percent"], progress)
    db.set_project_progress(project_id, new_progress)

    return JSONResponse(
        {
            "ok": True,
            "message": "Progress note recorded.",
            "project_progress": new_progress,
        },
        status_code=201,
    )


@router.get("/projects/{project_id}/updates")
async def get_project_updates(project_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    project = db.get_project_by_id(project_id)
    if not project or project["owner_id"] != user["id"]:
        return JSONResponse(
            {"ok": False, "message": "Project not found."}, status_code=404
        )

    updates = [
        display_update(u, owner_id=user["id"])
        for u in db.get_updates_for_project(project_id)
    ]
    return {"ok": True, "updates": updates}


@router.get("/projects")
async def list_projects(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    projects = [display_fields(p) for p in db.get_projects_for_user(user["id"])]
    return {"ok": True, "projects": projects}


@router.post("/projects")
async def create_project(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    errors, stored = validate_project(data)
    if errors:
        return bad_request("Please fix the highlighted fields.", errors)

    project_id = db.create_project(owner_id=user["id"], **stored)

    # Re-read from the database so the response reflects what is stored.
    project = display_fields(
        next(p for p in db.get_projects_for_user(user["id"]) if p["id"] == project_id)
    )

    return JSONResponse(
        {"ok": True, "message": "Project created.", "project": project},
        status_code=201,
    )
