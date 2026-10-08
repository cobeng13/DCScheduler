# Browser database backups and clearing

Sign in as an administrator and open **Administration → Database and timetable**. Scheduler accounts cannot use these controls or their API endpoints.

## Save the whole application database

Choose **Download full database backup**. The browser downloads a `.scheduler-backup` containing all application tables: every program and its assignments, accounts and password hashes, personal preferences, curricula, sections, rooms, faculty, rules/exceptions, schedules and activity/security history. Authentication tables are included in the archive, but sessions and login-attempt counters are deliberately discarded when restoring.

Keep this file private. It contains credential hashes and session-related secrets. The download uses a consistent snapshot under the shared mutation lock and creates an admin-only audit event. It is a complete application-data snapshot, not a PostgreSQL physical copy or a local SQLite database file. Schema/index definitions and Alembic revisions stay under deployment/migration control; the archive records the required schema/revision and can only be restored to a matching installation.

## Replace the current database

1. Download a current backup first if you need to preserve current work.
2. Select a `.scheduler-backup` previously downloaded here.
3. Enter your **current** administrator password and acknowledge replacement of the entire database.
4. Choose **Upload and replace database** and keep the page open until completion.
5. Everyone is signed out. Sign in with an account and password contained in the uploaded backup.

Replacement affects **all programs, accounts, schedules, settings, curricula and history**, not just the selected program. The uploaded backup must contain an enabled administrator account. Its passwords replace current passwords. Downloading a backup does not save unsaved browser forms.

The server reads uploads in chunks, checks the complete format/schema/checksum, and replaces rows in one transaction under the scheduling lock. Invalid files and constraint/ownership errors leave the current database unchanged. Files cannot supply SQL statements, table definitions or arbitrary table names. A restore audit event is appended to the restored history. Deleted current history is not retained separately; save the current backup if you need it. Readiness is checked after commit; a readiness failure reports that the database has already been restored and requires a server-health check.

Limits: **12 MiB total, 4 MiB per record, 100,000 records**. Temporary files stay in private app storage and are closed after use. Large archives or databases with additional tables use the existing server PostgreSQL backup/restore scripts. The browser format does not accept old local `.db` files or native PostgreSQL `.dump` files. Existing daily `.dump` backups and their server restore workflow remain unchanged.

## Clear the current timetable

Enter your current administrator password, acknowledge the all-program scope, and choose **Clear timetable for all programs**. This removes all scheduled classes while preserving accounts, program assignments, sections, rooms, faculty, curricula, personal preferences, scheduling rules/exceptions and audit history. Deletions appear in shared activity and other browsers refresh through SSE. Open forms are preserved; edits to deleted classes will fail rather than silently recreate them.

The **File → Clear Timetable (Admin)…** shortcut opens Administration. The older program-clear API also now requires administrator access and password confirmation. Ordinary authorized class edits and timetable CSV replacement remain available to program schedulers. Password confirmations are limited to five failures per account in 15 minutes; passwords are never placed in URLs or audit records.

No database migration or new environment variables are required.
