"""Agent 注册表 + 状态管理。"""

from __future__ import annotations

import time
import uuid
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import Agent, AuditLog, Site, _utcnow


HEARTBEAT_TIMEOUT_SECONDS = 45   # Agent 超过这个时间没心跳 → 离线
_offline_check_interval = 15      # 后台每 15s 扫一次


def ensure_agent(
    session: Session,
    *,
    site_id: str,
    agent_id: str,
    name: str = "",
    agent_version: str = "",
    platform: str = "",
    os_info: str = "",
) -> Agent:
    """存在则返回，不存在则创建。"""
    agent = session.get(Agent, agent_id)
    if agent is None:
        agent = Agent(
            id=agent_id,
            site_id=site_id,
            name=name or agent_id,
            agent_version=agent_version,
            platform=platform,
            os_info=os_info,
            status="offline",
            registered_at=_utcnow(),
        )
        session.add(agent)
    else:
        # 更新上报字段（不改 name — 用户可能改过）
        if agent_version:
            agent.agent_version = agent_version
        if platform:
            agent.platform = platform
        if os_info:
            agent.os_info = os_info
    session.commit()
    session.refresh(agent)
    return agent


def update_agent(session: Session, agent_id: str, **kwargs) -> Optional[Agent]:
    agent = session.get(Agent, agent_id)
    if agent is None:
        return None
    for k, v in kwargs.items():
        setattr(agent, k, v)
    session.commit()
    session.refresh(agent)
    return agent


def mark_online(session: Session, agent_id: str) -> Optional[Agent]:
    return update_agent(session, agent_id, status="online", last_seen_at=_utcnow())


def record_heartbeat(session: Session, agent_id: str) -> None:
    update_agent(session, agent_id, last_seen_at=_utcnow())


def list_agents(session: Session, site_id: Optional[str] = None,
                status: Optional[str] = None) -> list[Agent]:
    stmt = select(Agent)
    if site_id:
        stmt = stmt.where(Agent.site_id == site_id)
    if status:
        stmt = stmt.where(Agent.status == status)
    return session.execute(stmt.order_by(Agent.registered_at)).scalars().all()


def agent_to_dict(agent: Agent, *, device_count: int = 0) -> dict:
    return {
        "id": agent.id,
        "site_id": agent.site_id,
        "name": agent.name,
        "agent_version": agent.agent_version,
        "platform": agent.platform,
        "os_info": agent.os_info,
        "status": agent.status,
        "last_seen_at": agent.last_seen_at.isoformat() if agent.last_seen_at else None,
        "registered_at": agent.registered_at.isoformat() if agent.registered_at else None,
        "credential_id": agent.credential_id,
        "device_count": device_count,
    }


def check_offline_agents(session: Session) -> list[Agent]:
    """心跳超时的 Agent 标记 offline，返回这些 Agent。"""
    cutoff = _utcnow() - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
    online = list_agents(session, status="online")
    changed: list[Agent] = []
    for a in online:
        if a.last_seen_at is None or a.last_seen_at < cutoff:
            update_agent(session, a.id, status="offline")
            changed.append(a)
    return changed
