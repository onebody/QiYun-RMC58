"""配置 — YAML + Pydantic v2。所有字段都给默认值，这样无 YAML 也能 run。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover — pyyaml 是依赖
    yaml = None  # type: ignore[assignment]

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    id: str = "gateway-001"
    log_level: str = "INFO"


class ApiConfig(BaseModel):
    """本地 REST API 配置（Phase 07.1）。

    - require_auth: 生产默认 True。只有显式本地开发测试才可关闭。
    - api_token: 本地命令 API Token。**绝不写入日志**；推荐通过
      GATEWAY_API_TOKEN 环境变量注入，而不是写进 YAML。
    """
    host: str = "127.0.0.1"
    port: int = 8765
    require_auth: bool = True
    api_token: str = Field(default="", repr=False)


class DiscoveryConfig(BaseModel):
    usb_enabled: bool = True
    poll_interval: float = 5.0
    network_enabled: bool = False  # 未来


class SerialConfig(BaseModel):
    baudrate: int = 921600
    read_timeout: float = 0.2


class ReconnectConfig(BaseModel):
    enabled: bool = True
    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0


class StreamConfig(BaseModel):
    queue_size: int = 1024
    rawiq_enabled: bool = False
    max_websocket_queue: int = 2048


class SiteConfig(BaseModel):
    """Site Connector 配置 — Agent 反向连接 Site Server。"""
    enabled: bool = False
    endpoint: str = ""       # ws://host:port 或 wss://host:port
    credential: str = ""     # Site Server pair 返回的明文 credential（**不打印到日志**）
    heartbeat_interval: float = 15.0
    heartbeat_ack_timeout: float = 60.0
    reconnect_initial_delay: float = 2.0
    reconnect_max_delay: float = 60.0
    reconnect_multiplier: float = 2.0


class GatewayConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    transport: SerialConfig = Field(default_factory=SerialConfig)
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)


def load_config(path: Optional[str | Path] = None) -> GatewayConfig:
    """
    加载顺序：path 参数 → GATEWAY_CONFIG 环境变量 → 默认值。
    path 不存在 → 静默用默认（CLI 里可以用 --config 指定不存在的路径，照样跑）。

    Phase 07.1：加载后应用环境变量覆盖（GATEWAY_API_TOKEN /
    GATEWAY_API_REQUIRE_AUTH）。环境变量优先于 YAML，便于生产注入 Token。
    """
    p: Optional[Path] = None
    if path is not None:
        p = Path(path)
    else:
        env = os.environ.get("GATEWAY_CONFIG")
        if env:
            p = Path(env)

    if p is None or not p.is_file():
        return apply_env_overrides(GatewayConfig())

    if yaml is None:
        raise RuntimeError("pyyaml is required for YAML config")

    raw = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return apply_env_overrides(GatewayConfig.model_validate(data))


def apply_env_overrides(cfg: GatewayConfig) -> GatewayConfig:
    """用环境变量覆盖本地命令 API 认证配置（不记录 Token 内容）。"""
    token = os.environ.get("GATEWAY_API_TOKEN", "").strip()
    if token and not cfg.api.api_token:
        cfg.api.api_token = token
    require = os.environ.get("GATEWAY_API_REQUIRE_AUTH")
    if require is not None:
        cfg.api.require_auth = require.strip().lower() not in ("0", "false", "no", "off")
    return cfg


LOCAL_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


def resolve_bind_host(
    requested: str,
    *,
    auth_required: bool,
    token_configured: bool,
) -> tuple[str, str]:
    """解析实际监听地址。

    默认只允许本地回环。用户显式配置外部地址时，只有命令 API 认证生效
    （require_auth=True 且 Token 已配置）才放行，否则回退 127.0.0.1。
    监听地址永远不会自动放开命令权限 — 权限由认证依赖单独判定。

    Returns (host, warning_message)。warning 为空表示无提示。
    """
    host = (requested or "").strip()
    if host in LOCAL_BIND_HOSTS:
        return "127.0.0.1", ""
    if auth_required and token_configured:
        return host, f"external bind {host} allowed: command API auth is enabled"
    return "127.0.0.1", (
        f"external bind {host} rejected (require_auth={auth_required}, "
        f"token_configured={token_configured}) — falling back to 127.0.0.1"
    )


def save_config(cfg: GatewayConfig, path: Optional[str | Path] = None) -> Path:
    """写回 YAML 配置文件。创建父目录；**credential 字段原样保留，不打印日志**。"""
    if yaml is None:
        raise RuntimeError("pyyaml is required for YAML config")
    p: Path
    if path is not None:
        p = Path(path)
    else:
        env = os.environ.get("GATEWAY_CONFIG")
        if env:
            p = Path(env)
        else:
            p = Path("gateway.yaml")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json")
    p.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return p
