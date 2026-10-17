"""
Administrator tool for password resets.

HorizonView has no email service in this stage of the project, so a
forgotten password is handled like a real-world reception desk:

1. The customer submits the "Forgot your password?" form. Its only
   job is to record the request and reply (always, existing e-mail or
   not) that the administrator will follow up.
2. The administrator verifies the person's identity OUT OF BAND
   (a phone call to the registered number works well), then runs:

       .\\venv\\Scripts\\python.exe create_reset_link.py user@example.com
       [optional: --list  to see pending reset requests]

3. The script creates a single-use token that works for 60 minutes,
   prints the full reset URL, and the administrator hands the link to
   the customer through the same out-of-band channel.

This script runs directly on the server. It is NOT a web endpoint:
the general public can never mint reset links over HTTP.

Security properties:
- The token is 256 bits of randomness; the database stores only its
  SHA-256 hash, so a stolen database does not reveal live links.
- The token can be used exactly once, and only within 60 minutes.
- Using it does not require (and never reveals) the old password.
"""

import argparse
import hashlib
import secrets
import sys
from datetime import datetime, timedelta, timezone

import db

DEFAULT_BASE_URL = "http://127.0.0.1:8300"
RESET_TOKEN_MINUTES = 60


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a single-use password reset link."
    )
    parser.add_argument(
        "email", nargs="?", help="the account holder's email address"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"base URL to build the link from (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="show recent reset requests instead of minting a link",
    )
    args = parser.parse_args()

    if args.list:
        requests = db.list_reset_requests()
        if not requests:
            print("No reset requests recorded yet.")
            return 0
        print(f"{len(requests)} most recent reset request(s):\n")
        for r in requests:
            print(f"  {r['created_at'][:19]}  {r['email']}  (IP {r['requested_ip']})")
        return 0

    if not args.email:
        parser.error("give an email address (or use --list)")

    email = args.email.strip().lower()
    user = db.get_user_by_email(email)
    if user is None:
        print(f"No account uses {email}. Nothing was created.")
        return 1

    token = secrets.token_hex(32)  # 256 bits of randomness
    token_hash_out = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_MINUTES)
    ).isoformat()

    db.create_password_reset(
        user_id=user["id"],
        token_hash=token_hash_out,
        expires_at=expires_at,
        requested_ip="admin-script",
    )

    print(f"Account holder : {user['full_name']} ({user['email']})")
    print(f"Instructions   : hand this link ONLY to the person who")
    print(f"                 passed the identity verification.")
    print(f"How long it    : single use, expires {expires_at[:16]} UTC")
    print(f"works          : ({RESET_TOKEN_MINUTES} minutes)")
    print()
    print(f"{args.base_url.rstrip('/')}/reset-password.html?token={token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
