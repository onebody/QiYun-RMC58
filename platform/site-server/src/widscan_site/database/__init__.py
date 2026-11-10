"""数据库引擎 + Session。统一 async SQLAlchemy 2.0 + SQLite。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


# 默认 SQLite（同步 engine — V1 用同步足够，AsyncSession 在 Alembic 迁移上反而麻烦）
_engine_lock = threading.Lock()
_engine = None
_session_factory = None


def init_engine(database_url: str = "sqlite:///./data/site.db", echo: bool = False) -> None:
    """初始化全局 engine + sessionmaker。"""
    global _engine, _session_factory
    with _engine_lock:
        if database_url.startswith("sqlite:///./"):
            Path(database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        is_sqlite = database_url.startswith("sqlite")
        connect_args: dict = {}
        if is_sqlite:
            if ":memory:" in database_url and "cache=shared" not in database_url:
                # 让所有进程内连接共享同一份内存 DB（测试用）
                database_url = database_url.replace(":memory:", ":memory:?cache=shared", 1)
            connect_args = {"check_same_thread": False, "uri": ":memory:?cache=shared" in database_url}

        _engine = create_engine(database_url, echo=echo, future=True, connect_args=connect_args)
        _session_factory = sessionmaker(bind=_engine, class_=Session, expire_on_commit=False)


def get_session() -> Session:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory()


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


from contextlib import contextmanager

@contextmanager
def session_scope() -> Iterator[Session]:
    ctx = get_session_factory()
    session = ctx()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
