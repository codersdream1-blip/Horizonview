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

## What this does NOT promise (honesty notes)

- Backups live on the **same machine** as the data. A virus, theft or
  disk failure that destroys the data can destroy the backups too.
  The honest upgrade later: copy the newest backup folder to a second
  location on a different device (external drive / cloud folder).
- The nightly task on a dev Windows machine runs only while that
  machine is on and you are logged in. If it was off at 20:00, that
  night simply has no backup — check `backup.log`.
- Files are copied as they are; nothing is encrypted. Backups inherit
  the same privacy handling as the live data (never committed, never
  shared).
