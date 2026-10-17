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

import hashlib
import re
import secrets
import time
import threading
from datetime import datetime, timezone

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

# Password-reset windows: modest, because the endpoints never reveal
# whether an email exists — they are throttled to stop someone turning
# them into a spam channel or a (useless) probe.
FORGOT_WINDOW_SECONDS = 15 * 60
MAX_FORGOT_REQUESTS_PER_WINDOW = 5   # per IP
RESET_WINDOW_SECONDS = 60 * 60
MAX_RESET_ATTEMPTS_PER_WINDOW = 10   # failed token guesses per IP

_login_failures: dict[str, list[float]] = {}
_register_attempts: dict[str, list[float]] = {}
_forgot_requests: dict[str, list[float]] = {}
_reset_attempts: dict[str, list[float]] = {}
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


def forgot_is_blocked(ip: str) -> bool:
    with _limiter_lock:
        _forgot_requests[ip] = _prune_in_window(
            _forgot_requests.get(ip, []), time.monotonic(),
            FORGOT_WINDOW_SECONDS,
        )
        return len(_forgot_requests[ip]) >= MAX_FORGOT_REQUESTS_PER_WINDOW


def record_forgot_request(ip: str) -> None:
    _forgot_requests.setdefault(ip, []).append(time.monotonic())


def reset_is_blocked(ip: str) -> bool:
    with _limiter_lock:
        _reset_attempts[ip] = _prune_in_window(
            _reset_attempts.get(ip, []), time.monotonic(),
            RESET_WINDOW_SECONDS,
        )
        return len(_reset_attempts[ip]) >= MAX_RESET_ATTEMPTS_PER_WINDOW


def record_failed_reset(ip: str) -> None:
    _reset_attempts.setdefault(ip, []).append(time.monotonic())


def record_registration(ip: str) -> None:
    with _limiter_lock:
        _register_attempts.setdefault(ip, []).append(time.monotonic())


def reset_rate_limits() -> None:
    """Reset the in-memory windows (used by the test suite)."""
    _login_failures.clear()
    _register_attempts.clear()
    _forgot_requests.clear()
    _reset_attempts.clear()

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[\d\s()+-]{5,30}$")

# Used only to equalize response time on unknown emails.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_hex(16).encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def get_current_user(request: Request) -> dict | None:
    """Return the logged-in user's DB record, or None.

    The session cookie carries a session id (sid) that must still be
    registered in the database. A row that was revoked (logout) or
    deleted (password reset) makes this session dead even if the
    signed cookie itself is intact.
    """
    sid = request.session.get("sid")
    if not sid or not db.session_is_active(sid):
        return None
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

    # Housekeeping while we are here: registry rows whose cookie has
    # long expired are swept so the table cannot grow forever.
    db.prune_expired_sessions()

    # Signed session cookie (set up in main.py) + a session-registry
    # row so this session can be revoked later (logout, password
    # reset).
    sid = secrets.token_hex(32)
    request.session["user_id"] = user["id"]
    request.session["sid"] = sid
    db.create_session(sid, user["id"])

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
    # Revoke the registry row first: after this, even a copied cookie
    # no longer authenticates.
    sid = request.session.get("sid")
    if sid:
        db.revoke_session(sid)
    request.session.clear()
    return {"ok": True, "message": "Logged out."}


# ---------- Password reset ----------
#
# HorizonView has no email service in this stage of the project, so
# the flow is intentionally honest about that:
#
#   1. The user fills in "Forgot your password?" → this endpoint only
#      RECORDS a request (and replies the same thing whether or not
#      the email exists, so nobody can use it to probe accounts).
#   2. The HorizonView administrator verifies the person's identity
#      out of band (e.g. a phone call) and mints a single-use,
#      60-minute reset link with create_reset_link.py on the server.
#   3. The user's browser POSTs the token here to set a new password.
#
# Tokens are stored as SHA-256 hashes and can be consumed exactly
# once. No HTTP endpoint ever reveals a token.

RESET_TOKEN_MINUTES = 60


def _generic_forgot_reply() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "message": "Thank you. If this email has a HorizonView "
                       "account, your request has been recorded. Our "
                       "administrator will verify your identity and "
                       "sort out a reset link with you.",
        }
    )


@router.post("/forgot-password")
async def forgot_password(request: Request):
    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")
    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    email = str(data.get("email", "")).strip().lower()
    if not email or len(email) > 254 or not EMAIL_PATTERN.match(email):
        return bad_request("Please enter a valid email address.")

    ip = _client_ip(request)
    if forgot_is_blocked(ip):
        return JSONResponse(
            {
                "ok": False,
                "message": "Too many requests from this network "
                           "recently. Please try again later.",
            },
            status_code=429,
        )
    record_forgot_request(ip)

    user = db.get_user_by_email(email)
    if user is None:
        # Same reply as for a real account: the response must never
        # confirm whether a given email is registered.
        return _generic_forgot_reply()

    # Genuinely record the request so the administrator can act on it
    # (and so the reply above is the truth). The token for the actual
    # reset link is minted later, on the server, only after the
    # identity is verified out of band.
    db.record_reset_request(email, ip)
    return _generic_forgot_reply()


@router.post("/reset-password")
async def reset_password(request: Request):
    try:
        data = await request.json()
    except Exception:
        return bad_request("Send the request as JSON data.")
    if not isinstance(data, dict):
        return bad_request("Send the request as JSON data.")

    token = str(data.get("token", ""))
    new_password = str(data.get("new_password", ""))

    if len(new_password) < 8 or len(new_password) > 100:
        return bad_request(
            "Your new password must be between 8 and 100 characters."
        )
    if not token:
        return bad_request("This page needs a valid reset link.")

    ip = _client_ip(request)
    if reset_is_blocked(ip):
        return JSONResponse(
            {
                "ok": False,
                "message": "Too many attempts from this network "
                           "recently. Please try again later.",
            },
            status_code=429,
        )

    # Tokens are stored hashed, so failed lookups cost nothing and a
    # leaked database cannot yield working links.
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = db.get_password_reset_by_hash(token_hash)

    now = datetime.now(timezone.utc)
    expired = record and (
        datetime.fromisoformat(record["expires_at"]) <= now
        or record["used_at"] is not None
    )
    if not record or expired:
        record_failed_reset(ip)
        return JSONResponse(
            {
                "ok": False,
                "message": "This reset link is invalid or has expired. "
                           "Ask the HorizonView administrator for a "
                           "fresh one.",
            },
            status_code=400,
        )

    db.set_user_password(record["user_id"], hash_password(new_password))
    db.mark_password_reset_used(record["id"])

    # Security step the cookie alone could never do: sign EVERY device
    # out of this account. Anyone still holding an old session (the
    # person who forgot the password, a forgotten tab, or somebody
    # with unauthorized access) is logged out the moment the password
    # changes.
    db.revoke_all_sessions(record["user_id"])

    return JSONResponse(
        {
            "ok": True,
            "message": "Password changed. All existing logins on every "
                       "device were signed out, and you can now log in "
                       "with your new password.",
        }
    )


@router.get("/me")
async def me(request: Request):
    # Revocation-aware: goes through the session-registry check like
    # every other authenticated read.
    user = get_current_user(request)
    if not user:
        return {"logged_in": False}

    return {
        "logged_in": True,
        "full_name": user["full_name"],
        "email": user["email"],
    }
