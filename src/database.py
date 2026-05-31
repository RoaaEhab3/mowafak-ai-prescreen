"""Database session management.

Provides a SQLAlchemy engine + session factory backed by SQLite.
Exposes get_db() as a FastAPI dependency.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

from src.settings import settings


def _db_url() -> str:
    db_path = Path(settings.sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


engine = create_engine(
    _db_url(),
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def create_all_tables() -> None:
    """Create all tables (run once at startup)."""
    # import models_db to register ORM classes before create_all
    import src.models_db  # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a DB session and ensures it's closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
