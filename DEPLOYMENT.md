# CAMP Server deployment

The scheduler is a separate two-container stack on `apps-server`. It starts empty and does not change existing apps on ports 8001–8003. Run commands from `/opt/apps/TheScheduler-Online`.

## Initial setup

1. Clone `https://github.com/cobeng13/DCScheduler.git` to `/opt/apps/TheScheduler-Online`.
2. Copy `deploy/.env.example` to `deploy/.env`; set the real HTTPS `PUBLIC_ORIGIN`, available `APP_PORT` (default 8004), and `BACKUP_DIR` on your backup storage. Keep this file private and outside Git. `BIND_ADDRESS=127.0.0.1` is appropriate for a tunnel connector on this host. If the connector runs on a different Proxmox guest, use the apps-server LAN IP and restrict access to the connector with the server firewall.
3. Run `bash deploy/deploy.sh`. This builds the UI, creates a private Docker network and persistent PostgreSQL volume, runs Alembic migrations, and starts the app. PostgreSQL has no published port. The application uses UID 10001 and a read-only filesystem.
4. Run `docker exec -it camp-scheduler python -m app.bootstrap YOUR_USERNAME`. Enter the password interactively; it never appears in shell history. Sign in and change it.
5. In the existing Cloudflare Tunnel, add the scheduler hostname with service `http://APPS_SERVER_REACHABLE_ADDRESS:8004` (or your configured port). A containerized connector's `localhost` refers to its own container; use the host's reachable address or attach that connector to the `camp-scheduler` Docker network and target `http://camp-scheduler:8000`.
6. Disable Cloudflare caching for `/api/*`. Do not enable response buffering for `/api/events`; it uses `text/event-stream`, heartbeat comments, and `Cache-Control: no-cache, no-transform`. Public HTTPS is required for the secure session cookie. Tunnel access does not replace the app's account permissions.
7. Create two scheduler accounts and two programs in Administration, then assign one account to each program. Pilot with these accounts before general access.

Preflight runs before updates to running services. It requires Docker/daemon access and the Linux `ip`, `ss`, `df`, `stat`, `curl`, and `git` utilities (normally provided by iproute2, coreutils, curl, and git). It checks the configured port, the bind address, free space on the project/Docker/backup filesystems, backup directory creation and writability, secret ownership/modes, and health of an existing PostgreSQL container. The port may be occupied only by the scheduler's exact current mapping; a new address/port must be available. `BIND_ADDRESS` must be a numeric address assigned to the host or an explicit wildcard (`0.0.0.0`/`::`). Keep the same deployment account for updates, backups, restore, and rollback. Run `bash deploy/preflight.sh` independently to diagnose configuration before deployment.

`MIN_FREE_DISK_MB` defaults to 4096 on each checked filesystem. Set it to suit your image-build and database sizes; at least 256 MB is required, but that minimum does not guarantee adequate build/backup capacity. Confirm tunnel routes, database size, and memory manually. Preflight checks available capacity at that moment; it does not reserve space.

Keep the generated `deploy/secrets/db_password` with your recovery materials. The secrets directory must be owned by the deployment account with mode 700. The password file must be owned by that account with mode 644 inside that private directory: the mounted file must be readable by the non-root application UID. Symlinked secrets are rejected. An existing database with a missing secret fails preflight instead of generating an incompatible password. Do not rotate this file alone: changing it does not change an initialized PostgreSQL role's password.

## Backups and updates

Run `bash deploy/backup.sh` manually, then install a daily root cron entry using `crontab -e`:

```cron
15 2 * * * /bin/bash /opt/apps/TheScheduler-Online/deploy/backup.sh >> /var/log/camp-scheduler-backup.log 2>&1
```

The cron time uses the server timezone. Backups use UTC filenames, are validated PostgreSQL custom dumps, and retain 30 days by default. Configure log rotation for the cron log and monitor backup failures and storage capacity. Keep backups on storage outside the app host; Proxmox snapshots alone are not a database recovery procedure.

For a one-command update, run this with the same server account used for deployment:

```bash
cd /opt/apps/TheScheduler-Online
bash deploy/update.sh
```

The script requires a clean checkout on a branch. It pulls `main` directly from `https://github.com/cobeng13/DCScheduler.git` using `git pull --ff-only`, even if the checkout's origin still points at the original local scheduler repository. It then executes the freshly pulled deployment script to rebuild the image and relaunch the app. Git authentication must already work for that account. Local `deploy/.env`, secrets, and backups remain outside Git. A failed pull or image build stops the update before application downtime.

Deployment builds before downtime, takes a backup, records the previous app image, stops the app, migrates, and replaces only the scheduler container. To deploy an already checked-out reviewed revision without pulling, run `bash deploy/deploy.sh`. App and database logs are bounded to three 10 MB files each. Health checks report readiness; Docker restart policies recover exited containers, but an unhealthy running container needs operator attention.

Deployments require a clean, committed Git checkout so the image identifies reproducible source. The image embeds the commit in the OCI `org.opencontainers.image.revision` label and `APP_REVISION`; `/api/health` returns it as `revision`. After successful deployment/rollback, `deploy/secrets/deployed-revision` records the revision. Inspect the running image directly with `docker inspect --format '{{.Config.Image}}' camp-scheduler` and `docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' IMAGE`. Older images without this metadata report an unknown revision. `APP_REVISION` is supplied by the image build, not a secret or a required `.env` value.

Deploy, rollback, and restore poll `/api/health` automatically with bounded connection/request timeouts and return a nonzero exit code on readiness failure. A readiness failure does not automatically reverse a database migration or restoration; investigate and use the documented recovery procedure. A previously stopped/unhealthy PostgreSQL container fails preflight; diagnose/recover it explicitly before retrying deployment.

If migration fails, leave the app stopped, inspect the failure, and restore the pre-update dump if the schema changed. Do not run database downgrades that discard accounts or audit history. For an app-only rollback after confirming schema compatibility, run `bash deploy/rollback.sh`. It uses the immutable image ID recorded before the update and does not rebuild or migrate the database.

To restore a backup, first close access or announce maintenance, then run:

```bash
bash deploy/restore.sh /absolute/path/scheduler-BACKUP.dump --replace-database
```

This intentionally replaces the scheduler database, takes a safety backup first, and stops the app during restoration. Use the app image corresponding to that backup's schema. A failed restoration leaves the app stopped for investigation. Test restoration in an isolated stack before relying on backups.

## Input and authentication policies

Schedule writes (including batch changes and CSV imports) accept at most 200 characters for program/section/room/faculty labels, 100 for course codes and each time string, 2000 for course descriptions, and 64 for days. Override reasons are limited to 1200 characters. Existing longer records remain readable, but edits must meet the new limits. CSV exports prefix potentially executable spreadsheet cells with an apostrophe; ordinary cells are preserved. Export escaping does not alter stored schedule data.

Request bodies are counted as they stream, including when Content-Length is absent: 1 MiB generally, 384 KiB for settings, 6 MiB for the multipart CSV request (the CSV file itself remains limited to 5 MiB/10,000 rows), and 15 MiB for PNG JSON (the encoded image remains limited to 14 MiB). Settings storage is capped at 256 KiB per submitted payload; personal preferences at 16 KiB. Settings have a maximum nesting depth of 12, 20,000 nodes, 200-character keys, and 4096-character strings. Curriculum accepts at most 100 curricula/2000 total courses, also subject to the byte limits; course text has the corresponding schedule-field limits. Invalid requests return 413 or 422 before persistence/audit. Unknown settings keys count toward these limits too.

Every signed-in user may add shared faculty members. Timetable CSV imports may create missing faculty members and program-owned sections, but only in a program the importing user can edit. Only administrators may create rooms, including through CSV import, and only administrators may rename, merge, or delete either shared catalog. Schedulers must select an existing room or use TBA; ask an administrator to establish missing rooms before importing. Failed imports roll back new faculty/sections and schedule changes atomically. This policy needs no environment switch.

Login throttling uses the normalized account name (10 failures per 15-minute window). Neither arbitrary forwarded headers nor the tunnel peer determine a user's identity or trigger a shared account lockout. Successful login resets that account's counter. Stale LoginAttempt rows older than 24 hours and expired LoginSession rows are deleted on login and by a background task every 15 minutes. Authentication/security audit history is retained. Configure edge-level login abuse protection in Cloudflare if needed for distributed attempts against many account names; use Cloudflare's own visitor identity at the edge, not client-supplied headers in the application.

CSP allows same-origin scripts/API/EventSource, inline styles needed by React/report layouts, and data/blob images needed for PNG generation. It prohibits inline scripts, plugins, framing, external scripts, and external connections. Permissions-Policy disables camera, microphone, geolocation, payment, and USB. Do not add remote scripts (including Cloudflare script injection features such as Rocket Loader/Web Analytics) without reviewing CSP compatibility. Disable API caching and verify SSE through the real tunnel. Downloaded HTML contains escaped user text and static styles; browsers viewing a downloaded local file may not retain its HTTP headers.

No schema migration is introduced by this hardening pass. Existing PostgreSQL volumes, Alembic history, timetable records, and audit history remain in place. Existing oversized settings are not deleted automatically; review and reduce them before saving changes. Room additions must be made by administrators; faculty additions are available to all signed-in accounts.

## Acceptance checks on the actual host

- Verify `/api/health`, login, forced password change, and logout over the HTTPS hostname.
- Use two browsers with different program accounts. Confirm shared viewing, own-program editing, immediate activity updates, and online presence. Disconnect/reconnect one browser and verify the feed catches up.
- Attempt the same room/faculty booking simultaneously from both accounts: only one should save. Check stale edits and stale undo after an admin changes a row.
- Disable an account and reassign a program; check existing sessions and open event streams lose access appropriately.
- Test a CSV with a valid row followed by an invalid/conflicting row; the old timetable must remain intact. Check PNG/CSV and faculty report downloads.
- Restart both containers and verify accounts, assignments, schedules, and history persist. Restore a dump into an isolated database and compare record counts.

Live delivery targets two seconds under normal operation. Test it through your actual Cloudflare Tunnel. Host deployment, tunnel configuration, restart persistence, and restore drills require server access and are separate from local code verification.

## Development

Install `backend/requirements.txt`, then from `backend` run `alembic upgrade head` against a NEW database. Set `COOKIE_SECURE=false` for local HTTP development only, bootstrap an admin, and run `uvicorn app.main:app --port 8000`. Run `npm ci` and `npm run dev` in `frontend`; Vite proxies `/api` to the backend. Never point the new migration at a populated local timetable.

Backend tests: `python -m pytest backend/tests`. Optional PostgreSQL integration tests use a dedicated disposable database configured with `TEST_POSTGRES_URL`; never use a production URL. Frontend verification: `npm test` and `npm run build` in `frontend` with Node 22.18+.

## Shared conflict rules

The **Rules** menu restores Ignore room conflicts and Ignore faculty conflicts. All signed-in users can view these shared switches, but only admins can change them. Both default to off (checks enforced). Turning a switch on permits that resource overlap for all users/programs, including CSV imports and bulk saves. Section overlaps always remain blocked; TBA resources remain unassigned. Turning a switch off reports existing overlaps again without removing bookings. Admin overrides with reasons remain available for checks still enabled.

Changes use the normal CSRF/origin protection, transaction advisory lock, stale-version detection and shared audit/SSE history. `GET /api/rules` is authenticated; `PUT /api/admin/rules` is admin-only and requires the current version plus boolean `ignoreRoom` and `ignoreFaculty`. Old `/api/settings` conflictIgnore values cannot change these rules. Existing AppSettings JSON stores the flags and rules version; no migration or environment variable is required. Other browser sessions refresh the rules and conflict display via SSE without replacing open forms. Per-resource ignore lists are not enabled by this change.
