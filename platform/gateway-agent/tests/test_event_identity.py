"""Phase 07.3 / 07.4 — Agent 侧 event_id 与时间戳生成测试。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from widscan_agent.events import Event, EventBus
from widscan_agent.identity import (
    coerce_device_timestamp_ms,
    coerce_sequence,
    is_valid_event_id,
    make_event_id,
    utc_now_iso,
)
from widscan_agent.manager import DeviceManager
from widscan_agent.site_connector import SiteConnector
from widscan_agent.transports.simulator import SimulatorTransport


def test_event_id_format_and_validation():
    eid = make_event_id("dev-1", "boot-abcd1234", 1, sequence=42)
    assert eid == "dev-1:boot-abcd1234:1:42"
    assert is_valid_event_id(eid)
    assert not is_valid_event_id("")
    assert not is_valid_event_id("bad id with space")
    assert not is_valid_event_id("x" * 129)
    assert not is_valid_event_id(None)


def test_event_id_deterministic_for_same_event():
    """同一事件重发 → event_id 完全一致（同 Agent 内幂等基础）。"""
    a = make_event_id("dev-1", "boot-1", 1, sequence=7, device_timestamp_ms=100)
    b = make_event_id("dev-1", "boot-1", 1, sequence=7, device_timestamp_ms=100)
    assert a == b


def test_different_events_get_different_ids():
    a = make_event_id("dev-1", "boot-1", 1, sequence=1)
    b = make_event_id("dev-1", "boot-1", 1, sequence=2)
    c = make_event_id("dev-1", "boot-1", 2, sequence=1)
    d = make_event_id("dev-2", "boot-1", 1, sequence=1)
    assert len({a, b, c, d}) == 4


def test_timestamp_fallback_when_no_sequence():
    eid = make_event_id("dev-1", "boot-1", 3, sequence=None, device_timestamp_ms=1000)
    assert eid == "dev-1:boot-1:3:1000"
    assert is_valid_event_id(eid)


def test_uuid_fallback_is_valid_event_id():
    eid = make_event_id("dev-1", None, 1)
    assert is_valid_event_id(eid)


def test_sequence_wraparound_ids_are_distinct():
    last = make_event_id("dev-1", "boot-1", 1, sequence=2**32 - 1)
    first_after = make_event_id("dev-1", "boot-1", 1, sequence=0)
    assert last != first_after
    assert is_valid_event_id(last) and is_valid_event_id(first_after)


def test_cross_agent_different_boot_ids_produce_different_event_ids():
    """已知限制：两个 Agent 观察同一物理设备可能生成不同 boot_id。"""
    a = make_event_id("dev-1", "boot-agentA", 1, sequence=1)
    b = make_event_id("dev-1", "boot-agentB", 1, sequence=1)
    assert a != b  # 跨 Agent 无法自动去重


def test_utc_now_iso_has_timezone():
    value = utc_now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_coerce_helpers_reject_bad_values():
    assert coerce_sequence({"seq": 5}) == 5
    assert coerce_sequence({"seq": -1}) is None
    assert coerce_sequence({"seq": True}) is None
    assert coerce_sequence({}) is None
    assert coerce_device_timestamp_ms({"tMs": 1234}) == 1234
    assert coerce_device_timestamp_ms({"tMs": -5}) is None


def _make_manager():
    bus = EventBus()
    mgr = DeviceManager(bus=bus, poll_interval=999)
    mgr.start()
    sim = SimulatorTransport(device_id="id-sim-001")
    mgr.add_device(sim, device_id="id-sim-001")
    return bus, mgr


def test_device_frame_carries_event_id_and_agent_received_at():
    bus, mgr = _make_manager()
    captured = []
    bus.subscribe("device.frame", lambda e: captured.append(e))
    try:
        for _ in range(100):
            if captured:
                break
            time.sleep(0.05)
        assert captured, "no device.frame published"
        data = captured[0].data
        assert is_valid_event_id(data["event_id"])
        assert data["event_id"].startswith("id-sim-001:")
        assert data["agent_received_at"] is not None
        parsed = datetime.fromisoformat(data["agent_received_at"])
        assert parsed.tzinfo is not None
        assert "sequence" in data and "device_timestamp_ms" in data
    finally:
        mgr.stop()


def test_connector_envelope_preserves_event_id_and_agent_received_at():
    bus, mgr = _make_manager()
    sc = SiteConnector(
        bus=bus, agent_id="id-agent", manager=mgr,
        endpoint="ws://127.0.0.1:9", credential="", enabled=False,
    )
    try:
        dev = mgr.get("id-sim-001")
        assert dev is not None

        def _envelope(data):
            ev = Event(type="device.frame", device_id="id-sim-001", data=data)
            return sc._to_site_envelope(ev)

        fixed = {
            "type_code": 1,
            "decoded": {"seq": 99, "tMs": 5555},
            "event_id": "id-sim-001:boot-known:1:99",
            "agent_received_at": "2026-11-04T02:00:00+00:00",
            "sequence": 99,
            "device_timestamp_ms": 5555,
        }
        env = _envelope(fixed)
        assert env["event"]["data"]["event_id"] == "id-sim-001:boot-known:1:99"
        assert env["event"]["data"]["agent_received_at"] == "2026-11-04T02:00:00+00:00"
        assert env["event"]["data"]["sequence"] == 99
        assert env["event"]["data"]["device_timestamp_ms"] == 5555

        # 缺失 event_id 时 Connector 补齐一次，但不覆盖已有
        missing = {
            "type_code": 1,
            "decoded": {"seq": 3, "tMs": 10},
        }
        env2 = _envelope(missing)
        eid2 = env2["event"]["data"]["event_id"]
        assert is_valid_event_id(eid2)
        assert eid2.endswith(":1:3")
        assert env2["event"]["data"]["agent_received_at"]

        # JSON 可序列化（WS 发送前提）
        json.dumps(env)
        json.dumps(env2)
    finally:
        mgr.stop()
