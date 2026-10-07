"""Empty online database schema. Local timetable migration is intentionally excluded."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM schedule_entries")).scalar():
        raise RuntimeError("Online deployment must start empty. Use a NEW database; do not upgrade your local timetable.")
    for name in ("schedule_entries", "sections", "faculty", "rooms"):
        op.drop_table(name)
    op.create_table("users", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False), sa.Column("disabled", sa.Boolean, nullable=False),
        sa.Column("must_change_password", sa.Boolean, nullable=False), sa.Column("preferences_json", sa.Text, nullable=False))
    op.create_table("programs", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), unique=True, nullable=False),
        sa.Column("assigned_user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("settings_json", sa.Text, nullable=False), sa.Column("version", sa.Integer, nullable=False))
    op.create_table("sections", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False), sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.UniqueConstraint("program_id", "normalized_name"))
    for name in ("faculty", "rooms"):
        op.create_table(name, sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String, unique=True, nullable=False),
            sa.Column("normalized_name", sa.String(200), unique=True, nullable=False), sa.Column("version", sa.Integer, nullable=False))
    op.create_table("schedule_entries", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("section_id", sa.Integer, sa.ForeignKey("sections.id"), nullable=False),
        sa.Column("room_id", sa.Integer, sa.ForeignKey("rooms.id")), sa.Column("faculty_id", sa.Integer, sa.ForeignKey("faculty.id")),
        *[sa.Column(name, sa.String, nullable=False) for name in ("program", "section", "course_code", "course_description", "time_lpu", "days", "room", "faculty")],
        sa.Column("time_24", sa.String), sa.Column("units", sa.Float, nullable=False), sa.Column("hours", sa.Float, nullable=False),
        sa.Column("start_minutes", sa.Integer), sa.Column("end_minutes", sa.Integer), sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_schedule_entries_id", "schedule_entries", ["id"])
    op.create_index("ix_schedule_entries_program_id", "schedule_entries", ["program_id"])
    op.create_table("login_sessions", sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False), sa.Column("csrf_token", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table("login_attempts", sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer, nullable=False), sa.Column("window_start", sa.DateTime(timezone=True), nullable=False))
    op.create_table("activity", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id")), sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("program_id", sa.Integer, sa.ForeignKey("programs.id")), sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False), sa.Column("entity_id", sa.Integer),
        sa.Column("before_json", sa.Text), sa.Column("after_json", sa.Text), sa.Column("reason", sa.Text),
        sa.Column("security", sa.Boolean, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_activity_program_id", "activity", ["program_id"])
    op.create_table("app_settings", sa.Column("id", sa.Integer, primary_key=True), sa.Column("settings_json", sa.Text, nullable=False))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup rather than discarding online accounts and audit history.")
