"""Device 服务 — 发现 + 认领 + 所有权 + 状态 + identity dedup。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import (
    Device, DeviceEndpoint, ConnectionSession, AuditLog, SiteEvent, Agent, _utcnow, _uuid,
)


# --- 身份去重 ------------------------------------------------------------

def find_device_by_id(session: Session, device_id: str) -> Optional[Device]:
    return session.get(Device, device_id)


def upsert_device_from_agent(
    session: Session,
    *,
    site_id: str,
    agent_id: str,
    device_id: str,
    device_info: dict,        # agent.device.online event data
    transport_info: dict,
    endpoint_identifier: str,
    source: str = "hardware",
) -> Device:
    """
    Agent 上报新设备或已存在设备重新上线时调用。
    - device 不存在 → 创建 (status=pending 除非 device_info 里有新设备标记)
    - device 存在 → 更新 last_seen_at + firmware_version 等
    - DeviceEndpoint 不存在 → upsert
    - ConnectionSession → 新建一条 (boot_id=null — 固件没给)
    """
    dev = session.get(Device, device_id)
    now = _utcnow()

    if dev is None:
        dev = Device(
            id=device_id,
            site_id=site_id,
            display_name="",
            hardware_model=device_info.get("hardware_model", "esp32c5"),
            firmware_version=device_info.get("firmware_version", ""),
            protocol_version=device_info.get("protocol_version", 1),
            phy_version=device_info.get("phy_version", ""),
            capabilities=device_info.get("capabilities") or {},
            status="online",           # V1.1 FIX: Agent 上报 online → Device 就是 online
            identity_confidence=device_info.get("identity_confidence", "high"),
            # V1.1.2.1 Task 5: 新 Device 首次注册 → 上报 Agent 自动成为 Owner
            owner_agent_id=agent_id,
            ownership_status="owned",
            owner_assigned_at=now,
            owner_updated_at=now,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(dev)
        session.flush()
        # 审计：新设备认领
        session.add(AuditLog(
            site_id=site_id, actor="system", action="device.owner_assigned",
            target_type="device", target_id=device_id,
            details={"owner_agent_id": agent_id, "source": "first_seen"},
        ))
    else:
        # 更新可变字段
        if device_info.get("firmware_version"):
            dev.firmware_version = device_info["firmware_version"]
        if device_info.get("hardware_model"):
            dev.hardware_model = device_info["hardware_model"]
        if device_info.get("phy_version"):
            dev.phy_version = device_info["phy_version"]
        if device_info.get("capabilities"):
            dev.capabilities = device_info["capabilities"]
        dev.last_seen_at = now

        # V1.1 FIX: Agent 重新上报 online → Device 状态切回 online
        if dev.status in ("offline", "reconnecting", "pending", "error"):
            dev.status = "online"

        # V1.1.2.1 Task 5: 所有权逻辑
        if dev.owner_agent_id is None:
            # 旧 Device 没有 owner → 当前 Agent 认领（历史数据向后兼容）
            dev.owner_agent_id = agent_id
            dev.ownership_status = "owned"
            dev.owner_assigned_at = now
            dev.owner_updated_at = now
            session.add(AuditLog(
                site_id=site_id, actor="system", action="device.owner_assigned",
                target_type="device", target_id=device_id,
                details={"owner_agent_id": agent_id, "source": "legacy_migration"},
            ))
        elif dev.owner_agent_id == agent_id:
            # Owner Agent 自己重新上线 → 恢复所有权（offline→owned 但所有权不变）
            dev.owner_updated_at = now
            dev.ownership_status = "owned"  # 确保之前可能因为某种原因变成的 observer 状态恢复
        else:
            # 另一个 Agent 尝试上报已有 owner 的 Device → 静默允许观测但不抢占
            # DeviceEndpoint + ConnectionSession 还是会创建（多 Agent 同时连接）
            # 但 owner_agent_id 不变 → 这个 Agent 是 Observer
            dev.ownership_status = "observer"
            # 不审计（频繁 Observer 连接会炸审计表）

    # DeviceEndpoint
    transport_type = transport_info.get("kind", "usb_serial")
    existing_ep = session.execute(
        select(DeviceEndpoint).where(
            DeviceEndpoint.device_id == device_id,
            DeviceEndpoint.agent_id == agent_id,
            DeviceEndpoint.transport_type == transport_type,
            DeviceEndpoint.endpoint_identifier == endpoint_identifier,
        )
    ).scalar_one_or_none()
    if existing_ep is None:
        ep = DeviceEndpoint(
            id=_uuid(),
            device_id=device_id,
            agent_id=agent_id,
            transport_type=transport_type,
            endpoint_identifier=endpoint_identifier,
            status="connected",
            last_connected_at=now,
        )
        session.add(ep)
    else:
        existing_ep.status = "connected"
        existing_ep.last_connected_at = now
        existing_ep.last_disconnected_at = None

    # ConnectionSession — 先关闭同 device+agent 下所有旧的 connected session
    # 避免 MultipleResultsFound（devices_snapshot + device.online 都会进来）
    from sqlalchemy import update as sa_update
    close_old = sa_update(ConnectionSession).where(
        ConnectionSession.device_id == device_id,
        ConnectionSession.agent_id == agent_id,
        ConnectionSession.status == "connected",
    ).values(status="disconnected", disconnected_at=now, disconnect_reason="replaced")
    session.execute(close_old)

    sess = ConnectionSession(
        id=_uuid(),
        device_id=device_id,
        agent_id=agent_id,
        boot_id=None,     # 固件没给
        connected_at=now,
        status="connected",
    )
    session.add(sess)

    # SiteEvent
    session.add(SiteEvent(
        site_id=site_id,
        event_type="device.online",
        source_type="agent",
        source_id=agent_id,
        device_id=device_id,
        payload={"transport": transport_info},
    ))

    session.commit()
    session.refresh(dev)
    return dev


# --- 认领 ----------------------------------------------------------------

def adopt_device(
    session: Session,
    *,
    site_id: str,
    device_id: str,
    display_name: Optional[str] = None,
) -> Device:
    dev = session.get(Device, device_id)
    if dev is None:
        raise ValueError(f"device {device_id} not found")
    if dev.site_id != site_id:
        raise ValueError(f"device {device_id} not in site {site_id}")
    if dev.status == "adopted":
        raise ValueError(f"device {device_id} already adopted")
    dev.status = "online"
    dev.adopted_at = _utcnow()
    if display_name:
        dev.display_name = display_name

    session.add(AuditLog(
        site_id=site_id, actor="admin", action="device.adopted",
        target_type="device", target_id=device_id,
        details={"display_name": display_name},
    ))
    session.add(SiteEvent(
        site_id=site_id, event_type="device.adopted",
        source_type="user", device_id=device_id,
        payload={"display_name": display_name},
    ))
    session.commit()
    session.refresh(dev)
    return dev


def transfer_device_owner(
    session: Session,
    *,
    site_id: str,
    device_id: str,
    new_owner_agent_id: str,
    actor: str = "admin",
    reason: str = "",
) -> Device:
    """管理员授权 Device Owner 迁移（V1.1.2.1 Task 5）。

    安全规则：
    1. Device 必须在 site_id 内
    2. new_owner_agent 必须存在且在同一 site_id
    3. 不能迁移到当前 owner（幂等跳过）
    4. 低置信度 Device 不能成为 owner 的目标（identity_confidence 必须为 high）
    5. 事务内更新，失败回滚
    6. 必须写 AuditLog 审计记录 + SiteEvent
    7. 不删旧 Agent 的 DeviceEndpoint/ConnectionSession — 旧 Agent 仍是 Observer
    """
    dev = session.get(Device, device_id)
    if dev is None:
        raise ValueError(f"device {device_id} not found")
    if dev.site_id != site_id:
        raise ValueError(f"device {device_id} not in site {site_id}")

    new_owner = session.get(Agent, new_owner_agent_id)
    if new_owner is None:
        raise ValueError(f"agent {new_owner_agent_id} not found")
    if new_owner.site_id != site_id:
        raise ValueError(f"agent {new_owner_agent_id} not in site {site_id}")
    if new_owner.status == "disabled":
        raise ValueError(f"agent {new_owner_agent_id} is disabled")

    old_owner_id = dev.owner_agent_id

    # 幂等 — 已是目标 Owner
    if old_owner_id == new_owner_agent_id:
        return dev

    # 低置信度 Device 不能有 Owner（安全）
    if dev.identity_confidence != "high":
        raise ValueError(
            f"device {device_id} identity_confidence={dev.identity_confidence} — "
            f"cannot assign owner to low-confidence device"
        )

    now = _utcnow()
    dev.owner_agent_id = new_owner_agent_id
    dev.ownership_status = "owned"
    dev.owner_assigned_at = now   # 重新计时
    dev.owner_updated_at = now

    session.add(AuditLog(
        site_id=site_id, actor=actor, action="device.owner_transferred",
        target_type="device", target_id=device_id,
        details={
            "old_owner_agent_id": old_owner_id,
            "new_owner_agent_id": new_owner_agent_id,
            "reason": reason,
        },
    ))
    session.add(SiteEvent(
        site_id=site_id, event_type="device.owner_transferred",
        source_type="user", source_id=actor, device_id=device_id,
        payload={
            "old_owner_agent_id": old_owner_id,
            "new_owner_agent_id": new_owner_agent_id,
            "reason": reason,
        },
    ))
    session.commit()
    session.refresh(dev)
    return dev


def rename_device(session: Session, device_id: str, display_name: str) -> Optional[Device]:
    dev = session.get(Device, device_id)
    if dev is None:
        return None
    dev.display_name = display_name
    session.add(AuditLog(
        site_id=dev.site_id, actor="admin", action="device.renamed",
        target_type="device", target_id=device_id,
        details={"display_name": display_name},
    ))
    session.commit()
    session.refresh(dev)
    return dev


def remove_device(session: Session, device_id: str) -> bool:
    dev = session.get(Device, device_id)
    if dev is None:
        return False
    session.add(AuditLog(
        site_id=dev.site_id, actor="admin", action="device.removed",
        target_type="device", target_id=device_id,
    ))
    # 级联 endpoints / sessions / measurements 由 ORM relationship + cascade 处理
    session.delete(dev)
    session.commit()
    return True


def mark_devices_offline_by_agent(session: Session, agent_id: str) -> int:
    """Agent 断线时，把所有通过该 Agent 连接的 device status → offline。"""
    from sqlalchemy import update
    # 先把该 Agent 下所有 endpoint 标记 disconnected
    from ..database.models import DeviceEndpoint, ConnectionSession
    now = _utcnow()
    ep_stmt = (
        update(DeviceEndpoint)
        .where(DeviceEndpoint.agent_id == agent_id)
        .values(status="disconnected", last_disconnected_at=now)
    )
    session.execute(ep_stmt)
    sess_stmt = (
        update(ConnectionSession)
        .where(ConnectionSession.agent_id == agent_id, ConnectionSession.status == "connected")
        .values(status="disconnected", disconnected_at=now, disconnect_reason="agent_offline")
    )
    session.execute(sess_stmt)
    # 把 device 的 status 改为 offline（如果没有其他 agent 还连着它）
    from sqlalchemy import and_
    devices_with_other_agents = session.execute(
        select(DeviceEndpoint.device_id).where(DeviceEndpoint.status == "connected")
    ).scalars().all()

    all_devices_via_agent = session.execute(
        select(DeviceEndpoint.device_id).where(DeviceEndpoint.agent_id == agent_id)
    ).scalars().all()

    changed = 0
    for dev_id in set(all_devices_via_agent):
        if dev_id not in devices_with_other_agents:
            dev = session.get(Device, dev_id)
            if dev and dev.status in ("online", "reconnecting"):
                # 只覆盖 online/reconnecting — pending 保持 pending（未认领不管），
                # error/identity_conflict/disabled 是设备自身属性，不覆盖
                dev.status = "offline"
                changed += 1
                session.add(SiteEvent(
                    site_id=dev.site_id, event_type="device.offline",
                    source_type="agent", source_id=agent_id, device_id=dev_id,
                ))
    session.commit()
    return changed


# --- 状态查询 ------------------------------------------------------------

def list_devices(session: Session, *, site_id: str,
                 status: Optional[str] = None,
                 only_adopted: Optional[bool] = None) -> list[Device]:
    stmt = select(Device).where(Device.site_id == site_id)
    if status:
        stmt = stmt.where(Device.status == status)
    if only_adopted is True:
        stmt = stmt.where(Device.adopted_at.isnot(None))
    elif only_adopted is False:
        stmt = stmt.where(Device.adopted_at.is_(None))
    return session.execute(stmt.order_by(Device.created_at)).scalars().all()


def device_to_dict(dev: Device) -> dict:
    return {
        "id": dev.id,
        "site_id": dev.site_id,
        "display_name": dev.display_name,
        "hardware_model": dev.hardware_model,
        "firmware_version": dev.firmware_version,
        "protocol_version": dev.protocol_version,
        "phy_version": dev.phy_version,
        "capabilities": dev.capabilities,
        "status": dev.status,
        "identity_confidence": dev.identity_confidence,
        # V1.1.2.1 Task 5: 所有权字段
        "owner_agent_id": dev.owner_agent_id,
        "ownership_status": dev.ownership_status,
        "owner_assigned_at": dev.owner_assigned_at.isoformat() if dev.owner_assigned_at else None,
        "owner_updated_at": dev.owner_updated_at.isoformat() if dev.owner_updated_at else None,
        "first_seen_at": dev.first_seen_at.isoformat() if dev.first_seen_at else None,
        "last_seen_at": dev.last_seen_at.isoformat() if dev.last_seen_at else None,
        "adopted_at": dev.adopted_at.isoformat() if dev.adopted_at else None,
    }
