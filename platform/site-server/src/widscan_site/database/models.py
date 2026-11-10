"""Site Server 数据库模型。10 个核心表。

设计要点：
1. Site+Agent+Device 三层。Agent 是接入实体，Device 是业务核心。
2. device_id 字符串主键（来自 firmware/widscan-gateway 的稳定合成）。
3. ConnectionSession 不冒充固件 boot_id — boot_id 字段可空。
4. AgentCredential 存 hash 不是明文。
5. Measurement 区分 device_timestamp / agent_received_at / site_received_at。
6. Agent 心跳超时后 Agent.status → offline，其下所有 Device.status → offline。
7. 所有时间戳用 UTC UTCDateTime()。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, Boolean, JSON,
    ForeignKey, UniqueConstraint, Index, CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from . import Base


class UTCDateTime(TypeDecorator):
    """所有时间戳强制为 timezone-aware UTC —— SQLite 不存时区信息，
    读出来的 naive datetime 一律加 timezone.utc。"""
    impl = DateTime
    cache_ok = True

    def __init__(self):
        super().__init__(timezone=True)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ============================================================
# 1. Site
# ============================================================

class Site(Base):
    __tablename__ = "sites"

    id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    location = Column(String(255), default="")
    timezone = Column(String(64), default="UTC")
    status = Column(String(32), default="active")  # active | disabled
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False)

    agents = relationship("Agent", back_populates="site", cascade="all, delete-orphan")
    devices = relationship("Device", back_populates="site", cascade="all, delete-orphan")
    profiles = relationship("DeviceProfile", back_populates="site", cascade="all, delete-orphan")


# ============================================================
# 2. Agent
# ============================================================

class Agent(Base):
    __tablename__ = "agents"

    id = Column(String(64), primary_key=True)              # agent_id (uuid)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False, default="")
    agent_version = Column(String(64), default="")
    platform = Column(String(64), default="")              # darwin / linux / windows
    os_info = Column(String(255), default="")
    status = Column(String(32), default="offline")         # online | offline | disabled
    last_seen_at = Column(UTCDateTime(), nullable=True)
    registered_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    credential_id = Column(String(64), ForeignKey("agent_credentials.id"), nullable=True)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False)

    site = relationship("Site", back_populates="agents")
    credential = relationship(
        "AgentCredential",
        primaryjoin="Agent.credential_id == AgentCredential.id",
        lazy="joined",
    )
    endpoints = relationship("DeviceEndpoint", back_populates="agent")
    sessions = relationship("ConnectionSession", back_populates="agent")

    __table_args__ = (
        Index("ix_agents_site_status", "site_id", "status"),
    )


# ============================================================
# 3. AgentCredential
# ============================================================

class AgentCredential(Base):
    __tablename__ = "agent_credentials"

    id = Column(String(64), primary_key=True)
    agent_id = Column(String(64), nullable=True, index=True)  # 逻辑归属，不用 ORM FK 避免循环
    credential_hash = Column(String(255), nullable=False)  # SHA256 hex
    pairing_code = Column(String(32), nullable=True, index=True)
    pairing_code_expires_at = Column(UTCDateTime(), nullable=True)
    issued_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    expires_at = Column(UTCDateTime(), nullable=True)
    revoked_at = Column(UTCDateTime(), nullable=True)
    revoke_reason = Column(String(255), default="")

    # AgentCredential.agent_id 是逻辑归属，不走 ORM FK（避免循环）
    # 通过 credential.agent_id 查询反向关系：session.query(Agent).get(credential.agent_id)


# ============================================================
# 4. Device
# ============================================================

class Device(Base):
    __tablename__ = "devices"

    id = Column(String(64), primary_key=True)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    display_name = Column(String(255), default="")
    hardware_model = Column(String(128), default="esp32c5")
    firmware_version = Column(String(64), default="")
    protocol_version = Column(Integer, default=1)
    phy_version = Column(String(128), default="")
    capabilities = Column(JSON, default=dict)              # e.g. {"rawiq": true}
    status = Column(String(32), default="pending")         # pending | online | offline | error | identity_conflict | disabled
    identity_confidence = Column(String(16), default="high")  # high | low
    # V1.1.2.1 Task 5: 设备所有权
    owner_agent_id = Column(String(64), ForeignKey("agents.id"), nullable=True, index=True)
    ownership_status = Column(String(32), default="unassigned")  # owned | unassigned | observer
    owner_assigned_at = Column(UTCDateTime(), nullable=True)
    owner_updated_at = Column(UTCDateTime(), nullable=True)
    first_seen_at = Column(UTCDateTime(), default=_utcnow)
    last_seen_at = Column(UTCDateTime(), nullable=True)
    adopted_at = Column(UTCDateTime(), nullable=True)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False)

    site = relationship("Site", back_populates="devices")
    owner = relationship("Agent", foreign_keys=[owner_agent_id])
    endpoints = relationship("DeviceEndpoint", back_populates="device", cascade="all, delete-orphan")
    sessions = relationship("ConnectionSession", back_populates="device", cascade="all, delete-orphan")
    measurements = relationship("Measurement", back_populates="device")

    __table_args__ = (
        Index("ix_devices_site_status", "site_id", "status"),
    )


# ============================================================
# 5. DeviceEndpoint
# ============================================================

class DeviceEndpoint(Base):
    __tablename__ = "device_endpoints"

    id = Column(String(64), primary_key=True)
    device_id = Column(String(64), ForeignKey("devices.id"), nullable=False, index=True)
    agent_id = Column(String(64), ForeignKey("agents.id"), nullable=False, index=True)
    transport_type = Column(String(32), nullable=False)     # usb_serial | tcp | simulator
    endpoint_identifier = Column(String(255), nullable=False)  # /dev/ttyACM0 | 192.168.1.100:5000 | sim:xxx
    status = Column(String(32), default="connected")        # connected | disconnected
    last_connected_at = Column(UTCDateTime(), nullable=True)
    last_disconnected_at = Column(UTCDateTime(), nullable=True)

    device = relationship("Device", back_populates="endpoints")
    agent = relationship("Agent", back_populates="endpoints")

    __table_args__ = (
        UniqueConstraint("device_id", "agent_id", "transport_type", "endpoint_identifier",
                         name="uq_device_endpoint"),
    )


# ============================================================
# 6. ConnectionSession
# ============================================================

class ConnectionSession(Base):
    __tablename__ = "connection_sessions"

    id = Column(String(64), primary_key=True)
    device_id = Column(String(64), ForeignKey("devices.id"), nullable=False, index=True)
    agent_id = Column(String(64), ForeignKey("agents.id"), nullable=False, index=True)
    boot_id = Column(String(64), nullable=True)           # 固件没提供则为 null
    connected_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    disconnected_at = Column(UTCDateTime(), nullable=True)
    disconnect_reason = Column(String(255), default="")
    reconnect_count = Column(Integer, default=0)
    status = Column(String(32), default="connected")       # connected | disconnected | error

    device = relationship("Device", back_populates="sessions")
    agent = relationship("Agent", back_populates="sessions")

    __table_args__ = (
        Index("ix_sessions_device_connected", "device_id", "status"),
    )


# ============================================================
# 7. DeviceProfile
# ============================================================

class DeviceProfile(Base):
    __tablename__ = "device_profiles"

    id = Column(String(64), primary_key=True)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    profile_type = Column(String(64), default="scan")      # scan | generic
    config_json = Column(JSON, default=dict)
    version = Column(Integer, default=1)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at = Column(UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False)

    site = relationship("Site", back_populates="profiles")


# ============================================================
# 8. Measurement （持久化设备采集数据）
# ============================================================

class Measurement(Base):
    """
    区分：
    - device_timestamp：设备单调时间（device.fw_protocol_time 等，可能为 None）
    - agent_received_at：Gateway Agent 接到设备数据的时间
    - site_received_at：Site Server 接收 Agent 上报的时间（= created_at）
    - agent_id：实际接收/转发的 Gateway Agent（历史记录可 NULL）
    - device_boot_id：C5 设备启动周期标识（来自 Agent 上报，跨重启隔离幂等）
    """
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    device_id = Column(String(64), ForeignKey("devices.id"), nullable=False, index=True)
    session_id = Column(String(64), ForeignKey("connection_sessions.id"), nullable=True, index=True)
    # V1.1.2.1 Task 6: 数据来源追溯
    agent_id = Column(String(64), ForeignKey("agents.id"), nullable=True, index=True)
    device_boot_id = Column(String(64), nullable=True, index=True)
    frame_type = Column(String(32), nullable=False, index=True)  # METRICS | SPECTRUM | TELEMETRY | LEVELS | RAWIQ | LOG
    frame_type_code = Column(Integer, nullable=True)
    device_timestamp_ms = Column(Integer, nullable=True)     # fw_protocol_time 或 firmware t_ms，毫秒
    agent_received_at = Column(UTCDateTime(), nullable=True)
    sequence = Column(Integer, nullable=True)
    source = Column(String(32), default="hardware")          # hardware | simulator | replay
    payload_json = Column(JSON, default=dict)               # 解码后的 dict
    event_id = Column(String(128), nullable=True, index=True)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False, index=True)

    device = relationship("Device", back_populates="measurements")

    __table_args__ = (
        # 幂等键：同一场地的 event_id 去重
        UniqueConstraint("site_id", "event_id", name="uq_measurement_event"),
        Index("ix_measurements_device_time", "device_id", "created_at"),
        Index("ix_measurements_site_time", "site_id", "created_at"),
        Index("ix_measurements_site_frame", "site_id", "frame_type"),
        Index("ix_measurements_agent", "agent_id"),
        Index("ix_measurements_device_boot", "device_boot_id"),
    )


# ============================================================
# 9. AuditLog
# ============================================================

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    actor = Column(String(128), default="system")            # system | admin | agent
    action = Column(String(64), nullable=False, index=True)  # agent.paired | agent.revoked | device.adopted | ...
    target_type = Column(String(32), default="")            # agent | device | profile | credential
    target_id = Column(String(64), default="")
    details = Column(JSON, default=dict)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False, index=True)


# ============================================================
# 10. SiteEvent （需要追溯的业务事件）
# ============================================================

class SiteEvent(Base):
    __tablename__ = "site_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(64), ForeignKey("sites.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)  # device.discovered | device.online | agent.connected | ...
    source_type = Column(String(32), default="agent")       # agent | system | user
    source_id = Column(String(64), default="")              # agent_id 或 user
    device_id = Column(String(64), default="", index=True)
    payload = Column(JSON, default=dict)
    created_at = Column(UTCDateTime(), default=_utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_site_events_site_time", "site_id", "created_at"),
    )
