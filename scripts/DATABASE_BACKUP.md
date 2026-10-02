# Daily full PostgreSQL backup (Windows)

This replaces the removed `backup_wo.py` and `backup_wo.ps1`. No Python or MES
virtual environment is needed. It backs up **all objects/data in the configured
`postgres` database**, including approved orders, WOs, schedules, and MRP tables.
It does not back up other databases or server-wide roles/tablespaces; keep those
separately if full server disaster recovery is required.

## One-time setup

1. **Client tools are already installed:** PostgreSQL **18.6** at
   `D:\PostgreSQL\18\bin`, verified for `pg_dump`, `pg_restore`, and `psql`.
   These can dump the checked PostgreSQL 17.2 server. The config now explicitly
   uses this directory; do not use the older version 16 installation on C:.
   On another PC, install PostgreSQL client tools at least as new as the server
   from https://www.postgresql.org/download/windows/ (select Command Line Tools),
   or set `PgBinDirectory` to portable tools. No local database server is required.
2. Review `scripts/database_backup.json`. There are **no passwords** in this file.
3. From the MES project directory, run as the Windows account that will run the task:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_database_backup_credentials.ps1
   ```

   Enter the **PostgreSQL password**. This creates a dedicated pgpass file under
   `%APPDATA%\postgresql`, outside OneDrive, readable only by that account and
   SYSTEM. PostgreSQL requires this password file to be plaintext; its ACL
   protects it. Never put it in the shared backup folder or copy it into chat.

4. Register the task (use an elevated PowerShell window if Windows denies access):

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_database_backup_task.ps1
   ```

   The installer first runs and validates a **complete backup**. It refuses to
   register/update the task if that fails. Then enter your **Windows sign-in
   password** (not a PIN and not the PostgreSQL password). Task Scheduler stores
   this credential securely to run whether or not you are logged on. Use the
   same Windows account as step 3. If its Windows password changes, update the
   task by running this installer again.

**Task registration is not performed just by creating these files.** Run the
installer and check its success message. With the default configuration the task
runs every day at **23:00**, starts after missed runs when possible, requests wake
from sleep, permits battery operation, retries failures three times at 15-minute
intervals, and does not start overlapping copies. It cannot run while the PC is
powered off; the PC must eventually be on and able to reach the database server.

## Where the backup goes

```text
D:\OneDrive - neousys-tech\Share NTA Warehouse\Database Backup\postgres_latest.dump
```

Default mode is **overwrite**, matching the requested latest-only behavior. A
new dump is produced locally, its entire archive is read/decompressed by
`pg_restore` without executing SQL, and a SHA256-verified copy is placed in the
backup directory. Only then is the final file atomically replaced. A dump,
validation, copy, or replacement failure retains the previous finalized file.
Local temporary work directories are cleaned up; a file lock prevents overlaps.

A successful local backup does **not** prove OneDrive uploaded it. Keep OneDrive
running, check its sync status, and mark this folder **Always keep on this device**.
Restrict shared-folder access appropriately: the archive contains the entire
business database. OneDrive is an off-PC copy only after its upload succeeds.

## Editable settings

Edit `scripts/database_backup.json`; backup settings are read on each run.

| Setting | Meaning |
|---|---|
| `Host`, `Port`, `Database`, `Username` | Database connection; changing these requires rerunning credential setup |
| `BackupDirectory` | Requested shared OneDrive destination |
| `StagingDirectory` | Local working space and logs, outside OneDrive |
| `PasswordFile` | Dedicated local pgpass path; expands Windows environment variables |
| `PgBinDirectory` | Optional explicit PostgreSQL `bin` path; empty selects highest installed version from standard folders, installation registry, or PATH |
| `Mode` | `overwrite` (one latest file) or `dated` (timestamped recovery copies) |
| `Filename` | Latest archive filename; use a plain `.dump` name |
| `RetentionDays` | Used **only in dated mode**; removes matching old archives after a new successful backup |
| `DailyTime`, `TaskName` | Task settings; rerun the task installer after changing them |
| `ToolTimeoutSeconds` | Timeout for each client command; default 3600 seconds |

For protection against accidentally backing up bad/deleted data, **dated mode**
with 30-day retention is safer than latest-only overwrite. Change `Mode` to
`dated` if you want that protection; default remains overwrite. Neither mode
replaces regular restore testing.

## Manual run / monitoring

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\backup_database.ps1
Get-ScheduledTaskInfo -TaskName "MES - Full PostgreSQL Database Backup"
```

- Task Scheduler **Last Run Result 0** means success; nonzero means failure.
- Local append-only daily logs: `D:\DatabaseBackupStaging\logs\backup_YYYY-MM-DD.log`.
- Logs include final path, byte count and SHA256, or the failure reason.
- No task or successful live backup has been claimed until prerequisites and
  setup actually succeed. Existing older backup files are not deleted.

## Restore / recovery

Inspect an archive:

```powershell
& "D:\PostgreSQL\18\bin\pg_restore.exe" --list "D:\OneDrive - neousys-tech\Share NTA Warehouse\Database Backup\postgres_latest.dump"
```

Regularly perform an actual restore into a **separate test database**, not
`postgres`. A full restore test is stronger than archive validation. Original
owners/grants are stored in the archive; their roles must exist on the recovery
server, or deliberately use `--no-owner --no-privileges` when restoring to a test
DB. Do not use `--clean` or restore over the production database without a
reviewed recovery plan.
