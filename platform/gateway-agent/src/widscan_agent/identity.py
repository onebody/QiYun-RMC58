"""事件身份与时间戳工具（Phase 07.3 / 07.4）。

event_id 语义：
    {device_id}:{device_boot_id}:{frame_type}:{sequence}
    {device_id}:{device_boot_id}:{frame_type}:{device_timestamp_ms}   （无 seq 时）
    uuid4                                                           （无任何身份材料时）

设计约束：
- event_id 在 Agent 接收事件时**只生成一次**，之后全链路原样传递。
- Site Connector / Site Server 不允许重算已存在的 event_id。
- device_boot_id 由 Agent 本地基于 uptime_ms 生成，跨 Agent 不保证一致（已知限制）。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9:_.\-]{1,128}$")
EVENT_ID_MAX_LEN = 128


def make_event_id(
    device_id: str,
    device_boot_id: Optional[str],
    frame_type: int,
    sequence: Optional[int] = None,
    device_timestamp_ms: Optional[int] = None,
) -> str:
    """按固定格式生成 event_id。缺少身份材料时退化为 uuid4（显式兜底）。"""
    boot = device_boot_id or "unknown-boot"
    if sequence is not None:
        return f"{device_id}:{boot}:{int(frame_type)}:{int(sequence)}"
    if device_timestamp_ms is not None:
        return f"{device_id}:{boot}:{int(frame_type)}:{int(device_timestamp_ms)}"
    return str(uuid.uuid4())


def is_valid_event_id(value: Any) -> bool:
    """event_id 格式校验：1–128 字符，仅允许字母数字与 : _ . -"""
    return (
        isinstance(value, str)
        and 0 < len(value) <= EVENT_ID_MAX_LEN
        and EVENT_ID_PATTERN.match(value) is not None
    )


def utc_now_iso() -> str:
    """当前 UTC 时间（ISO 8601，带 +00:00 时区）。"""
    return datetime.now(timezone.utc).isoformat()


def coerce_sequence(decoded: Any) -> Optional[int]:
    """从解码帧中提取 sequence（uint32）。非法值返回 None，不伪造。"""
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("seq")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def coerce_device_timestamp_ms(decoded: Any) -> Optional[int]:
    """从解码帧中提取设备时间戳（毫秒）。缺失则返回 None。"""
    if not isinstance(decoded, dict):
        return None
    value = decoded.get("tMs")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None
