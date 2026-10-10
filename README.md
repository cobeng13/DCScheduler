# CAMP Online Scheduler

Academic scheduling web app with a FastAPI backend, React frontend, PostgreSQL, program ownership, administrator accounts, and live shared activity. Everyone can view all programs; only assigned schedulers and administrators may edit them.

See [DEPLOYMENT.md](DEPLOYMENT.md) for CAMP Server Docker deployment, Cloudflare Tunnel configuration, account bootstrap, backups, restoration, and local development. Online deployment starts with an empty database. Do not upgrade a populated local timetable database.

Production hardening includes bounded request/settings sizes, shared schema validation for schedule edits and CSV imports, spreadsheet-safe CSV exports, account-based login throttling suitable for a shared tunnel connector, periodic authentication cleanup, and CSP/Permissions-Policy headers. Faculty and Room records are shared: every signed-in user may add faculty members, including through authorized timetable CSV imports; only administrators may add rooms. Renaming, merging, or deleting either shared catalog remains admin-only. Schedulers create sections and import schedules only within their assigned programs.

Run `bash deploy/update.sh` on the configured Linux server for a Git pull, image rebuild, backup, migration, and relaunch. Preflight checks run before changes to running services, and deploy/rollback/restore require a successful readiness check. The image label `org.opencontainers.image.revision` and `/api/health` identify the running Git revision. See [HARDENING.md](HARDENING.md) for review findings and validation results.

## Repository Structure

- `backend/` - FastAPI + SQLAlchemy + Alembic + PostgreSQL + pytest (SQLite for local tests)
- `frontend/` - React + Vite + TypeScript UI

## Backend Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.bootstrap admin
uvicorn app.main:app --reload --port 8000
```

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Then open <http://localhost:5173>.

Set `COOKIE_SECURE=false` for local HTTP development only. Production uses secure cookies and the HTTPS origin configured in `deploy/.env`.

## Notes

- Use **File > Load Curricula** for curriculum catalogs and **Import Timetable CSV** for scheduled classes. Multiple curricula and per-section curriculum overrides are supported. See [CURRICULUM_IMPORTS.md](CURRICULUM_IMPORTS.md) for accepted headers, BSMLS file compatibility, and assignment instructions.

- Production data is stored in a dedicated persistent PostgreSQL volume. A NEW SQLite database at `backend/scheduler.db` can be used for local development.
- The **File** menu provides program-scoped CSV import/download and an admin clear shortcut. In **Administration → Database and timetable**, admins can download a full application backup, upload it to replace the database, or clear classes across all programs. Restore and clear require the current admin password. See [DATABASE_BACKUPS.md](DATABASE_BACKUPS.md).
- CSV exports and timetable PNG export are available in the **Export** group.
- Conflicts highlight in red in both the grid and text view.
- Saves block cross-program room/faculty overlaps by default. **Rules** shows shared Ignore room/faculty conflicts switches; only admins can change them. Expand **Room exceptions** or **Faculty exceptions** to bypass checks for selected records while leaving global ignore switches off. Changes apply to every user/program and are audited and delivered live. When a check remains enabled, admin overrides require a recorded reason. Section overlaps cannot be ignored or overridden.

Use **Hide live updates** in the top bar to hide the right activity panel and expand the scheduler; **Show live updates** restores it. Schedules continue updating live while the panel is hidden. The choice is remembered per account in this browser.

- **Split View** shows independently selectable section, faculty, or room schedules. Room panes include every program; select a class's program before editing its bookings. The divider can be dragged or resized with arrow keys; double-click or Home resets it. A blue border marks the active pane.
- Split View uses a popup class editor. Dragging across panes changes only the dragged meeting's section, faculty, or room to the destination selection; the preview shows the assignment and time. Conflicts block the move, and Undo restores the complete move.
- Vertical scrolling is linked by time by default, even at different zoom levels. Horizontal scrolling remains independent. Below 900px, or when the activity panel leaves insufficient space, switch between the saved left and right panes using the pane buttons.
- Current timetable PNG export uses the active pane; mass export keeps both displayed selections intact. Pane settings are remembered per account and program on this browser. Text View temporarily suspends Split View.
- Ctrl/Cmd+C copies a focused class block, Ctrl/Cmd+V pastes into the active section view, and Ctrl/Cmd+Z undoes the latest action. These shortcuts leave text fields and popup drafts alone.
- Meeting moves and reverts commit atomically. Undo references authoritative snapshots in Online activity history, checks ownership and current program permissions, and rejects later changes or new conflicts.

## Verification

Run `python -m pytest tests -q` from `backend/`. Tests use a temporary database.

From `frontend/`, run `npm run build`, `npx playwright install chromium`, then `npm test` and `npm run test:browser`. Browser tests use isolated synthetic schedule data and do not modify the working database. To use an installed Chrome instead, set `SCHEDULER_TEST_BROWSER=chrome` before running the tests.
