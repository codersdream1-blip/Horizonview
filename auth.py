"""
Account creation and login for HorizonView.

Security notes (read these, they matter):

- Passwords are hashed with bcrypt. The plain password is never
  stored, never logged, and cannot be recovered from the hash.
- Register responses say "already registered" (that is normal and
  expected), but login failures always return one generic message so
  nobody can discover which email addresses have accounts.
- The dummy hash below keeps login response time similar whether or
  not the email exists, removing a timing side-channel.
- Validation is repeated here on the server. Browser validation only
  improves user experience; it is never a security boundary.
"""

import re
import secrets

import bcrypt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db

router = APIRouter(prefix="/api", tags=["auth"])

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[\d\s()+-]{5,30}$")

# Used only to equalize response time on unknown emails.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_hex(16).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def bad_request(message: str, errors: dict | None = None) -> JSONResponse:
    payload: dict = {"ok": False, "message": message}
    if errors:
        payload["errors"] = errors
    return JSONResponse(payload, status_code=400)


def validate_register(data: dict) -> dict:
    errors: dict = {}

    full_name = str(data.get("full_name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    phone = str(data.get("phone", "")).strip()
    password = str(data.get("password", ""))  # never stripped: spaces can be intentional

    if not 2 <= len(full_name) <= 120:
        errors["full_name"] = "Your full name must be between 2 and 120 characters."

    if not email or len(email) > 254 or not EMAIL_PATTERN.match(email):
        errors["email"] = "Please enter a valid email address."

    if not PHONE_PATTERN.match(phone):
        errors["phone"] = "Please enter a valid phone number (5-30 digits, spaces or +)."

    if not 8 <= len(password) <= 100:
        errors["password"] = "Your password must be between 8 and 100 characters."

    return errors


@router.post("/register")
async def register(request: Request):
    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    errors = validate_register(data)
    if errors:
        return bad_request("Please fix the highlighted fields.", errors)

    email = str(data["email"]).strip().lower()

    if db.get_user_by_email(email):
        return bad_request(
            "This email address is already registered. Try logging in instead."
        )

    db.create_user(
        full_name=str(data["full_name"]).strip(),
        email=email,
        phone=str(data["phone"]).strip(),
        password_hash=hash_password(str(data["password"])),
    )

    return JSONResponse(
        {"ok": True, "message": "Account created. You can log in now."},
        status_code=201,
    )


@router.post("/login")
async def login(request: Request):
    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")

    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not email or not password:
        return bad_request("Enter your email address and password.")

    user = db.get_user_by_email(email)

    if user is None:
        # Burn the same hashing time as a real check so response
        # timing does not reveal that this email is unknown.
        verify_password(password, _DUMMY_HASH)
        return JSONResponse(
            {"ok": False, "message": "Email or password is incorrect."},
            status_code=401,
        )

    if not verify_password(password, user["password_hash"]):
        return JSONResponse(
            {"ok": False, "message": "Email or password is incorrect."},
            status_code=401,
        )

    # Signed session cookie (set up in main.py).
    request.session["user_id"] = user["id"]

    return JSONResponse(
        {"ok": True, "message": "Logged in.", "full_name": user["full_name"]}
    )


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True, "message": "Logged out."}


@router.get("/me")
async def me(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return {"logged_in": False}

    user = db.get_user_by_id(user_id)
    if not user:
        return {"logged_in": False}

    return {
        "logged_in": True,
        "full_name": user["full_name"],
        "email": user["email"],
    }
