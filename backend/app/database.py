"""Database + cache. Postgres via DATABASE_URL in prod; SQLite file for local
dev only (PRD Sec 9/10). Redis for slot holds / live call state, with an
in-memory fallback so local runs still work when Redis is absent.
"""
import threading
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = settings.database_url
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_pre_ping"] = True
            kwargs["pool_recycle"] = 300
        _engine = create_engine(url, **kwargs)
    return _engine


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401  (registers tables)
    Base.metadata.create_all(bind=get_engine())


class KVStore:
    """Tiny key/value store with TTL: Redis when available, else in-memory."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self._mem = {}
        if settings.redis_url:
            try:
                import redis as redis_lib
                self._client = redis_lib.Redis.from_url(
                    settings.redis_url, decode_responses=True, socket_connect_timeout=2
                )
                self._client.ping()
            except Exception:
                self._client = None

    def set(self, key: str, value: str, ttl_seconds: int = 0) -> None:
        if self._client:
            self._client.set(key, value, ex=ttl_seconds or None)
            return
        with self._lock:
            self._mem[key] = (value, time.time() + ttl_seconds if ttl_seconds else None)

    def get(self, key: str):
        if self._client:
            return self._client.get(key)
        with self._lock:
            item = self._mem.get(key)
            if not item:
                return None
            value, expires = item
            if expires and time.time() > expires:
                del self._mem[key]
                return None
            return value

    def delete(self, key: str) -> None:
        if self._client:
            self._client.delete(key)
            return
        with self._lock:
            self._mem.pop(key, None)


kv_store = KVStore()
