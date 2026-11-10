"""数据摄入管道 — Agent 事件 → 校验 → 去重 → 持久化 → WS 广播。

Phase 07.3 / 07.4：
- event_id 由上游 Agent 生成并原样入库；本模块只在缺失时按明确兼容策略补一次。
- event_id 格式校验失败 → 拒绝该 Measurement（不写入坏身份）。
- 重复 event_id 与真正的数据完整性错误必须区分：只有唯一键重复按幂等处理。
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database.models import Measurement, SiteEvent, Device, ConnectionSession, _utcnow, _uuid

log = logging.getLogger("widscan-site.ingestion")

EVENT_ID_MAX_LEN = 128
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9:_.\-]{1,128}$")

FRAME_TYPE_MAP = {
    1: "METRICS",
    2: "SPECTRUM",
    3: "TELEMETRY",
    4: "RAWIQ",
    5: "LOG",
    6: "LEVELS",
}

# --- 摄入统计（重复事件计数 / 诊断）---------------------------------------
_STATS_LOCK = threading.Lock()
_STATS: dict[str, int] = {
    "persisted": 0,
    "duplicate_events": 0,
    "invalid_event_id": 0,
    "legacy_uuid_fallback": 0,
    "derived_event_id": 0,
    "agent_provided_event_id": 0,
    "invalid_agent_timestamp": 0,
    "integrity_errors": 0,
}


def _incr(name: str, amount: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] = _STATS.get(name, 0) + amount


def get_ingestion_stats() -> dict[str, int]:
    """返回统计快照（诊断端点 / 测试使用）。"""
    with _STATS_LOCK:
        return dict(_STATS)


def reset_ingestion_stats() -> None:
    """仅用于测试隔离。"""
    with _STATS_LOCK:
        for key in _STATS:
            _STATS[key] = 0


def map_frame_type(code: int) -> str:
    return FRAME_TYPE_MAP.get(code, f"UNKNOWN_{code}")


def is_valid_event_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= EVENT_ID_MAX_LEN
        and EVENT_ID_PATTERN.match(value) is not None
    )


def parse_agent_timestamp(value: Any) -> Optional[datetime]:
    """把 Agent 传来的接收时间解析为 UTC datetime。

    支持 datetime / ISO 8601 字符串 / epoch 秒。无时区信息按 UTC 解释。
    解析失败返回 None —— 不得用 site_received_at 冒充 agent_received_at。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return None


def is_duplicate_event_error(exc: IntegrityError) -> bool:
    """识别 event_id 唯一键冲突（SQLite / PostgreSQL 两种报错文案）。"""
    text = str(getattr(exc, "orig", exc)).lower()
    if "uq_measurement_event" in text:
        return True
    return "unique" in text and "event_id" in text


def _resolve_event_id(
    *,
    device_id: str,
    device_boot_id: Optional[str],
    frame_type_code: int,
    sequence: Optional[int],
    device_timestamp_ms: Optional[int],
    provided: Optional[str],
) -> Optional[str]:
    """确定最终 event_id。返回 None 表示拒绝写入（格式非法）。

    优先级：上游 event_id（原样）> sequence 派生 > device_timestamp 派生 > uuid 兜底。
    """
    if provided is not None:
        if is_valid_event_id(provided):
            _incr("agent_provided_event_id")
            return provided
        _incr("invalid_event_id")
        log.warning(
            "reject measurement: invalid event_id (device=%s event_id=%r)",
            device_id, provided,
        )
        return None
    boot = device_boot_id or "unknown-boot"
    if sequence is not None:
        _incr("derived_event_id")
        return f"{device_id}:{boot}:{frame_type_code}:{sequence}"
    if device_timestamp_ms is not None:
        _incr("derived_event_id")
        # 保留 V1.1.2 之前的派生格式，确保旧 Agent 跨版本升级不改变事件身份
        return f"{device_id}:{boot}:{frame_type_code}:{device_timestamp_ms}"
    # 旧固件 / 旧 Agent 没有任何身份材料 —— 显式 uuid 兜底（兼容路径）
    _incr("legacy_uuid_fallback")
    log.debug(
        "event_id fallback to uuid4 (no upstream identity) device=%s frame_type=%s",
        device_id, frame_type_code,
    )
    return str(uuid.uuid4())


def build_measurement_payload_from_frame(frame_type: int, decoded: dict,
                                         *, retention_rawiq_enabled: bool = False) -> Optional[dict]:
    """
    帧 payload 的裁剪策略：
    - METRICS / TELEMETRY / LEVELS — 全量保留（JSON 小）
    - SPECTRUM / RAWIQ — RAWIQ 默认丢弃；SPECTRUM 保留但 bins 数组太大时截断
    - LOG — 全量保留
    返回 None = 不写
    """
    if frame_type == 4 and not retention_rawiq_enabled:
        return None

    if frame_type == 2 and decoded:  # SPECTRUM
        bins = decoded.get("bins") or decoded.get("levels") or decoded.get("level_bins")
        if isinstance(bins, list) and len(bins) > 512:
            decoded = dict(decoded)
            decoded["bins"] = bins[:256] + [{"__truncated__": len(bins) - 256}]
    return decoded if decoded else {}


def persist_measurement(
    session: Session,
    *,
    site_id: str,
    agent_id: Optional[str],    # V1.1.2.1 Task 6: 真实来源 Agent（历史可 None）
    device_id: str,
    session_id: Optional[str],
    frame_type_code: int,
    decoded: dict,
    agent_received_at: Optional[datetime],
    sequence: Optional[int] = None,
    source: str = "hardware",
    device_timestamp_ms: Optional[int] = None,
    retention_rawiq_enabled: bool = False,
    device_boot_id: Optional[str] = None,
    event_id: Optional[str] = None,
) -> Optional[Measurement]:
    frame_type = map_frame_type(frame_type_code)
    payload = build_measurement_payload_from_frame(
        frame_type_code, decoded, retention_rawiq_enabled=retention_rawiq_enabled,
    )
    if payload is None:
        return None  # 策略跳过（RAWIQ 默认）

    # Phase 07.4：上游 event_id 原样入库，不重算。
    resolved_event_id = _resolve_event_id(
        device_id=device_id,
        device_boot_id=device_boot_id,
        frame_type_code=frame_type_code,
        sequence=sequence,
        device_timestamp_ms=device_timestamp_ms,
        provided=event_id,
    )
    if resolved_event_id is None:
        return None  # event_id 非法 → 拒绝写入

    # Phase 07.3：agent_received_at 缺失/非法时保持 NULL，绝不用 site 时间冒充。
    agent_received = agent_received_at
    if agent_received is not None and not isinstance(agent_received, datetime):
        agent_received = parse_agent_timestamp(agent_received)
        if agent_received is None:
            _incr("invalid_agent_timestamp")
            log.warning(
                "invalid agent_received_at ignored (device=%s value=%r)",
                device_id, agent_received_at,
            )

    try:
        m = Measurement(
            site_id=site_id,
            agent_id=agent_id,                # V1.1.2.1 Task 6: 追溯来源 Agent
            device_id=device_id,
            device_boot_id=device_boot_id,     # V1.1.2.1 Task 6: 设备启动周期
            session_id=session_id,
            frame_type=frame_type,
            frame_type_code=frame_type_code,
            device_timestamp_ms=device_timestamp_ms,
            agent_received_at=agent_received,
            sequence=sequence,
            source=source,
            payload_json=payload,
            event_id=resolved_event_id,
        )
        session.add(m)
        session.flush()
        _incr("persisted")
        return m
    except IntegrityError as exc:
        session.rollback()
        if is_duplicate_event_error(exc):
            # 明确的 event_id 重复 → 幂等丢弃，不覆盖原始记录
            _incr("duplicate_events")
            log.info(
                "duplicate event ignored (site=%s device=%s event_id=%s)",
                site_id, device_id, resolved_event_id,
            )
            return None
        # 其他完整性错误（FK / NOT NULL / CHECK 等）不得吞掉
        _incr("integrity_errors")
        log.error(
            "measurement integrity error (non-duplicate) site=%s device=%s event_id=%s: %s",
            site_id, device_id, resolved_event_id, exc,
        )
        raise


def get_active_session_for_device(session: Session, device_id: str, agent_id: str) -> Optional[ConnectionSession]:
    from sqlalchemy import select as sa_select
    stmt = (
        sa_select(ConnectionSession)
        .where(
            ConnectionSession.device_id == device_id,
            ConnectionSession.agent_id == agent_id,
            ConnectionSession.status == "connected",
        )
        .order_by(ConnectionSession.connected_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def record_device_frame(
    session: Session,
    *,
    site_id: str,
    agent_id: str,
    device_id: str,
    frame_type_code: int,
    decoded: dict,
    agent_received_at: Optional[datetime] = None,
    retention_rawiq_enabled: bool = False,
    device_boot_id: Optional[str] = None,
    event_id: Optional[str] = None,
    sequence: Optional[int] = None,
    device_timestamp_ms: Optional[int] = None,
) -> Optional[Measurement]:
    sess = get_active_session_for_device(session, device_id, agent_id)
    m = persist_measurement(
        session,
        site_id=site_id,
        agent_id=agent_id,  # V1.1.2.1 Task 6: 追溯来源 Agent
        device_id=device_id,
        session_id=sess.id if sess else None,
        frame_type_code=frame_type_code,
        decoded=decoded,
        agent_received_at=agent_received_at,
        retention_rawiq_enabled=retention_rawiq_enabled,
        device_boot_id=device_boot_id,
        event_id=event_id,
        sequence=sequence,
        device_timestamp_ms=device_timestamp_ms,
    )
    session.commit()
    return m
