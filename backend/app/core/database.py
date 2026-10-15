from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings

settings = get_settings()
# Fail fast (instead of hanging on the OS TCP timeout) when PostgreSQL is down.
_connect_args = {"connect_timeout": 5} if settings.database_url.startswith("postgresql") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)


def get_db():
    """Provide a database session to future API routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
