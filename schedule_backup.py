"""
Turn the backup habit on (or off) with one command:

    .\\venv\\Scripts\\python.exe schedule_backup.py                # nightly 20:00
    .\\venv\\Scripts\\python.exe schedule_backup.py --time 19:30   # different time
    .\\venv\\Scripts\\python.exe schedule_backup.py --remove       # switch it off

This registers a Windows scheduled task named "HorizonView daily
backup" that runs backup-db.py every night (keeping the newest 14
backups). Nothing is registered by any other script: taking backups
or restoring data stays manual and deliberate everywhere else.

Check it afterwards:
    backup-db.py --list        (folders appear as backups happen)
    backups\\backup.log         (one line per attempt, including failures)
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
BACKUP_SCRIPT = PROJECT_ROOT / "backup-db.py"
DEFAULT_TIME = "20:00"
DEFAULT_KEEP = 14
TASK_NAME = "HorizonView daily backup"


def backup_action_string(python_exe: str = str(PYTHON_EXE),
                         backup_script: str = str(BACKUP_SCRIPT),
                         keep: int = DEFAULT_KEEP,
                         copy_to: list[str] | None = None) -> str:
    """What the scheduler runs each night. Quoted because this
    project's folder contains spaces. Several second locations are
    allowed — one --copy-to per location."""
    action = f'"{python_exe}" "{backup_script}" --keep {keep}'
    for location in copy_to or []:
        action += f' --copy-to "{location}"'
    return action


def schtasks_create_command(time: str = DEFAULT_TIME,
                            task_name: str = TASK_NAME,
                            **kwargs) -> list[str]:
    argv = [
        "schtasks", "/Create",
        "/tn", task_name,
        "/sc", "DAILY",
        "/st", time,
        "/tr", backup_action_string(**kwargs),
        "/f",  # replace any previous version of the same task
    ]
    return argv


def schtasks_remove_command(task_name: str = TASK_NAME) -> list[str]:
    return ["schtasks", "/Delete", "/tn", task_name, "/f"]


def run_command(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()

def run_command(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        argv, capture_output=True, text=True, check=False)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def parse_time(value: str) -> str:
    """schtasks wants HH:MM; accept 20:00, 8:05pm, 2040 and answer
    with a valid one so registration never half-fails."""
    value = value.strip().lower().replace(" ", "")
    if ":" in value:
        hour, minute = value.split(":", 1)
        minute = minute.replace("pm", "").replace("am", "")
        am_pm = "pm" if "pm" in value else ("am" if "am" in value else "")
        hours, minutes = int(hour), int(minute)
        if am_pm == "pm" and hours < 12:
            hours += 12
        elif am_pm == "am" and hours == 12:
            hours = 0
    elif len(value) in (3, 4) and value.isdigit():
        hours, minutes = int(value[:-2]), int(value[-2:])
    else:
        raise SystemExit(
            f"Could not read the time '{value}'. Use 20:00 or 9:30pm.")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise SystemExit(
            f"Time '{value}' is not a real clock time. Use HH:MM.")
    return f"{hours:02d}:{minutes:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register (or remove) the nightly HorizonView backup "
                    "task. Opt-in: nothing registers without this script.")
    parser.add_argument("--time", default=DEFAULT_TIME,
                        help=f"nightly time, HH:MM (default {DEFAULT_TIME})")
    parser.add_argument("--remove", action="store_true",
                        help="delete the scheduled task instead")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"how many backups to keep (default "
                             f"{DEFAULT_KEEP})")
    parser.add_argument(
        "--copy-to", action="append", metavar="FOLDER",
        help="the nightly task also mirrors every backup into this "
             "second location (other drive or cloud-synced folder); "
             "repeat the flag for several locations")
    args = parser.parse_args()

    if not PYTHON_EXE.exists():
        print(f"Expected the venv at {PYTHON_EXE} but it is missing — "
              "run this from the project folder after setting up venv.")
        return 1

    if args.remove:
        code, output = run_command(schtasks_remove_command())
        print(output or f"Task '{TASK_NAME}' removed (or was not present).")
        return code

    time = parse_time(args.time)
    argv = schtasks_create_command(time=time, keep=args.keep,
                                   copy_to=args.copy_to)
    print("Registering nightly backup task:")
    print("  " + " ".join(argv[1:]))
    code, output = run_command(argv)
    if code != 0:
        print("Registration FAILED:")
        print(output)
        return code
    print("Registered.")

    _, status = run_command(["schtasks", "/Query", "/tn", TASK_NAME,
                             "/fo", "LIST"])
    print("\n" + status)
    print("The first automatic run happens tonight at "
          f"{time}. Evidence afterwards:")
    print("  .\\venv\\Scripts\\python.exe backup-db.py --list")
    print("  (or read backups\\backup.log)")
    print("Switch it off any time:")
    print("  .\\venv\\Scripts\\python.exe schedule_backup.py --remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
