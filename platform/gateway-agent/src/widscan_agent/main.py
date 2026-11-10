"""main — 启动脚本。

run() 做三件事：
1. 根据 config 构造 EventBus + DeviceManager
2. 启动 manager（发现 + 轮询）
3. 启动 FastAPI + uvicorn

为了让 `python -m widscan_agent` 和 `widscan-gateway` CLI 都工作，
这里有 run(host, port, config) 和 main() 两个入口。
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

from .config import GatewayConfig, load_config, resolve_bind_host
from .device import Device
from .events import EventBus
from .manager import DeviceManager
from .protocol import find_frame_parser_module_path
from .transports.simulator import SimulatorTransport


def make_manager(bus: EventBus, cfg: GatewayConfig) -> DeviceManager:
    return DeviceManager(
        bus=bus,
        poll_interval=cfg.discovery.poll_interval,
        reconnect_enabled=cfg.reconnect.enabled,
        reconnect_initial_delay=cfg.reconnect.initial_delay,
        reconnect_max_delay=cfg.reconnect.max_delay,
        reconnect_multiplier=cfg.reconnect.multiplier,
        read_timeout=cfg.transport.read_timeout,
        baudrate=cfg.transport.baudrate,
    )


def run(
    cfg: Optional[GatewayConfig] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    simulator_count: int = 0,
) -> None:
    cfg = cfg or GatewayConfig()
    requested_host = host or cfg.api.host
    port = port or cfg.api.port

    # Phase 07.1：监听地址解析。默认只允许本地回环；提权到外部监听
    # 必须同时满足 require_auth=True 且已配置 Token，否则回退 127.0.0.1。
    host, bind_warning = resolve_bind_host(
        requested_host,
        auth_required=cfg.api.require_auth,
        token_configured=bool(cfg.api.api_token),
    )

    log_level = cfg.agent.log_level.upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log = logging.getLogger("gateway")

    if bind_warning:
        log.warning(bind_warning)
    if not cfg.api.require_auth:
        log.warning("command API authentication is DISABLED (development mode only)")

    bus = EventBus()
    manager = make_manager(bus, cfg)

    # simulator 注入
    for i in range(simulator_count):
        sim = SimulatorTransport(device_id=f"sim-c5-{i:03d}")
        manager.add_device(sim, device_id=f"sim-c5-{i:03d}")

    manager.start()

    # --- Site Connector（可选，独立启停，不影响 Agent 本地功能）---
    connector = None
    try:
        from .site_connector import SiteConnector
        sc = cfg.site
        if sc.enabled and sc.endpoint:
            connector = SiteConnector(
                bus=bus,
                agent_id=cfg.agent.id,
                manager=manager,
                endpoint=sc.endpoint,
                credential=sc.credential,
                enabled=sc.enabled,
                heartbeat_interval=sc.heartbeat_interval,
                heartbeat_ack_timeout=sc.heartbeat_ack_timeout,
                reconnect_initial_delay=sc.reconnect_initial_delay,
                reconnect_max_delay=sc.reconnect_max_delay,
                reconnect_multiplier=sc.reconnect_multiplier,
            )
            connector.start()
            log.info("Site Connector started: enabled=%s endpoint=%s", sc.enabled, sc.endpoint)
        else:
            log.info("Site Connector disabled (site.enabled=%s, site.endpoint=%s)", sc.enabled, bool(sc.endpoint))
    except Exception as exc:
        log.error("Site Connector init failed (Agent continues normally): %s", exc, exc_info=True)
        connector = None

    from .api import build_app

    app = build_app(
        manager=manager,
        bus=bus,
        agent_id=cfg.agent.id,
        frame_parser_path=find_frame_parser_module_path(),
        connector=connector,  # REST API 暴露 connector 状态
        api_token=cfg.api.api_token,
        require_auth=cfg.api.require_auth,
    )

    log.info(
        "Gateway Agent started: agent=%s host=%s:%d devices=%d site_connector=%s frame_parser=%s",
        cfg.agent.id, host, port, len(manager.list_devices()),
        connector.state.value if connector else "disabled",
        find_frame_parser_module_path(),
    )

    import uvicorn

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        log.info("interrupted by user")
    finally:
        if connector is not None:
            log.info("stopping Site Connector")
            connector.stop()
        log.info("shutting down manager")
        manager.stop()
        log.info("bye")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="widscan-gateway",
        description="WIDSCAN58 Gateway Agent — multi-device host multiplexer",
    )
    parser.add_argument("--config", "-c", help="YAML config path")
    parser.add_argument("--host", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--port", "-p", type=int, help="Bind port (default 8765)")
    parser.add_argument(
        "--simulators", "-s",
        type=int, default=0,
        help="注入 N 台 simulator 设备（默认 0）",
    )
    parser.add_argument("--log-level", help="日志级别 (DEBUG/INFO/WARNING/ERROR)")

    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.log_level:
        cfg.agent.log_level = args.log_level

    run(cfg=cfg, host=args.host, port=args.port, simulator_count=args.simulators)
    return 0


if __name__ == "__main__":
    sys.exit(main())
