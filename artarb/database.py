import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from artarb.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    # QueuePool is the default for non-SQLite; listed explicitly for clarity.
    pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    # Recycle connections before the server's idle timeout kills them.
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
    # Issue a lightweight SELECT 1 before handing a connection to the caller.
    pool_pre_ping=True,
)

# expire_on_commit=False keeps ORM objects usable after session.commit().
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Transactional context manager for scripts and background tasks.

    Commits on clean exit, rolls back on any exception.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """Generator-style dependency for frameworks like FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> bool:
    """Return True if the database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
