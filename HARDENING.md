# Production hardening review

Reviewed against `cobeng13/DCScheduler` main at `b398a0947546c66348737c76a5e9f15a81d642a7`. No deployment was performed. The FastAPI/React/PostgreSQL application, two-container topology, persistent database volume, Alembic migrations, ownership/version checks, transactional conflict locking, SSE/history, and backup/restore architecture are retained. No frontend files were changed.

## Findings and implemented changes

| Finding | Resolution |
| --- | --- |
| Schedule text had no useful maximum; CSV used the same unbounded schema. | Write schemas now bound labels (200), codes/time (100), descriptions (2000), days (64); overrides allow 1200 characters. Batch and CSV use the same checks. Null characters and overlong normalized resource/user names fail validation rather than PostgreSQL writes. Legacy longer schedule rows remain readable. |
| Settings/curriculum JSON could expand PostgreSQL and audit records without bounds, and malformed nested shapes could raise server errors. | Byte, depth, node, key/string, curricula/course counts, personal-preference, and course-text limits precede persistence/audit. Malformed inputs return 422; excessive byte sizes return 413. |
| PNG size checking happened only after the whole JSON body was parsed. | ASGI middleware caps declared and streamed request sizes before downstream body assembly, including chunked requests and multipart CSV. PNG type/encoded-size checks remain. No arbitrary-size body is buffered by the limiter. |
| CSV quoting did not prevent spreadsheet formulas. | Export cells with formula/control prefixes (including whitespace before formulas) receive a leading apostrophe. Stored data and ordinary exported cells remain unchanged. |
| Shared tunnel-peer failures could throttle unrelated users. | Login throttling is per normalized account, not peer or forwarded headers. Ten failures per 15-minute window remain enforced. Login origin checks now also cover the login endpoint. |
| Login attempts were never removed; expired sessions were only removed on successful login. | Cleanup runs on login and every 15 minutes while the app is running; attempts older than 24 hours and expired sessions are deleted. Security audit records remain. |
| Security headers lacked CSP and Permissions-Policy. | Same-origin scripts/connections, inline styles for existing UI/report layouts, and data/blob images are permitted. Inline scripts, plugins, framing and external connections are restricted; sensitive browser capabilities are disabled. Existing CSRF/cookie/origin controls remain. |
| Shared-resource creation permissions differed from modification permissions, including CSV auto-creation. | Faculty/Room creation, renaming, merging and deletion are admin-only. Schedulers can use existing resources/TBA and create sections only in assigned programs. CSV rollback preserves existing schedules if a scheduler attempts to introduce an unknown shared resource. |
| Deployment preflight covered little beyond an initial port check. | Added Docker/daemon, port/current mapping, host bind address, disk, writable backup directory, secret ownership/modes, and existing PostgreSQL health checks before running-service changes. |
| Running revision was not identifiable. | Build embeds Git SHA in OCI label and APP_REVISION; health returns it. Successful deploy/rollback records it in the private secrets directory. Deployment requires a clean committed checkout. |
| Restore/rollback could announce success before app readiness. | All three operations perform bounded readiness polling and exit nonzero on failure. No automatic destructive database reversal is attempted. |

Non-root UID 10001, read-only app filesystem, dropped capabilities, no-new-privileges, bounded container logs, restart policy, private Docker network and unpublished PostgreSQL remain intact. Docker build context now excludes all local `.test-deps*` directories.

## Files changed

- API/auth/input/export: `backend/app/auth.py`, `main.py`, `online_service.py`, `schemas.py`, `reports.py`, new `limits.py`.
- Tests: updated `backend/tests/test_api.py`, `test_events.py`, `test_postgres.py`; new `test_hardening.py`, `test_deploy_scripts.py`.
- Deployment: `Dockerfile`, `.dockerignore`, `deploy/.env.example`, `common.sh`, `deploy.sh`, `update.sh`, `rollback.sh`, `restore.sh`, new `preflight.sh`.
- Documentation: `README.md`, `DEPLOYMENT.md`, this report.

## Migrations and environment

No new database schema revision or destructive data migration is needed. Existing migrations still run explicitly during deployment. Existing volumes, accounts, ownership, schedules and audit history are preserved. Existing oversized data is not silently truncated; oversized schedule fields must be reduced before an edit is accepted, and administrators should review oversized legacy settings before editing them.

Required deployment values remain `PUBLIC_ORIGIN` (real HTTPS origin) and `BACKUP_DIR` (dedicated absolute writable path, preferably off-host storage). `APP_PORT` remains 8004 by default, `BIND_ADDRESS` remains 127.0.0.1, and image/retention settings remain unchanged. New optional `MIN_FREE_DISK_MB` defaults to 4096 on project, Docker and backup filesystems. `APP_REVISION` is automatically embedded by deployment; do not configure it manually to claim a different revision. Request/storage/auth limits are fixed in code; shared catalog permissions have no permissive environment switch.

## Validation

Commands run from the repository root unless otherwise noted. On this Windows workstation, `python` below is the bundled Python executable at `C:/Users/aaron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`; dependencies were already installed in the ignored `.test-deps-current` directory. The exact PowerShell setup used was:

```powershell
$env:PYTHONPATH="$PWD/.test-deps-current;$PWD/backend"
& 'C:/Users/aaron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m pytest backend/tests -q
& 'C:/Users/aaron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m pytest backend/tests/test_postgres.py -q -rs
& 'C:/Users/aaron/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' -m pytest backend/tests/test_deploy_scripts.py -q
& 'C:/Program Files/Git/bin/bash.exe' -c 'for script in deploy/*.sh; do bash -n "$script" || exit; done'
git diff --check
docker build -t camp-scheduler:hardening-test .
```

From `frontend`:

```bash
npm test
npm run build
```

| Check | Result |
| --- | --- |
| Complete backend suite | 43 passed, 4 PostgreSQL cases skipped (110.66 seconds). After adding Docker NAT-port detection, the deployment tests were rerun separately: 9 passed. |
| PostgreSQL integration suite | 4 skipped: TEST_POSTGRES_URL is unset; no local PostgreSQL service available. Tests cover independent simultaneous room, faculty and section bookings plus same-version updates. CI provides PostgreSQL 16 for these tests. |
| Frontend tests | 8 passed. |
| Production frontend build | Passed TypeScript and Vite build. |
| Bash syntax | All deployment scripts passed. |
| Deployment behavior | 9 passed. Mocked-host tests cover Docker failure, occupied ports (including Docker NAT mappings invisible to ss), the scheduler's own current mapping, invalid address, disk shortage, invalid/uncreatable backup location, secret modes, unhealthy/missing database secret, readiness failure and successful preflight/readiness. No production containers were touched. |
| Git whitespace | Passed; Git reports only expected Windows line-ending normalization warnings. |
| Docker image build | Attempted command failed because Docker is not installed/on PATH here. Image build and runtime validation remain required before production. |

The backend suite emits an existing Starlette TestClient deprecation warning about httpx/httpx2. This does not fail tests.

## Manual acceptance before production

1. Commit/review this change, run CI's real PostgreSQL races and Docker build, and inspect the build result before manual deployment. These checks are not claimed as locally verified.
2. Configure hostname, tunnel destination/connector placement, backup storage, required `.env` values and secret permissions. Confirm free port 8004/current mapping and sufficient disk/memory. Install required Linux utilities; run preflight with the deployment account.
3. Verify HTTPS secure cookies, same-origin login/API, disabled/reset/logout sessions, CSV/PNG/HTML downloads, and two-browser SSE/reconnect/presence through the actual Cloudflare Tunnel. API caching must be disabled; review any Cloudflare features that inject external scripts against CSP. Configure Cloudflare edge login abuse protection if needed without teaching the app to trust arbitrary proxy headers.
4. Have an administrator establish Faculty/Room records before scheduler imports. Review oversized legacy settings/fields; no automatic cleanup of business data is performed.
5. Test PostgreSQL restart persistence, off-host backup storage, and a restore drill in an isolated stack. Check /api/health revision after deploy/rollback/restore. Readiness failures require investigation; they do not reverse schema/data changes.
