"""Exercise preflight/readiness with fake host commands; never run Docker."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"


def shell(code, *args, env=None):
    if not BASH or not Path(BASH).exists():
        pytest.skip("Bash is unavailable")
    return subprocess.run([BASH, "-c", code, "test", *map(str, args)], env=env, capture_output=True, text=True, timeout=15)


def test_all_deployment_scripts_have_valid_syntax():
    for script in (ROOT / "deploy").glob("*.sh"):
        result = shell('bash -n "$1"', script)
        assert result.returncode == 0, result.stderr


@pytest.fixture
def host(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("common.sh", "preflight.sh"):
        shutil.copyfile(ROOT / "deploy" / name, deploy / name)
    # pwd resolves native Windows paths to Git Bash's POSIX spelling too.
    resolved = shell('cd "$1" && pwd', tmp_path)
    assert resolved.returncode == 0, resolved.stderr
    posix = resolved.stdout.strip()
    (deploy / ".env").write_text(f'PUBLIC_ORIGIN=https://scheduler.camp.edu\nBACKUP_DIR="{posix}/backups"\nMIN_FREE_DISK_MB=4096\n')
    mocks = tmp_path / "bin"
    mocks.mkdir()
    commands = {
        "docker": '''case "$*" in
info) [[ "${CASE:-}" != daemon ]] ;;
"info --format {{.DockerRootDir}}") printf '%s\\n' "$MOCK_ROOT" ;;
"ps --format {{.Names}}") if [[ "${CASE:-}" == dockerport ]]; then echo other-app; fi ;;
"port other-app") echo "8000/tcp -> 0.0.0.0:8004" ;;
"inspect --format={{.State.Running}} camp-scheduler") [[ "${CASE:-}" == ownport ]] && echo true ;;
"port camp-scheduler 8000/tcp") echo "127.0.0.1:8004" ;;
"container inspect scheduler-db") [[ "${CASE:-}" == database || "${CASE:-}" == secret ]] ;;
"inspect --format={{.State.Health.Status}} scheduler-db") echo unhealthy ;;
*) exit 1 ;;
esac''',
        "ip": 'echo "1: lo inet 127.0.0.1/8 scope host lo"',
        "ss": 'if [[ "${CASE:-}" == port || "${CASE:-}" == ownport ]]; then echo "LISTEN 0 10 127.0.0.1:8004"; fi',
        "df": 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"; if [[ "${CASE:-}" == disk ]]; then echo "disk 1000 999 1 99% /"; else echo "disk 999999999 0 999999999 0% /"; fi',
        "curl": '[[ "${CASE:-}" != readiness ]]',
        "stat": '''case "$1" in
-c) case "$2" in
%u) id -u ;;
%a) if [[ "${CASE:-}" == permissions ]]; then echo 777; elif [[ "$3" == */db_password ]]; then echo 644; else echo 700; fi ;;
esac ;;
esac''',
    }
    for name, body in commands.items():
        script = mocks / name
        script.write_text("#!/usr/bin/env bash\n" + body + "\n", newline="\n")
        script.chmod(0o755)
    env = {**os.environ, "MOCK_ROOT": posix}
    def run(case="", code='bash "$2/preflight.sh"'):
        # Only fixture paths are converted; production scripts remain Linux Bash.
        prefix = 'export PATH="$1:$PATH"; '
        if os.name == "nt":
            prefix = 'export PATH="$(cygpath -u "$1"):$PATH"; '
        return shell(prefix + code, mocks, deploy, env={**env, "CASE": case})
    return run, deploy


@pytest.mark.parametrize("case,message", [("daemon", "Docker daemon"), ("port", "Port 8004"), ("dockerport", "another container"),
                                          ("disk", "free at"), ("database", "secret is missing")])
def test_preflight_stops_on_unusable_host(host, case, message):
    run, _ = host
    result = run(case)
    assert result.returncode != 0
    assert message in result.stderr, result.stderr


def test_preflight_success_and_invalid_bind_backup(host):
    run, deploy = host
    result = run()
    assert result.returncode == 0, result.stderr
    result = run("ownport")
    assert result.returncode == 0, result.stderr
    with (deploy / ".env").open("a") as file:
        file.write("BIND_ADDRESS=192.0.2.1\n")
    assert "not assigned" in run().stderr
    with (deploy / ".env").open("a") as file:
        file.write("BIND_ADDRESS=127.0.0.1\nBACKUP_DIR=/\n")
    assert "dedicated absolute" in run().stderr


def test_readiness_failure_never_reports_success(host):
    run, _ = host
    code = 'source "$2/common.sh"; sleep() { :; }; check_readiness'
    success = run(code=code)
    assert success.returncode == 0 and "passed" in success.stdout
    failed = run("readiness", code)
    assert failed.returncode != 0 and "Readiness failed" in failed.stderr
    assert "passed" not in failed.stdout


def test_preflight_secret_permissions_database_health_and_backup_creation(host):
    run, deploy = host
    secrets = deploy / "secrets"
    secrets.mkdir()
    (secrets / "db_password").write_text("test-only-secret")
    result = run("permissions")
    assert result.returncode != 0 and "mode 700" in result.stderr
    result = run("database")
    assert result.returncode != 0 and "not healthy" in result.stderr
    (deploy.parent / "backups").rmdir()
    (deploy.parent / "backups").write_text("a file cannot be used as a backup directory")
    result = run()
    assert result.returncode != 0 and "Cannot create BACKUP_DIR" in result.stderr
