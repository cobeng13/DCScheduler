# CAMP Online Scheduler

Academic scheduling web app with a FastAPI backend, React frontend, PostgreSQL, program ownership, administrator accounts, and live shared activity. Everyone can view all programs; only assigned schedulers and administrators may edit them.

See [DEPLOYMENT.md](DEPLOYMENT.md) for CAMP Server Docker deployment, Cloudflare Tunnel configuration, account bootstrap, backups, restoration, and local development. Online deployment starts with an empty database. Do not upgrade a populated local timetable database.

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

- Production data is stored in a dedicated persistent PostgreSQL volume. A NEW SQLite database at `backend/scheduler.db` can be used for local development.
- The **File** menu provides program-scoped clearing/import and CSV downloads. Server backups replace database-file Open/Save.
- CSV exports and timetable PNG export are available in the **Export** group.
- Conflicts highlight in red in both the grid and text view.
- Saves block cross-program room/faculty overlaps; admin overrides require a recorded reason. Section overlaps cannot be overridden.
