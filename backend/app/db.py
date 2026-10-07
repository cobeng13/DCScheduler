from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_PATH = Path(
    os.environ.get("SCHEDULER_DB_PATH", Path(__file__).resolve().parents[1] / "scheduler.db")
)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")
if os.environ.get("DATABASE_PASSWORD_FILE"):
    password = Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
    DATABASE_URL = f"postgresql+psycopg://scheduler:{quote(password, safe='')}@{os.getenv('DATABASE_HOST', 'scheduler-db')}:5432/scheduler"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass
