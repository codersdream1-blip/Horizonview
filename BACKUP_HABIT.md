# The HorizonView backup habit

The database is the whole business now, and every uploaded photo,
receipt and document is the other half of it. Backups here always
save BOTH halves together, into one timestamped folder under
`backups/`. One half without the other is not a backup.

## The habit, as decided

| When                          | What                                                    |
| ----------------------------- | ------------------------------------------------------- |
| **Every night at 20:00**      | Automatic (Windows scheduled task). Keeps newest 14.    |
| **Before any risky change**   | Manual: `backup-db.py` first, then change things.       |
| **Once a month**              | A restore drill: actually restore a backup and look.    |

"Risky change" means: replacing files, editing the database by hand,
updating the app's code, or anything you would undo afterwards.

## Making the nightly run happen (one-time, opt-in)

```
.\venv\Scripts\python.exe schedule_backup.py
```

That registers a Windows scheduled task called
**"HorizonView daily backup"**. Different time, or switch-off:

```
.\venv\Scripts\python.exe schedule_backup.py --time 19:30
.\venv\Scripts\python.exe schedule_backup.py --remove
```

## How to check last night actually happened

```
.\venv\Scripts\python.exe backup-db.py --list     (newest first)
more backups\backup.log                            (one line per attempt,
                                                    runs nobody watched
                                                    included — ABORT and
                                                    FAIL lines show up
                                                    here, never hidden)
```

## Restoring (deliberately manual — no command wipes live data)

Each backup folder's `MANIFEST.txt` has the same four steps:

1. Stop the server.
2. Replace `horizonview.db` with the copy in the backup folder.
3. Replace the `uploads/` folder with the one in the backup folder.
4. Start the server and sign in to confirm.

Then take a fresh backup, since the restored state is now the newest.

## Second location: the honest upgrade (built in)

```
.\venv\Scripts\python.exe backup-db.py --copy-to D:\HorizonBackups
.\venv\Scripts\python.exe schedule_backup.py --copy-to D:\HorizonBackups
```

With `--copy-to`, every VERIFIED backup is mirrored into the second
folder under the same folder name (never an unverified one), and the
nightly task can carry the flag. Point it at:

- an **external drive** — a second disk survives a dead internal drive
- a **cloud-synced folder** (OneDrive / Dropbox / Google Drive) — the
  copy also lives on the provider's servers, so theft, virus or a
  disk failure of this machine can no longer take everything

If the mirror fails (drive unplugged, network path missing) the run
says so and `backup.log` records `SECONDARY-FAIL` — the local backup
is still sound, but the gap is never hidden.

## What this does NOT promise (honesty notes)

- The nightly task on a dev Windows machine runs only while this
  machine is on and you are logged in. If it was off at 20:00, that
  night simply has no backup — `backup.log` shows the gap.
- A folder on the SAME disk is not a second location — it only
  survives accidental deletion, not the machine itself.
- Cloud folders protect across machines, but they sync from this
  machine's account; the account itself needs its own strong
  password and, ideally, two-factor authentication.
- Files are copied as they are; nothing is encrypted. Backups inherit
  the same privacy handling as the live data (never committed, never
  shared).
