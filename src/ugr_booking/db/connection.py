"""Database connection and session factory."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from ugr_booking.config import DATABASE_URL
from ugr_booking.models.orm import Base


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _make_engine(url: str | None = None):
    db_url = url or DATABASE_URL
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    engine = create_engine(db_url, connect_args=connect_args, echo=False)

    # Enable WAL mode and foreign keys for SQLite
    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def init_db(url: str | None = None) -> None:
    """Create all tables (idempotent)."""
    target_engine = _make_engine(url) if url else engine
    Base.metadata.create_all(bind=target_engine)


def get_session() -> Session:
    """Return a new session.  Caller is responsible for closing it."""
    return SessionLocal()
