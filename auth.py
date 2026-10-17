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
import time
import threading

import bcrypt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db

router = APIRouter(prefix="/api", tags=["auth"])

# ---------- Attempt rate limiting (brute-force protection) ----------
#
# Fixed windows held in memory. Adequate for this stage; note for the
# future: multi-process deployments need a shared store (e.g. Redis).

LOGIN_WINDOW_SECONDS = 15 * 60   # failures counted per 15 minutes
MAX_LOGIN_FAILURES = 5           # per (IP, email) pair; at the sixth,
                                 # login is refused even with correct
                                 # credentials until the window clears
REGISTER_WINDOW_SECONDS = 60 * 60
MAX_REGISTRATIONS_PER_WINDOW = 10  # per IP, deter bulk sign-up spam

_login_failures: dict[str, list[float]] = {}
_register_attempts: dict[str, list[float]] = {}
_limiter_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _prune_in_window(window: list[float], now: float, limit_seconds: int) -> list[float]:
    return [ts for ts in window if now - ts <= limit_seconds]


def login_is_blocked(ip: str, email: str) -> bool:
    key = f"{ip}|{email}"
    with _limiter_lock:
        _login_failures[key] = _prune_in_window(
            _login_failures.get(key, []), time.monotonic(),
            LOGIN_WINDOW_SECONDS,
        )
        return len(_login_failures[key]) >= MAX_LOGIN_FAILURES


def record_login_failure(ip: str, email: str) -> None:
    key = f"{ip}|{email}"
    with _limiter_lock:
        _login_failures.setdefault(key, []).append(time.monotonic())


def clear_login_failures(ip: str, email: str) -> None:
    _login_failures.pop(f"{ip}|{email}", None)


def register_is_blocked(ip: str) -> bool:
    with _limiter_lock:
        _register_attempts[ip] = _prune_in_window(
            _register_attempts.get(ip, []), time.monotonic(),
            REGISTER_WINDOW_SECONDS,
        )
        return len(_register_attempts[ip]) >= MAX_REGISTRATIONS_PER_WINDOW


def record_registration(ip: str) -> None:
    with _limiter_lock:
        _register_attempts.setdefault(ip, []).append(time.monotonic())


def reset_rate_limits() -> None:
    """Reset the in-memory windows (used by the test suite)."""
    _login_failures.clear()
    _register_attempts.clear()

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[\d\s()+-]{5,30}$")

# Used only to equalize response time on unknown emails.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_hex(16).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def get_current_user(request: Request) -> dict | None:
    """Return the logged-in user's DB record, or None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get_user_by_id(user_id)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ---------- CSRF (cross-site request forgery) defence ----------
#
# How it works:
# 1. When you log in, the server creates a random token and stores it
#    inside your signed session, AND mirrors it as a readable
#    csrf_token cookie (the browser must be able to read the mirror
#    for JavaScript to echo it back).
# 2. Every state-changing fetch the page JavaScript sends carries the
#    token in the X-CSRF-Token header.
# 3. Mutating /api routes compare the header with the session copy.
#    A request forged from another website (which cannot read your
#    cookies) fails that comparison.
#
# Only logged-in mutations are guarded: /api/login and /api/register
# run without a session at all and are already covered by modern
# browsers refusing to attach cookies to cross-site POSTs
# (the session cookie is SameSite=Lax).

CSRF_COOKIE_NAME = "csrf_token"


def create_csrf_token(request: Request) -> str:
    token = secrets.token_hex(32)
    request.session["csrf_token"] = token
    return token


def csrf_valid(request: Request) -> bool:
    session_token = request.session.get("csrf_token")
    given = request.headers.get("x-csrf-token", "")
    if not session_token or not given:
        return False
    return secrets.compare_digest(session_token, given)


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

    ip = _client_ip(request)
    if register_is_blocked(ip):
        return JSONResponse(
            {
                "ok": False,
                "message": "Too many accounts created from this network "
                           "recently. Please try again later.",
            },
            status_code=429,
        )

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
    record_registration(ip)

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

    ip = _client_ip(request)

    # Brute-force guard: checked BEFORE credential work, so an attacker
    # cannot even burn hashing cycles (and a blocked correct password
    # still counts — the attacker cannot tell).
    if login_is_blocked(ip, email):
        return JSONResponse(
            {
                "ok": False,
                "message": "Too many unsuccessful attempts. Please try "
                           "again in about 15 minutes.",
            },
            status_code=429,
        )

    user = db.get_user_by_email(email)

    if user is None:
        # Burn the same hashing time as a real check so response
        # timing does not reveal that this email is unknown.
        verify_password(password, _DUMMY_HASH)
        record_login_failure(ip, email)
        return JSONResponse(
            {"ok": False, "message": "Email or password is incorrect."},
            status_code=401,
        )

    if not verify_password(password, user["password_hash"]):
        record_login_failure(ip, email)
        return JSONResponse(
            {"ok": False, "message": "Email or password is incorrect."},
            status_code=401,
        )

    # Success resets the failure window for this (IP, email) pair.
    clear_login_failures(ip, email)

    # Signed session cookie (set up in main.py).
    request.session["user_id"] = user["id"]

    # Fresh CSRF token per login, mirrored as a readable cookie so the
    # page JavaScript can echo it back in the X-CSRF-Token header.
    csrf_token = create_csrf_token(request)
    response = JSONResponse(
        {"ok": True, "message": "Logged in.", "full_name": user["full_name"]}
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        samesite="lax",
        path="/",
    )
    return response


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
