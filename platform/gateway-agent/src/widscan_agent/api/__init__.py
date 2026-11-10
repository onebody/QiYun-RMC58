"""FastAPI app — REST endpoints + WebSocket 事件流。

设计约束：
- 默认绑定 127.0.0.1（config 里改不了这点 — 安全硬限制）
- 所有设备操作走 DeviceManager
- WebSocket 客户端自己维护队列；慢客户端由 manager 隔离（Manager 层会做）
- RAWIQ 默认不广播 — 它的帧量太大会把 WS 挤爆

Phase 07.1 安全加固：
- 所有会触发设备操作 / 变更 Connector 的 POST 端点必须携带本地 API Token。
- 生产默认要求认证；Token 从配置或环境变量读取，绝不写入日志。
- 未配置 Token 时命令 API 直接返回 503（拒绝服务而不是放开）。
- 认证失败 401；已认证但命令不在白名单 403。
- 认证只做身份边界，不替代 `is_command_allowed` 白名单。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from collections import deque
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..events import Event, EventBus
from ..manager import DeviceManager

log = logging.getLogger("widscan-agent.api")

API_TOKEN_HEADER = "x-api-token"


def mask_secret(value: Optional[str]) -> str:
    """日志/诊断用 — 永不返回原始 Token。"""
    return "***" if value else ""


def extract_api_token(request: Request) -> str:
    """从 Authorization: Bearer 或 X-API-Token 读取凭据。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get(API_TOKEN_HEADER, "").strip()


# --- Pydantic 请求/响应模型 ----------------------------------------------


class AgentInfo(BaseModel):
    id: str
    version: str
    uptime: float
    event_history_count: int
    frame_parser_path: str


class DeviceStatus(BaseModel):
    device_id: str
    state: str
    transport_info: Dict[str, Any]
    info: Dict[str, Any]
    metrics: Dict[str, Any]
    last_seen_ts: Optional[float] = None


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=200)


class CommandResponse(BaseModel):
    ok: bool
    reply: str
    command: str


class ApiError(BaseModel):
    detail: str


def build_app(
    manager: DeviceManager,
    bus: EventBus,
    agent_id: str = "gateway-001",
    frame_parser_path: str = "<unknown>",
    start_ts: Optional[float] = None,
    connector: Optional[Any] = None,
    api_token: str = "",
    require_auth: bool = True,
) -> FastAPI:
    """
    工厂函数 — 不 import 任何 Device 实现细节，方便测试。

    api_token / require_auth 是 Phase 07.1 的本地命令 API 认证配置：
    - 生产：require_auth=True 且 api_token 非空。
    - 未配置 api_token：命令 API 返回 503（拒绝所有命令请求）。
    - require_auth=False 只能用于显式本地开发测试，不得作为生产默认。
    """
    app = FastAPI(
        title="WIDSCAN58 Gateway Agent",
        version="0.1.0",
        description="Multi-device multiplexer for ESP32-C5 receivers",
    )
    start = start_ts if start_ts is not None else time.time()
    token = api_token or ""
    auth_required = bool(require_auth)
    token_configured = bool(token)

    async def require_command_auth(request: Request) -> None:
        """命令 API 认证依赖。只做身份边界，不做命令白名单。"""
        if not auth_required:
            return
        if not token_configured:
            # 生产保护：宁可拒绝服务，也不能在无 Token 时放开命令入口
            log.error("command API request rejected: no API token configured")
            raise HTTPException(
                status_code=503,
                detail="command API disabled: no API token configured",
            )
        supplied = extract_api_token(request)
        if not supplied or not hmac.compare_digest(supplied, token):
            client = request.client.host if request.client else "unknown"
            # 注意：绝不记录 Token 内容，只记录来源与路径
            log.warning(
                "command API authentication failed (client=%s path=%s)",
                client, request.url.path,
            )
            raise HTTPException(
                status_code=401,
                detail="unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth_dep = [Depends(require_command_auth)]
    log.info(
        "command API security: require_auth=%s token_configured=%s",
        auth_required, token_configured,
    )

    # --- REST --------------------------------------------------------

    @app.get("/api/v1/agent", response_model=AgentInfo)
    async def get_agent() -> AgentInfo:
        return AgentInfo(
            id=agent_id,
            version="0.1.0",
            uptime=time.time() - start,
            event_history_count=len(bus.recent()),
            frame_parser_path=frame_parser_path,
        )

    @app.get("/api/v1/devices", response_model=List[DeviceStatus])
    async def list_devices() -> List[DeviceStatus]:
        out = []
        for d in manager.list_devices():
            out.append(DeviceStatus(
                device_id=d.device_id,
                state=d.state.value,
                transport_info=d.transport_info.__dict__,
                info=d.info,
                metrics=d.metrics.__dict__,
                last_seen_ts=d.metrics.last_seen_ts or None,
            ))
        return out

    @app.get("/api/v1/devices/{device_id}", response_model=DeviceStatus)
    async def get_device(device_id: str) -> DeviceStatus:
        d = manager.get(device_id)
        if d is None:
            raise HTTPException(status_code=404, detail="device not found")
        return DeviceStatus(
            device_id=d.device_id,
            state=d.state.value,
            transport_info=d.transport_info.__dict__,
            info=d.info,
            metrics=d.metrics.__dict__,
            last_seen_ts=d.metrics.last_seen_ts or None,
        )

    @app.post("/api/v1/devices/{device_id}/connect", response_model=Dict[str, Any], dependencies=auth_dep)
    async def connect_device(device_id: str) -> Dict[str, Any]:
        d = manager.get(device_id)
        if d is None:
            raise HTTPException(status_code=404, detail="device not found")
        d.start()
        return {"ok": True, "device_id": device_id, "state": d.state.value}

    @app.post("/api/v1/devices/{device_id}/disconnect", response_model=Dict[str, Any], dependencies=auth_dep)
    async def disconnect_device(device_id: str) -> Dict[str, Any]:
        d = manager.get(device_id)
        if d is None:
            raise HTTPException(status_code=404, detail="device not found")
        d.stop()
        return {"ok": True, "device_id": device_id, "state": d.state.value}

    @app.post("/api/v1/devices/{device_id}/reconnect", response_model=Dict[str, Any], dependencies=auth_dep)
    async def reconnect_device(device_id: str) -> Dict[str, Any]:
        d = manager.get(device_id)
        if d is None:
            raise HTTPException(status_code=404, detail="device not found")
        d.reconnect()
        return {"ok": True, "device_id": device_id, "state": d.state.value}

    @app.post("/api/v1/devices/{device_id}/commands", response_model=CommandResponse, dependencies=auth_dep)
    async def send_command(device_id: str, req: CommandRequest) -> CommandResponse:
        d = manager.get(device_id)
        if d is None:
            raise HTTPException(status_code=404, detail="device not found")
        ok, reply = d.send_command(req.command)
        if not ok and reply.startswith("command not allowed"):
            raise HTTPException(status_code=403, detail=reply)
        if not ok and "transport not connected" in reply:
            raise HTTPException(status_code=503, detail=reply)
        return CommandResponse(ok=ok, reply=reply, command=req.command)

    @app.get("/api/v1/diagnostics")
    async def diagnostics() -> Dict[str, Any]:
        recent = bus.recent(limit=200)
        counts: Dict[str, int] = {}
        for e in recent:
            counts[e.type] = counts.get(e.type, 0) + 1
        out = {
            "agent": agent_id,
            "uptime": time.time() - start,
            "devices": len(manager.list_devices()),
            "event_counts_last_200": counts,
            "events_last_10": [e.to_jsonable() for e in recent[-10:]],
            "command_api_security": {
                "require_auth": auth_required,
                "token_configured": token_configured,
                "token": mask_secret(token),
            },
        }
        if connector is not None:
            out["site_connector"] = connector.diagnostics
        return out

    @app.get("/api/v1/site-connector")
    async def site_connector_status() -> Dict[str, Any]:
        if connector is None:
            return {"enabled": False, "state": "disabled", "available": False}
        return {"available": True, **connector.diagnostics}

    @app.post("/api/v1/site-connector/enable", dependencies=auth_dep)
    async def site_connector_enable(body: Dict[str, Any]) -> Dict[str, Any]:
        """运行时启用/配置 Site Connector。"""
        if connector is None:
            raise HTTPException(status_code=503, detail="connector not initialized")
        endpoint = body.get("endpoint", "")
        credential = body.get("credential", "")
        connector.enable(endpoint=endpoint, credential=credential)
        return connector.diagnostics

    @app.post("/api/v1/site-connector/disable", dependencies=auth_dep)
    async def site_connector_disable() -> Dict[str, Any]:
        if connector is None:
            return {"enabled": False}
        connector.disable()
        return connector.diagnostics

    @app.post("/api/v1/site-connector/pair", dependencies=auth_dep)
    async def site_connector_pair(body: Dict[str, Any]) -> Dict[str, Any]:
        """
        带 pairing_code 向 Site Server 换 credential，然后启用 Connector。
        body: {site_url, pairing_code, agent_version, platform}
        """
        import httpx

        site_url = body.get("site_url", "").rstrip("/")
        pairing_code = body.get("pairing_code", "")
        agent_version = body.get("agent_version", "")
        platform = body.get("platform", "")
        name = body.get("name", agent_id)

        if not site_url or not pairing_code:
            raise HTTPException(status_code=400, detail="site_url and pairing_code required")

        # 1. 拿 pairing-codes → 拿 plain_credential
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 获取新的 pairing-codes（我们用它返回的 plain_credential 而不是 body 里传的）
            # 但调用方也可以直接在 body 里传 plain_credential
            plain_credential = body.get("plain_credential", "")

            # 2. pair
            resp = await client.post(
                f"{site_url}/api/v1/agents/pair",
                json={
                    "pairing_code": pairing_code,
                    "agent_version": agent_version,
                    "platform": platform,
                    "name": name,
                    "agent_id": agent_id,
                },
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Site Server pair failed: {resp.status_code} {resp.text[:200]}",
                )
            pair_resp = resp.json()

            # 3. 如果没传 plain_credential，需要用 pairing-codes API 再拿一次
            # （pairing_code 是一次性的，pair 之后就作废了）
            # 实际使用场景：Agent 先 POST pairing-codes（获得 code + plain），
            # 再 POST pair（获得 agent_id + credential_id）。所以 plain credential
            # 必须在第一次调用时就传过来。
            if not plain_credential:
                raise HTTPException(
                    status_code=400,
                    detail="plain_credential required — get it from /api/v1/agents/pairing-codes",
                )

        # 4. 计算 WS endpoint
        # site_url 是 http://...，换成 ws://...
        ws_endpoint = site_url.replace("http://", "ws://").replace("https://", "wss://")

        # 5. 启用 Connector
        if connector is None:
            raise HTTPException(status_code=503, detail="connector not initialized")
        connector.enable(endpoint=ws_endpoint, credential=plain_credential)

        return {
            "ok": True,
            "agent_id": pair_resp.get("agent_id"),
            "credential_id": pair_resp.get("credential_id"),
            "site_endpoint": ws_endpoint,
            "connector_state": connector.state.value,
        }

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        out: Dict[str, Any] = {"ok": True, "agent": agent_id}
        if connector is not None:
            out["site_connector_connected"] = connector.is_connected
        return out

    # --- WebSocket ---------------------------------------------------

    @app.websocket("/ws/v1/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()

        # 每个 WS 客户端有自己的 EventBus 订阅 + 本地队列
        queue: deque[Event] = deque(maxlen=2048)

        def handler(event: Event) -> None:
            # RAWIQ 默认丢弃 — 帧量太大
            if event.type == "device.frame":
                tc = event.data.get("type_code") if event.data else None
                if tc == 4:  # FRAME_RAWIQ
                    return
            queue.append(event)

        bus.subscribe("*", handler)

        async def send_loop() -> None:
            try:
                while True:
                    if not queue:
                        await asyncio.sleep(0.01)
                        continue
                    ev = queue.popleft()
                    await ws.send_json(ev.to_jsonable())
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        async def recv_loop() -> None:
            try:
                while True:
                    # 客户端发什么我们都忽略 — 目前 WS 是单向事件流
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass

        try:
            await asyncio.gather(send_loop(), recv_loop())
        finally:
            bus.unsubscribe(handler)

    return app
