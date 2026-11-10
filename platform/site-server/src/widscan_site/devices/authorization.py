"""设备命令授权服务（Phase 07.2）。

核心原则：
- Owner Agent 拥有设备管理权；Observer Agent 只读。
- Site Administrator 由可信的服务端调用方（管理 REST）声明，不接受 Agent 自报。
- 授权结论必须基于**服务端可信 Device 记录** + **已认证连接身份**，
  绝不信任命令消息里自带的 owner_agent_id / device 归属字段。

本模块只提供判定逻辑，不执行命令。任何未来的远程命令入口
（REST / WebSocket / dispatcher）都必须在真正操作设备前调用 `authorize_device_action`
或 `authorize_device_command`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..database.models import Agent, Device

ROLE_OWNER = "owner"
ROLE_OBSERVER = "observer"
ROLE_SITE_ADMIN = "site_admin"
ROLE_UNAUTHORIZED = "unauthorized"

ACTOR_AGENT = "agent"
ACTOR_SITE_ADMIN = "site_admin"

ACTION_READ = "read"
ACTION_COMMAND = "command"
ACTION_CONFIGURE = "configure"
ACTION_TRANSFER_OWNER = "transfer_owner"
ACTION_DELETE = "delete"

# 权限矩阵：动作 → 允许的角色。Observer 永远不能做管理动作。
_ALLOWED_ROLES = {
    ACTION_READ: frozenset({ROLE_OWNER, ROLE_OBSERVER, ROLE_SITE_ADMIN}),
    ACTION_COMMAND: frozenset({ROLE_OWNER, ROLE_SITE_ADMIN}),
    ACTION_CONFIGURE: frozenset({ROLE_OWNER, ROLE_SITE_ADMIN}),
    ACTION_TRANSFER_OWNER: frozenset({ROLE_SITE_ADMIN}),
    ACTION_DELETE: frozenset({ROLE_SITE_ADMIN}),
}

# Device 处于这些状态时不允许下发命令（设备不可执行）。
_NON_COMMANDABLE_STATUSES = frozenset(
    {"pending", "offline", "error", "disabled", "identity_conflict"})


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    role: str
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - 便利语法
        return self.allowed


class DeviceAuthorizationError(PermissionError):
    """已认证但无权限执行设备动作。"""

    def __init__(self, decision: AuthorizationDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


def _load_device(session: Session, *, site_id: str, device_id: str) -> Optional[Device]:
    if not site_id or not device_id:
        return None
    return (
        session.query(Device)
        .filter(Device.id == device_id, Device.site_id == site_id)
        .one_or_none()
    )


def _load_agent(session: Session, *, site_id: str, agent_id: str) -> Optional[Agent]:
    if not site_id or not agent_id:
        return None
    return (
        session.query(Agent)
        .filter(Agent.id == agent_id, Agent.site_id == site_id)
        .one_or_none()
    )


def resolve_agent_role(
    session: Session,
    *,
    site_id: str,
    agent_id: str,
    device_id: str,
    device: Optional[Device] = None,
) -> tuple[str, Optional[Device]]:
    """返回 (role, device)。role ∈ owner | observer | unauthorized。

    规则全部来自服务端记录：
    - Device 不存在 / 不属于该 Site → unauthorized
    - Agent 不存在 / disabled → unauthorized
    - Device 无 Owner（unassigned / NULL）→ observer（只能读，不能管理）
    - Device.owner_agent_id == 已认证 agent → owner
    - 其他已认证 Agent → observer
    """
    dev = device if device is not None else _load_device(
        session, site_id=site_id, device_id=device_id)
    if dev is None:
        return ROLE_UNAUTHORIZED, None

    agent = _load_agent(session, site_id=site_id, agent_id=agent_id)
    if agent is None or agent.status == "disabled":
        return ROLE_UNAUTHORIZED, dev

    if dev.owner_agent_id and dev.owner_agent_id == agent.id:
        return ROLE_OWNER, dev
    return ROLE_OBSERVER, dev


def authorize_device_action(
    session: Session,
    *,
    site_id: str,
    device_id: str,
    action: str,
    agent_id: Optional[str] = None,
    actor_type: str = ACTOR_AGENT,
    device: Optional[Device] = None,
) -> AuthorizationDecision:
    """统一授权入口。actor_type=site_admin 表示服务端管理调用方（可信）。"""
    if actor_type == ACTOR_SITE_ADMIN:
        dev = device if device is not None else _load_device(
            session, site_id=site_id, device_id=device_id)
        if dev is None:
            return AuthorizationDecision(False, ROLE_UNAUTHORIZED, "device_not_found")
        if action in _ALLOWED_ROLES and ROLE_SITE_ADMIN in _ALLOWED_ROLES[action]:
            return AuthorizationDecision(True, ROLE_SITE_ADMIN, "site_admin_policy")
        return AuthorizationDecision(False, ROLE_SITE_ADMIN, "action_not_allowed_for_site_admin")

    role, dev = resolve_agent_role(
        session, site_id=site_id, agent_id=agent_id or "", device_id=device_id,
        device=device,
    )
    if dev is None:
        return AuthorizationDecision(False, ROLE_UNAUTHORIZED, "device_not_found")
    if role == ROLE_UNAUTHORIZED:
        return AuthorizationDecision(False, role, "agent_unknown_or_disabled")

    allowed_roles = _ALLOWED_ROLES.get(action)
    if allowed_roles is None:
        return AuthorizationDecision(False, role, "unknown_action")
    if role not in allowed_roles:
        reason = "observer_read_only" if role == ROLE_OBSERVER else "role_not_allowed"
        return AuthorizationDecision(False, role, reason)
    if action in (ACTION_COMMAND, ACTION_CONFIGURE) and dev.status in _NON_COMMANDABLE_STATUSES:
        return AuthorizationDecision(False, role, f"device_unavailable:{dev.status}")
    if dev.identity_confidence == "low" and action != ACTION_READ:
        return AuthorizationDecision(False, role, "low_confidence_device")
    return AuthorizationDecision(True, role, "allowed")


def authorize_device_command(
    session: Session,
    *,
    site_id: str,
    device_id: str,
    agent_id: Optional[str] = None,
    actor_type: str = ACTOR_AGENT,
    device: Optional[Device] = None,
) -> AuthorizationDecision:
    """设备命令授权（命令入口必须调用）。"""
    return authorize_device_action(
        session,
        site_id=site_id,
        device_id=device_id,
        action=ACTION_COMMAND,
        agent_id=agent_id,
        actor_type=actor_type,
        device=device,
    )


def authorize_device_read(
    session: Session,
    *,
    site_id: str,
    device_id: str,
    agent_id: Optional[str] = None,
    device: Optional[Device] = None,
) -> AuthorizationDecision:
    """设备读取授权（Owner / Observer / Site Admin 均允许）。"""
    return authorize_device_action(
        session,
        site_id=site_id,
        device_id=device_id,
        action=ACTION_READ,
        agent_id=agent_id,
        device=device,
    )
