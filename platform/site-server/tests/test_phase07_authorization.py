"""Phase 07.2 — Device Owner 命令授权与 Observer 只读测试。"""

from __future__ import annotations

import uuid

import pytest

from widscan_site.database.models import Agent, Device
from widscan_site.devices.authorization import (
    ACTION_COMMAND,
    ACTION_CONFIGURE,
    ACTION_DELETE,
    ACTION_READ,
    ACTION_TRANSFER_OWNER,
    ACTOR_SITE_ADMIN,
    ROLE_OBSERVER,
    ROLE_OWNER,
    ROLE_SITE_ADMIN,
    ROLE_UNAUTHORIZED,
    authorize_device_action,
    authorize_device_command,
    authorize_device_read,
)


def _agent(session, agent_id: str, *, status: str = "online") -> Agent:
    existing = session.get(Agent, agent_id)
    if existing:
        return existing
    a = Agent(
        id=agent_id, site_id="site-001", name=agent_id,
        status=status, platform="test", agent_version="1.0",
    )
    session.add(a)
    session.commit()
    return a


def _device(session, device_id: str, owner: str | None, *,
            status: str = "online", confidence: str = "high") -> Device:
    d = Device(
        id=device_id, site_id="site-001", display_name=device_id,
        status=status,
        identity_confidence=confidence,
        ownership_status="owned" if owner else "unassigned",
        owner_agent_id=owner,
    )
    session.add(d)
    session.commit()
    return d


def test_owner_agent_command_allowed(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1")
    assert decision.allowed is True
    assert decision.role == ROLE_OWNER


def test_observer_agent_command_denied(session):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-B", device_id="dev-1")
    assert decision.allowed is False
    assert decision.role == ROLE_OBSERVER
    assert decision.reason == "observer_read_only"


def test_observer_can_read_device(session):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _device(session, "dev-1", "agent-A")
    assert authorize_device_read(
        session, site_id="site-001", agent_id="agent-B", device_id="dev-1").allowed


def test_unknown_agent_is_unauthorized(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-ghost", device_id="dev-1")
    assert decision.allowed is False
    assert decision.role == ROLE_UNAUTHORIZED
    assert decision.reason == "agent_unknown_or_disabled"


def test_disabled_agent_is_unauthorized(session):
    _agent(session, "agent-D", status="disabled")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_read(
        session, site_id="site-001", agent_id="agent-D", device_id="dev-1")
    assert decision.allowed is False


def test_agent_cannot_command_other_agent_device(session):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _device(session, "dev-B", "agent-B")
    assert authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-B").allowed is False


def test_forged_owner_agent_id_in_message_is_ignored(session):
    """授权只读服务端记录；消息里的 owner 声明不参与判定。"""
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _device(session, "dev-1", "agent-A")
    # 模拟消息声称 owner_agent_id=agent-B —— 服务层不接收该输入
    decision = authorize_device_action(
        session, site_id="site-001", agent_id="agent-B", device_id="dev-1",
        action=ACTION_COMMAND, actor_type="agent")
    assert decision.allowed is False


def test_unknown_or_cross_site_device_leaks_nothing(session):
    _agent(session, "agent-A")
    _device(session, "dev-other", "agent-A")
    decision = authorize_device_command(
        session, site_id="site-other", agent_id="agent-A", device_id="dev-other")
    assert decision.allowed is False
    assert decision.reason == "device_not_found"


def test_owner_offline_device_command_denied_with_explicit_reason(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A", status="offline")
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1")
    assert decision.allowed is False
    assert decision.reason == "device_unavailable:offline"
    # 只读仍然允许
    assert authorize_device_read(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1").allowed


def test_owner_transfer_revokes_old_owner_permission(session):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    dev = _device(session, "dev-1", "agent-A")
    dev.owner_agent_id = "agent-B"
    dev.ownership_status = "owned"
    session.commit()

    assert authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1").allowed is False
    assert authorize_device_command(
        session, site_id="site-001", agent_id="agent-B", device_id="dev-1").allowed is True


def test_unowned_legacy_device_is_read_only(session):
    _agent(session, "agent-A")
    _device(session, "dev-legacy", None)
    assert authorize_device_read(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-legacy").allowed
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-legacy")
    assert decision.allowed is False
    assert decision.role == ROLE_OBSERVER


def test_low_confidence_device_cannot_be_managed(session):
    _agent(session, "agent-A")
    _device(session, "dev-low", "agent-A", confidence="low")
    decision = authorize_device_command(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-low")
    assert decision.allowed is False
    assert decision.reason == "low_confidence_device"
    assert authorize_device_read(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-low").allowed


def test_site_admin_can_command_and_transfer(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    assert authorize_device_action(
        session, site_id="site-001", device_id="dev-1",
        action=ACTION_COMMAND, actor_type=ACTOR_SITE_ADMIN).allowed
    assert authorize_device_action(
        session, site_id="site-001", device_id="dev-1",
        action=ACTION_TRANSFER_OWNER, actor_type=ACTOR_SITE_ADMIN).allowed


def test_owner_cannot_transfer_or_delete_device(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    assert authorize_device_action(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1",
        action=ACTION_TRANSFER_OWNER).allowed is False
    assert authorize_device_action(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1",
        action=ACTION_DELETE).allowed is False


def test_owner_can_configure_own_device(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    assert authorize_device_action(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1",
        action=ACTION_CONFIGURE).allowed


def test_observer_cannot_configure(session):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_action(
        session, site_id="site-001", agent_id="agent-B", device_id="dev-1",
        action=ACTION_CONFIGURE)
    assert decision.allowed is False
    assert decision.reason == "observer_read_only"


def test_unknown_action_denied(session):
    _agent(session, "agent-A")
    _device(session, "dev-1", "agent-A")
    decision = authorize_device_action(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-1",
        action="firmware_ota")
    assert decision.allowed is False
    assert decision.reason == "unknown_action"
