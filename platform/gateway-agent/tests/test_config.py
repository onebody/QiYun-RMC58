"""Config 加载 — YAML + 默认值。"""

import os
import tempfile
from pathlib import Path

import pytest

from widscan_agent.config import GatewayConfig, load_config


def test_default_config():
    cfg = GatewayConfig()
    assert cfg.agent.id == "gateway-001"
    assert cfg.api.host == "127.0.0.1"
    assert cfg.api.port == 8765
    assert cfg.transport.baudrate == 921600
    assert cfg.reconnect.enabled is True


def test_load_from_yaml(tmp_path: Path):
    p = tmp_path / "agent.yaml"
    p.write_text(
        """
agent:
  id: my-gateway
  log_level: DEBUG
api:
  port: 9000
transport:
  baudrate: 115200
reconnect:
  initial_delay: 2.5
  max_delay: 30.0
  multiplier: 1.5
""".strip()
    )
    cfg = load_config(str(p))
    assert cfg.agent.id == "my-gateway"
    assert cfg.agent.log_level == "DEBUG"
    assert cfg.api.port == 9000
    assert cfg.transport.baudrate == 115200
    assert cfg.reconnect.initial_delay == 2.5
    assert cfg.reconnect.multiplier == 1.5
    # 没写的字段保持默认
    assert cfg.api.host == "127.0.0.1"


def test_load_nonexistent_path_returns_default(tmp_path: Path):
    cfg = load_config(str(tmp_path / "no_such.yaml"))
    assert cfg == GatewayConfig()


def test_load_empty_yaml(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    cfg = load_config(str(p))
    assert cfg == GatewayConfig()


def test_load_env_var(monkeypatch, tmp_path: Path):
    p = tmp_path / "env.yaml"
    p.write_text("agent:\n  id: env-gateway\n")
    monkeypatch.setenv("GATEWAY_CONFIG", str(p))
    cfg = load_config()  # 不传 path — 用环境变量
    assert cfg.agent.id == "env-gateway"


# --- Phase 07.1: 命令 API 认证配置 -----------------------------------

def test_api_auth_defaults_are_production_safe():
    cfg = GatewayConfig()
    assert cfg.api.require_auth is True
    assert cfg.api.api_token == ""


def test_api_token_from_env(monkeypatch, tmp_path: Path):
    p = tmp_path / "token.yaml"
    p.write_text("agent:\n  id: token-gateway\n")
    monkeypatch.setenv("GATEWAY_API_TOKEN", "env-injected-token")
    cfg = load_config(str(p))
    assert cfg.api.api_token == "env-injected-token"
    assert cfg.api.require_auth is True


def test_api_token_env_does_not_override_yaml(monkeypatch, tmp_path: Path):
    p = tmp_path / "token2.yaml"
    p.write_text("api:\n  api_token: yaml-token\n")
    monkeypatch.setenv("GATEWAY_API_TOKEN", "env-token")
    cfg = load_config(str(p))
    assert cfg.api.api_token == "yaml-token"


def test_api_require_auth_can_be_disabled_only_explicitly(monkeypatch, tmp_path: Path):
    p = tmp_path / "dev.yaml"
    p.write_text("api:\n  require_auth: false\n")
    monkeypatch.delenv("GATEWAY_API_REQUIRE_AUTH", raising=False)
    cfg = load_config(str(p))
    assert cfg.api.require_auth is False


def test_api_token_not_in_config_repr():
    cfg = GatewayConfig()
    cfg.api.api_token = "secret-not-in-repr"
    assert "secret-not-in-repr" not in repr(cfg)
