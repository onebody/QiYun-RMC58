"""Phase 07 跨模块集成测试 — 真实 Site Server + 真实 WebSocket。

覆盖：
- 首条 auth message 认证（生产默认禁止 URL credential）
- Owner 命令授权 / Observer 命令拒绝
- 设备归属不被其他 Agent 抢占
- Measurement 三类时间戳 + 来源 Agent
- event_id 全链路保持、重复事件幂等
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import httpx
import pytest
import uvicorn
import websockets

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from widscan_site.config import DatabaseConfig, ServerConfig, WebSocketConfig
from widscan_site.database.models import Measurement, Device
from widscan_site.ingestion import get_ingestion_stats
from widscan_site.main import create_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _boot(cfg: ServerConfig, port: int):
    app = create_app(cfg)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                return server, th, base
        except Exception:
            pass
        time.sleep(0.1)
    server.should_exit = True
    th.join(timeout=5)
    pytest.fail("site server did not start")


@pytest.fixture(scope="function")
def site(tmp_path):
    port = _free_port()
    cfg = ServerConfig(
        database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'site.db'}"),
        websocket=WebSocketConfig(allow_url_credential=False, auth_timeout_seconds=3.0),
    )
    server, th, base = _boot(cfg, port)

    engine = create_engine(f"sqlite:///{tmp_path / 'site.db'}", future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    yield {
        "base": base,
        "ws": f"ws://127.0.0.1:{port}/ws/v1/agents/connect",
        "session": Session,
        "port": port,
        "cfg": cfg,
        "server": server,
        "thread": th,
    }

    engine.dispose()
    server.should_exit = True
    th.join(timeout=5)


def _pair(base: str, agent_id: str) -> str:
    code = httpx.post(base + "/api/v1/agents/pairing-codes", json={}, timeout=5).json()
    r = httpx.post(base + "/api/v1/agents/pair", json={
        "pairing_code": code["pairing_code"],
        "agent_id": agent_id,
        "platform": "test",
        "name": agent_id,
    }, timeout=5)
    assert r.status_code == 200, r.text
    return code["plain_credential"]


async def _connect(ws_url: str, agent_id: str, credential: str):
    ws = await websockets.connect(ws_url, open_timeout=5)
    await ws.send(json.dumps({"type": "auth", "agent_id": agent_id,
                              "credential": credential}))
    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert resp["type"] == "connected", resp
    return ws


async def _send(ws, payload: dict):
    await ws.send(json.dumps(payload))


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _rows(site) -> list[Measurement]:
    s = site["session"]()
    try:
        return s.query(Measurement).all()
    finally:
        s.close()


def _devices(site) -> list[Device]:
    s = site["session"]()
    try:
        return s.query(Device).all()
    finally:
        s.close()


ONLINE_EVENT = {
    "type": "agent.event",
    "device_boot_id": "boot-0001",
    "event": {
        "type": "device.online",
        "device_id": "dev-1",
        "data": {"info": {"hardware_model": "esp32c5", "firmware_version": "0.1.0"},
                 "transport": {"kind": "simulator", "path": "sim:dev-1"}},
    },
}


FRAME_EVENT = {
    "type": "agent.event",
    "device_boot_id": "boot-0001",
    "event": {
        "type": "device.frame",
        "device_id": "dev-1",
        "data": {
            "type_code": 1,
            "decoded": {"seq": 42, "tMs": 1000, "rssi": -40},
            "event_id": "dev-1:boot-0001:1:42",
            "agent_received_at": "2026-11-04T02:00:00+00:00",
            "sequence": 42,
            "device_timestamp_ms": 1000,
        },
    },
}


def test_url_credential_rejected_by_production_default(site):
    """默认生产配置：URL Query Credential 被拒绝。"""
    async def _run():
        ws = await websockets.connect(
            site["ws"] + "?agent_id=agent-A&credential=whatever", open_timeout=5)
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg["type"] == "error"
            assert "URL credential" in msg["message"]
        finally:
            await ws.close()

    asyncio.run(_run())


def test_first_message_business_data_rejected(site):
    """未认证连接不能提交业务数据。"""
    async def _run():
        ws = await websockets.connect(site["ws"], open_timeout=5)
        try:
            await _send(ws, ONLINE_EVENT)
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg["type"] == "error"
            assert "expected auth" in msg["message"]
        finally:
            await ws.close()

    asyncio.run(_run())


def test_owner_command_authorized_observer_denied(site):
    cred_a = _pair(site["base"], "agent-A")
    cred_b = _pair(site["base"], "agent-B")

    async def _run():
        ws_a = await _connect(site["ws"], "agent-A", cred_a)
        ws_b = await _connect(site["ws"], "agent-B", cred_b)
        try:
            await _send(ws_a, ONLINE_EVENT)
            assert _wait_for(lambda: len(_devices(site)) == 1)
            s = site["session"]()
            try:
                dev = s.query(Device).filter(Device.id == "dev-1").one()
                assert dev.owner_agent_id == "agent-A"
            finally:
                s.close()

            # Observer 无权认证设备
            await _send(ws_b, ONLINE_EVENT)
            time.sleep(0.3)
            s = site["session"]()
            try:
                dev = s.query(Device).filter(Device.id == "dev-1").one()
                assert dev.owner_agent_id == "agent-A", "observer must not steal ownership"
            finally:
                s.close()

            # Owner 命令请求：授权通过，但远程下发未实现
            await _send(ws_a, {"type": "command.request", "request_id": "a-1",
                               "device_id": "dev-1", "command": "INFO"})
            resp_a = json.loads(await asyncio.wait_for(ws_a.recv(), timeout=5))
            assert resp_a["type"] == "command.response"
            assert resp_a["ok"] is False
            assert resp_a["reply"] == "remote command dispatch not implemented"

            # Observer 命令请求：拒绝
            await _send(ws_b, {"type": "command.request", "request_id": "b-1",
                               "device_id": "dev-1", "command": "INFO"})
            resp_b = json.loads(await asyncio.wait_for(ws_b.recv(), timeout=5))
            assert resp_b["ok"] is False
            assert resp_b["reply"] == "denied: observer_read_only"
        finally:
            await ws_a.close()
            await ws_b.close()

    asyncio.run(_run())


def test_measurement_timestamps_and_event_id_end_to_end(site):
    cred_a = _pair(site["base"], "agent-A")

    async def _run():
        ws = await _connect(site["ws"], "agent-A", cred_a)
        try:
            await _send(ws, ONLINE_EVENT)
            time.sleep(0.2)
            await _send(ws, FRAME_EVENT)
            assert _wait_for(lambda: len(_rows(site)) == 1)
        finally:
            await ws.close()

    asyncio.run(_run())

    m = _rows(site)[0]
    assert m.event_id == "dev-1:boot-0001:1:42"
    assert m.agent_id == "agent-A"
    assert m.device_boot_id == "boot-0001"
    assert m.sequence == 42
    assert m.device_timestamp_ms == 1000
    assert m.agent_received_at is not None
    assert m.agent_received_at.replace(tzinfo=timezone.utc) == datetime(
        2026, 11, 4, 2, 0, tzinfo=timezone.utc)
    assert m.created_at is not None
    assert m.created_at != m.agent_received_at


def test_duplicate_event_deduplicated_end_to_end(site):
    cred_a = _pair(site["base"], "agent-A")

    async def _run():
        ws = await _connect(site["ws"], "agent-A", cred_a)
        try:
            await _send(ws, ONLINE_EVENT)
            time.sleep(0.2)
            await _send(ws, FRAME_EVENT)
            await _send(ws, FRAME_EVENT)   # 同一 event_id 重发
            assert _wait_for(lambda: get_ingestion_stats()["duplicate_events"] >= 1)
            # 连接必须仍然可用
            await _send(ws, {"type": "heartbeat"})
        finally:
            await ws.close()

    asyncio.run(_run())
    assert len(_rows(site)) == 1


def test_cross_agent_same_physical_event_remains_two_rows(site):
    """已知限制：不同 Agent 的 boot_id 不同 → event_id 不同 → 不去重。"""
    cred_a = _pair(site["base"], "agent-A")
    cred_b = _pair(site["base"], "agent-B")

    frame_b = json.loads(json.dumps(FRAME_EVENT))
    frame_b["device_boot_id"] = "boot-agentB"
    frame_b["event"]["data"]["event_id"] = "dev-1:boot-agentB:1:42"

    async def _run():
        ws_a = await _connect(site["ws"], "agent-A", cred_a)
        ws_b = await _connect(site["ws"], "agent-B", cred_b)
        try:
            await _send(ws_a, ONLINE_EVENT)
            time.sleep(0.2)
            await _send(ws_a, FRAME_EVENT)
            await _send(ws_b, frame_b)
            assert _wait_for(lambda: len(_rows(site)) == 2)
        finally:
            await ws_a.close()
            await ws_b.close()

    asyncio.run(_run())

    rows = _rows(site)
    assert len(rows) == 2
    assert {m.agent_id for m in rows} == {"agent-A", "agent-B"}
    assert {m.event_id for m in rows} == {
        "dev-1:boot-0001:1:42", "dev-1:boot-agentB:1:42"}


def test_agent_disconnect_keeps_owner_and_reconnect_restores(site):
    cred = _pair(site["base"], "agent-A")

    async def _first():
        ws = await _connect(site["ws"], "agent-A", cred)
        await _send(ws, ONLINE_EVENT)
        assert _wait_for(lambda: len(_devices(site)) == 1)
        await ws.close()
        # 断线后 Device 变 offline，但 Owner 不被清除
        assert _wait_for(lambda: _devices(site)[0].status == "offline")
        s = site["session"]()
        try:
            dev = s.query(Device).filter(Device.id == "dev-1").one()
            assert dev.owner_agent_id == "agent-A"
        finally:
            s.close()

    asyncio.run(_first())

    async def _second():
        ws = await _connect(site["ws"], "agent-A", cred)
        try:
            await _send(ws, ONLINE_EVENT)
            assert _wait_for(lambda: _devices(site)[0].status == "online")
            s = site["session"]()
            try:
                dev = s.query(Device).filter(Device.id == "dev-1").one()
                assert dev.owner_agent_id == "agent-A"
            finally:
                s.close()
        finally:
            await ws.close()

    asyncio.run(_second())


def test_site_server_restart_preserves_measurements_and_ownership(site):
    cred = _pair(site["base"], "agent-A")

    async def _run():
        ws = await _connect(site["ws"], "agent-A", cred)
        try:
            await _send(ws, ONLINE_EVENT)
            time.sleep(0.2)
            await _send(ws, FRAME_EVENT)
            assert _wait_for(lambda: len(_rows(site)) == 1)
        finally:
            await ws.close()

    asyncio.run(_run())

    # 重启 Site Server（同一 SQLite 文件）
    site["server"].should_exit = True
    site["thread"].join(timeout=10)
    server, th, base = _boot(site["cfg"], site["port"])
    site["server"], site["thread"], site["base"] = server, th, base

    # 历史 Measurement 仍在
    assert len(_rows(site)) == 1
    assert _rows(site)[0].event_id == "dev-1:boot-0001:1:42"

    # 归属与设备记录仍在，且能通过 REST 查询
    s = site["session"]()
    try:
        dev = s.query(Device).filter(Device.id == "dev-1").one()
        assert dev.owner_agent_id == "agent-A"
    finally:
        s.close()

    res = httpx.get(f"{base}/api/v1/devices/dev-1/measurements", timeout=5)
    assert res.status_code == 200
    rows = res.json()
    assert isinstance(rows, list) and len(rows) >= 1
    assert rows[0]["event_id"] == "dev-1:boot-0001:1:42"
