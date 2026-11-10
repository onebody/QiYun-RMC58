"""FastAPI 主入口 — 所有 REST + 前端 WS 路由都在这里。

分层组织：
- /health
- /api/v1/site                Site CRUD
- /api/v1/agents              Agent 配对 + 注册
- /api/v1/devices             Device 发现 + 认领
- /api/v1/measurements        数据查询
- /api/v1/diagnostics         运行诊断
- /ws/v1/agents/connect       Agent 反向连接（见 agents.connection）
- /ws/v1/site                 前端 Web Workbench 事件流
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import text

from .config import ServerConfig, load_config
from .database import init_engine, get_session
from .site import bootstrap_default_site, get_site, upsert_site, list_sites, site_to_dict
from .auth import (
    create_pairing_code, pair_with_code, verify_credential, revoke_credential,
)
from .agents import (
    list_agents, update_agent, agent_to_dict, check_offline_agents, record_heartbeat,
)
from .agents.connection import agent_connect_ws, registry as agent_conn_registry
from .devices import (
    list_devices, adopt_device, rename_device, remove_device, device_to_dict,
    transfer_device_owner,
)
from .ingestion import map_frame_type


# --- Pydantic 请求/响应模型（必须在模块顶层才能被 FastAPI + Pydantic v2 正确解析）---

class PairingCodeRequest(BaseModel):
    ttl_seconds: int = 300
    max_active: int = 5


class PairingCodeResponse(BaseModel):
    pairing_code: str
    plain_credential: str
    expires_at: str


class PairRequest(BaseModel):
    pairing_code: str
    agent_id: Optional[str] = None
    agent_version: str = ""
    platform: str = ""
    os_info: str = ""
    name: str = ""


class AgentRevokeRequest(BaseModel):
    reason: str = "manual"


class DeviceAdoptRequest(BaseModel):
    display_name: Optional[str] = None


class DeviceUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    status: Optional[str] = None


def create_app(cfg: Optional[ServerConfig] = None) -> FastAPI:
    cfg = cfg or ServerConfig()

    # 确保 data dir
    db_url = cfg.database.url
    if db_url.startswith("sqlite:///./"):
        Path(db_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    elif db_url.startswith("sqlite:////"):
        Path(db_url.replace("sqlite:///", "").split("?")[0]).parent.mkdir(parents=True, exist_ok=True)

    init_engine(db_url, echo=cfg.database.echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 把 cfg 挂到 app.state → Agent WS handler 能读安全配置
        app.state.cfg = cfg

        # 确保 schema 存在（开发空库 fallback；生产应走 alembic upgrade）
        from .database import models  # noqa: F401  (import side-effect)
        from .database import Base, get_engine
        Base.metadata.create_all(get_engine())

        # --- V1.1.2.1 迁移自检：提醒用户跑 alembic -------------------------
        try:
            with get_engine().connect() as conn:
                # SQLite PRAGMA 检查新列是否存在
                col_checks = [
                    ("devices", "owner_agent_id", "alembic upgrade head  — Device ownership cols missing"),
                    ("devices", "ownership_status", None),  # 第一个 warning 足够
                    ("measurements", "agent_id", "alembic upgrade head  — Measurement traceability cols missing"),
                    ("measurements", "device_boot_id", None),
                ]
                for table, col, hint in col_checks:
                    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()  # noqa: S608
                    col_names = {r[1] for r in rows}
                    if col not in col_names and hint:
                        log.warning("DB schema check: %s.%s missing → run %s", table, col, hint)
        except Exception as exc:
            log.debug("Schema check skipped: %s", exc)

        # Bootstrap default site
        with get_session() as sess:
            bootstrap_default_site(
                sess, site_id=cfg.site.id,
                name=cfg.site.name, timezone=cfg.site.timezone,
            )

        # 安全配置启动日志
        ws_cfg = cfg.websocket
        log.info("Site Server bootstrap complete (site_id=%s)", cfg.site.id)
        log.info("WebSocket security: allow_url_credential=%s auth_timeout=%.1fs",
                 ws_cfg.allow_url_credential, ws_cfg.auth_timeout_seconds)
        if ws_cfg.allow_url_credential:
            log.warning("⚠️  allow_url_credential=True — URL credential enabled for local compatibility ONLY!")
        yield

    app = FastAPI(
        title="WIDSCAN58 Site Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- /health ---------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "site_id": cfg.site.id}

    # --- Site ------------------------------------------------------------

    @app.get("/api/v1/site")
    async def site_get(site_id: str = Query(default=None)) -> dict:
        sid = site_id or cfg.site.id
        with get_session() as sess:
            s = get_site(sess, sid)
            if s is None:
                raise HTTPException(404, "site not found")
            counts = _counts_for_site(sess, sid)
            return {**site_to_dict(s), "counts": counts}

    @app.patch("/api/v1/site")
    async def site_update(body: dict, site_id: str = Query(default=None)) -> dict:
        sid = site_id or cfg.site.id
        allowed = {"name", "description", "location", "timezone", "status"}
        kwargs = {k: body[k] for k in body if k in allowed}
        with get_session() as sess:
            s = upsert_site(sess, sid, **kwargs)
            return site_to_dict(s)

    # --- Agents ----------------------------------------------------------

    @app.get("/api/v1/agents")
    async def agents_list(site_id: str = Query(default=None),
                          status: Optional[str] = None) -> list[dict]:
        sid = site_id or cfg.site.id
        with get_session() as sess:
            agents = list_agents(sess, site_id=sid, status=status)
            out = []
            for a in agents:
                device_count = _agent_device_count(sess, a.id)
                out.append(agent_to_dict(a, device_count=device_count))
            return out

    @app.post("/api/v1/agents/pairing-codes")
    async def agents_create_pairing(req: PairingCodeRequest = Body(...),
                                    site_id: str = Query(default=None)) -> PairingCodeResponse:
        sid = site_id or cfg.site.id
        with get_session() as sess:
            plain, cred = create_pairing_code(
                sess, site_id=sid,
                ttl_seconds=req.ttl_seconds, max_active=req.max_active,
            )
        return PairingCodeResponse(
            pairing_code=cred.pairing_code or "",
            plain_credential=plain,
            expires_at=cred.pairing_code_expires_at.isoformat() if cred.pairing_code_expires_at else "",
        )

    @app.post("/api/v1/agents/pair")
    async def agents_pair(body: PairRequest = Body(...), site_id: str = Query(default=None)) -> dict:
        sid = site_id or cfg.site.id
        with get_session() as sess:
            cred = pair_with_code(sess, site_id=sid, pairing_code=body.pairing_code,
                                  agent_info=body.model_dump())
            if cred is None:
                raise HTTPException(400, "pairing code invalid/expired/used")
            from .database.models import Agent
            from sqlalchemy import select as sa_select
            agent = sess.execute(sa_select(Agent).where(Agent.id == cred.agent_id)).scalar_one_or_none()
            return {
                "agent_id": cred.agent_id,
                "credential_id": cred.id,
                "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
                "status": "paired",
            }

    @app.post("/api/v1/agents/{agent_id}/revoke")
    async def agents_revoke(agent_id: str, body: AgentRevokeRequest = Body(...)) -> dict:
        with get_session() as sess:
            from .database.models import AgentCredential
            # 找该 agent 的有效 credential
            from sqlalchemy import select
            cred = sess.execute(
                select(AgentCredential).where(
                    AgentCredential.agent_id == agent_id,
                    AgentCredential.revoked_at.is_(None),
                )
            ).scalar_one_or_none()
            if cred is None:
                raise HTTPException(404, "agent has no active credential")
            ok = revoke_credential(sess, cred.id, reason=body.reason)
            return {"revoked": ok, "agent_id": agent_id}

    # --- Device ----------------------------------------------------------

    @app.get("/api/v1/devices")
    async def devices_list(
        site_id: str = Query(default=None),
        status: Optional[str] = None,
        only_adopted: Optional[bool] = None,
        transport_type: Optional[str] = None,
    ) -> list[dict]:
        sid = site_id or cfg.site.id
        with get_session() as sess:
            devs = list_devices(sess, site_id=sid, status=status, only_adopted=only_adopted)
            out = []
            for d in devs:
                dd = device_to_dict(d)
                dd["endpoints"] = _device_endpoints(sess, d.id)
                dd["adopted"] = d.adopted_at is not None
                out.append(dd)
            return out

    @app.get("/api/v1/devices/{device_id}")
    async def device_get(device_id: str) -> dict:
        from .database.models import Device
        with get_session() as sess:
            d = sess.get(Device, device_id)
            if d is None:
                raise HTTPException(404, "device not found")
            dd = device_to_dict(d)
            dd["endpoints"] = _device_endpoints(sess, device_id)
            dd["sessions"] = _device_sessions(sess, device_id)
            return dd

    @app.post("/api/v1/devices/{device_id}/adopt")
    async def device_adopt(device_id: str, body: DeviceAdoptRequest = Body(...),
                           site_id: str = Query(default=None)) -> dict:
        sid = site_id or cfg.site.id
        try:
            with get_session() as sess:
                d = adopt_device(sess, site_id=sid, device_id=device_id,
                                 display_name=body.display_name)
                dd = device_to_dict(d)
                dd["adopted"] = d.adopted_at is not None
                return dd
        except ValueError as exc:
            raise HTTPException(404, str(exc))

    @app.patch("/api/v1/devices/{device_id}")
    async def device_patch(device_id: str, body: DeviceUpdateRequest = Body(...)) -> dict:
        from .database.models import Device
        with get_session() as sess:
            if body.display_name is not None:
                d = rename_device(sess, device_id, body.display_name)
            else:
                d = sess.get(Device, device_id)
            if d is None:
                raise HTTPException(404, "device not found")
            if body.status is not None:
                d.status = body.status
                sess.commit()
                sess.refresh(d)
            return device_to_dict(d)

    @app.delete("/api/v1/devices/{device_id}")
    async def device_delete(device_id: str) -> dict:
        with get_session() as sess:
            ok = remove_device(sess, device_id)
            if not ok:
                raise HTTPException(404, "device not found")
            return {"removed": True, "device_id": device_id}

    @app.post("/api/v1/devices/{device_id}/transfer-owner")
    async def device_transfer_owner(device_id: str, body: dict = Body(...),
                                    site_id: str = Query(default=None)) -> dict:
        """管理员授权 Device Owner 迁移（V1.1.2.1 Task 5）。

        body:
          new_owner_agent_id: str  — 目标 Agent ID
          reason?: str             — 可选迁移原因（审计用）
        """
        sid = site_id or cfg.site.id
        new_owner = body.get("new_owner_agent_id")
        if not new_owner:
            raise HTTPException(400, "new_owner_agent_id required")
        try:
            with get_session() as sess:
                d = transfer_device_owner(
                    sess, site_id=sid, device_id=device_id,
                    new_owner_agent_id=new_owner,
                    reason=body.get("reason", ""),
                )
                return device_to_dict(d)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    # --- Device Command（Site Server → Agent → Device 下行命令）----------

    class DeviceCommandRequest(BaseModel):
        command: str = Field(..., min_length=1, max_length=120,
                             description="固件 ASCII 命令行，如 PROTOCOL RH")

    @app.post("/api/v1/devices/{device_id}/command")
    async def device_command(device_id: str, body: DeviceCommandRequest,
                             timeout: float = Query(15.0, ge=1.0, le=60.0)) -> dict:
        """向远程设备下发一条固件命令（经 Agent 中继）。

        流程：查 device → 拿 owner_agent_id → 走 Agent WS 发 command.request
        → 等待 command.response 或超时。
        """
        from .database.models import Device
        # 1. 查 device
        with get_session() as sess:
            d = sess.get(Device, device_id)
            if d is None:
                raise HTTPException(404, "device not found")
            if not d.owner_agent_id:
                raise HTTPException(400, "device has no owner agent — cannot dispatch command")
            owner_agent_id = d.owner_agent_id

        # 2. 找 Agent 连接
        conn = agent_conn_registry.get(owner_agent_id)
        if conn is None or not conn.running:
            raise HTTPException(503, f"agent {owner_agent_id} is offline")

        # 3. 下发命令
        try:
            result = await conn.send_command(device_id, body.command, timeout=timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504, f"command timed out after {timeout}s")
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            log.exception("device command dispatch failed")
            raise HTTPException(500, f"command dispatch error: {exc}")

        return {
            "device_id": device_id,
            "command": body.command,
            "ok": result.get("ok", False),
            "reply": result.get("reply", ""),
        }

    # --- Measurements ----------------------------------------------------

    @app.get("/api/v1/devices/{device_id}/measurements")
    async def measurements_list(
        device_id: str,
        frame_type: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = 0,
    ) -> list[dict]:
        from sqlalchemy import select as sa_select
        from .database.models import Measurement
        with get_session() as sess:
            stmt = sa_select(Measurement).where(Measurement.device_id == device_id)
            if frame_type:
                stmt = stmt.where(Measurement.frame_type == frame_type)
            stmt = stmt.order_by(Measurement.created_at.desc()).offset(offset).limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "id": m.id,
                    "frame_type": m.frame_type,
                    "device_timestamp_ms": m.device_timestamp_ms,
                    "sequence": m.sequence,
                    "source": m.source,
                    # V1.1.2.1 Task 6: 追溯字段
                    "agent_id": m.agent_id,
                    "device_boot_id": m.device_boot_id,
                    "event_id": m.event_id,
                    "payload": m.payload_json,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in rows
            ]

    @app.get("/api/v1/measurements/stats")
    async def measurements_stats(site_id: str = Query(default=None)) -> dict:
        from sqlalchemy import func
        from .database.models import Measurement
        sid = site_id or cfg.site.id
        with get_session() as sess:
            total = sess.execute(
                func.count(Measurement.id).select().where(Measurement.site_id == sid)
            ).scalar()
            last = sess.execute(
                sess.query(func.max(Measurement.created_at)).filter(Measurement.site_id == sid)
            ).scalar()
            return {
                "total": total or 0,
                "last_created_at": last.isoformat() if last else None,
            }

    # --- Diagnostics -----------------------------------------------------

    @app.get("/api/v1/diagnostics")
    async def diagnostics() -> dict:
        from .ingestion import get_ingestion_stats
        with get_session() as sess:
            counts = _counts_for_site(sess, cfg.site.id)
            active_agents = list(agent_conn_registry.list_ids())
            return {
                "site_id": cfg.site.id,
                "counts": counts,
                "active_agent_connections": active_agents,
                "ingestion": get_ingestion_stats(),
                "db_path": str(Path(cfg.database.url.replace("sqlite:///", ""))),
            }

    # --- Agent WS endpoint (Site Server ↔ Agent) -------------------------

    @app.websocket("/ws/v1/agents/connect")
    async def ws_agents_connect(ws: WebSocket) -> None:
        await agent_connect_ws(ws)

    # --- Phase 08.4: Platform UI（同源静态托管，只读） -------------------
    platform_dir = Path(os.environ.get("SITE_PLATFORM_WEB_DIR")
                        or (Path(__file__).resolve().parents[2] / "web"))
    if platform_dir.is_dir():
        @app.get("/platform", include_in_schema=False)
        async def platform_redirect() -> RedirectResponse:
            return RedirectResponse(url="/platform/")

        @app.get("/", include_in_schema=False)
        async def root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/platform/")

        app.mount("/platform", StaticFiles(directory=str(platform_dir), html=True), name="platform")
        log.info("Platform UI mounted at /platform (dir=%s)", platform_dir)
    else:
        log.warning("Platform UI not mounted: directory not found (%s)", platform_dir)

    # 原始 RF Console：可选挂载 repo/web/dist（不存在时不影响 Site Server）。
    console_dir = Path(os.environ.get("SITE_CONSOLE_DIR")
                       or (Path(__file__).resolve().parents[2].parent / "repo" / "web" / "dist"))
    if console_dir.is_dir():
        @app.get("/app", include_in_schema=False)
        async def console_redirect() -> RedirectResponse:
            return RedirectResponse(url="/app/")

        app.mount("/app", StaticFiles(directory=str(console_dir), html=True), name="console")
        log.info("RF Console mounted at /app (dir=%s)", console_dir)
    else:
        log.info("RF Console dist not found (%s); platform link stays disabled", console_dir)

    return app


# --- Helpers -------------------------------------------------------------

def _counts_for_site(sess, site_id: str) -> dict:
    from sqlalchemy import func
    from .database.models import Agent, Device, Measurement, SiteEvent
    agents_total = sess.execute(
        func.count(Agent.id).select().where(Agent.site_id == site_id)
    ).scalar() or 0
    agents_online = sess.execute(
        func.count(Agent.id).select().where(Agent.site_id == site_id, Agent.status == "online")
    ).scalar() or 0
    devices_total = sess.execute(
        func.count(Device.id).select().where(Device.site_id == site_id)
    ).scalar() or 0
    devices_online = sess.execute(
        func.count(Device.id).select().where(Device.site_id == site_id, Device.status == "online")
    ).scalar() or 0
    devices_pending = sess.execute(
        func.count(Device.id).select().where(Device.site_id == site_id, Device.adopted_at.is_(None))
    ).scalar() or 0
    devices_adopted = devices_total - devices_pending
    measurements_total = sess.execute(
        func.count(Measurement.id).select().where(Measurement.site_id == site_id)
    ).scalar() or 0
    return {
        "agents_total": agents_total,
        "agents_online": agents_online,
        "devices_total": devices_total,
        "devices_online": devices_online,
        "devices_pending": devices_pending,
        "devices_adopted": devices_adopted,
        "measurements_total": measurements_total,
    }


def _agent_device_count(sess, agent_id: str) -> int:
    from .database.models import DeviceEndpoint
    from sqlalchemy import func
    n = sess.execute(
        func.count(DeviceEndpoint.device_id.distinct()).select().where(DeviceEndpoint.agent_id == agent_id)
    ).scalar()
    return n or 0


def _device_endpoints(sess, device_id: str) -> list[dict]:
    from .database.models import DeviceEndpoint
    eps = sess.query(DeviceEndpoint).filter(DeviceEndpoint.device_id == device_id).all()
    return [
        {
            "id": e.id,
            "agent_id": e.agent_id,
            "transport_type": e.transport_type,
            "endpoint_identifier": e.endpoint_identifier,
            "status": e.status,
            "last_connected_at": e.last_connected_at.isoformat() if e.last_connected_at else None,
        }
        for e in eps
    ]


def _device_sessions(sess, device_id: str) -> list[dict]:
    from .database.models import ConnectionSession
    ss = sess.query(ConnectionSession).filter(ConnectionSession.device_id == device_id).order_by(
        ConnectionSession.connected_at.desc()).limit(20).all()
    return [
        {
            "id": s.id, "agent_id": s.agent_id,
            "boot_id": s.boot_id, "connected_at": s.connected_at.isoformat() if s.connected_at else None,
            "disconnected_at": s.disconnected_at.isoformat() if s.disconnected_at else None,
            "disconnect_reason": s.disconnect_reason, "status": s.status,
        }
        for s in ss
    ]


# --- main() --------------------------------------------------------------

log = logging.getLogger("site-server")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="widscan-site")
    parser.add_argument("--config", "-c", help="YAML config path")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", "-p", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    app = create_app(cfg)
    host = args.host or cfg.api.host
    port = args.port or cfg.api.port

    # 安全限制：默认绑定 0.0.0.0 (Site Server 跑在主机端) — 但明确警告
    log.warning("Site Server binding %s:%d — make sure it's behind a firewall/reverse proxy!", host, port)

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
