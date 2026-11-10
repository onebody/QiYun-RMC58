"""V1.1.2.1 Task 7: Authentication URL Credential Hardening 测试。

策略：
- 单元测试为主（不需要完整 WebSocket 生命周期）
- URL 脱敏 / 配置默认值 / 迁移兼容 — 单元测试
- Auth 完整流程 — 独立脚本 integration_ws_test.py（TestClient 有 async 生命周期问题）
"""

from __future__ import annotations

import pytest


# -----------------------------------------------------------------
# 1. URL 脱敏工具函数
# -----------------------------------------------------------------

from widscan_site.agents.connection import _redact_ws_url


def test_url_redaction_basic():
    assert (
        _redact_ws_url("/ws/v1/agents/connect?agent_id=a&credential=SECRET")
        == "/ws/v1/agents/connect?agent_id=a&credential=***"
    )


def test_url_redaction_no_credential():
    # 无 credential → 不变
    assert _redact_ws_url("/ws/v1/agents/connect?agent_id=a") == \
        "/ws/v1/agents/connect?agent_id=a"


def test_url_redaction_multi_param():
    assert (
        _redact_ws_url("ws://host/path?credential=abc&other=xyz")
        == "ws://host/path?credential=***&other=xyz"
    )


def test_url_redaction_credential_last():
    assert (
        _redact_ws_url("ws://host/path?a=1&credential=xyz")
        == "ws://host/path?a=1&credential=***"
    )


# -----------------------------------------------------------------
# 2. 配置默认值 — 生产安全正确
# -----------------------------------------------------------------

from widscan_site.config import ServerConfig, WebSocketConfig


def test_production_defaults_disallow_url_credential():
    """生产默认配置：allow_url_credential=False。"""
    cfg = ServerConfig()  # 零参数
    assert cfg.websocket.allow_url_credential is False
    assert cfg.websocket.auth_timeout_seconds == 10.0


def test_local_compat_explicitly_enabled():
    """本地兼容必须显式开启。"""
    cfg = ServerConfig(
        websocket=WebSocketConfig(allow_url_credential=True)
    )
    assert cfg.websocket.allow_url_credential is True


def test_yaml_config_loads_websocket_section():
    """YAML 配置能正确解析 websocket 段。"""
    import tempfile, os
    yaml_text = """
site:
  id: site-001
websocket:
  allow_url_credential: true
  auth_timeout_seconds: 15.0
"""
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    tmp.write(yaml_text)
    tmp.close()
    try:
        from widscan_site.config import load_config
        cfg = load_config(tmp.name)
        assert cfg.websocket.allow_url_credential is True
        assert cfg.websocket.auth_timeout_seconds == 15.0
    finally:
        os.unlink(tmp.name)


def test_missing_yaml_websocket_section_uses_defaults():
    """YAML 无 websocket 段时使用默认值（生产安全）。"""
    import tempfile, os
    yaml_text = """
site:
  id: site-001
"""
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    tmp.write(yaml_text)
    tmp.close()
    try:
        from widscan_site.config import load_config
        cfg = load_config(tmp.name)
        assert cfg.websocket.allow_url_credential is False  # 生产默认
    finally:
        os.unlink(tmp.name)


# -----------------------------------------------------------------
# 3. 迁移兼容 — 002 downgrade + upgrade 往返
# -----------------------------------------------------------------

def test_alembic_002_roundtrip():
    """002 迁移 upgrade → downgrade → upgrade 往返正常。"""
    import tempfile, os
    from alembic.config import Config
    from alembic import command

    tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpdb.close()
    os.unlink(tmpdb.name)

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmpdb.name}")

    try:
        # Upgrade
        command.upgrade(cfg, "head")

        # Verify new cols present
        import sqlite3
        conn = sqlite3.connect(tmpdb.name)
        dev_cols = {r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()}
        meas_cols = {r[1] for r in conn.execute("PRAGMA table_info(measurements)").fetchall()}
        assert "owner_agent_id" in dev_cols
        assert "ownership_status" in dev_cols
        assert "agent_id" in meas_cols
        assert "device_boot_id" in meas_cols
        conn.close()

        # Downgrade
        command.downgrade(cfg, "base")

        # Verify cols removed
        conn2 = sqlite3.connect(tmpdb.name)
        dev_cols2 = {r[1] for r in conn2.execute("PRAGMA table_info(devices)").fetchall()}
        meas_cols2 = {r[1] for r in conn2.execute("PRAGMA table_info(measurements)").fetchall()}
        assert "owner_agent_id" not in dev_cols2
        assert "agent_id" not in meas_cols2
        conn2.close()

        # Upgrade again
        command.upgrade(cfg, "head")
        conn3 = sqlite3.connect(tmpdb.name)
        dev_cols3 = {r[1] for r in conn3.execute("PRAGMA table_info(devices)").fetchall()}
        meas_cols3 = {r[1] for r in conn3.execute("PRAGMA table_info(measurements)").fetchall()}
        assert "owner_agent_id" in dev_cols3
        assert "agent_id" in meas_cols3
        conn3.close()
    finally:
        try:
            os.unlink(tmpdb.name)
        except OSError:
            pass
