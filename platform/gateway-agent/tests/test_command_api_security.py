"""Phase 07.1 — Gateway Agent 本地命令 API 认证测试。"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from widscan_agent.api import build_app, mask_secret
from widscan_agent.config import GatewayConfig, resolve_bind_host
from widscan_agent.events import EventBus
from widscan_agent.manager import DeviceManager
from widscan_agent.transports.simulator import SimulatorTransport

TOKEN = "unit-test-token"


def _build_app(*, token: str = TOKEN, require_auth: bool = True):
    bus = EventBus()
    mgr = DeviceManager(bus=bus, poll_interval=999)
    mgr.start()
    sim = SimulatorTransport(device_id="sec-sim-001")
    mgr.add_device(sim, device_id="sec-sim-001")
    app = build_app(
        manager=mgr, bus=bus, agent_id="sec-agent",
        frame_parser_path="/tmp/fp.py",
        api_token=token, require_auth=require_auth,
    )
    time.sleep(0.5)
    return app, mgr


def _client(app, *, token=None):
    headers = {"X-API-Token": token} if token is not None else {}
    return TestClient(app, headers=headers)


def test_command_without_token_returns_401():
    app, mgr = _build_app()
    try:
        with _client(app) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code == 401
            assert r.headers.get("www-authenticate") == "Bearer"
    finally:
        mgr.stop()


def test_command_with_wrong_token_returns_401():
    app, mgr = _build_app()
    try:
        with _client(app, token="wrong-token") as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code == 401
    finally:
        mgr.stop()


def test_command_with_correct_token_header_returns_200():
    app, mgr = _build_app()
    try:
        with _client(app, token=TOKEN) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code == 200, r.text
    finally:
        mgr.stop()


def test_command_with_bearer_token_returns_200():
    app, mgr = _build_app()
    try:
        with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code == 200, r.text
    finally:
        mgr.stop()


def test_missing_token_config_blocks_all_commands_with_503():
    """生产保护：Token 未配置时拒绝所有命令请求，而不是放开。"""
    app, mgr = _build_app(token="", require_auth=True)
    try:
        with _client(app) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code == 503
            r2 = c.post("/api/v1/devices/sec-sim-001/connect")
            assert r2.status_code == 503
    finally:
        mgr.stop()


def test_explicit_dev_mode_without_auth_only_when_configured():
    """显式 require_auth=False 才允许本地开发免认证（不是生产默认）。"""
    assert GatewayConfig().api.require_auth is True  # 生产默认
    app, mgr = _build_app(token="", require_auth=False)
    try:
        with _client(app) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
            assert r.status_code in (200, 500)  # sim 已连接时 200
    finally:
        mgr.stop()


def test_auth_does_not_bypass_command_whitelist():
    app, mgr = _build_app()
    try:
        with _client(app, token=TOKEN) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "ERASE_FLASH"})
            assert r.status_code == 403
    finally:
        mgr.stop()


def test_authenticated_invalid_payload_rejected_by_schema():
    app, mgr = _build_app()
    try:
        with _client(app, token=TOKEN) as c:
            r = c.post("/api/v1/devices/sec-sim-001/commands", json={"command": ""})
            assert r.status_code == 422
    finally:
        mgr.stop()


def test_unauthenticated_request_cannot_reach_device_endpoints():
    app, mgr = _build_app()
    try:
        with _client(app) as c:
            for path in (
                "/api/v1/devices/sec-sim-001/connect",
                "/api/v1/devices/sec-sim-001/disconnect",
                "/api/v1/devices/sec-sim-001/reconnect",
                "/api/v1/devices/sec-sim-001/commands",
                "/api/v1/site-connector/enable",
                "/api/v1/site-connector/disable",
                "/api/v1/site-connector/pair",
            ):
                r = c.post(path, json={})
                assert r.status_code == 401, f"{path} -> {r.status_code}"
    finally:
        mgr.stop()


def test_token_never_appears_in_logs(caplog):
    app, mgr = _build_app()
    caplog.set_level(logging.DEBUG)
    try:
        with _client(app, token="leaky-token-value") as c:
            c.post("/api/v1/devices/sec-sim-001/commands", json={"command": "INFO"})
        assert "leaky-token-value" not in caplog.text
        assert TOKEN not in caplog.text
    finally:
        mgr.stop()


def test_mask_secret_never_returns_token():
    assert mask_secret("secret") != "secret"
    assert mask_secret("secret") == "***"
    assert mask_secret("") == ""


def test_diagnostics_reports_security_without_token():
    app, mgr = _build_app()
    try:
        with _client(app, token=TOKEN) as c:
            body = c.get("/api/v1/diagnostics").json()
            sec = body["command_api_security"]
            assert sec["require_auth"] is True
            assert sec["token_configured"] is True
            assert sec["token"] == "***"
    finally:
        mgr.stop()


# --- 监听地址 ---------------------------------------------------------

def test_bind_local_hosts_are_forced_to_loopback():
    for requested in ("127.0.0.1", "localhost", "::1", ""):
        host, warning = resolve_bind_host(
            requested, auth_required=True, token_configured=True)
        assert host == "127.0.0.1"
        assert warning == ""


def test_bind_external_requires_auth_and_token():
    host, warning = resolve_bind_host(
        "0.0.0.0", auth_required=True, token_configured=True)
    assert host == "0.0.0.0"
    assert "allowed" in warning


def test_bind_external_rejected_without_token():
    host, warning = resolve_bind_host(
        "0.0.0.0", auth_required=True, token_configured=False)
    assert host == "127.0.0.1"
    assert "rejected" in warning


def test_bind_external_rejected_when_auth_disabled():
    host, warning = resolve_bind_host(
        "0.0.0.0", auth_required=False, token_configured=True)
    assert host == "127.0.0.1"
    assert "rejected" in warning
