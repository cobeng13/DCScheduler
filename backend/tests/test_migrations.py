import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from app.models import Base


def test_alembic_cli_imports_app_without_backend_pythonpath(tmp_path):
    """Console scripts do not put the working directory on Python's import path."""
    backend = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    # Keep external test dependency locations, but remove the path that previously
    # masked the missing Alembic prepend_sys_path setting in our test harness.
    env["PYTHONPATH"] = os.pathsep.join(part for part in env.get("PYTHONPATH", "").split(os.pathsep)
        if part and Path(part).resolve() not in {backend, backend.parent})
    database = tmp_path / "cli.db"
    env["DATABASE_URL"] = "sqlite:///" + str(database)
    env.pop("DATABASE_PASSWORD_FILE", None)
    result = subprocess.run([sys.executable, "-c", "from alembic.config import main; main()",
        "-c", str(backend / "alembic.ini"), "upgrade", "head"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            assert "users" in inspect(connection).get_table_names()
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()


def apply_revisions(connection, stop=None):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        for path in sorted((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py")):
            spec = importlib.util.spec_from_file_location("migration_" + path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
            if stop and module.revision == stop:
                break


def test_empty_database_migrates_to_current_model():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        apply_revisions(connection)
        assert "users" in inspect(connection).get_table_names()
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.execute(text("SELECT count(*) FROM schedule_entries")).scalar() == 0


def test_populated_local_database_is_not_discarded():
    import pytest
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        apply_revisions(connection, "0002")
        connection.execute(text("INSERT INTO schedule_entries (program, section, course_code, course_description, units, hours, time_lpu, days, room, faculty) VALUES ('P', 'A', 'C', 'D', 3, 3, 'TBA', 'TBA', 'TBA', 'TBA')"))
        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0003_online.py"
        spec = importlib.util.spec_from_file_location("online_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)), pytest.raises(RuntimeError, match="NEW database"):
            module.upgrade()
        assert connection.execute(text("SELECT count(*) FROM schedule_entries")).scalar() == 1
