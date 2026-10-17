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
