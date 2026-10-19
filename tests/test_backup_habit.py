"""
Backup-habit tests.

What the habit needs from code, tested offline:

- every backup attempt leaves a one-line evidence trail in
  backups/backup.log, including failures (the nightly scheduled run
  happens while nobody watches — the log is its witness)
- the command registered with Windows Task Scheduler is exactly
  what it claims to be: the venv python running backup-db.py with
  the retention flag, daily, at the promised time
- time strings humans type (20:00, 9:30pm, 2040) become valid
  HH:MM the scheduler understands
"""

import pytest

import admin_tool
import db
import schedule_backup as sched


# ---------- evidence trail ----------

@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live.db")
    db.init_db()
    db.create_user("Ama Mensah", "ama@example.com", "+233 20", "hash")
    return db


def test_successful_backup_logs_a_line(fresh_db, tmp_path, capsys):
    admin_tool.do_backup(dest_root=tmp_path / "backups",
                         upload_dir=tmp_path / "uploads")
    log_text = (tmp_path / "backups" / "backup.log").read_text("utf-8")
    assert "PASS" in log_text
    assert "backup-" in log_text  # names the folder it made


def test_failed_backup_logs_an_honest_abort(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "does-not-exist.db")
    exit_code = admin_tool.do_backup(dest_root=tmp_path / "backups",
                                     upload_dir=tmp_path / "uploads")
    log_text = (tmp_path / "backups" / "backup.log").read_text("utf-8")
    assert exit_code == 1
    assert "ABORT" in log_text

    # And a folder must still never exist for that no-op:
    assert list((tmp_path / "backups").glob("backup-*")) == []


def test_log_entries_are_one_line_each(fresh_db, tmp_path, capsys):
    admin_tool.do_backup(dest_root=tmp_path / "backups",
                         upload_dir=tmp_path / "uploads")
    admin_tool.do_backup(dest_root=tmp_path / "backups",
                         upload_dir=tmp_path / "uploads")
    lines = (tmp_path / "backups" / "backup.log").read_text(
        "utf-8").strip().splitlines()
    assert len(lines) == 2


# ---------- the scheduled task definition ----------

def test_registered_command_is_exactly_what_it_claims():
    argv = sched.schtasks_create_command(time="20:00", keep=14)

    assert argv[0] == "schtasks" and argv[1] == "/Create"
    assert "/sc" in argv and "DAILY" in argv
    assert "/f" in argv
    task_index = argv.index("/tn")
    assert argv[task_index + 1] == "HorizonView daily backup"
    time_index = argv.index("/st")
    assert argv[time_index + 1] == "20:00"
    tr_index = argv.index("/tr")
    action = argv[tr_index + 1]
    assert action == f'"{sched.PYTHON_EXE}" "{sched.BACKUP_SCRIPT}" --keep 14'


def test_remove_command_targets_the_task_only():
    argv = sched.schtasks_remove_command()
    assert argv[:2] == ["schtasks", "/Delete"]
    assert "/tn" in argv and "HorizonView daily backup" in argv
    assert "/f" in argv


def test_retention_flag_travels_into_the_registered_action():
    action = sched.backup_action_string(
        keep=30, python_exe="C:/py/python.exe", backup_script="C:/b.py")
    assert action == '"C:/py/python.exe" "C:/b.py" --keep 30'


# ---------- human time input ----------

def test_time_parsing_variants():
    assert sched.parse_time("20:00") == "20:00"
    assert sched.parse_time("9:30pm") == "21:30"
    assert sched.parse_time("12:15am") == "00:15"
    assert sched.parse_time("2040") == "20:40"


def test_time_parsing_rejects_nonsense():
    with pytest.raises(SystemExit):
        sched.parse_time("25:00")
    with pytest.raises(SystemExit):
        sched.parse_time("next Tuesday")


# ---------- second location ----------

def test_registered_action_can_carry_the_second_location():
    action = sched.backup_action_string(
        copy_to=[r"D:\HorizonBackups"],
        python_exe="C:/py/python.exe", backup_script="C:/b.py")
    assert action == ('"C:/py/python.exe" "C:/b.py" --keep 14 '
                      '--copy-to "D:\\HorizonBackups"')


def test_registered_action_can_carry_two_second_locations():
    action = sched.backup_action_string(
        copy_to=[r"D:\HorizonBackups", r"C:\Users\THIS PC\OneDrive\hv"],
        python_exe="C:/py/python.exe", backup_script="C:/b.py", keep=7)
    assert action == ('"C:/py/python.exe" "C:/b.py" --keep 7 '
                      '--copy-to "D:\\HorizonBackups" '
                      '--copy-to "C:\\Users\\THIS PC\\OneDrive\\hv"')


def test_registered_action_without_copy_to_stays_clean():
    action = sched.backup_action_string(
        python_exe="C:/py/python.exe", backup_script="C:/b.py")
    assert "--copy-to" not in action


def test_do_backup_mirrors_the_verified_folder(fresh_db, tmp_path, capsys):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "receipt.png").write_bytes(b"PNG" * 40)
    primary = tmp_path / "backups"
    secondary = tmp_path / "second" / "HorizonBackups"

    exit_code = admin_tool.do_backup(
        dest_root=primary, upload_dir=uploads, copy_to=[secondary])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Second copy    : PASS" in out
    # same folder name on both sides, same content
    local = next(p.name for p in primary.glob("backup-*") if p.is_dir())
    mirror = secondary / local
    assert mirror.is_dir()
    assert admin_tool.file_set(mirror) == admin_tool.file_set(
        primary / local)
    log_text = (primary / "backup.log").read_text("utf-8")
    assert "SECONDARY-OK" in log_text


def test_failed_mirror_never_passes_silently(fresh_db, tmp_path, capsys):
    """A second location that could not be written cannot be faked:
    the run says SECONDARY-FAIL and exits non-zero, while the local
    backup stays sound."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "receipt.png").write_bytes(b"PNG")
    primary = tmp_path / "backups"
    broken_second = tmp_path / "a-file-named-like-a-path"
    broken_second.write_text("not a folder")  # copytree must fail here

    exit_code = admin_tool.do_backup(
        dest_root=primary, upload_dir=uploads, copy_to=[broken_second])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "Verification   : PASS" in out       # local still sound
    assert "Second copy    : FAIL" in out
    log_text = (primary / "backup.log").read_text("utf-8")
    assert "SECONDARY-FAIL" in log_text
    assert "SECONDARY-OK" not in log_text


def test_guard_branch_skips_mirror_when_primary_failed(tmp_path):
    """Directly exercise the primary-failed guard with a monkeypatch,
    without contorting a healthy run into failing."""
    from unittest.mock import patch

    calls = []
    with patch.object(admin_tool, "mirror_to_secondary",
                      side_effect=lambda folder, target: calls.append(
                          (folder, target)) or {"ok": True}):
        with patch.object(admin_tool, "verify_backup",
                          return_value={
                              "ok": False, "integrity": "pretend-corrupt",
                              "file_count": 0, "missing_files": [],
                              "unexpected_files": []}):
            db_ok_path = tmp_path / "live.db"
            db_ok_path.write_bytes(b"placeholder")  # exists, so no abort

            old_init = db.init_db
            db.init_db = lambda: None
            try:
                exit_code = admin_tool.do_backup(
                    dest_root=tmp_path / "backups",
                    upload_dir=tmp_path / "uploads",
                    copy_to=tmp_path / "second")
            finally:
                db.init_db = old_init

    assert exit_code == 1
    assert calls == []  # the broken backup was never copied anywhere


def test_two_locations_are_both_reported(fresh_db, tmp_path, capsys):
    """The nightly habit's real shape from now on: H: disk AND a
    cloud-synced folder. One gone bad never masks the other."""
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "photo.jpg").write_bytes(b"JPG")
    primary = tmp_path / "backups"
    healthy = tmp_path / "second" / "HorizonBackups"
    broken = tmp_path / "a-file-named-like-a-path"
    broken.write_text("not a folder")

    exit_code = admin_tool.do_backup(
        dest_root=primary, upload_dir=uploads, copy_to=[healthy, broken])

    assert exit_code == 1  # one failed door fails the run
    out = capsys.readouterr().out
    assert out.count("Second copy    : PASS") == 1
    assert out.count("Second copy    : FAIL") == 1
    log_text = (primary / "backup.log").read_text("utf-8")
    assert "SECONDARY-FAIL(1/2)" in log_text
    # the healthy one still holds the paired folder
    local_name = next(p.name for p in primary.glob("backup-*")
                      if p.is_dir())
    assert (healthy / local_name).is_dir()


def test_both_succeed_logs_one_plain_ok(fresh_db, tmp_path, capsys):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    primary = tmp_path / "backups"
    one = tmp_path / "second" / "HorizonBackups"
    two = tmp_path / "third" / "OneDriveFolder"

    exit_code = admin_tool.do_backup(
        dest_root=primary, upload_dir=uploads, copy_to=[one, two])

    assert exit_code == 0
    log_text = (primary / "backup.log").read_text("utf-8")
    assert "SECONDARY-OK" in log_text and "SECONDARY-FAIL" not in log_text
    local_name = next(p.name for p in primary.glob("backup-*")
                      if p.is_dir())
    assert (one / local_name).is_dir() and (two / local_name).is_dir()


def test_secondary_slot_is_pruned_like_the_primary(fresh_db, tmp_path,
                                                   capsys):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    primary = tmp_path / "backups"
    secondary = tmp_path / "second"
    seeds = ["backup-20200101-000000", "backup-20200102-000000"]
    for name in seeds:
        (secondary / name).mkdir(parents=True)

    admin_tool.do_backup(dest_root=primary, upload_dir=uploads,
                         copy_to=secondary, keep=2)

    names = [p.name for p in secondary.iterdir()]
    assert "backup-20200101-000000" not in names              # pruned
    assert "backup-20200102-000000" in names   # newer seed survives
    assert len([n for n in names if n.startswith("backup-")]) == 2
