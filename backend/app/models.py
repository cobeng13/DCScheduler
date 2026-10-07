from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from .db import Base


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False, index=True)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    program = Column(String, nullable=False)
    section = Column(String, nullable=False)
    course_code = Column(String, nullable=False)
    course_description = Column(String, nullable=False)
    units = Column(Float, nullable=False)
    hours = Column(Float, nullable=False)
    time_lpu = Column(String, nullable=False)
    time_24 = Column(String, nullable=True)
    days = Column(String, nullable=False)
    room = Column(String, nullable=False)
    faculty = Column(String, nullable=False)
    start_minutes = Column(Integer, nullable=True)
    end_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("program_id", "normalized_name"),)

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String(200), nullable=False)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=False)
    version = Column(Integer, nullable=False, default=1)


class Faculty(Base):
    __tablename__ = "faculty"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    normalized_name = Column(String(200), unique=True, nullable=False)
    version = Column(Integer, nullable=False, default=1)


class Room(Base):
    __tablename__ = "rooms"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    normalized_name = Column(String(200), unique=True, nullable=False)
    version = Column(Integer, nullable=False, default=1)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    settings_json = Column(Text, nullable=False, default="{}")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    disabled = Column(Boolean, nullable=False, default=False)
    must_change_password = Column(Boolean, nullable=False, default=True)
    preferences_json = Column(Text, nullable=False, default="{}")


class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), unique=True, nullable=False)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    settings_json = Column(Text, nullable=False, default="{}")
    version = Column(Integer, nullable=False, default=1)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    csrf_token = Column(String(100), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    key = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False, default=0)
    window_start = Column(DateTime(timezone=True), nullable=False)


class Activity(Base):
    __tablename__ = "activity"
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor = Column(String(100), nullable=False)
    program_id = Column(Integer, ForeignKey("programs.id"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(Integer, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    security = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
