"""V1.1.2.1 Task 6: Measurement Agent Traceability 测试。

覆盖：
- agent_id 正确写入（来自上游 Agent）
- device_boot_id 正确写入
- event_id 幂等键：device_id + device_boot_id + frame_type + sequence
- 同一 event_id 重复上报 → IntegrityError → 静默丢弃
- 历史 Measurement agent_id = NULL（自动兼容）
- Observer Agent 上报 → agent_id 记录为 Observer，不是 Device Owner
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from widscan_site.database.models import Device, Measurement, Agent, ConnectionSession
from widscan_site.ingestion import persist_measurement, record_device_frame

NOW = datetime.now(timezone.utc)


def _setup_device_with_agents(session):
    """创建 1 Device + 2 Agent，返回 (dev, agent_a, agent_b)。"""
    agent_a = Agent(id="agent-A", site_id="site-001", name="AgentA", status="online")
    agent_b = Agent(id="agent-B", site_id="site-001", name="AgentB", status="online")
    session.add_all([agent_a, agent_b])

    dev = Device(
        id="c5-AA:BB:CC:DD:EE:FF",
        site_id="site-001",
        status="online",
        identity_confidence="high",
        owner_agent_id="agent-A",
        ownership_status="owned",
    )
    session.add(dev)
    session.commit()
    session.refresh(dev)
    return dev, agent_a, agent_b


# -----------------------------------------------------------------
# agent_id + device_boot_id 正确写入
# -----------------------------------------------------------------

def test_persist_measurement_records_agent_and_boot(session):
    dev, agent_a, _ = _setup_device_with_agents(session)
    dev_boot = "boot-abc-123"

    m = persist_measurement(
        session,
        site_id="site-001",
        agent_id="agent-A",
        device_id=dev.id,
        session_id=None,
        frame_type_code=1,  # METRICS
        decoded={"rssi": -65, "snr": 22},
        agent_received_at=NOW,
        sequence=42,
        device_boot_id=dev_boot,
    )
    session.commit()
    assert m is not None
    assert m.agent_id == "agent-A"
    assert m.device_boot_id == dev_boot
    assert m.event_id == f"{dev.id}:{dev_boot}:1:42"


# -----------------------------------------------------------------
# event_id 幂等：同 event_id 重复上报 → 静默丢弃
# -----------------------------------------------------------------

def test_event_id_idempotency(session):
    dev, agent_a, _ = _setup_device_with_agents(session)
    dev_boot = "boot-dup-test"

    m1 = persist_measurement(
        session, site_id="site-001",
        agent_id="agent-A", device_id=dev.id, session_id=None,
        frame_type_code=1, decoded={"rssi": -65},
        agent_received_at=NOW, sequence=1, device_boot_id=dev_boot,
    )
    session.commit()
    assert m1 is not None
    first_id = m1.id

    # 同一 event_id 再上（同一个 agent + device + frame + seq）
    m2 = persist_measurement(
        session, site_id="site-001",
        agent_id="agent-A", device_id=dev.id, session_id=None,
        frame_type_code=1, decoded={"rssi": -70},  # payload 变了也没用
        agent_received_at=NOW, sequence=1, device_boot_id=dev_boot,
    )
    # persist_measurement 会 IntegrityError → rollback → 返回 None
    # 注意：persist_measurement 内部做了 session.rollback() 但 session.commit() 是调用方做的
    # 这里因为第一次 commit 了，第二次 persist 里 rollback + 继续 → m2=None
    assert m2 is None


# -----------------------------------------------------------------
# Observer Agent 上报 — agent_id 正确记录为 Observer，不是 Device Owner
# -----------------------------------------------------------------

def test_observer_agent_traceability(session):
    dev, agent_a, agent_b = _setup_device_with_agents(session)

    # Device Owner 是 agent-A，Observer 是 agent-B
    assert dev.owner_agent_id == "agent-A"

    m = persist_measurement(
        session, site_id="site-001",
        agent_id="agent-B",  # 真实来源是 Observer Agent B
        device_id=dev.id, session_id=None,
        frame_type_code=1, decoded={"rssi": -65},
        agent_received_at=NOW, sequence=10, device_boot_id="boot-observer-test",
    )
    session.commit()

    # agent_id 记录的是真实上报者 agent-B，不是 owner agent-A
    assert m.agent_id == "agent-B"
    # 绝对不能根据 Device 当前 Owner 反推历史来源
    assert m.agent_id != dev.owner_agent_id


# -----------------------------------------------------------------
# 历史 Measurement agent_id = NULL（自动兼容）
# -----------------------------------------------------------------

def test_legacy_measurement_agent_id_null(session):
    """用 ORM 直接插入一条模拟旧数据 — agent_id 应为 NULL。"""
    dev, _, _ = _setup_device_with_agents(session)
    m = Measurement(
        site_id="site-001",
        device_id=dev.id,
        frame_type="METRICS",
        frame_type_code=1,
        payload_json={"rssi": -60},
        event_id=f"{dev.id}:unknown-boot:1:9999",
        # 故意不填 agent_id — 默认 NULL
    )
    session.add(m)
    session.commit()
    session.refresh(m)
    assert m.agent_id is None
    assert m.device_boot_id is None


# -----------------------------------------------------------------
# RAWIQ 默认被跳过
# -----------------------------------------------------------------

def test_rawiq_skipped_by_default(session):
    dev, _, _ = _setup_device_with_agents(session)
    m = persist_measurement(
        session, site_id="site-001",
        agent_id="agent-A", device_id=dev.id, session_id=None,
        frame_type_code=4,  # RAWIQ
        decoded={"samples": [1, 2, 3]},
        agent_received_at=NOW, sequence=1, device_boot_id="boot-rawiq",
        retention_rawiq_enabled=False,  # 默认关闭
    )
    assert m is None  # RAWIQ 默认不持久化
