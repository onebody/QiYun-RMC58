"""Agent 反向 WebSocket 连接处理器（V1.1.2.1 Task 7 已加固）。

流程：
1. Agent 发起 WS 连接 /ws/v1/agents/connect
2. 生产安全默认：**禁止 URL credential**（allow_url_credential=False）
   - URL 带 ?credential= 且 allow_url_credential=False → 直接 4401 拒绝
   - 只有显式开启本地兼容测试时才允许 URL credential
3. 认证必须在首条消息完成：{type: "auth", agent_id, credential}
   - 认证成功前所有业务消息被拒绝
   - 认证超时（默认 10s）关闭连接
4. 认证通过 → 校验 credential → 查出 Agent → 绑 site_id → 进入事件循环
5. 进入事件循环（心跳 + Agent 事件 + Command 转发）
6. 断线 → Agent status → offline，其管理设备 status → offline
7. Agent 重连 → 重新走上面流程

安全承诺：
- Credential 永不出现在日志
- WebSocket URL 日志中 credential 参数必须脱敏
- 认证成功前不处理任何业务消息
- 认证失败/超时立即关闭连接
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from ..database import init_engine, get_session
from ..auth import verify_credential, _sha256
from ..agents import mark_online, record_heartbeat, check_offline_agents, agent_to_dict
from ..devices import (
    upsert_device_from_agent, mark_devices_offline_by_agent, list_devices, device_to_dict,
)
from ..devices.authorization import authorize_device_command
from ..ingestion import map_frame_type, record_device_frame, parse_agent_timestamp
from ..database.models import Agent, Device, Measurement

log = logging.getLogger("site-server.ws")


# --- 工具：URL 脱敏 --------------------------------------------------------

def _redact_ws_url(url: str) -> str:
    """把 WebSocket URL 里的 credential= 参数替换为 ***，防止日志泄漏。"""
    if "credential=" not in url:
        return url
    import re
    return re.sub(r'credential=[^&]*', 'credential=***', url)


# --- 连接注册表（进程内 dict）--------------------------------------------

class AgentConnection:
    def __init__(self, agent_id: str, websocket: WebSocket, site_id: str) -> None:
        self.agent_id = agent_id
        self.websocket = websocket
        self.site_id = site_id
        self.connected_at = time.monotonic()
        self.connected_at_dt = datetime.now(timezone.utc)
        self.running = True
        self._lock = asyncio.Lock()
        # 下行命令等待表：request_id → asyncio.Future[dict]
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_lock = asyncio.Lock()

    async def send_json(self, obj: dict) -> None:
        async with self._lock:
            try:
                await self.websocket.send_json(obj)
            except Exception:
                self.running = False

    async def send_command(self, device_id: str, command: str,
                           timeout: float = 15.0) -> dict:
        """向该 Agent 下发一条设备命令，等待 response 或超时。

        返回：{"ok": bool, "reply": str}
        超时 / 连接断开 → raise asyncio.TimeoutError 或 RuntimeError
        """
        if not self.running:
            raise RuntimeError("agent connection closed")
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        async with self._pending_lock:
            self._pending[request_id] = fut
        try:
            await self.send_json({
                "type": "command.request",
                "request_id": request_id,
                "device_id": device_id,
                "command": command,
            })
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            async with self._pending_lock:
                self._pending.pop(request_id, None)

    async def resolve_command_response(self, request_id: str, result: dict) -> None:
        """收到 Agent 的 command.response 时 resolve 对应 Future。"""
        async with self._pending_lock:
            fut = self._pending.get(request_id)
        if fut and not fut.done():
            fut.set_result(result)

    async def fail_all_pending(self) -> None:
        """连接关闭时 cancel 所有等待中的命令。"""
        async with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(RuntimeError("agent disconnected"))


class AgentConnectionRegistry:
    """进程内 dict — agent_id → AgentConnection。多 Agent 同时连入。"""
    def __init__(self) -> None:
        self._conns: dict[str, AgentConnection] = {}
        self._lock = threading.Lock()

    def register(self, conn: AgentConnection) -> None:
        with self._lock:
            self._conns[conn.agent_id] = conn

    def unregister(self, agent_id: str) -> Optional[AgentConnection]:
        with self._lock:
            return self._conns.pop(agent_id, None)

    def get(self, agent_id: str) -> Optional[AgentConnection]:
        with self._lock:
            return self._conns.get(agent_id)

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._conns.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._conns)


registry = AgentConnectionRegistry()


# --- WS 端点 ------------------------------------------------------------

async def agent_connect_ws(ws: WebSocket) -> None:
    """Agent 反向连接入口（V1.1.2.1 Task 7 安全加固）。

    认证方式：
    - 生产默认：仅允许首条消息 auth {type:"auth", agent_id, credential}
    - 本地兼容：allow_url_credential=True 时才允许 ?credential= URL query
    - 认证成功前禁止处理任何业务消息
    - 认证超时立即关闭连接
    """
    from ..config import ServerConfig

    # --- 1. 读安全配置 ---------------------------------------------------
    try:
        cfg: Optional[ServerConfig] = getattr(ws.app.state, "cfg", None)
    except Exception:
        cfg = None
    if cfg is None:
        from ..config import ServerConfig as _SC
        cfg = _SC()
    ws_cfg = cfg.websocket
    auth_timeout = ws_cfg.auth_timeout_seconds
    allow_url = ws_cfg.allow_url_credential

    await ws.accept()

    # 连接建立时打一条脱敏 URL — 不含 credential
    raw_path = getattr(ws, "url", None) or getattr(ws, "scope", {}).get("path", "")
    safe_url = _redact_ws_url(str(raw_path))
    log.info("Agent WS connected (allow_url_credential=%s): %s", allow_url, safe_url)

    # --- 2. URL credential 检查 ------------------------------------------
    url_agent_id = ws.query_params.get("agent_id", "").strip()
    url_credential = ws.query_params.get("credential", "").strip()

    if url_credential and not allow_url:
        # 生产环境禁止 URL credential — 直接拒绝
        log.warning("Rejected Agent WS: URL credential not allowed in production (path=%s)", safe_url)
        await ws.send_json({"type": "error", "message": "URL credential not allowed — use first-message auth"})
        await ws.close(code=4401, reason="url_credential_not_allowed")
        return

    # --- 3. 确定 credential 来源 -----------------------------------------
    agent_id = url_agent_id
    credential = url_credential if allow_url else ""

    if not credential:
        # 等首条消息 auth（V1.1+ 的标准方式）
        try:
            first_raw = await asyncio.wait_for(ws.receive_json(), timeout=auth_timeout)
        except asyncio.TimeoutError:
            log.warning("Agent auth timeout after %.1fs (path=%s)", auth_timeout, safe_url)
            await ws.send_json({"type": "error", "message": "auth timeout"})
            await ws.close(code=4401, reason="auth_timeout")
            return
        except Exception as exc:
            log.warning("Agent auth receive failed: %s", exc)
            await ws.close(code=4401)
            return

        ftype = first_raw.get("type", "")
        if ftype != "auth":
            # 首条消息不是 auth — 业务数据被拒绝
            log.warning("Agent first message is not auth (got type=%s) — rejected", ftype or "empty")
            await ws.send_json({"type": "error", "message": "expected auth as first message"})
            await ws.close(code=4401, reason="first_message_not_auth")
            return

        agent_id = first_raw.get("agent_id", agent_id).strip()
        credential = first_raw.get("credential", "").strip()
        if not agent_id or not credential:
            log.warning("Agent auth missing agent_id or credential (path=%s)", safe_url)
            await ws.send_json({"type": "error", "message": "missing agent_id or credential in auth"})
            await ws.close(code=4401, reason="missing_auth_fields")
            return

    if not agent_id or not credential:
        await ws.send_json({"type": "error", "message": "missing agent_id or credential"})
        await ws.close(code=4401)
        return

    # --- 4. 校验 credential ----------------------------------------------
    try:
        with get_session() as sess:
            cred = verify_credential(sess, agent_id, credential)
            if cred is None or cred.agent_id != agent_id:
                log.warning("Agent credential invalid or revoked (agent_id=%s)", agent_id)
                await ws.send_json({"type": "error", "message": "credential invalid or revoked"})
                await ws.close(code=4401, reason="credential_invalid")
                return
            agent = sess.get(Agent, agent_id)
            site_id = agent.site_id if agent else "site-001"
            mark_online(sess, agent_id)
    except Exception as exc:
        log.error("Agent auth verification error: %s", exc)
        await ws.send_json({"type": "error", "message": "auth server error"})
        await ws.close(code=4500)
        return

    # --- 5. 注册连接 + 进入主循环 ----------------------------------------
    conn = AgentConnection(agent_id=agent_id, websocket=ws, site_id=site_id)
    registry.register(conn)

    log.info("Agent authenticated: agent_id=%s site_id=%s", agent_id, site_id)

    await ws.send_json({
        "type": "connected",
        "agent_id": agent_id,
        "site_id": site_id,
        "server_time": datetime.now(timezone.utc).isoformat(),
    })

    # --- 主事件循环 ------------------------------------------------------
    try:
        heartbeat_received = True

        async def heartbeat_ack_task() -> None:
            """每 30s 发心跳 ack，没收到 Agent 心跳视为断开。"""
            nonlocal heartbeat_received
            while conn.running:
                await asyncio.sleep(30.0)
                if not heartbeat_received:
                    break
                heartbeat_received = False
                try:
                    await ws.send_json({"type": "heartbeat.ack"})
                except Exception:
                    break

        ack_task = asyncio.create_task(heartbeat_ack_task())

        while conn.running:
            try:
                raw = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
            except asyncio.TimeoutError:
                break
            except WebSocketDisconnect:
                break

            await _handle_agent_message(raw, agent_id=agent_id, site_id=site_id, conn=conn)
            heartbeat_received = True

        ack_task.cancel()
    except Exception as exc:
        log.error("Agent WS loop error (agent_id=%s): %s", agent_id, exc)
    finally:
        # 先 fail 所有等待中的命令，再 unregister——这样 REST 端能收到明确的 RuntimeError
        try:
            await conn.fail_all_pending()
        except Exception:
            pass
        registry.unregister(agent_id)
        log.info("Agent disconnected: agent_id=%s", agent_id)
        try:
            with get_session() as sess:
                mark_devices_offline_by_agent(sess, agent_id)
                from ..agents import update_agent
                update_agent(sess, agent_id, status="offline")
        except Exception:
            pass


# --- Agent 消息处理 ------------------------------------------------------


async def _handle_agent_message(msg: dict, *, agent_id: str, site_id: str,
                                conn: AgentConnection) -> None:
    """Agent 发来的消息分发。"""
    msg_type = msg.get("type", "")

    # 心跳
    if msg_type == "heartbeat":
        try:
            with get_session() as sess:
                record_heartbeat(sess, agent_id)
        except Exception:
            pass
        return

    # Agent 上报 EventBus 事件
    if msg_type == "agent.event":
        event = msg.get("event", {})
        device_boot_id = msg.get("device_boot_id")
        await _process_agent_event(event, agent_id=agent_id, site_id=site_id, conn=conn,
                                   device_boot_id=device_boot_id)
        return

    # 命令请求（Phase 07.2）— 必须先过 Device Owner 授权
    if msg_type in ("command.request", "command"):
        await _handle_inbound_command(msg, agent_id=agent_id, site_id=site_id, conn=conn)
        return

    # Agent 回复 command — resolve REST 端点的等待 Future
    if msg_type == "command.response":
        request_id = str(msg.get("request_id", ""))
        if request_id:
            await conn.resolve_command_response(request_id, {
                "ok": bool(msg.get("ok", False)),
                "reply": str(msg.get("reply", "")),
            })
        return

    # 同步设备快照（Agent 启动后 / 重连后发）
    if msg_type == "agent.devices_snapshot":
        devices = msg.get("devices", [])
        for dev_event in devices:
            await _process_agent_event(dev_event, agent_id=agent_id, site_id=site_id, conn=conn)
        return


async def _handle_inbound_command(msg: dict, *, agent_id: str, site_id: str,
                                  conn: AgentConnection) -> None:
    """处理 Agent 发来的 command.request（授权拦截，不做远程下发）。

    安全要点（Phase 07.2）：
    - 只使用**已认证连接的 agent_id** 判定身份，忽略消息里的 requester/owner 声明。
    - Device Owner 从服务端 Device 记录读取，绝不信任客户端提供的 owner_agent_id。
    - 授权在真正执行设备操作之前完成。当前没有远程命令分发实现，
      即使授权通过也不会执行设备命令，只返回明确的未实现信息。
    """
    request_id = str(msg.get("request_id", ""))
    device_id = str(msg.get("device_id", ""))

    if not request_id or not device_id:
        await conn.send_json({
            "type": "command.response",
            "request_id": request_id,
            "ok": False,
            "reply": "invalid command request: request_id and device_id required",
        })
        return

    try:
        with get_session() as sess:
            decision = authorize_device_command(
                sess, site_id=site_id, agent_id=agent_id, device_id=device_id,
            )
    except Exception as exc:
        log.error("command authorization error (agent=%s device=%s): %s", agent_id, device_id, exc)
        await conn.send_json({
            "type": "command.response",
            "request_id": request_id,
            "ok": False,
            "reply": "authorization error",
        })
        return

    if not decision.allowed:
        log.warning(
            "inbound command denied: agent=%s device=%s role=%s reason=%s request_id=%s",
            agent_id, device_id, decision.role, decision.reason, request_id,
        )
        await conn.send_json({
            "type": "command.response",
            "request_id": request_id,
            "ok": False,
            "reply": f"denied: {decision.reason}",
        })
        return

    # 授权通过，但本轮未实现远程命令分发（不得为了测试权限而新增远程命令系统）
    log.info(
        "inbound command authorized but remote dispatch not implemented "
        "(agent=%s device=%s role=%s request_id=%s)",
        agent_id, device_id, decision.role, request_id,
    )
    await conn.send_json({
        "type": "command.response",
        "request_id": request_id,
        "ok": False,
        "reply": "remote command dispatch not implemented",
    })


async def _process_agent_event(event: dict, *, agent_id: str, site_id: str,
                                conn: AgentConnection,
                                device_boot_id: Optional[str] = None) -> None:
    event_type = event.get("type", "")
    device_id = event.get("device_id", "")
    if not device_id:
        return

    event_data = event.get("data", {})

    try:
        with get_session() as sess:
            if event_type == "device.online":
                transport_info = event_data.get("transport") or {}
                transport_kind = transport_info.get("kind", "usb_serial")
                transport_path = transport_info.get("path", "") or transport_info.get("label", "")
                upsert_device_from_agent(
                    sess,
                    site_id=site_id,
                    agent_id=agent_id,
                    device_id=device_id,
                    device_info=event_data.get("info", {}),
                    transport_info=transport_info,
                    endpoint_identifier=transport_path or f"{transport_kind}:{device_id}",
                )
            elif event_type == "device.discovered":
                # 和 device.online 类似 — Agent 发现设备但还没握手
                transport_info = event_data.get("transport") or {}
                transport_kind = transport_info.get("kind", "usb_serial")
                transport_path = transport_info.get("path", "") or transport_info.get("label", "")
                upsert_device_from_agent(
                    sess,
                    site_id=site_id,
                    agent_id=agent_id,
                    device_id=device_id,
                    device_info=event_data.get("info", {}),
                    transport_info=transport_info,
                    endpoint_identifier=transport_path or f"{transport_kind}:{device_id}",
                )
            elif event_type == "device.frame":
                type_code = event_data.get("type_code")
                decoded = event_data.get("decoded") or {}
                if type_code is None:
                    return
                # Phase 07.3 / 07.4：上游 event_id / agent_received_at 原样入库
                record_device_frame(
                    sess,
                    site_id=site_id,
                    agent_id=agent_id,
                    device_id=device_id,
                    frame_type_code=int(type_code),
                    decoded=decoded,
                    retention_rawiq_enabled=False,
                    device_boot_id=device_boot_id,
                    event_id=event_data.get("event_id"),
                    agent_received_at=parse_agent_timestamp(event_data.get("agent_received_at")),
                    sequence=event_data.get("sequence"),
                    device_timestamp_ms=event_data.get("device_timestamp_ms"),
                )
            elif event_type == "device.state_change":
                # 连接状态变化 → 更新 Device status
                status_to_set = _agent_state_to_site_status(event_data.get("to", ""))
                if status_to_set:
                    dev = sess.get(Device, device_id)
                    if dev:
                        dev.status = status_to_set
                        sess.commit()
            elif event_type == "device.removed":
                # 设备从 Agent 消失 — 不直接删 Site Device（可能只是临时）
                # 仅更新 endpoint status
                pass
    except Exception as exc:
        # 持久化失败不应影响 WS 连接，但必须留痕（尤其非重复类 IntegrityError）
        log.error(
            "agent event persistence failed (type=%s device=%s agent=%s): %s",
            event_type, device_id, agent_id, exc,
        )


def _agent_state_to_site_status(agent_state: str) -> str:
    """Gateway Agent DeviceState → Site Device.status 映射。"""
    mapping = {
        "online": "online",
        "connected": "online",
        "disconnected": "offline",
        "error": "error",
        "reconnecting": "offline",
        "unsupported": "unsupported",
        "identity_conflict": "identity_conflict",
    }
    return mapping.get(agent_state, "")
