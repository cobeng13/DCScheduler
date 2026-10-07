import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from app.models import Base


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
