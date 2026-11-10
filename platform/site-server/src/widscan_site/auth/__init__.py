"""配对码 + Agent 凭证体系 — SHA256 hash，一次性 code。"""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ..database.models import AgentCredential, AuditLog, _utcnow


PAIRING_ALPHABET = string.ascii_uppercase + string.digits


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_pairing_code() -> str:
    """8 位大写字母+数字。"""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))


def generate_credential() -> str:
    """48 字节随机 base64url — 持久化在 Agent 侧。"""
    import base64
    raw = secrets.token_bytes(48)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- 审计日志辅助 --------------------------------------------------------

def _audit(session: Session, *, site_id: str, actor: str, action: str,
           target_type: str = "", target_id: str = "", details: Optional[dict] = None) -> None:
    session.add(AuditLog(
        site_id=site_id,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
    ))


# --- 核心 API ------------------------------------------------------------


def create_pairing_code(
    session: Session,
    site_id: str,
    ttl_seconds: int = 300,
    max_active: int = 5,
) -> tuple[str, AgentCredential]:
    """
    生成一次性配对码 + 对应的 AgentCredential（还没绑定 agent_id）。
    配对码过期 = AgentCredential 自动作废逻辑由应用层处理。
    """
    # 先清过期
    now = _utcnow()
    expired = session.execute(
        select(AgentCredential).where(
            AgentCredential.pairing_code.isnot(None),
            AgentCredential.pairing_code_expires_at < now,
        )
    ).scalars().all()
    for c in expired:
        session.delete(c)

    # 限制活跃数量
    active = session.execute(
        select(AgentCredential).where(
            AgentCredential.pairing_code.isnot(None),
            AgentCredential.pairing_code_expires_at >= now,
        )
    ).scalars().all()
    if len(active) >= max_active:
        # 删最老的再继续
        active.sort(key=lambda c: c.pairing_code_expires_at)
        session.delete(active[0])
        session.flush()

    code = generate_pairing_code()
    plain = generate_credential()
    cred = AgentCredential(
        id=str(uuid.uuid4()),
        credential_hash=_sha256(plain),
        pairing_code=code,
        pairing_code_expires_at=now + timedelta(seconds=ttl_seconds),
        issued_at=now,
        expires_at=now + timedelta(hours=720),
    )
    session.add(cred)
    _audit(session, site_id=site_id, actor="system",
           action="agent.pairing_code_created",
           target_type="agent_credential", target_id=cred.id,
           details={"ttl_seconds": ttl_seconds})
    session.commit()
    return plain, cred


def pair_with_code(
    session: Session,
    site_id: str,
    pairing_code: str,
    agent_info: dict,
) -> Optional[AgentCredential]:
    """
    Agent 带 pairing_code 来换正式 credential。code 一次性。
    返回绑定 agent_id 并清除 pairing_code 的 AgentCredential。
    失败 → 返回 None（code 无效/过期/已使用）。
    """
    code = pairing_code.strip().upper()
    now = _utcnow()
    cred = session.execute(
        select(AgentCredential).where(AgentCredential.pairing_code == code)
    ).scalar_one_or_none()
    if cred is None:
        return None
    if cred.pairing_code_expires_at < now:
        session.delete(cred)
        session.commit()
        return None
    if cred.revoked_at is not None:
        return None
    if cred.agent_id is not None:
        # 已用过 — 作废
        cred.pairing_code = None
        cred.pairing_code_expires_at = None
        cred.revoked_at = now
        cred.revoke_reason = "pairing_code reused"
        session.commit()
        return None

    # 绑定 agent_id
    from ..agents import ensure_agent, update_agent
    agent = ensure_agent(
        session, site_id=site_id,
        agent_id=agent_info.get("agent_id") or str(uuid.uuid4()),
        name=agent_info.get("name", f"Agent-{agent_info.get('platform', 'unknown')}"),
        agent_version=agent_info.get("agent_version", ""),
        platform=agent_info.get("platform", ""),
        os_info=agent_info.get("os_info", ""),
    )
    cred.agent_id = agent.id
    cred.pairing_code = None
    cred.pairing_code_expires_at = None
    cred.expires_at = now + timedelta(hours=720)
    session.commit()
    session.refresh(cred)

    # 更新 Agent 的 credential_id 关联
    update_agent(session, agent.id, credential_id=cred.id)

    _audit(session, site_id=site_id, actor="system",
           action="agent.paired", target_type="agent", target_id=agent.id,
           details={"agent_version": agent.agent_version, "platform": agent.platform})
    return cred


def verify_credential(session: Session, agent_id: str, credential_plain: str) -> Optional[AgentCredential]:
    """
    检查 Agent 带来的 credential 是否仍然有效且归属该 agent。
    返回 AgentCredential 或 None。
    """
    cred_hash = _sha256(credential_plain)
    cred = session.execute(
        select(AgentCredential).where(AgentCredential.credential_hash == cred_hash)
    ).scalar_one_or_none()
    if cred is None:
        return None
    if cred.revoked_at is not None:
        return None
    if cred.expires_at is not None and cred.expires_at < _utcnow():
        return None
    if cred.agent_id != agent_id:
        return None
    return cred


def revoke_credential(session: Session, credential_id: str, reason: str = "manual") -> bool:
    cred = session.get(AgentCredential, credential_id)
    if cred is None:
        return False
    cred.revoked_at = _utcnow()
    cred.revoke_reason = reason
    cred.pairing_code = None
    session.commit()
    if cred.agent_id:
        # Agent 一起离线
        from ..agents import update_agent
        update_agent(session, cred.agent_id, status="disabled")
    return True
