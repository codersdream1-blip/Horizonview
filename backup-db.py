"""
Take a verified backup of the HorizonView database AND every uploaded
photo/receipt/document. This is an alias for admin_tool's `backup`
command, so all of the logic lives in one place:

    .\\venv\\Scripts\\python.exe backup-db.py              # take a backup
    .\\venv\\Scripts\\python.exe backup-db.py --keep 14    # also prune old ones
    .\\venv\\Scripts\\python.exe backup-db.py --list       # = list-backups

Why it copies uploads too: the database only remembers WHERE each
uploaded file lives — the files themselves sit in uploads/ on disk.
A database-only backup would restore records pointing at empty air.
One backup folder always holds BOTH halves.
"""

import sys

import admin_tool


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--list":
        return admin_tool.main(["list-backups"])
    return admin_tool.main(["backup", *argv])


if __name__ == "__main__":
    sys.exit(main())
