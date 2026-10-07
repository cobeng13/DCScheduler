# CAMP Server deployment

The scheduler is a separate two-container stack on `apps-server`. It starts empty and does not change existing apps on ports 8001–8003. Run commands from `/opt/apps/TheScheduler-Online`.

## Initial setup

1. Clone this repository to `/opt/apps/TheScheduler-Online`.
2. Copy `deploy/.env.example` to `deploy/.env`; set the real HTTPS `PUBLIC_ORIGIN`, available `APP_PORT` (default 8004), and `BACKUP_DIR` on your backup storage. Keep this file private and outside Git. `BIND_ADDRESS=127.0.0.1` is appropriate for a tunnel connector on this host. If the connector runs on a different Proxmox guest, use the apps-server LAN IP and restrict access to the connector with the server firewall.
3. Run `bash deploy/deploy.sh`. This builds the UI, creates a private Docker network and persistent PostgreSQL volume, runs Alembic migrations, and starts the app. PostgreSQL has no published port. The application uses UID 10001 and a read-only filesystem.
4. Run `docker exec -it camp-scheduler python -m app.bootstrap YOUR_USERNAME`. Enter the password interactively; it never appears in shell history. Sign in and change it.
5. In the existing Cloudflare Tunnel, add the scheduler hostname with service `http://APPS_SERVER_REACHABLE_ADDRESS:8004` (or your configured port). A containerized connector's `localhost` refers to its own container; use the host's reachable address or attach that connector to the `camp-scheduler` Docker network and target `http://camp-scheduler:8000`.
6. Disable Cloudflare caching for `/api/*`. Do not enable response buffering for `/api/events`; it uses `text/event-stream`, heartbeat comments, and `Cache-Control: no-cache, no-transform`. Public HTTPS is required for the secure session cookie. Tunnel access does not replace the app's account permissions.
7. Create two scheduler accounts and two programs in Administration, then assign one account to each program. Pilot with these accounts before general access.

The app port is checked before first deployment. Confirm it is still free and inspect current container names, tunnel routes, disk space, and available memory before running the scripts. Keep the generated `deploy/secrets/db_password` with your recovery materials. Do not rotate this file alone: changing it does not change an initialized PostgreSQL role's password.

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

The script requires a clean checkout and a branch with a configured upstream. It runs `git pull --ff-only`, then executes the freshly pulled deployment script to rebuild the image and relaunch the app. Git authentication must already work for that account. Local `deploy/.env`, secrets, and backups remain outside Git. A failed pull or image build stops the update before application downtime.

Deployment builds before downtime, takes a backup, records the previous app image, stops the app, migrates, and replaces only the scheduler container. To deploy an already checked-out reviewed revision without pulling, run `bash deploy/deploy.sh`. App and database logs are bounded to three 10 MB files each. Health checks report readiness; Docker restart policies recover exited containers, but an unhealthy running container needs operator attention.

If migration fails, leave the app stopped, inspect the failure, and restore the pre-update dump if the schema changed. Do not run database downgrades that discard accounts or audit history. For an app-only rollback after confirming schema compatibility, run `bash deploy/rollback.sh`. It uses the immutable image ID recorded before the update and does not rebuild or migrate the database.

To restore a backup, first close access or announce maintenance, then run:

```bash
bash deploy/restore.sh /absolute/path/scheduler-BACKUP.dump --replace-database
```

This intentionally replaces the scheduler database, takes a safety backup first, and stops the app during restoration. Use the app image corresponding to that backup's schema. A failed restoration leaves the app stopped for investigation. Test restoration in an isolated stack before relying on backups.

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
