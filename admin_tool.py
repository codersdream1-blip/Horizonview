"""
HorizonView local administrator toolkit.

SECURITY STANCE (decided, not accidental):

There is deliberately NO web-based admin panel. Server operations run
only here, on the machine that holds the database. That keeps the
public attack surface honest: a customer account is never one-click
away from administrative power, and no browser endpoint can mint
reset links, flip admin flags or kill sessions.

What the toolkit does today:

    list-users                  account overview with live counts
    list-resets                 pending "forgot password" requests
    reset-link <email>          mint a single-use 60-minute reset link
    sessions <email>            show a user's registered devices
    revoke-sessions <email>     sign that user out of EVERY device
    promote-admin <email>       grant the administrator identity
    demote-admin <email>        remove the administrator identity

Run it from the project folder:

    .\\venv\\Scripts\\python.exe admin_tool.py list-users

Out-of-band identity verification rule (phone call to the registered
number) applies before handing over any reset link.
"""

import argparse
import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone

import db

BASE_URL = "http://127.0.0.1:8300"
RESET_TOKEN_MINUTES = 60


# ---------- individual commands (testable, no I/O surprises) ----------

def mint_reset_token(email: str, base_url: str = BASE_URL) -> tuple[str, str] | None:
    """Create the single-use token + printable link for one account.
    Returns (token, link) or None when the email has no account."""
    user = db.get_user_by_email(email.strip().lower())
    if user is None:
        return None

    token = secrets.token_hex(32)  # 256 bits; DB stores only the hash
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_MINUTES)
    ).isoformat()

    db.create_password_reset(
        user_id=user["id"],
        token_hash=token_hash,
        expires_at=expires_at,
        requested_ip="admin-tool",
    )
    link = f"{base_url.rstrip('/')}/reset-password.html?token={token}"
    return token, link


def print_users() -> None:
    users = db.list_users()
    if not users:
        print("No accounts yet.")
        return
    print(f"{len(users)} account(s):\n")
    for u in users:
        tag = "ADMIN" if u["is_admin"] else "customer"
        print(f"  #{u['id']} {u['full_name']} <{u['email']}> [{tag}]")
        print(
            f"      projects: {u['project_count']}   "
            f"active sessions: {u['active_sessions']}   "
            f"joined: {u['created_at'][:10]}"
        )


def print_resets() -> None:
    requests = db.list_reset_requests()
    if not requests:
        print("No reset requests recorded yet.")
        return
    print(f"{len(requests)} most recent reset request(s):\n")
    for r in requests:
        print(f"  {r['created_at'][:19]}  {r['email']}  (IP {r['requested_ip']})")
    print("\nNext step: verify identity out of band, then:")
    print("  .\\venv\\Scripts\\python.exe admin_tool.py reset-link <email>")


def print_sessions(email: str) -> int:
    user = db.get_user_by_email(email.strip().lower())
    if user is None:
        print(f"No account uses {email}. Nothing shown.")
        return 1
    rows = db.sessions_for_user(user["id"])
    print(f"{user['full_name']} <{user['email']}> — "
          f"{len(rows)} registered device session(s)")
    for r in rows:
        print(f"  session …{r['sid'][-8:]}  started {r['created_at'][:19]}")
    return 0


def do_revoke_sessions(email: str) -> int:
    user = db.get_user_by_email(email.strip().lower())
    if user is None:
        print(f"No account uses {email}. Nothing revoked.")
        return 1
    db.revoke_all_sessions(user["id"])
    print(f"All device sessions for {user['email']} were signed out.")
    print("(Their password was NOT changed.)")
    return 0


def do_set_admin(email: str, flag: bool) -> int:
    user = db.get_user_by_email(email.strip().lower())
    if user is None:
        print(f"No account uses {email}. Nothing changed.")
        return 1
    db.set_admin_flag(user["id"], flag)
    state = "PROMOTED to administrator" if flag else "is no longer an administrator"
    print(f"{user['full_name']} <{user['email']}> {state}.")
    print("Remember: this flag is groundwork only — the site has no "
          "web admin interface yet and grants nothing from it.")
    return 0


# ---------- command line ----------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HorizonView local administrator toolkit.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-users", help="account overview with live counts")
    sub.add_parser("list-resets", help="pending forgot-password requests")

    p_link = sub.add_parser(
        "reset-link", help="mint a single-use 60-minute reset link"
    )
    p_link.add_argument("email")
    p_link.add_argument(
        "--base-url", default=BASE_URL,
        help=f"link base (default: {BASE_URL})",
    )

    p_sess = sub.add_parser(
        "sessions", help="list a user's registered device sessions"
    )
    p_sess.add_argument("email")

    p_revoke = sub.add_parser(
        "revoke-sessions", help="sign a user out of every device"
    )
    p_revoke.add_argument("email")

    p_prom = sub.add_parser("promote-admin", help="grant administrator")
    p_prom.add_argument("email")

    p_dem = sub.add_parser("demote-admin", help="remove administrator")
    p_dem.add_argument("email")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "list-users":
        print_users()
        return 0
    if args.command == "list-resets":
        print_resets()
        return 0
    if args.command == "reset-link":
        email = args.email.strip().lower()
        if db.get_user_by_email(email) is None:
            print(f"No account uses {email}. Nothing was created.")
            return 1
        user = db.get_user_by_email(email)
        token, link = mint_reset_token(email, args.base_url)
        print(f"Account holder : {user['full_name']} ({user['email']})")
        print("Hand this link ONLY to the person who passed the")
        print("out-of-band identity verification (phone call works).")
        print(f"Works once, for {RESET_TOKEN_MINUTES} minutes.")
        print()
        print(link)
        return 0
    if args.command == "sessions":
        return print_sessions(args.email)
    if args.command == "revoke-sessions":
        return do_revoke_sessions(args.email)
    if args.command == "promote-admin":
        return do_set_admin(args.email, True)
    if args.command == "demote-admin":
        return do_set_admin(args.email, False)
    return 1


if __name__ == "__main__":
    sys.exit(main())
