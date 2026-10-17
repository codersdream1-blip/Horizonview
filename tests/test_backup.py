"""
Backup tests.

A backup you cannot restore is worthless, so every property tested
here is a restore-relevant property:

- the copied database passes SQLite's own integrity check and holds
  the real data, even while another connection has the live database
  open (the "server is running" case)
- every uploaded file is mirrored (files live OUTSIDE the database)
- a backup that loses an uploaded file is reported honestly, and the
  whole run FAILS instead of printing a cheery PASS
- pruning removes only the oldest backup-* folders, keeping the
  newest, and never touches anything it was not told about
"""

import sqlite3
from pathlib import Path

import pytest

import admin_tool
import db

A_USER = ("Ama Mensah", "ama@example.com", "+233 20 111 2222",
          "$2b$12$seedhashseedhashseedhashseedhashseedhashseeds")


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live.db")
    db.init_db()
    db.create_user(*A_USER)
    return db


def make_uploads(root: Path) -> Path:
    """Mimic the real uploads/ layout: files under project folders."""
    uploads = root / "uploads"
    p1 = uploads / "project_1"
    p1.mkdir(parents=True)
    (p1 / "site_photo.jpg").write_bytes(b"JPG-PC" * 200)
    (p1 / "contract.pdf").write_bytes(b"%PDF" * 500)
    p2 = uploads / "project_2"
    p2.mkdir()
    (p2 / "receipt3.png").write_bytes(b"PNG" * 100)
    (p2 / "even" / "deeper.txt").parent.mkdir()
    (p2 / "even" / "deeper.txt").write_text("nested ok")
    return uploads


# ---------- database snapshot ----------

def test_backup_is_a_valid_database_copy(fresh_db, tmp_path):
    dest_db = tmp_path / "backups" / "b1" / "live.db"
    dest_db.parent.mkdir(parents=True)

    verdict = admin_tool.backup_database_snapshot(Path(db.DB_PATH), dest_db)

    assert verdict == "ok"
    check = sqlite3.connect(dest_db)
    name = check.execute("SELECT full_name FROM users").fetchone()[0]
    check.close()
    assert name == "Ama Mensah"


def test_snapshot_is_safe_while_the_database_is_in_use(fresh_db, tmp_path):
    """The realistic case: the server holds the database open —
    simulated here by a second connection mid-transaction."""
    victim = sqlite3.connect(str(db.DB_PATH))
    victim.execute("BEGIN")
    victim.execute(
        "INSERT INTO users (full_name, email, phone, password_hash,"
        " created_at) VALUES ('Half-written', 'x@example.com',"
        " '+233', 'hash', '2026-01-01')"
    )  # NOT committed — a plain file copy would risk catching this

    dest_db = tmp_path / "backups" / "b2" / "live.db"
    dest_db.parent.mkdir(parents=True)
    verdict = admin_tool.backup_database_snapshot(Path(db.DB_PATH), dest_db)
    victim.rollback()
    victim.close()

    assert verdict == "ok"
    check = sqlite3.connect(dest_db)
    emails = [r[0] for r in check.execute("SELECT email FROM users")]
    check.close()
    # The snapshot saw the last fully consistent state: the committed
    # row only, never the half-written one.
    assert emails == ["ama@example.com"]


# ---------- the uploads half ----------

def test_backup_mirrors_uploads_tree(fresh_db, tmp_path):
    uploads = make_uploads(tmp_path)
    dest = tmp_path / "backups" / "b3"
    count, total = admin_tool.snapshot_uploads(uploads, dest / "uploads")

    assert count == 4 and total > 0
    assert admin_tool.file_set(dest / "uploads") == {
        "project_1/site_photo.jpg",
        "project_1/contract.pdf",
        "project_2/receipt3.png",
        "project_2/even/deeper.txt",
    }


def test_backup_with_no_uploads_yet(fresh_db, tmp_path):
    count, total = admin_tool.snapshot_uploads(
        tmp_path / "uploads", tmp_path / "dest" / "uploads")
    assert (count, total) == (0, 0)


def test_verify_flags_an_upload_file_missing_from_the_copy(tmp_path,
                                                           fresh_db):
    """A file vanishing between source and copy is exactly what a
    file backup exists to prevent, so verification must flag it
    honestly instead of printing a cheery all-clear."""
    uploads = make_uploads(tmp_path)
    admin_tool.do_backup(
        dest_root=tmp_path / "backups", upload_dir=uploads)
    folder = next(p for p in (tmp_path / "backups").glob("backup-*"))
    (folder / "uploads" / "project_1" / "site_photo.jpg").unlink()

    verify = admin_tool.verify_backup(
        folder / Path(db.DB_PATH).name, uploads, folder / "uploads")

    assert verify["missing_files"] == ["project_1/site_photo.jpg"]
    assert verify["ok"] is False


# ---------- the whole run ----------

def test_full_backup_run_verifies_and_writes_manifest(fresh_db, tmp_path,
                                                      capsys):
    uploads = make_uploads(tmp_path)

    exit_code = admin_tool.do_backup(
        dest_root=tmp_path / "backups", upload_dir=uploads)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Verification   : PASS" in out
    folder = next(p for p in (tmp_path / "backups").glob("backup-*")
                  if p.is_dir())
    manifest = (folder / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "Restore BOTH" in manifest
    assert "4 file(s)" in manifest
    assert (folder / Path(db.DB_PATH).name).exists()


def test_backup_without_a_database_refuses_honestly(tmp_path, monkeypatch,
                                                    capsys):
    """No database on disk -> say so, create nothing."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "does-not-exist.db")
    exit_code = admin_tool.do_backup(
        dest_root=tmp_path / "backups", upload_dir=tmp_path / "uploads")
    assert exit_code == 1
    assert "nothing to back up" in capsys.readouterr().out
    assert list((tmp_path / "backups").glob("backup-*")) == []


def test_keep_prunes_oldest_and_only_backup_folders(fresh_db, tmp_path,
                                                    capsys):
    root = tmp_path / "backups"
    for stale in ("backup-20200101-000000", "backup-20200102-000000"):
        (root / stale).mkdir(parents=True)
        (root / stale / "file.txt").write_text("old")
    (root / "not-a-backup").mkdir()  # must never be touched

    uploads = make_uploads(tmp_path)
    exit_code = admin_tool.do_backup(
        dest_root=root, upload_dir=uploads, keep=2)

    assert exit_code == 0
    # One fake-old folder was pruned (keep=2, three folders existed),
    # the unrelated folder was untouched:
    names = sorted(p.name for p in root.iterdir())
    assert "not-a-backup" in names
    assert "backup-20200101-000000" not in names
    assert len([n for n in names if n.startswith("backup-")]) == 2


def test_backup_folder_names_never_collide(fresh_db, tmp_path):
    root = tmp_path / "backups"
    first = admin_tool.fresh_backup_folder(root)
    admin_tool.fresh_backup_folder(root)  # even within the same second
    assert first.name.startswith("backup-")
    assert len(list(root.glob("backup-*"))) == 2
