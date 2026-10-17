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

    backup                      snapshot the database AND every uploaded
                                file into backups/<timestamp>/
    list-backups                show the backups you already have

Run it from the project folder:

    .\\venv\\Scripts\\python.exe admin_tool.py list-users

Out-of-band identity verification rule (phone call to the registered
number) applies before handing over any reset link.

WHY "backup" COPIES UPLOADS TOO (decided, not accidental):

The database only remembers WHERE each uploaded photo/receipt lives.
The files themselves sit in uploads/ on disk. A database-only backup
would be a false comfort: after a restore, every record would point
at a file that no longer exists. So one backup folder always holds
BOTH halves, or it counts as no backup at all.
"""

import argparse
import hashlib
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"

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


# ---------- backup (database + uploads, verified) ----------

def file_set(root: Path) -> set[str]:
    """Relative paths of every file under root, or an empty set when
    the folder does not exist. Used to compare source against copy."""
    return {
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*") if p.is_file()
    }


def backup_database_snapshot(db_path: Path, dest_db: Path) -> str:
    """Consistent snapshot of the database using SQLite's own backup
    machinery — safe even while the server is running and writing,
    unlike a plain file copy that can tear mid-write. Returns the
    integrity-check verdict of the fresh copy (expected: 'ok')."""
    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(dest_db))
        try:
            with destination:
                source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    check = sqlite3.connect(str(dest_db))
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    return verdict


def snapshot_uploads(upload_dir: Path | None, dest_uploads: Path) -> tuple[int, int]:
    """Mirror uploads/ (photos and documents LIVE OUTSIDE the DB) into
    the backup folder. Returns (file_count, total_bytes)."""
    if upload_dir is None or not upload_dir.exists():
        return 0, 0
    if dest_uploads.exists():
        shutil.rmtree(dest_uploads)
    shutil.copytree(upload_dir, dest_uploads, dirs_exist_ok=True)
    files = [p for p in dest_uploads.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def verify_backup(dest_db: Path, source_upload_dir: Path | None,
                  dest_uploads: Path | None) -> dict:
    """A backup you cannot restore is worthless, so every run verifies
    itself: integrity-check the copied database and confirm no upload
    went missing between source and copy."""
    if not dest_db.exists():
        return {"ok": False, "integrity": "database copy missing",
                "file_count": 0, "missing_files": [],
                "unexpected_files": []}
    conn = sqlite3.connect(str(dest_db))
    try:
        verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()

    source_files = file_set(source_upload_dir) if (
        source_upload_dir is not None and source_upload_dir.exists()
    ) else set()
    copied_files = file_set(dest_uploads) if (
        dest_uploads is not None and dest_uploads.exists()
    ) else set()
    missing = sorted(source_files - copied_files)
    extra = sorted(copied_files - source_files)

    return {
        "ok": verdict == "ok" and not missing,
        "integrity": verdict,
        "file_count": len(source_files),
        "missing_files": missing,
        "unexpected_files": extra,
    }


def fresh_backup_folder(dest_root: Path) -> Path:
    """Timestamped folder that never collides (second resolution is
    enough for humans; a suffix covers repeated runs within the one
    second, which normally never happens)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = dest_root / f"backup-{stamp}"
    suffix = 2
    while folder.exists():
        folder = dest_root / f"backup-{stamp}-{suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def write_manifest(folder: Path, db_path: Path, verify: dict) -> None:
    lines = [
        "HorizonView backup",
        f"taken   : {datetime.now().isoformat(timespec='seconds')}",
        f"database: {db_path.name}  (integrity_check: {verify['integrity']})",
        f"uploads : {verify['file_count']} file(s) mirrored into uploads/",
        "",
        "One backup folder always holds BOTH halves:",
        "the database and the files it points at. Restore BOTH.",
        "",
        "Restore steps (deliberately manual — no command can wipe",
        "live data on its own):",
        "  1. Stop the server.",
        "  2. Replace horizonview.db with the copy in this folder.",
        "  3. Replace the uploads/ folder with the one in this folder.",
        "  4. Start the server and sign in to confirm.",
    ]
    (folder / "MANIFEST.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def prune_backups(dest_root: Path, keep: int) -> list[str]:
    """Delete the OLDEST backup folders beyond the newest `keep` ones.
    Only ever touches folders named backup-* inside the destination
    root — never anything else."""
    folders = sorted(
        (p for p in dest_root.glob("backup-*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for old in folders[keep:]:
        shutil.rmtree(old)
        removed.append(old.name)
    return removed


def do_backup(dest_root: Path | None = None, keep: int | None = None,
              upload_dir: Path | None = None) -> int:
    dest_root = dest_root or DEFAULT_BACKUP_DIR
    db_path = Path(db.DB_PATH)
    if not db_path.exists():
        print(f"No database found at {db_path} — nothing to back up.\n"
              "Start the server once so it creates the database.")
        return 1

    folder = fresh_backup_folder(dest_root)
    print(f"Back-up target : {folder}")

    dest_db = folder / db_path.name
    verdict = backup_database_snapshot(db_path, dest_db)
    print(f"Database copy  : {dest_db.name} (snapshot API used, "
          "safe while the server runs)")

    upload_dir = upload_dir or (PROJECT_ROOT / "uploads")
    dest_uploads = folder / "uploads"
    count, total_bytes = snapshot_uploads(upload_dir, dest_uploads)
    if upload_dir.exists():
        size = f"{total_bytes:,} bytes"
    else:
        size = "no uploads folder exists yet (nothing on disk to mirror)"
    print(f"Uploads copy   : {count} file(s), {size}")

    verify_result = verify_backup(dest_db, upload_dir, dest_uploads)
    if verify_result["ok"]:
        print("Verification   : PASS — database integrity ok, "
              f"{verify_result['file_count']} upload(s) all present")
    else:
        print("Verification   : FAIL — a backup you cannot restore "
              "is worthless:")
        print(f"    integrity_check : {verify_result['integrity']}")
        for name in verify_result["missing_files"]:
            print(f"    missing from copy: {name}")
        for name in verify_result["unexpected_files"]:
            print(f"    unexpected in copy: {name}")

    write_manifest(folder, db_path, verify_result)
    print(f"Manifest       : {folder / 'MANIFEST.txt'}")

    if keep is not None:
        removed = prune_backups(dest_root, keep)
        if removed:
            print(f"Pruned         : {len(removed)} old backup(s) removed "
                  f"(kept the {keep} newest)")
        else:
            print(f"Pruned         : nothing to prune (kept the {keep} "
                  "newest)")
    else:
        total = len(list(dest_root.glob('backup-*')))
        if total > 20:
            print("Hint           : many backups are piling up — "
                  "run again with --keep 14 to prune to the newest 14.")
    return 0 if verify_result["ok"] else 1


def do_list_backups(dest_root: Path | None = None) -> int:
    dest_root = dest_root or DEFAULT_BACKUP_DIR
    folders = sorted(
        (p for p in dest_root.glob("backup-*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not folders:
        print("No backups yet. Run:")
        print("  .\\venv\\Scripts\\python.exe backup-db.py")
        return 1
    print(f"{len(folders)} backup(s), newest first:\n")
    for f in folders:
        db_copy = next(
            (p for p in f.glob("*.db") if p.is_file()), None)
        db_line = f"{db_copy.name}  {db_copy.stat().st_size:,} bytes" \
            if db_copy else "(no database copy!)"
        uploads_count = len([p for p in (f / 'uploads').rglob('*')
                             if p.is_file()]) if (f / 'uploads').exists() else 0
        print(f"  {f.name}   {db_line}   {uploads_count} upload file(s)")
    print("\nRestore steps are inside each folder's MANIFEST.txt.")
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

    p_backup = sub.add_parser(
        "backup",
        help="snapshot the database AND uploads into backups/<timestamp>",
    )
    p_backup.add_argument(
        "--keep", type=int, default=None, metavar="N",
        help="after the run, delete all but the newest N backups",
    )
    p_backup.add_argument(
        "--dest", default=None, metavar="FOLDER",
        help=f"destination root (default: {DEFAULT_BACKUP_DIR.name})",
    )
    sub.add_parser(
        "list-backups", help="show existing backup folders (newest first)",
    )

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
    if args.command == "backup":
        return do_backup(
            dest_root=Path(args.dest) if args.dest else None,
            keep=args.keep,
        )
    if args.command == "list-backups":
        return do_list_backups()
    return 1


if __name__ == "__main__":
    sys.exit(main())
