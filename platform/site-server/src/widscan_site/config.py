"""Site Server config — YAML + Pydantic v2。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    id: str = "site-001"
    name: str = "WIDSCAN58 Site"
    description: str = "Default site"
    timezone: str = "Asia/Shanghai"
    location: str = ""


class ApiConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8766


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/site.db"
    echo: bool = False


class PairingConfig(BaseModel):
    code_ttl_seconds: int = 300       # 5 min
    max_active_codes: int = 5
    credential_ttl_hours: int = 720   # 30 days


class RetentionConfig(BaseModel):
    metrics_hours: int = 24
    telemetry_hours: int = 48
    spectrum_hours: int = 6
    levels_hours: int = 12
    rawiq_enabled: bool = False       # 默认关闭


class WebSocketConfig(BaseModel):
    """Agent 反向 WebSocket 连接安全配置（V1.1.2.1 Task 7）。

    生产安全默认值：
    - allow_url_credential = False — 禁止通过 ?credential= URL query 传凭证
      只有显式开启本地兼容测试时才允许 URL Credential
    - auth_timeout_seconds = 10.0 — 认证建立超时
    """
    allow_url_credential: bool = False          # 生产默认关闭 ✅
    auth_timeout_seconds: float = 10.0


class ServerConfig(BaseModel):
    site: SiteConfig = Field(default_factory=SiteConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    pairing: PairingConfig = Field(default_factory=PairingConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)


def load_config(path: Optional[str | Path] = None) -> ServerConfig:
    p: Optional[Path] = None
    if path is not None:
        p = Path(path)
    else:
        env = os.environ.get("SITE_SERVER_CONFIG")
        if env:
            p = Path(env)

    if p is None or not p.is_file():
        return ServerConfig()

    if yaml is None:
        raise RuntimeError("pyyaml required for YAML config")

    raw = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) or {}
    return ServerConfig.model_validate(data)
