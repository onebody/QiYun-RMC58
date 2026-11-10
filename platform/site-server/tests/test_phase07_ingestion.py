"""Phase 07.3 / 07.4 — Site Server 时间戳与 event_id 幂等测试（真实 SQLite）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from widscan_site.database.models import Measurement
from widscan_site.ingestion import (
    get_ingestion_stats,
    is_duplicate_event_error,
    is_valid_event_id,
    parse_agent_timestamp,
    persist_measurement,
    record_device_frame,
    reset_ingestion_stats,
)


@pytest.fixture(autouse=True)
def _reset_stats():
    reset_ingestion_stats()
    yield


def _frames(session):
    return session.query(Measurement).all()


def test_upstream_event_id_preserved(session):
    event_id = "dev-9:boot-abc123:1:77"
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-9",
        frame_type_code=1, decoded={"seq": 77, "tMs": 1200},
        device_boot_id="boot-abc123", event_id=event_id,
        agent_received_at="2026-11-04T02:00:00+00:00",
        sequence=77, device_timestamp_ms=1200,
    )
    assert m is not None
    assert m.event_id == event_id
    assert get_ingestion_stats()["agent_provided_event_id"] == 1


def test_event_id_missing_uses_upstream_identity_when_available(session):
    """旧 Agent 无 event_id → 按 device/boot/frame/sequence 派生（兼容路径）。"""
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-old", device_id="dev-old",
        frame_type_code=1, decoded={"seq": 5}, device_boot_id="boot-old",
        sequence=5,
    )
    assert m.event_id == "dev-old:boot-old:1:5"
    assert get_ingestion_stats()["derived_event_id"] == 1


def test_invalid_event_id_is_rejected(session):
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-x",
        frame_type_code=1, decoded={"seq": 1}, event_id="bad id with space",
    )
    assert m is None
    assert _frames(session) == []
    assert get_ingestion_stats()["invalid_event_id"] == 1


def test_unknown_frame_type_maps_without_crash(session):
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-y",
        frame_type_code=99, decoded={"x": 1}, event_id="dev-y:boot-1:99:1",
    )
    assert m is not None and m.frame_type == "UNKNOWN_99"


def test_duplicate_event_is_idempotent_and_does_not_overwrite(session):
    kwargs = dict(
        site_id="site-001", agent_id="agent-A", device_id="dev-dup",
        frame_type_code=1, device_boot_id="boot-1", event_id="dev-dup:boot-1:1:10",
        sequence=10,
    )
    first = record_device_frame(session, decoded={"seq": 10, "rssi": -50}, **kwargs)
    assert first is not None
    second = record_device_frame(session, decoded={"seq": 10, "rssi": -90}, **kwargs)
    assert second is None
    rows = _frames(session)
    assert len(rows) == 1
    # 原始 payload 未被覆盖
    assert rows[0].payload_json["rssi"] == -50
    assert get_ingestion_stats()["duplicate_events"] == 1


def test_distinct_events_are_both_persisted(session):
    for seq in (11, 12):
        record_device_frame(
            session, site_id="site-001", agent_id="agent-A", device_id="dev-multi",
            frame_type_code=1, decoded={"seq": seq}, device_boot_id="boot-1",
            event_id=f"dev-multi:boot-1:1:{seq}", sequence=seq,
        )
    assert len(_frames(session)) == 2


def test_non_duplicate_integrity_error_is_not_swallowed(session):
    """非 event_id 重复的完整性错误必须抛出，不能当作幂等丢弃。"""
    with pytest.raises(IntegrityError):
        persist_measurement(
            session,
            site_id=None,  # NOT NULL 违反 — 不是唯一键重复
            agent_id="agent-A",
            device_id="dev-bad",
            session_id=None,
            frame_type_code=1,
            decoded={"seq": 1},
            agent_received_at=None,
            event_id="dev-bad:boot-1:1:1",
        )
    assert get_ingestion_stats()["integrity_errors"] == 1


def test_is_duplicate_event_error_only_matches_event_unique_key():
    from sqlalchemy.exc import IntegrityError as IE

    dup_pg = IE("stmt", {"x": 1}, Exception(
        'duplicate key value violates unique constraint "uq_measurement_event"'))
    dup_sqlite = IE("stmt", {"x": 1}, Exception(
        "UNIQUE constraint failed: measurements.site_id, measurements.event_id"))
    other = IE("stmt", {"x": 1}, Exception(
        "NOT NULL constraint failed: measurements.site_id"))
    assert is_duplicate_event_error(dup_pg)
    assert is_duplicate_event_error(dup_sqlite)
    assert not is_duplicate_event_error(other)


def test_three_timestamps_are_distinct_fields(session):
    agent_ts = datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-ts",
        frame_type_code=1, decoded={"seq": 1},
        device_boot_id="boot-1", event_id="dev-ts:boot-1:1:1",
        agent_received_at=agent_ts, sequence=1, device_timestamp_ms=555,
    )
    assert m.agent_received_at == agent_ts
    assert m.device_timestamp_ms == 555
    assert m.created_at is not None            # site_received_at
    assert m.created_at != m.agent_received_at
    assert m.agent_received_at.tzinfo is not None


def test_missing_device_timestamp_stays_null(session):
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-nots",
        frame_type_code=2, decoded={"bins": [1, 2]}, device_boot_id="boot-1",
        event_id="dev-nots:boot-1:2:1",
    )
    assert m.device_timestamp_ms is None
    assert m.agent_received_at is None  # 绝不拿 site 时间冒充


def test_historical_measurement_stays_null():
    """历史记录不被回填（本轮不迁移历史数据）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from widscan_site.database.models import Base

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    legacy = Measurement(
        site_id="site-001", agent_id=None,
        device_id="legacy-dev", frame_type="METRICS", frame_type_code=1,
        payload_json={"seq": 1}, event_id=str(uuid.uuid4()),
        agent_received_at=None, device_timestamp_ms=None,
    )
    s.add(legacy)
    s.commit()
    stored = s.query(Measurement).one()
    assert stored.agent_received_at is None
    assert stored.agent_id is None
    assert stored.event_id == legacy.event_id
    s.close()
    engine.dispose()


def test_parse_agent_timestamp_timezone_conversion():
    utc = parse_agent_timestamp("2026-11-04T10:00:00+08:00")
    assert utc is not None
    assert utc.hour == 2 and utc.minute == 0
    assert utc.tzinfo == timezone.utc

    naive = parse_agent_timestamp("2026-11-04T00:00:00")
    assert naive.tzinfo == timezone.utc

    assert parse_agent_timestamp("not-a-time") is None
    assert parse_agent_timestamp(None) is None


def test_invalid_agent_timestamp_does_not_fake_site_time(session):
    m = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-badts",
        frame_type_code=1, decoded={"seq": 1}, device_boot_id="boot-1",
        event_id="dev-badts:boot-1:1:1", agent_received_at="garbage",
    )
    assert m.agent_received_at is None
    assert get_ingestion_stats()["invalid_agent_timestamp"] == 1


def test_sequence_wraparound_ids_valid_and_distinct(session):
    last = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-wrap",
        frame_type_code=1, decoded={"seq": 2**32 - 1}, device_boot_id="boot-1",
        event_id="dev-wrap:boot-1:1:4294967295", sequence=2**32 - 1,
    )
    first = record_device_frame(
        session, site_id="site-001", agent_id="agent-A", device_id="dev-wrap",
        frame_type_code=1, decoded={"seq": 0}, device_boot_id="boot-1",
        event_id="dev-wrap:boot-1:1:0", sequence=0,
    )
    assert last.event_id != first.event_id
    assert len(_frames(session)) == 2


def test_cross_agent_same_physical_event_not_deduplicated(session):
    """已知限制：两个 Agent 的 device_boot_id 不同 → event_id 不同 → 2 条记录。"""
    common = dict(
        site_id="site-001", device_id="dev-shared", frame_type_code=1,
        decoded={"seq": 1}, sequence=1,
    )
    a = record_device_frame(
        session, agent_id="agent-A", device_boot_id="boot-agentA",
        event_id="dev-shared:boot-agentA:1:1", **common)
    b = record_device_frame(
        session, agent_id="agent-B", device_boot_id="boot-agentB",
        event_id="dev-shared:boot-agentB:1:1", **common)
    assert a is not None and b is not None
    assert a.event_id != b.event_id
    assert len(_frames(session)) == 2
    # 来源 Agent 必须各自真实记录
    assert {m.agent_id for m in _frames(session)} == {"agent-A", "agent-B"}


def test_valid_event_id_bounds():
    assert is_valid_event_id("a:1")
    assert is_valid_event_id("x" * 128)
    assert not is_valid_event_id("x" * 129)
    assert not is_valid_event_id("")
    assert not is_valid_event_id(None)
    assert not is_valid_event_id("spaces in id")
