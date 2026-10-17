"""
Project records API for HorizonView.

Every project belongs to exactly one owner. Routes check the session
before touching the database, so a user can only ever see and create
their own records.
"""

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from auth import get_current_user, csrf_valid
import db

router = APIRouter(prefix="/api", tags=["projects"])

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"

MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB

# Documents get a bigger allowance: contracts and drawings are heavy.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# Recognized by file signature (magic bytes), never by the browser's
# declared content type, which is trivially forged.
PHOTO_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)

EXPENSE_CATEGORIES = {
    "materials": "Materials",
    "labour": "Labour",
    "land-permits": "Land & Permits",
    "equipment": "Equipment Hire",
    "transport": "Transport",
    "professional": "Professional Fees",
    "other": "Other",
}

# Sanity ceiling so a typo cannot store absurd money values.
MAX_EXPENSE_GHS = 100_000_000


def _money(value: float) -> str:
    return f"GH₵{value:,.2f}"


# Public alias for use outside this module (dashboards, etc.).
format_money = _money


@router.delete("/photos/{photo_id}")
async def remove_photo(photo_id: int, request: Request):
    photo = db.get_photo_by_id(photo_id)
    if not photo:
        return JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )

    user, denial = owner_or_response(request, photo["project_id"])
    if denial:
        return denial

    denial = bad_csrf(request)
    if denial:
        return denial

    _remove_stored_file(photo["stored_path"])
    db.delete_photo(photo_id)

    return JSONResponse({"ok": True})


@router.delete("/documents/{document_id}")
async def remove_document(document_id: int, request: Request):
    document = db.get_document_by_id(document_id)
    if not document:
        return JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )

    user, denial = owner_or_response(request, document["project_id"])
    if denial:
        return denial

    denial = bad_csrf(request)
    if denial:
        return denial

    _remove_stored_file(document["stored_path"])
    db.delete_document(document_id)

    return JSONResponse({"ok": True})


def detect_document_extension(data: bytes) -> str | None:
    """PDFs and image tables, recognised by signature only."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    return detect_photo_extension(data)

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

    denial = bad_csrf(request)
    if denial:
        return denial

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


def detect_photo_extension(data: bytes) -> str | None:
    for signature, extension in PHOTO_SIGNATURES:
        if data.startswith(signature):
            return extension
    return None


def display_photo(photo: dict) -> dict:
    photo = dict(photo)
    photo["created_display"] = photo["created_at"][:10]
    photo["api_url"] = f"/api/photos/{photo['id']}"
    return photo


@router.post("/projects/{project_id}/photos")
async def upload_photo(
    project_id: int,
    request: Request,
    photo: UploadFile = File(...),
    caption: str = Form(""),
):
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

    denial = bad_csrf(request)
    if denial:
        return denial

    data = await photo.read()

    if not data:
        return JSONResponse(
            {"ok": False, "message": "The uploaded file is empty."}, status_code=400
        )

    if len(data) > MAX_PHOTO_BYTES:
        return JSONResponse(
            {
                "ok": False,
                "message": "Photo is too large. Maximum allowed size is 5 MB.",
            },
            status_code=400,
        )

    extension = detect_photo_extension(data)
    if not extension:
        return JSONResponse(
            {
                "ok": False,
                "message": "Only JPEG or PNG photos are accepted. The file "
                           "content did not look like either.",
            },
            status_code=400,
        )

    # Random filename: original names are user-controlled and must
    # never touch the filesystem directly.
    project_dir = UPLOAD_DIR / f"project_{project_id}"
    project_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    (project_dir / filename).write_bytes(data)

    clean_caption = str(caption).strip()[:200] or None

    db.create_photo(
        project_id=project_id,
        uploader_id=user["id"],
        original_filename=str(photo.filename or "unnamed")[:200],
        stored_path=f"project_{project_id}/{filename}",
        extension=extension,
        caption=clean_caption,
    )

    return JSONResponse(
        {"ok": True, "message": "Photo uploaded.", "caption": clean_caption},
        status_code=201,
    )


@router.get("/photos/{photo_id}")
async def serve_photo(photo_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    photo = db.get_photo_by_id(photo_id)
    if not photo:
        return JSONResponse(
            {"ok": False, "message": "Photo not found."}, status_code=404
        )

    project = db.get_project_by_id(photo["project_id"])
    if not project or project["owner_id"] != user["id"]:
        return JSONResponse(
            {"ok": False, "message": "Photo not found."}, status_code=404
        )

    file_path = UPLOAD_DIR / photo["stored_path"]
    if not file_path.is_file():
        return JSONResponse(
            {"ok": False, "message": "Photo not found."}, status_code=404
        )

    media_type = "image/png" if photo["extension"] == "png" else "image/jpeg"
    return FileResponse(file_path, media_type=media_type)


def display_document(document: dict) -> dict:
    document = dict(document)
    document["created_display"] = document["created_at"][:10]
    document["api_url"] = f"/api/documents/{document['id']}"
    label = {
        "pdf": "PDF",
        "png": "PNG image",
        "jpg": "JPEG image",
    }.get(document["extension"], document["extension"])
    document["type_label"] = label
    return document


@router.post("/projects/{project_id}/documents")
async def upload_document(
    project_id: int,
    request: Request,
    document: UploadFile = File(...),
    description: str = Form(""),
):
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

    denial = bad_csrf(request)
    if denial:
        return denial

    data = await document.read()

    if not data:
        return JSONResponse(
            {"ok": False, "message": "The uploaded file is empty."},
            status_code=400,
        )

    if len(data) > MAX_DOCUMENT_BYTES:
        return JSONResponse(
            {
                "ok": False,
                "message": "Document is too large. Maximum allowed size "
                           "is 10 MB.",
            },
            status_code=400,
        )

    extension = detect_document_extension(data)
    if not extension:
        return JSONResponse(
            {
                "ok": False,
                "message": "Only PDF, PNG or JPEG documents are accepted. "
                           "The file content did not look like any of them.",
            },
            status_code=400,
        )

    project_dir = UPLOAD_DIR / f"project_{project_id}"
    project_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{extension}"
    (project_dir / filename).write_bytes(data)

    clean_description = str(description).strip()[:200] or None

    document_id = db.create_document(
        project_id=project_id,
        uploader_id=user["id"],
        original_filename=str(document.filename or "unnamed")[:200],
        stored_path=f"project_{project_id}/{filename}",
        extension=extension,
        description=clean_description,
    )

    return JSONResponse(
        {
            "ok": True,
            "message": "Document uploaded.",
            "document_id": document_id,
        },
        status_code=201,
    )


@router.get("/documents/{document_id}")
async def serve_document(document_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )

    document = db.get_document_by_id(document_id)
    if not document:
        return JSONResponse(
            {"ok": False, "message": "Document not found."}, status_code=404
        )

    project = db.get_project_by_id(document["project_id"])
    if not project or project["owner_id"] != user["id"]:
        return JSONResponse(
            {"ok": False, "message": "Document not found."}, status_code=404
        )

    file_path = UPLOAD_DIR / document["stored_path"]
    if not file_path.is_file():
        return JSONResponse(
            {"ok": False, "message": "Document not found."}, status_code=404
        )

    media_type = {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
    }.get(document["extension"], "application/octet-stream")

    # Served inline so PDFs open in the browser viewer, with the
    # safe stored name — never the user-supplied name (header injection).
    return FileResponse(file_path, media_type=media_type)


def bad_csrf(request: Request) -> JSONResponse | None:
    """Return a 403 for logged-in mutations without the CSRF header."""
    if csrf_valid(request):
        return None
    return JSONResponse(
        {
            "ok": False,
            "message": "Security check failed. Refresh the page and try again.",
        },
        status_code=403,
    )


def owner_or_response(request: Request, project_id: int):
    """Shared identity-first access check for single-record routes.

    Returns (user, None) when allowed, or (None, response) with
    401 for anonymous users and 404 for anyone else — so a URL never
    confirms a record exists for strangers.
    """
    user = get_current_user(request)
    if not user:
        return None, JSONResponse(
            {"ok": False, "message": "Log in first."}, status_code=401
        )
    project = db.get_project_by_id(project_id)
    if not project or project["owner_id"] != user["id"]:
        return None, JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )
    return user, None


def _remove_stored_file(stored_path: str) -> None:
    """Delete a stored upload, but only if it sits inside uploads/."""
    base = UPLOAD_DIR.resolve()
    target = (UPLOAD_DIR / stored_path).resolve()
    if target != base and base in target.parents and target.is_file():
        target.unlink()


def parse_expense(data: dict):
    """Shared validation for creating and editing an expense.

    Returns (fields, errors); exactly one of the two is empty.
    """
    errors: dict = {}

    title = str(data.get("title", "")).strip()
    if not 3 <= len(title) <= 150:
        errors["title"] = "Describe the expense in 3 to 150 characters."

    # Accept what people actually type: "1,500" or "1500.50".
    amount_raw = str(data.get("amount_ghs", "") or "").strip().replace(",", "")
    try:
        amount = round(float(amount_raw), 2)
    except ValueError:
        amount = -1
    if not 0 < amount <= MAX_EXPENSE_GHS:
        errors["amount_ghs"] = "Enter the amount in Ghana cedis, a number above 0."

    category = str(data.get("category", "") or "").strip().lower()
    if category not in EXPENSE_CATEGORIES:
        errors["category"] = "Choose one of the listed expense categories."

    spent_on = str(data.get("spent_on", "") or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(spent_on)
    except ValueError:
        errors["spent_on"] = "Use the date picker (or type YYYY-MM-DD)."

    if errors:
        return None, errors
    return {
        "title": title,
        "amount_ghs": amount,
        "category": category,
        "spent_on": spent_on,
    }, None


@router.put("/expenses/{expense_id}")
async def edit_expense(expense_id: int, request: Request):
    expense = db.get_expense_by_id(expense_id)
    if not expense:
        return JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )

    user, denial = owner_or_response(request, expense["project_id"])
    if denial:
        return denial

    denial = bad_csrf(request)
    if denial:
        return denial

    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")
    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    fields, errors = parse_expense(data)
    if errors:
        return bad_request("Please fix the highlighted fields.", errors)

    project_id = expense["project_id"]  # unchanged; never editable
    db.delete_expense(expense_id)
    db.create_expense(
        project_id=project_id,
        entered_by=user["id"],
        **fields,
    )

    project = db.get_project_by_id(project_id)
    summary = expense_summary(project, db.get_expenses_for_project(project_id))
    return JSONResponse({"ok": True, **summary})


@router.delete("/expenses/{expense_id}")
async def remove_expense(expense_id: int, request: Request):
    expense = db.get_expense_by_id(expense_id)
    if not expense:
        return JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )

    user, denial = owner_or_response(request, expense["project_id"])
    if denial:
        return denial

    denial = bad_csrf(request)
    if denial:
        return denial

    project_id = expense["project_id"]
    db.delete_expense(expense_id)

    project = db.get_project_by_id(project_id)
    summary = expense_summary(project, db.get_expenses_for_project(project_id))
    return JSONResponse({"ok": True, **summary})


@router.delete("/updates/{update_id}")
async def remove_update(update_id: int, request: Request):
    note = db.get_update_by_id(update_id)
    if not note:
        return JSONResponse(
            {"ok": False, "message": "Record not found."}, status_code=404
        )

    project_id = note["project_id"]
    user, denial = owner_or_response(request, project_id)
    if denial:
        return denial

    denial = bad_csrf(request)
    if denial:
        return denial

    db.delete_update(update_id)

    # Overall progress is the max of the remaining notes (was a max
    # while adding; deleting the top note must not leave a ghost bar).
    remaining = db.get_updates_for_project(project_id)
    recalculated = max(
        (u["progress_percent"] for u in remaining), default=0
    )
    db.set_project_progress(project_id, recalculated)

    return JSONResponse({"ok": True, "project_progress": recalculated})


def display_expense(expense: dict) -> dict:
    expense = dict(expense)
    expense["amount_display"] = _money(expense["amount_ghs"])
    expense["category_display"] = EXPENSE_CATEGORIES.get(
        expense["category"], expense["category"]
    )
    return expense


def expense_summary(project: dict, expenses: list[dict]) -> dict:
    """Running totals for the budget panel, computed from the ledger."""
    total_spent = round(sum(e["amount_ghs"] for e in expenses), 2)
    summary = {
        "total_spent": total_spent,
        "total_spent_display": _money(total_spent),
    }
    if project["budget_ghs"] is not None:
        remaining = round(project["budget_ghs"] - total_spent, 2)
        summary["remaining"] = remaining
        summary["remaining_display"] = _money(remaining)
        summary["budget_display"] = f"GH₵{project['budget_ghs']:,.0f}"
        summary["spent_percent"] = round(
            total_spent / project["budget_ghs"] * 100
        )
    return summary


@router.post("/projects/{project_id}/expenses")
async def add_expense(project_id: int, request: Request):
    user, denial = owner_or_response(request, project_id)
    if denial:
        return denial
    project = db.get_project_by_id(project_id)

    denial = bad_csrf(request)
    if denial:
        return denial

    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    fields, errors = parse_expense(data)
    if errors:
        return bad_request("Please fix the highlighted fields.", errors)

    db.create_expense(project_id=project_id, entered_by=user["id"], **fields)

    summary = expense_summary(project, db.get_expenses_for_project(project_id))
    return JSONResponse(
        {
            "ok": True,
            "message": "Expense recorded.",
            "total_spent_display": summary["total_spent_display"],
            **({"remaining_display": summary["remaining_display"]}
               if "remaining_display" in summary else {}),
        },
        status_code=201,
    )


def display_message(message: dict, owner_id: int) -> dict:
    message = dict(message)
    message["time_display"] = message["created_at"][:16].replace("T", " ")
    message["sender_display"] = (
        "You" if message["sender_id"] == owner_id
        else "HorizonView team"
    )
    return message


@router.post("/projects/{project_id}/messages")
async def post_message(project_id: int, request: Request):
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

    denial = bad_csrf(request)
    if denial:
        return denial

    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    body = str(data.get("body", "")).strip()
    if not 1 <= len(body) <= 2000:
        return bad_request(
            "Write your message (1 to 2000 characters).",
            {"body": "Write your message (1 to 2000 characters)."},
        )

    db.create_message(
        project_id=project_id,
        sender_id=user["id"],
        sender_role="owner",
        body=body,
    )

    return JSONResponse(
        {"ok": True, "message": "Message sent."}, status_code=201
    )


@router.get("/projects/{project_id}/messages")
async def get_messages(project_id: int, request: Request):
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

    messages = [
        display_message(m, owner_id=user["id"])
        for m in db.get_messages_for_project(project_id)
    ]
    return {"ok": True, "messages": messages}


@router.get("/projects/{project_id}/expenses")
async def get_expenses(project_id: int, request: Request):
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

    expenses = [
        display_expense(e) for e in db.get_expenses_for_project(project_id)
    ]
    return {
        "ok": True,
        "expenses": expenses,
        "summary": expense_summary(project, expenses),
    }


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

    denial = bad_csrf(request)
    if denial:
        return denial

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
