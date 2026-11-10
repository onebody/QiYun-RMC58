"""Site Connector V1.1 — Gateway Agent → Site Server 反向 WebSocket 连接管理器。

V1.0 → V1.1 变更：
  A1  future.result() 阻塞 EventBus → 改为 fire-and-forget + add_done_callback
  A2  Credential 在 URL Query → 首条消息 auth {type: "auth", agent_id, credential}
  A3  单一 256 队列 → Critical (128) + Measurement (128) 双队列 + 优先级发送
  A4  WS 多处并发 send → asyncio.Lock 串行化所有 _send_json 调用
  A5  Command 无状态/幂等 → 7 态状态机 + request_id 去重 registry
  A6  Queue.qsize() 跨线程 → 手动计数器维护，REST 线程只读计数器
  A7  6 态 → 9 态（AUTHENTICATING / RECONNECTING / STOPPING / AUTH_FAILED / STOPPED）
  A8  Diagnostics 12 字段 → 30+ 字段
  A9  stop() 仅 join 3s → 超时后 force shutdown via loop
  A10 无 TLS 配置 → ssl_ctx 支持 CA file + verify

设计原则：
1. Connector 独立 asyncio loop 跑在 daemon 线程 — 不影响 Agent 主进程（threading）
2. EventBus 同步 handler → asyncio Queue bridge 纯 fire-and-forget
3. Critical / Measurement 双队列，重要事件不被高频频谱挤占
4. 所有 WS send 通过 asyncio.Lock 串行化（websockets asyncio 不支持并发写）
5. 首条消息认证（URL 仅带 agent_id）— 生产安全
6. Command 7 态状态机 + request_id registry 保证幂等
7. 9 态 ConnectorState + 统一断线入口 + 指数退避重连
8. Diagnostics 全部字段在 event loop 中维护 — REST 线程只读计数器
9. 故障隔离：Connector 任何异常都 catch，不传播到 Agent 主进程
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import ssl
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
import websockets
from websockets.exceptions import (
    ConnectionClosed, ConnectionClosedError, ConnectionClosedOK,
    InvalidStatusCode, WebSocketException,
)

from .device import Device, DeviceState
from .events import Event, EventBus
from .identity import (
    coerce_device_timestamp_ms,
    coerce_sequence,
    make_event_id,
    utc_now_iso,
)
from .protocol import is_command_allowed


log = logging.getLogger("site-connector")


# ============================================================
# Connector 状态（V1.1: 9 态）
# ============================================================

class ConnectorState(str, enum.Enum):
    DISABLED = "disabled"        # 未启用
    STOPPED = "stopped"          # 已显式停止
    CONNECTING = "connecting"    # WS 握手中
    AUTHENTICATING = "authenticating"  # WS 已连，发送首条 auth 消息
    CONNECTED = "connected"      # 认证通过 + 心跳正常 + 事件转发中
    RECONNECTING = "reconnecting"  # 断线，退避等待下一次连接
    AUTH_FAILED = "auth_failed"  # 认证失败（credential 撤销/过期）— 停止重连
    ERROR = "error"              # 持续性错误 — 等待手动处理
    STOPPING = "stopping"        # stop() 正在执行


# ============================================================
# 断线原因（统一入口驱动重连）
# ============================================================

class DisconnectReason(str, enum.Enum):
    USER_INITIATED = "user_initiated"
    CONNECT_FAILED = "connect_failed"
    AUTH_FAILED = "auth_failed"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    WS_CLOSED = "ws_closed"
    WS_ERROR = "ws_error"
    SITE_UNAVAILABLE = "site_unavailable"


# ============================================================
# Command 状态机
# ============================================================

class CommandState(str, enum.Enum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


@dataclass
class CommandRecord:
    """已处理的命令（用于幂等性）。"""
    request_id: str
    device_id: str
    command: str
    state: CommandState
    ok: bool = False
    reply: str = ""
    received_at: float = field(default_factory=time.monotonic)
    completed_at: Optional[float] = None


# ============================================================
# Queue 容量
# ============================================================

CRITICAL_QUEUE_MAX = 128
MEASUREMENT_QUEUE_MAX = 128
MAX_COMMAND_REGISTRY = 1024  # 最多记住这么多 request_id，防止内存泄漏
COMMAND_EXEC_TIMEOUT = 30.0  # 命令执行总超时（秒）


# ============================================================
# Connector 主类
# ============================================================

class SiteConnector:
    """
    Agent ↔ Site Server 反向 WS 连接管理器（V1.1）。

    用法：
        connector = SiteConnector(
            bus=bus, agent_id=cfg.agent.id, manager=manager,
            endpoint="ws://192.168.1.100:8766", credential="...",
        )
        connector.start()   # 非阻塞
        ...
        connector.stop()    # 阻塞直到线程退出（最多 5s）
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        agent_id: str,
        manager: Optional[Any] = None,
        endpoint: str = "",
        credential: str = "",
        enabled: bool = False,
        heartbeat_interval: float = 15.0,
        heartbeat_ack_timeout: float = 60.0,
        reconnect_initial_delay: float = 2.0,
        reconnect_max_delay: float = 60.0,
        reconnect_multiplier: float = 2.0,
        critical_queue_max: int = CRITICAL_QUEUE_MAX,
        measurement_queue_max: int = MEASUREMENT_QUEUE_MAX,
        tls_ca_file: Optional[str] = None,
        tls_verify: bool = True,
        agent_boot_id: Optional[str] = None,
    ) -> None:
        # --- 配置 ---
        self._bus = bus
        self._agent_id = agent_id
        self._manager = manager
        self._endpoint = endpoint
        self._credential = credential
        self._enabled = enabled
        self._agent_boot_id = agent_boot_id or uuid.uuid4().hex  # Agent 启动周期标识
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_ack_timeout = heartbeat_ack_timeout
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_multiplier = reconnect_multiplier
        self._critical_queue_max = critical_queue_max
        self._measurement_queue_max = measurement_queue_max
        self._tls_ca_file = tls_ca_file
        self._tls_verify = tls_verify

        # --- 状态机 ---
        if not enabled:
            self._state = ConnectorState.DISABLED
        elif not credential or not endpoint:
            self._state = ConnectorState.STOPPED
        else:
            self._state = ConnectorState.STOPPED  # start() 会推进到 CONNECTING

        self._disconnect_reason: Optional[DisconnectReason] = None
        self._lock = threading.Lock()

        # --- 运行时 ---
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.asyncio.WebSocketClientProtocol] = None
        self._send_lock: Optional[asyncio.Lock] = None

        # --- 连接时间戳 ---
        self._connected_at: Optional[float] = None
        self._last_disconnected_at: Optional[float] = None
        self._last_heartbeat_sent: float = 0.0
        self._last_heartbeat_ack: float = 0.0
        self._last_error: str = ""
        self._reconnect_attempts: int = 0
        self._total_disconnects: int = 0

        # --- 双队列（仅在 event loop 内操作）---
        self._critical_queue: Optional[asyncio.Queue] = None
        self._measurement_queue: Optional[asyncio.Queue] = None

        # --- 手动队列长度计数器（REST 线程安全只读）---
        self._critical_queue_len = 0
        self._measurement_queue_len = 0

        # --- 丢弃/统计计数器（event loop 维护，线程安全只读）---
        self._total_events_sent = 0
        self._total_events_failed = 0
        self._dropped_critical = 0
        self._dropped_measurement = 0
        self._dropped_by_type: Dict[str, int] = defaultdict(int)
        self._critical_queue_full_count = 0
        self._measurement_queue_full_count = 0

        # --- Command 状态机 ---
        self._command_registry: Dict[str, CommandRecord] = {}
        self._commands_received = 0
        self._commands_succeeded = 0
        self._commands_failed = 0
        self._commands_timeout = 0
        self._commands_rejected = 0
        self._commands_unauthorized = 0

        # --- EventBus handler 注册 ---
        self._handlers_registered = False

    # ============================================================
    # 属性（线程安全只读）
    # ============================================================

    @property
    def state(self) -> ConnectorState:
        with self._lock:
            return self._state

    @property
    def is_connected(self) -> bool:
        return self.state == ConnectorState.CONNECTED

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def has_credential(self) -> bool:
        return bool(self._credential)

    @property
    def diagnostics(self) -> dict:
        """丰富诊断 — 线程安全只读（计数器均为 Python int 原子读）。"""
        with self._lock:
            state = self._state.value
        return {
            # --- 配置 ---
            "enabled": self._enabled,
            "state": state,
            "endpoint": self._endpoint,
            "has_credential": bool(self._credential),
            "tls_ca_file": self._tls_ca_file,
            "tls_verify": self._tls_verify,

            # --- 连接 ---
            "connected_at": self._connected_at,
            "last_disconnected_at": self._last_disconnected_at,
            "last_error": self._last_error,
            "reconnect_attempts": self._reconnect_attempts,
            "total_disconnects": self._total_disconnects,
            "ws_open": self._ws is not None,

            # --- 心跳 ---
            "last_heartbeat_sent": self._last_heartbeat_sent,
            "last_heartbeat_ack": self._last_heartbeat_ack,

            # --- 队列 ---
            "critical_queue_len": self._critical_queue_len,
            "critical_queue_cap": self._critical_queue_max,
            "measurement_queue_len": self._measurement_queue_len,
            "measurement_queue_cap": self._measurement_queue_max,

            # --- 事件统计 ---
            "total_events_sent": self._total_events_sent,
            "total_events_failed": self._total_events_failed,
            "dropped_critical": self._dropped_critical,
            "dropped_measurement": self._dropped_measurement,
            "dropped_by_type": dict(self._dropped_by_type),
            "critical_queue_full_count": self._critical_queue_full_count,
            "measurement_queue_full_count": self._measurement_queue_full_count,

            # --- Command ---
            "pending_commands": len(self._command_registry),
            "commands_received": self._commands_received,
            "commands_succeeded": self._commands_succeeded,
            "commands_failed": self._commands_failed,
            "commands_timeout": self._commands_timeout,
            "commands_rejected": self._commands_rejected,
            "commands_unauthorized": self._commands_unauthorized,
        }

    # ============================================================
    # 启停（线程安全）
    # ============================================================

    def start(self) -> None:
        """启动 Connector（非阻塞）。"""
        with self._lock:
            if not self._enabled:
                log.info("Site Connector disabled — skipping start")
                self._state = ConnectorState.DISABLED
                return

            if self._thread is not None and self._thread.is_alive():
                # 已有活着的线程 — 可能是上一个 stop() 超时残留
                log.warning("Site Connector thread already alive — forcing stale shutdown")
                self._force_stale_shutdown()

            if not self._credential or not self._endpoint:
                log.warning("Site Connector enabled but missing credential or endpoint — waiting pair()")
                self._state = ConnectorState.STOPPED
                return

            # 重置运行时
            self._stop_event.clear()
            self._connected_at = None
            self._last_disconnected_at = None
            self._reconnect_attempts = 0

            self._state = ConnectorState.CONNECTING

        # 注册 EventBus handler
        self._register_event_handlers()

        # 启动 daemon 线程
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name="SiteConnector",
            daemon=True,
        )
        self._thread.start()
        log.info("Site Connector starting: agent_id=%s endpoint=%s",
                 self._agent_id[:12], self._endpoint)

    def stop(self) -> None:
        """优雅关闭（阻塞直到线程退出或最多 5s）。"""
        log.info("Site Connector stopping...")

        with self._lock:
            if self._state in (ConnectorState.DISABLED, ConnectorState.STOPPED, ConnectorState.STOPPING):
                return
            self._state = ConnectorState.STOPPING

        self._disconnect_all(DisconnectReason.USER_INITIATED)

        if self._thread is not None:
            # 先优雅等 3s
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                log.warning("Site Connector thread didn't exit in 3s — force shutdown")
                self._force_stale_shutdown()
                self._thread.join(timeout=2.0)
            self._thread = None

        self._unregister_event_handlers()

        with self._lock:
            self._state = ConnectorState.STOPPED
        log.info("Site Connector stopped")

    def enable(self, endpoint: str = "", credential: str = "") -> None:
        with self._lock:
            if endpoint:
                self._endpoint = endpoint
            if credential:
                self._credential = credential
            self._enabled = True
        self.start()

    def disable(self) -> None:
        with self._lock:
            self._enabled = False
        self.stop()

    def _force_stale_shutdown(self) -> None:
        """线程已超时但 loop 还在 — 强行在 loop 里 cancel 所有 task + close WS。"""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            # 设 stop_event 让 loop 看到
            self._stop_event.set()
            # 调度一个强杀协程
            future = asyncio.run_coroutine_threadsafe(self._force_shutdown_async(), self._loop)
            future.result(timeout=2.0)
        except Exception as exc:
            log.debug("force shutdown exception (ignored): %s", exc)

    async def _force_shutdown_async(self) -> None:
        """在 Connector event loop 内执行的强杀。"""
        # 取消所有正在跑的 task
        current = asyncio.current_task()
        for task in asyncio.all_tasks(self._loop):
            if task is not current and not task.done():
                task.cancel()
        # 关闭 WS
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ============================================================
    # 配对（REST → 拿 credential）
    # ============================================================

    async def pair_via_rest(
        self,
        site_url: str,
        pairing_code: str,
        agent_version: str = "",
        platform: str = "",
        name: str = "",
    ) -> bool:
        """带 pairing_code 向 Site Server REST 换 credential（plain credential 由调用方注入）。"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{site_url.rstrip('/')}/api/v1/agents/pair",
                json={
                    "pairing_code": pairing_code,
                    "agent_version": agent_version,
                    "platform": platform,
                    "name": name or self._agent_id,
                    "agent_id": self._agent_id,
                },
            )
            if resp.status_code != 200:
                log.error("pair failed: %d %s", resp.status_code, resp.text[:200])
                return False
        return True

    # ============================================================
    # EventBus → asyncio Queue 桥接（V1.1 关键：纯 fire-and-forget）
    # ============================================================

    def _register_event_handlers(self) -> None:
        if self._handlers_registered:
            return
        self._handlers_registered = True
        self._bus.subscribe("device.online", self._bus_handler)
        self._bus.subscribe("device.frame", self._bus_handler)
        self._bus.subscribe("device.state_change", self._bus_handler)
        self._bus.subscribe("device.registered", self._bus_handler)
        self._bus.subscribe("device.removed", self._bus_handler)
        self._bus.subscribe("agent.started", self._bus_handler)
        self._bus.subscribe("agent.error", self._bus_handler)

    def _unregister_event_handlers(self) -> None:
        if not self._handlers_registered:
            return
        self._handlers_registered = False
        self._bus.unsubscribe(self._bus_handler)

    def _bus_handler(self, event: Event) -> None:
        """
        EventBus 同步回调 — 在 Device 线程中执行。

        V1.1 关键约束：
        1. 不得阻塞发布线程 — 纯 fire-and-forget
        2. 不得等待 Connector event loop
        3. 队列满时丢弃 + 计数
        """
        # 快速门控：Connector 不在正常态 → 丢弃
        state = self.state
        if state not in (ConnectorState.CONNECTED, ConnectorState.CONNECTING,
                         ConnectorState.AUTHENTICATING, ConnectorState.RECONNECTING):
            return

        loop = self._loop
        q_crit = self._critical_queue
        q_meas = self._measurement_queue
        if loop is None or loop.is_closed():
            return

        # 决定进哪个队列
        is_critical = self._is_critical_event(event)
        queue = q_crit if is_critical else q_meas

        # 构造入队协程（put_nowait 不阻塞）
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._queue_put_nowait(queue, event, is_critical),
                loop,
            )
            # 仅注册 done callback 做统计 — 不等待
            future.add_done_callback(self._on_bus_future_done)
        except Exception as exc:
            log.debug("event submit failed (loop=%s): %s", loop.is_closed(), exc)
            self._dropped_by_type[event.type] += 1

    def _is_critical_event(self, event: Event) -> bool:
        """哪些事件进 Critical Queue（不被高频频谱挤占）。"""
        etype = event.type
        if etype in ("device.online", "device.state_change", "device.registered",
                      "device.removed", "agent.started", "agent.error"):
            return True
        # device.frame RAWIQ 跳过（Connector 内过滤）— 但非 RAWIQ frame 进 Measurement
        return False

    async def _queue_put_nowait(self, queue: asyncio.Queue, event: Event,
                                 is_critical: bool) -> bool:
        """非阻塞入队。队列满时丢弃 + 计数。返回 True=已入队。"""
        try:
            queue.put_nowait(event)
            # 更新计数器
            if is_critical:
                self._critical_queue_len = queue.qsize()
            else:
                self._measurement_queue_len = queue.qsize()
            return True
        except asyncio.QueueFull:
            if is_critical:
                self._dropped_critical += 1
                self._critical_queue_full_count += 1
                # Critical 队列满 — 严重错误，打印 warning
                log.warning("CRITICAL QUEUE FULL — dropping event type=%s", event.type)
            else:
                self._dropped_measurement += 1
                self._measurement_queue_full_count += 1
            self._dropped_by_type[event.type] += 1
            return False

    def _on_bus_future_done(self, future: asyncio.Future) -> None:
        """EventBus 投递完成回调（可能丢事件）。"""
        try:
            ok = future.result()
            if not ok:
                # put_nowait 返回 False = 队列满丢弃
                pass  # 计数在 _queue_put_nowait 里已经加了
        except (asyncio.CancelledError, Exception):
            # loop 已关闭 / future 被取消 — 正常 shutdown 路径
            pass

    # ============================================================
    # 事件转换：Agent EventBus Event → Site Server envelope
    # ============================================================

    def _to_site_envelope(self, bus_event: Event) -> Optional[dict]:
        """返回 None = 跳过不转发的事件。"""
        etype = bus_event.type
        did = bus_event.device_id
        agent = self._manager.get(did) if self._manager and did else None
        # device_boot_id: C5 设备启动周期标识（设备重启后自动更新）
        # agent_boot_id: Agent 进程启动标识（仅用于诊断）
        device_boot_id = agent.device_boot_id if agent else None
        agent_boot_id = self._agent_boot_id

        if etype == "device.online" and did:
            agent = self._manager.get(did) if self._manager else None
            info = agent.info if agent else bus_event.data.get("info", {})
            ti = agent.transport_info if agent else None
            td = bus_event.data.get("transport") or {}
            return {
                "type": "agent.event",
                "device_boot_id": device_boot_id,
                "event": {
                    "type": "device.online",
                    "device_id": did,
                    "data": {
                        "info": info,
                        "transport": {
                            "kind": ti.kind if ti else td.get("kind", "usb_serial"),
                            "path": (ti.path if ti else td.get("path")) or (ti.label if ti else td.get("label", "")),
                        },
                    },
                },
            }

        if etype == "device.frame" and did:
            type_code = bus_event.data.get("type_code", 0)
            if type_code == 4:  # RAWIQ — 默认跳过
                return None
            decoded = bus_event.data.get("decoded") or {}
            # Phase 07.3 / 07.4：Agent 已生成的时间戳与 event_id 原样转发；
            # 仅在旧数据源缺失时补齐一次（不覆盖已有值）。
            event_id = bus_event.data.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                event_id = make_event_id(
                    did,
                    device_boot_id,
                    type_code,
                    coerce_sequence(decoded),
                    coerce_device_timestamp_ms(decoded),
                )
            agent_received_at = bus_event.data.get("agent_received_at") or utc_now_iso()
            return {
                "type": "agent.event",
                "device_boot_id": device_boot_id,
                "event": {
                    "type": "device.frame",
                    "device_id": did,
                    "data": {
                        "type_code": type_code,
                        "decoded": decoded,
                        "event_id": event_id,
                        "agent_received_at": agent_received_at,
                        "sequence": bus_event.data.get("sequence"),
                        "device_timestamp_ms": bus_event.data.get("device_timestamp_ms"),
                    },
                },
            }

        if etype == "device.state_change" and did:
            to_state = bus_event.data.get("to", "")
            if to_state in ("discovered",):
                return None
            return {
                "type": "agent.event",
                "device_boot_id": device_boot_id,
                "event": {
                    "type": "device.state_change",
                    "device_id": did,
                    "data": {
                        "from": bus_event.data.get("from"),
                        "to": to_state,
                        "error": bus_event.data.get("error"),
                    },
                },
            }

        if etype == "device.registered" and did:
            transport = bus_event.data.get("transport") or {}
            return {
                "type": "agent.event",
                "device_boot_id": device_boot_id,
                "event": {
                    "type": "device.discovered",
                    "device_id": did,
                    "data": {"info": {}, "transport": transport},
                },
            }

        if etype == "device.removed" and did:
            return {
                "type": "agent.event",
                "device_boot_id": device_boot_id,
                "event": {
                    "type": "device.removed",
                    "device_id": did,
                    "data": {},
                },
            }

        return None  # agent.started 等不转发

    # ============================================================
    # asyncio 主循环
    # ============================================================

    def _run_event_loop(self) -> None:
        """Connector daemon 线程入口。"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._lifecycle())
        except Exception as exc:
            log.error("Connector event loop crashed: %s", exc, exc_info=True)
        finally:
            # 清理 WS（在 close 之前）
            ws = self._ws
            self._ws = None
            try:
                self._loop.run_until_complete(self._safe_close_ws(ws))
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    async def _lifecycle(self) -> None:
        """核心状态机循环。"""
        assert self._loop is not None
        self._send_lock = asyncio.Lock()

        # 初始化双队列 + counters
        self._critical_queue = asyncio.Queue(maxsize=self._critical_queue_max)
        self._measurement_queue = asyncio.Queue(maxsize=self._measurement_queue_max)
        self._critical_queue_len = 0
        self._measurement_queue_len = 0

        while not self._stop_event.is_set():
            # 1. 建立 WS 连接
            self._set_state(ConnectorState.CONNECTING)
            ws = await self._try_connect()
            if ws is None:
                if self._stop_event.is_set():
                    break
                await self._backoff_reconnect()
                if self._stop_event.is_set():
                    break
                continue

            # 2. 首条消息认证
            self._set_state(ConnectorState.AUTHENTICATING)
            auth_ok = await self._do_auth(ws)
            if not auth_ok:
                self._close_ws_obj(ws)
                if self._stop_event.is_set():
                    break
                await self._backoff_reconnect()
                continue

            # 3. 标记连接成功
            self._ws = ws
            self._connected_at = time.monotonic()
            self._reconnect_attempts = 0
            self._set_state(ConnectorState.CONNECTED)
            log.info("Site Connector connected & authenticated: %s → %s",
                     self._agent_id[:12], self._endpoint)

            # 4. 发送设备快照
            await self._send_devices_snapshot()

            # 5. 启动三个并发 task
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            send_task = asyncio.create_task(self._outbound_priority_dispatch())
            recv_task = asyncio.create_task(self._inbound_dispatch_loop())

            # 6. 等任一 task 完成（断线）
            done, pending = await asyncio.wait(
                [heartbeat_task, send_task, recv_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # 7. 清理所有 task
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await asyncio.wait_for(t, timeout=2.0)
                except (asyncio.CancelledError, Exception):
                    pass

            # 8. 推断断线原因 → 决定重连
            reason = self._infer_disconnect_reason(done)
            await self._safe_close_ws(self._ws)
            self._ws = None
            self._last_disconnected_at = time.monotonic()
            self._total_disconnects += 1

            if reason == DisconnectReason.USER_INITIATED or self._stop_event.is_set():
                self._set_state(ConnectorState.STOPPED)
                break

            if reason == DisconnectReason.AUTH_FAILED:
                self._set_state(ConnectorState.AUTH_FAILED, reason)
                log.error("AUTH_FAILED — credential rejected; stopping reconnect")
                # AUTH_FAILED 后清空 credential 防止无意义重试
                self._credential = ""
                self._enabled = False
                break

            # 9. 进入 RECONNECTING + 退避
            self._set_state(ConnectorState.RECONNECTING, reason)
            await self._backoff_reconnect()

        log.info("Site Connector lifecycle exited")

    # ============================================================
    # WS 连接 + 首条消息认证
    # ============================================================

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """构建 TLS context（仅 wss://）。"""
        if not self._endpoint.startswith("wss://"):
            return None
        if not self._tls_verify:
            ctx = ssl._create_unverified_context()
        else:
            ctx = ssl.create_default_context(cafile=self._tls_ca_file)
        return ctx

    async def _try_connect(self) -> Optional[websockets.asyncio.WebSocketClientProtocol]:
        """建立 WS 连接（URL 仅带 agent_id — 不带 credential）。"""
        url = f"{self._endpoint.rstrip('/')}/ws/v1/agents/connect?agent_id={self._agent_id}"
        ssl_ctx = self._build_ssl_context()

        try:
            ws = await asyncio.wait_for(
                websockets.connect(url, ssl=ssl_ctx,
                                  additional_headers={"User-Agent": "WIDSCAN58-Agent/1.1.0"}),
                timeout=10.0,
            )
            return ws

        except InvalidStatusCode as exc:
            log.error("connect rejected (HTTP %d): %s", exc.status_code, exc.reason)
            self._last_error = f"HTTP {exc.status_code}"
            if exc.status_code in (4401, 401):
                self._set_state(ConnectorState.AUTH_FAILED, DisconnectReason.AUTH_FAILED)
                self._credential = ""
                self._enabled = False
                return None
            self._disconnect_reason = DisconnectReason.CONNECT_FAILED
            return None

        except (ConnectionClosed, ConnectionClosedError, WebSocketException, OSError) as exc:
            log.warning("connect transport error: %s", exc)
            self._last_error = str(exc)
            self._disconnect_reason = DisconnectReason.SITE_UNAVAILABLE
            return None

        except Exception as exc:
            log.error("connect unexpected error: %s", exc, exc_info=True)
            self._last_error = str(exc)
            self._disconnect_reason = DisconnectReason.WS_ERROR
            return None

    async def _do_auth(self, ws) -> bool:
        """
        发送首条 auth 消息。
        V1.1 安全设计：Credential 走消息体，不走 URL。
        同时兼容 Site Server V1.0（它从 URL 读 credential；我们已经不带 credential，
        所以 Site Server V1.0 会回 4401）。
        """
        # 先等 Site Server 的 connected（V1.0 协议）— 或 Site Server V1.1 等我们 auth
        # 我们先尝试发 auth；Site Server V1.1 会处理 auth 消息，V1.0 会等 URL credential 然后拒绝
        auth_msg = {
            "type": "auth",
            "agent_id": self._agent_id,
            "credential": self._credential,
            "agent_version": "1.1.0",
            "ts": time.monotonic(),
        }
        try:
            await self._send_json_raw(ws, auth_msg)
        except Exception as exc:
            log.warning("auth send failed: %s", exc)
            self._last_error = f"auth send failed: {exc}"
            return False

        # 等 Site Server 回 connected（V1.1: 收到 auth 后发 connected；V1.0: URL 无 credential 会发 4401 close）
        try:
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            resp = json.loads(resp_raw)
            if resp.get("type") == "connected":
                log.info("auth OK: site=%s", resp.get("site_id"))
                return True
            if resp.get("type") == "error":
                log.error("auth rejected: %s", resp.get("message", resp))
                self._last_error = f"auth rejected: {resp.get('message')}"
                self._disconnect_reason = DisconnectReason.AUTH_FAILED
                return False
            log.warning("auth unexpected response: %s", resp)
            return False
        except asyncio.TimeoutError:
            log.warning("auth handshake timeout (5s)")
            self._last_error = "auth handshake timeout"
            return False
        except Exception as exc:
            log.warning("auth handshake failed: %s", exc)
            self._last_error = f"auth handshake failed: {exc}"
            return False

    # ============================================================
    # 指数退避重连（带随机抖动）
    # ============================================================

    async def _backoff_reconnect(self) -> None:
        self._reconnect_attempts += 1
        import random
        delay = self._reconnect_initial_delay * (self._reconnect_multiplier ** (self._reconnect_attempts - 1))
        delay = min(delay, self._reconnect_max_delay)
        delay *= (0.5 + random.random() * 0.5)  # 抖动 ±50%
        log.info("Site Connector reconnect attempt %d in %.1fs",
                 self._reconnect_attempts, delay)

        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and not self._stop_event.is_set():
            await asyncio.sleep(0.2)

    # ============================================================
    # 三个并发 task
    # ============================================================

    async def _heartbeat_loop(self) -> None:
        """每 N 秒发 heartbeat，超时没 ack → 断开。"""
        self._last_heartbeat_ack = time.monotonic()
        while not self._stop_event.is_set():
            await asyncio.sleep(self._heartbeat_interval)
            if self._stop_event.is_set():
                break

            if time.monotonic() - self._last_heartbeat_ack > self._heartbeat_ack_timeout:
                log.warning("heartbeat ack timeout (%.1fs) — disconnecting",
                            self._heartbeat_ack_timeout)
                self._disconnect_reason = DisconnectReason.HEARTBEAT_TIMEOUT
                self._last_error = "heartbeat ack timeout"
                raise RuntimeError("heartbeat ack timeout")

            msg = {
                "type": "heartbeat",
                "agent_id": self._agent_id,
                "uptime": int(time.monotonic() - (self._connected_at or time.monotonic())),
                "devices": [
                    {"device_id": d.device_id}
                    for d in (self._manager.list_devices() if self._manager else [])
                ],
            }
            try:
                await self._send_json(msg)
                self._last_heartbeat_sent = time.monotonic()
            except Exception:
                log.warning("heartbeat send failed")
                raise

    async def _outbound_priority_dispatch(self) -> None:
        """
        V1.1: 优先级发送 — Critical 队列优先消费，间隙拉 Measurement。
        单一消费者串行发送 — 保证 WS 不被并发写。
        """
        assert self._critical_queue is not None and self._measurement_queue is not None

        async def _recv_critical() -> Optional[Event]:
            try:
                return await asyncio.wait_for(
                    self._critical_queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                return None

        async def _recv_measurement() -> Optional[Event]:
            try:
                return await asyncio.wait_for(
                    self._measurement_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                return None

        while not self._stop_event.is_set():
            # 优先 critical
            got_critical = await _recv_critical()
            if got_critical is None:
                # 间隙拉 measurement
                got_critical = await _recv_measurement()
            if got_critical is None:
                continue

            envelope = self._to_site_envelope(got_critical)
            if envelope is None:
                continue

            try:
                await self._send_json(envelope)
                self._total_events_sent += 1
            except Exception as exc:
                log.debug("event send failed: %s", exc)
                self._total_events_failed += 1
                raise  # 抛出让上层感知断线

            # 更新计数器（event loop 内原子）
            self._critical_queue_len = self._critical_queue.qsize()
            self._measurement_queue_len = self._measurement_queue.qsize()

    async def _inbound_dispatch_loop(self) -> None:
        """接收 Site Server 消息 → 分发。"""
        assert self._ws is not None
        while not self._stop_event.is_set():
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=90.0)
            except asyncio.TimeoutError:
                continue
            except ConnectionClosed:
                self._disconnect_reason = DisconnectReason.WS_CLOSED
                raise
            except Exception as exc:
                self._last_error = f"ws.recv: {exc}"
                raise

            try:
                msg = json.loads(raw)
            except Exception:
                log.warning("invalid JSON from Site Server: %s", raw[:200])
                continue

            await self._handle_site_message(msg)

    # ============================================================
    # Site Server 消息 → 分发
    # ============================================================

    async def _handle_site_message(self, msg: dict) -> None:
        mtype = msg.get("type", "")
        if mtype == "heartbeat.ack":
            self._last_heartbeat_ack = time.monotonic()
            return
        if mtype == "command.request":
            await self._handle_command_request(msg)
            return
        log.debug("ignoring Site Server msg type=%s", mtype)

    async def _handle_command_request(self, msg: dict) -> None:
        """
        V1.1: Command 7 态状态机 + 幂等性 registry。

        状态流转:
          RECEIVED → ACCEPTED → EXECUTING → SUCCEEDED/FAILED/TIMEOUT/REJECTED

        同 request_id 重复到达:
          查 registry → 若已终态 → 直接返回缓存结果（不重新执行）
        """
        request_id = msg.get("request_id", "")
        device_id = msg.get("device_id", "")
        command = msg.get("command", "")

        self._commands_received += 1

        # --- 0. request_id 校验 ---
        if not request_id:
            log.warning("command.request missing request_id — ignoring")
            self._commands_rejected += 1
            return

        # --- 1. 幂等性检查 ---
        existing = self._command_registry.get(request_id)
        if existing is not None:
            log.info("command.request duplicate request_id=%s — returning cached result (state=%s)",
                     request_id, existing.state.value)
            if existing.state in (CommandState.SUCCEEDED, CommandState.FAILED,
                                  CommandState.TIMEOUT, CommandState.REJECTED):
                await self._send_command_response(request_id, ok=existing.ok, reply=existing.reply)
            return

        # --- 1.5 Agent 侧归属授权（Phase 07.2）---
        # 不信任命令消息里自带的 Agent ID，只把它当作断言：
        # 断言必须与当前已认证 Agent 身份一致，否则拒绝。
        claimed_agent = msg.get("target_agent_id", msg.get("agent_id"))
        if claimed_agent is not None and claimed_agent != self._agent_id:
            log.warning(
                "command.request rejected: agent ownership mismatch "
                "(claimed=%s actual=%s request_id=%s)",
                claimed_agent, self._agent_id, request_id,
            )
            self._commands_unauthorized += 1
            await self._register_and_respond(
                request_id, device_id, command,
                CommandState.REJECTED, ok=False,
                reply="command rejected: agent ownership mismatch")
            return

        # --- 2. 基本校验 ---
        if self._manager is None:
            await self._register_and_respond(
                request_id, device_id, command,
                CommandState.REJECTED, ok=False, reply="connector has no device manager")
            self._commands_rejected += 1
            return

        device = self._manager.get(device_id)
        if device is None:
            await self._register_and_respond(
                request_id, device_id, command,
                CommandState.REJECTED, ok=False, reply=f"device {device_id} not found")
            self._commands_rejected += 1
            return

        if device.state in (DeviceState.DISCONNECTED, DeviceState.RECONNECTING, DeviceState.ERROR):
            await self._register_and_respond(
                request_id, device_id, command,
                CommandState.REJECTED, ok=False,
                reply=f"device offline/error ({device.state.value})")
            self._commands_rejected += 1
            return

        if not is_command_allowed(command):
            await self._register_and_respond(
                request_id, device_id, command,
                CommandState.REJECTED, ok=False, reply=f"command not allowed: {command}")
            self._commands_rejected += 1
            return

        # --- 3. 入 registry + 执行 ---
        rec = CommandRecord(
            request_id=request_id, device_id=device_id, command=command,
            state=CommandState.ACCEPTED,
        )
        self._command_registry[request_id] = rec

        # ACCEPTED → EXECUTING
        rec.state = CommandState.EXECUTING

        try:
            loop = asyncio.get_event_loop()
            ok, reply = await asyncio.wait_for(
                loop.run_in_executor(None, device.send_command, command),
                timeout=COMMAND_EXEC_TIMEOUT,
            )
            rec.state = CommandState.SUCCEEDED if ok else CommandState.FAILED
            rec.ok = ok
            rec.reply = (reply or "")[:500]
            rec.completed_at = time.monotonic()

            if ok:
                self._commands_succeeded += 1
            else:
                self._commands_failed += 1

        except asyncio.TimeoutError:
            rec.state = CommandState.TIMEOUT
            rec.ok = False
            rec.reply = f"execution timeout ({COMMAND_EXEC_TIMEOUT}s)"
            rec.completed_at = time.monotonic()
            self._commands_timeout += 1
            log.warning("command timeout: request_id=%s device=%s", request_id, device_id)

        except Exception as exc:
            rec.state = CommandState.FAILED
            rec.ok = False
            rec.reply = f"execution error: {exc}"
            rec.completed_at = time.monotonic()
            self._commands_failed += 1
            log.warning("command execution failed: %s", exc)

        # --- 4. 回传结果 ---
        try:
            await self._send_command_response(request_id, ok=rec.ok, reply=rec.reply)
        except Exception as exc:
            log.debug("command.response send failed: %s", exc)
            # 结果发送失败 — 但 registry 里已有终态，重连后 Site Server 重发同一 request_id 时
            # 我们会返回缓存结果（不重复执行）
            pass

        # --- 5. 清理旧 registry 条目（防内存泄漏）---
        if len(self._command_registry) > MAX_COMMAND_REGISTRY:
            # 删掉最老的一半
            old_keys = list(self._command_registry.keys())[:MAX_COMMAND_REGISTRY // 2]
            for k in old_keys:
                self._command_registry.pop(k, None)

    async def _register_and_respond(self, request_id, device_id, command,
                                     state: CommandState, *, ok: bool, reply: str) -> None:
        """REJECTED 等早期退出也入 registry（防重复执行）。"""
        self._command_registry[request_id] = CommandRecord(
            request_id=request_id, device_id=device_id, command=command,
            state=state, ok=ok, reply=reply,
            completed_at=time.monotonic(),
        )
        await self._send_command_response(request_id, ok=ok, reply=reply)

    async def _send_command_response(self, request_id: str, *, ok: bool, reply: str) -> None:
        if self._ws is None:
            return
        try:
            await self._send_json({
                "type": "command.response",
                "request_id": request_id,
                "ok": ok,
                "reply": reply[:500],
            })
        except Exception:
            pass

    # ============================================================
    # 设备快照（重连补偿）
    # ============================================================

    async def _send_devices_snapshot(self) -> None:
        if self._manager is None:
            return
        devices = self._manager.list_devices()
        events = []
        for agent in devices:
            ti = agent.transport_info
            events.append({
                "type": "device.online",
                "device_id": agent.device_id,
                "data": {
                    "info": agent.info,
                    "transport": {
                        "kind": ti.kind,
                        "path": ti.path or ti.label,
                    },
                },
            })
        if events:
            try:
                await self._send_json({"type": "agent.devices_snapshot", "devices": events})
                log.info("sent devices_snapshot: %d devices", len(events))
            except Exception:
                log.warning("devices_snapshot send failed")

    # ============================================================
    # WS 发送（asyncio.Lock 串行化 — 关键！V1.1 A4 修复）
    # ============================================================

    async def _send_json(self, obj: dict) -> None:
        """带串行锁的安全发送。"""
        if self._ws is None or self._stop_event.is_set() or self._send_lock is None:
            raise RuntimeError("connector not connected")
        await self._send_json_raw(self._ws, obj)

    async def _send_json_raw(self, ws, obj: dict) -> None:
        """内部：获取锁 + send。"""
        assert self._send_lock is not None
        async with self._send_lock:
            await ws.send(json.dumps(obj))

    async def _safe_close_ws(self, ws) -> None:
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.close(), timeout=2.0)
        except Exception:
            pass

    def _close_ws_obj(self, ws) -> None:
        """同步关闭（在 Connector 线程中）。"""
        if ws is None:
            return
        if self._loop is None or self._loop.is_closed():
            try:
                asyncio.get_event_loop().run_until_complete(ws.close())
            except Exception:
                pass
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._safe_close_ws(ws), self._loop)
            future.result(timeout=2.0)
        except Exception:
            pass

    def _disconnect_all(self, reason: DisconnectReason) -> None:
        """统一断线入口。"""
        self._disconnect_reason = reason
        self._stop_event.set()

    # ============================================================
    # 状态管理
    # ============================================================

    def _set_state(self, new_state: ConnectorState, reason: Optional[Any] = None) -> None:
        with self._lock:
            old = self._state
            self._state = new_state
            if reason is not None:
                self._disconnect_reason = reason if isinstance(reason, DisconnectReason) else None
        if old != new_state:
            reason_str = ""
            if reason is not None:
                reason_str = f" reason={reason.value if isinstance(reason, DisconnectReason) else str(reason)}"
            log.info("state: %s → %s%s", old.value, new_state.value, reason_str)

    def _infer_disconnect_reason(self, done_tasks: set) -> DisconnectReason:
        for t in done_tasks:
            exc = t.exception()
            if exc is None:
                continue
            msg = str(exc).lower()
            if "heartbeat" in msg or "ack timeout" in msg:
                return DisconnectReason.HEARTBEAT_TIMEOUT
            if isinstance(exc, (ConnectionClosed, ConnectionClosedError)):
                return DisconnectReason.WS_CLOSED
            if isinstance(exc, (OSError, ConnectionError)):
                return DisconnectReason.SITE_UNAVAILABLE
        return self._disconnect_reason or DisconnectReason.WS_ERROR
