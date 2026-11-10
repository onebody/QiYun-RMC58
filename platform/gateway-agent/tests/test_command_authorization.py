"""Phase 07.2 — Gateway Agent 侧命令归属授权测试。"""

from __future__ import annotations

import asyncio
import time

from widscan_agent.events import EventBus
from widscan_agent.manager import DeviceManager
from widscan_agent.site_connector import CommandState, SiteConnector
from widscan_agent.transports.simulator import SimulatorTransport


def _connector(agent_id: str = "agent-A"):
    bus = EventBus()
    mgr = DeviceManager(bus=bus, poll_interval=999)
    mgr.start()
    sim = SimulatorTransport(device_id="auth-sim-001")
    mgr.add_device(sim, device_id="auth-sim-001")
    time.sleep(0.6)
    sc = SiteConnector(
        bus=bus, agent_id=agent_id, manager=mgr,
        endpoint="ws://127.0.0.1:9", credential="", enabled=False,
    )
    return sc, mgr


def _run(sc, msg):
    asyncio.run(sc._handle_command_request(msg))


def test_owner_agent_command_in_scope_is_executed():
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-1",
            "device_id": "auth-sim-001",
            "command": "INFO",
            "target_agent_id": "agent-A",
        })
        rec = sc._command_registry["req-1"]
        assert rec.state in (CommandState.SUCCEEDED, CommandState.FAILED)
    finally:
        mgr.stop()


def test_command_for_other_agent_is_rejected():
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-2",
            "device_id": "auth-sim-001",
            "command": "INFO",
            "target_agent_id": "agent-B",
        })
        rec = sc._command_registry["req-2"]
        assert rec.state == CommandState.REJECTED
        assert "ownership mismatch" in rec.reply
        assert sc._commands_unauthorized == 1
    finally:
        mgr.stop()


def test_forged_legacy_agent_id_is_rejected():
    """伪造消息内 agent_id（旧字段）同样必须被拒绝。"""
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-3",
            "device_id": "auth-sim-001",
            "command": "INFO",
            "agent_id": "agent-B",
        })
        rec = sc._command_registry["req-3"]
        assert rec.state == CommandState.REJECTED
        assert sc._commands_unauthorized == 1
    finally:
        mgr.stop()


def test_unknown_device_never_executes():
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-4",
            "device_id": "not-my-device",
            "command": "INFO",
            "target_agent_id": "agent-A",
        })
        rec = sc._command_registry["req-4"]
        assert rec.state == CommandState.REJECTED
        assert sc._commands_succeeded == 0
    finally:
        mgr.stop()


def test_command_whitelist_still_enforced():
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-5",
            "device_id": "auth-sim-001",
            "command": "ERASE_FLASH",
            "target_agent_id": "agent-A",
        })
        rec = sc._command_registry["req-5"]
        assert rec.state == CommandState.REJECTED
    finally:
        mgr.stop()


def test_duplicate_request_id_is_idempotent():
    sc, mgr = _connector("agent-A")
    try:
        _run(sc, {
            "type": "command.request",
            "request_id": "req-6",
            "device_id": "auth-sim-001",
            "command": "INFO",
        })
        first_state = sc._command_registry["req-6"].state
        _run(sc, {
            "type": "command.request",
            "request_id": "req-6",
            "device_id": "auth-sim-001",
            "command": "INFO",
        })
        assert sc._command_registry["req-6"].state == first_state
    finally:
        mgr.stop()
