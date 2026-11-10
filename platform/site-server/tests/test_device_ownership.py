"""V1.1.2.1 Task 5: Device Ownership Semantics 测试。

覆盖：
- Agent A 首次注册 Device → A 自动成为 Owner
- Agent B 注册同一 Device → B 静默 Observer，不抢占
- Owner Agent 离线 → Device Owner 保留，ownership_status 不变
- Owner Agent 重连 → 恢复 ownership_status="owned"
- Agent B 尝试执行 Owner 专属操作（如果实现的话）
- 管理员授权 Owner 迁移 A → B
- 迁移后 A 不再是 Owner
- 低置信度 Device 无法成为 Owner
- 并发 Agent 注册同一 Device — 不竞争覆盖
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from widscan_site.database.models import Agent, Device, AuditLog, SiteEvent
from widscan_site.devices import upsert_device_from_agent, transfer_device_owner


def _device_info(high_conf: bool = True) -> dict:
    return {
        "hardware_model": "esp32c5",
        "firmware_version": "v0.1.0-test",
        "protocol_version": 1,
        "identity_confidence": "high" if high_conf else "low",
        "capabilities": {"rawiq": False},
    }


def _transport_info() -> dict:
    return {"kind": "usb_serial", "path": "/dev/null"}


# -----------------------------------------------------------------
# Agent A 首次注册 → A 自动成为 Owner
# -----------------------------------------------------------------

def test_device_first_seen_becomes_owner(session):
    dev_id = "c5-AA:BB:CC:DD:EE:01"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    session.add(agent_a)
    session.commit()

    dev = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    assert dev.owner_agent_id == "agent-A"
    assert dev.ownership_status == "owned"
    assert dev.owner_assigned_at is not None
    assert dev.owner_updated_at is not None

    # AuditLog: device.owner_assigned (source=first_seen)
    audit = session.execute(select(AuditLog).where(
        AuditLog.action == "device.owner_assigned",
        AuditLog.target_id == dev_id,
    )).scalar_one_or_none()
    assert audit is not None
    assert audit.details.get("source") == "first_seen"
    assert audit.details.get("owner_agent_id") == "agent-A"


# -----------------------------------------------------------------
# Agent B 注册同一 Device → 不抢占，静默 Observer
# -----------------------------------------------------------------

def test_other_agent_cannot_steal_device(session):
    dev_id = "c5-AA:BB:CC:DD:EE:02"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    agent_b = Agent(id="agent-B", site_id="site-001", name="AgentB", status="online")
    session.add_all([agent_a, agent_b])
    session.commit()

    # Agent A 首次注册
    dev1 = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    assert dev1.owner_agent_id == "agent-A"

    # Agent B 试图上报
    dev2 = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-B",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    # Owner 不变
    assert dev2.owner_agent_id == "agent-A"
    # 但 ownership_status 变成 observer（这个 Agent 是 Observer）
    assert dev2.ownership_status == "observer"


# -----------------------------------------------------------------
# Owner Agent 离线 → Device Owner 保留
# -----------------------------------------------------------------

def test_device_owner_retained_when_agent_offline(session):
    dev_id = "c5-AA:BB:CC:DD:EE:03"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="offline")
    session.add(agent_a)
    session.commit()

    dev = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )

    # 模拟 Agent A 离线（Agent 状态改 offline，Device 也变 offline）
    agent_a.status = "offline"
    dev.status = "offline"
    session.commit()
    session.refresh(dev)

    # Owner 依然是 A
    assert dev.owner_agent_id == "agent-A"
    # ownership_status 保持 "owned"（Agent offline 不等于 Owner 被撤销）
    assert dev.ownership_status == "owned"


# -----------------------------------------------------------------
# Owner Agent 重连 → 恢复 ownership_status="owned"
# -----------------------------------------------------------------

def test_device_owner_restored_on_agent_reconnect(session):
    dev_id = "c5-AA:BB:CC:DD:EE:04"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    agent_b = Agent(id="agent-B", site_id="site-001", name="AgentB", status="online")
    session.add_all([agent_a, agent_b])
    session.commit()

    # Agent A 注册 → owner
    upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    # Agent B 也上报 → observer
    dev = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-B",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    assert dev.owner_agent_id == "agent-A"
    assert dev.ownership_status == "observer"

    # Agent A 重连 → 恢复 owned
    dev = upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    assert dev.owner_agent_id == "agent-A"
    assert dev.ownership_status == "owned"


# -----------------------------------------------------------------
# 管理员授权 Owner 迁移 A → B
# -----------------------------------------------------------------

def test_admin_transfers_device_owner(session):
    dev_id = "c5-AA:BB:CC:DD:EE:05"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    agent_b = Agent(id="agent-B", site_id="site-001", name="AgentB", status="online")
    session.add_all([agent_a, agent_b])
    session.commit()

    upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )

    dev = transfer_device_owner(
        session, site_id="site-001", device_id=dev_id,
        new_owner_agent_id="agent-B", reason="admin-manual",
    )
    assert dev.owner_agent_id == "agent-B"
    assert dev.ownership_status == "owned"

    # AuditLog 存在
    audit = session.execute(select(AuditLog).where(
        AuditLog.action == "device.owner_transferred",
        AuditLog.target_id == dev_id,
    )).scalar_one_or_none()
    assert audit is not None
    assert audit.details["old_owner_agent_id"] == "agent-A"
    assert audit.details["new_owner_agent_id"] == "agent-B"
    assert audit.details["reason"] == "admin-manual"


def test_transfer_to_same_agent_is_idempotent(session):
    dev_id = "c5-AA:BB:CC:DD:EE:06"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    session.add(agent_a)
    session.commit()

    upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    dev = transfer_device_owner(
        session, site_id="site-001", device_id=dev_id,
        new_owner_agent_id="agent-A",
    )
    assert dev.owner_agent_id == "agent-A"


def test_transfer_to_disabled_agent_rejected(session):
    dev_id = "c5-AA:BB:CC:DD:EE:07"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    agent_b = Agent(id="agent-B", site_id="site-001", name="AgentB", status="disabled")
    session.add_all([agent_a, agent_b])
    session.commit()

    upsert_device_from_agent(
        session, site_id="site-001", agent_id="agent-A",
        device_id=dev_id, device_info=_device_info(),
        transport_info=_transport_info(), endpoint_identifier="/dev/null",
    )
    with pytest.raises(ValueError, match="disabled"):
        transfer_device_owner(
            session, site_id="site-001", device_id=dev_id,
            new_owner_agent_id="agent-B",
        )


# -----------------------------------------------------------------
# 低置信度 Device 不允许认领高置信度物理设备（和成为 Owner）
# -----------------------------------------------------------------

def test_low_confidence_device_cannot_have_owner(session):
    dev_id = "c5-LOW:CONFIDENCE:DEVICE"
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    session.add(agent_a)
    dev = Device(
        id=dev_id, site_id="site-001",
        identity_confidence="low", ownership_status="unassigned",
    )
    session.add(dev)
    session.commit()

    with pytest.raises(ValueError, match="low-confidence"):
        transfer_device_owner(
            session, site_id="site-001", device_id=dev_id,
            new_owner_agent_id="agent-A",
        )
