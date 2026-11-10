"""Site Server pytest fixtures（V1.1.2.1）。

核心 fixture：
- session: 共享内存 SQLite Session（所有 fixture 共享同一份 DB）
- cfg: ServerConfig（可切换 allow_url_credential）
- app: FastAPI create_app(cfg)
- client: httpx TestClient
- paired_agent_factory: 生成已配对 Agent + credential 的 helper
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from widscan_site.config import ServerConfig, WebSocketConfig
from widscan_site.database import Base, init_engine
from widscan_site.site import bootstrap_default_site
from widscan_site.auth import create_pairing_code, pair_with_code, _sha256


# --- 共享内存 DB Engine — 所有测试共享一份同进程 SQLite：memory DB --------

@pytest.fixture(scope="function")
def session():
    """每次测试函数全新 DB（但 engine 共享同一 memory 实例）。"""
    engine = create_engine(
        "sqlite:///:memory:?cache=shared",
        connect_args={"check_same_thread": False, "uri": True},
        future=True,
    )
    # 每个测试清表再建表
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    sess = SessionFactory()
    # Bootstrap default site
    bootstrap_default_site(sess, site_id="site-001", name="Test Site", timezone="UTC")
    yield sess
    sess.close()
    # 清表让下个测试拿到干净状态
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def allow_url_cfg() -> ServerConfig:
    """显式允许 URL credential（本地兼容测试用）。"""
    return ServerConfig(
        websocket=WebSocketConfig(allow_url_credential=True, auth_timeout_seconds=5.0),
    )


@pytest.fixture(scope="function")
def secure_cfg() -> ServerConfig:
    """生产安全默认：禁止 URL credential。"""
    return ServerConfig(
        websocket=WebSocketConfig(allow_url_credential=False, auth_timeout_seconds=5.0),
    )


@pytest.fixture(scope="function")
def paired_agent_factory(session: Session):
    """helper: 生成已配对的 Agent + plain credential。

    Returns: callable(agent_id=None, platform="darwin") -> dict(agent_id, credential, agent)
    """
    from widscan_site.auth import create_pairing_code as _cpc
    from widscan_site.auth import pair_with_code as _pwc

    def _make(agent_id: Optional[str] = None, platform: str = "darwin",
              name: Optional[str] = None) -> dict:
        agent_id = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
        plain, cred = _cpc(session, site_id="site-001")
        final = _pwc(session, site_id="site-001", pairing_code=cred.pairing_code,
                     agent_info={
                         "agent_id": agent_id,
                         "platform": platform,
                         "agent_version": "0.1.0-test",
                         "name": name or f"TestAgent-{agent_id[:8]}",
                     })
        assert final is not None
        from widscan_site.agents import ensure_agent
        agent = session.get(ensure_agent(session, site_id="site-001", agent_id=agent_id).__class__, agent_id)
        # ensure_agent 不返回当前 session 里的对象？手动 get
        from widscan_site.database.models import Agent
        agent = session.get(Agent, agent_id)
        return {"agent_id": agent_id, "credential": plain, "credential_hash": _sha256(plain), "agent": agent}

    return _make


# --- TestClient fixture（需要配合 cfg + session engine）-----------------

@pytest.fixture(scope="function")
def make_app(session: Session):
    """helper: 用指定 cfg 创建 FastAPI app。"""
    def _build(cfg: ServerConfig):
        from widscan_site.database import get_session_factory
        # init_engine 用 session 里的 engine URL
        import os
        from widscan_site.database import get_engine
        # 强制让 main.py 的 create_all 跟 session 用同一个 engine
        from widscan_site.main import create_app as _ca
        app = _ca(cfg)
        # 让 app.state.cfg 已经有（create_app lifespan 里会设）
        # 但 lifespan 是 async context manager — TestClient 会触发
        return app
    return _build
