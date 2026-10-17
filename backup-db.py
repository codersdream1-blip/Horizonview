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

Second location (the honest upgrade over one machine):

    .\\venv\\Scripts\\python.exe backup-db.py --copy-to D:\\HorizonBackups

mirrors the +verified+ backup into the second folder (same folder
name on both sides) — an external drive today, or a OneDrive/Dropbox
folder for automatic across-machine protection. If the mirror fails
(unplugged drive, missing network path), the command says so loudly
and exits non-zero instead of pretending.
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
