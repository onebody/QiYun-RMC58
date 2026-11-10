"""Device — 单设备的生命周期状态机。

状态转换：
    DISCOVERED → CONNECTING → HANDSHAKING → ONLINE
                                       ↓         ↑
                                    ERROR → RECONNECTING → ...
                                       ↓
                                   UNSUPPORTED / IDENTITY_CONFLICT

每台设备一个后台线程（不是 asyncio task）：
- 线程里用 sync Transport.read() 循环，因为 pyserial 不是 async 的
- 读到数据喂给 FrameStream，解码后 publish 事件
- 状态机 + 重连 + 命令回复全走 EventBus

为什么不用 asyncio + to_thread：
  serial.read 的 100ms timeout 本身就是个阻塞调用，但它需要被**循环
  调用**。如果每 100ms 调一次 to_thread，event loop 的调度开销比串行读
  还大。用一个 dedicated thread 更清晰，Device 就是它的 owner。
"""

from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .events import Event, EventBus
from .identity import (
    coerce_device_timestamp_ms,
    coerce_sequence,
    make_event_id,
    utc_now_iso,
)
from .protocol import FrameStream, decode_frame, is_command_allowed
from .transports.base import Transport, TransportInfo


class DeviceState(str, enum.Enum):
    DISCOVERED = "discovered"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    ONLINE = "online"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    IDENTITY_CONFLICT = "identity_conflict"


@dataclass
class DeviceMetrics:
    """设备运行指标。"""
    bytes_in: int = 0
    frames_decoded: int = 0
    crc_errors: int = 0
    lines_decoded: int = 0
    last_seen_ts: float = 0.0
    connect_time: float = 0.0
    reconnections: int = 0
    state_changes: int = 0


class Device:
    """
    构造函数里 Device 拿到 Transport 实例但**不打开**它。

    调用方决定何时 start()。start() 打开 Transport → PROTOCOL MONITOR → INFO →
    进入读循环。stop() 关闭 Transport 并终止线程。
    """

    def __init__(
        self,
        transport: Transport,
        bus: EventBus,
        device_id: Optional[str] = None,
        identity_confidence: str = "high",
        reconnect_enabled: bool = True,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        reconnect_multiplier: float = 2.0,
        read_timeout: float = 0.2,
    ) -> None:
        self._transport = transport
        self._bus = bus
        self._device_id = device_id or f"anon-{id(transport):04x}"
        self._identity_confidence = identity_confidence
        self._reconnect_enabled = reconnect_enabled
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_multiplier = reconnect_multiplier
        self._read_timeout = read_timeout

        self._state = DeviceState.DISCOVERED
        self._info: Dict[str, Any] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._reconnect_attempt: int = 0
        self._frame_stream: Optional[FrameStream] = None
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._metrics = DeviceMetrics()

        # --- 启动周期标识 (V1.1.2) ---
        # device_boot_id: C5 设备启动周期标识（不是 Agent 的！）
        # 通过固件 INFO 的 uptime_ms 对比检测设备重启：
        #   - uptime 变小/归零 → 设备重启 → 生成新的 device_boot_id
        #   - uptime 正常增长 → 同一启动周期，boot_id 不变
        # Agent 无 uptime 数据时（老固件）→ boot_id = "unknown-{device_id}"
        self._device_boot_id: str = f"boot-{uuid.uuid4().hex[:8]}"
        self._last_seen_uptime_ms: Optional[int] = None

    # --- 身份 & 状态 ---------------------------------------------------

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def device_boot_id(self) -> str:
        """C5 设备启动周期标识。设备重启后自动更新。"""
        with self._lock:
            return self._device_boot_id

    def check_device_boot(self, uptime_ms: Optional[int]) -> bool:
        """根据 INFO 返回的 uptime_ms 检测设备是否重启。

        Returns True if device rebooted (new boot_id generated).
        """
        if uptime_ms is None:
            # 老固件没有 uptime — 保持当前 boot_id 不变
            return False
        with self._lock:
            last = self._last_seen_uptime_ms
            self._last_seen_uptime_ms = uptime_ms
            if last is None:
                # 第一次看到 uptime，记录并保持当前 boot_id
                return False
            # 设备重启检测：uptime 明显变小
            # 正常增长应该是 uptime >= last（允许小幅抖动）
            if uptime_ms + 1000 < last:  # 1s 抖动容限
                # 设备重启了！生成新的 boot_id
                self._device_boot_id = f"boot-{uuid.uuid4().hex[:8]}"
                return True
            # uptime 正常增长 → 同一启动周期
            return False

    @property
    def state(self) -> DeviceState:
        with self._lock:
            return self._state

    @property
    def info(self) -> Dict[str, Any]:
        return dict(self._info)

    @property
    def transport_info(self) -> TransportInfo:
        return self._transport.get_info()

    @property
    def metrics(self) -> DeviceMetrics:
        return DeviceMetrics(
            bytes_in=self._metrics.bytes_in,
            frames_decoded=self._metrics.frames_decoded,
            crc_errors=self._metrics.crc_errors,
            lines_decoded=self._metrics.lines_decoded,
            last_seen_ts=self._metrics.last_seen_ts,
            connect_time=self._metrics.connect_time,
            reconnections=self._metrics.reconnections,
            state_changes=self._metrics.state_changes,
        )

    # --- 状态机 -------------------------------------------------------

    def _set_state(self, new_state: DeviceState, error: Optional[str] = None) -> None:
        old_state = self._state
        self._state = new_state
        self._metrics.state_changes += 1
        self._bus.publish(
            Event(
                type="device.state_change",
                device_id=self._device_id,
                data={
                    "from": old_state.value,
                    "to": new_state.value,
                    "error": error,
                    "reconnect_attempt": self._reconnect_attempt,
                },
            )
        )
        if new_state == DeviceState.ONLINE:
            self._metrics.connect_time = time.time()
            self._reconnect_attempt = 0

    # --- start / stop --------------------------------------------------

    def start(self) -> None:
        """启动后台线程（不阻塞调用者）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._set_state(DeviceState.CONNECTING)
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"Device-{self._device_id[:16]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """停止读循环，关闭 transport，阻塞直到线程结束。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        try:
            self._transport.close()
        except Exception:
            pass
        with self._lock:
            self._set_state(DeviceState.DISCONNECTED)

    def reconnect(self) -> None:
        """外部触发一次立即重连（不等待退避）。"""
        self._reconnect_attempt = 0
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._stop_event.clear()
        self._set_state(DeviceState.RECONNECTING)
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"Device-{self._device_id[:16]}",
            daemon=True,
        )
        self._thread.start()

    # --- 命令 ---------------------------------------------------------

    def send_command(self, line: str) -> tuple[bool, str]:
        """
        发送一条命令。返回 (ok, reply)。

        Agent 做命令白名单校验 — 不允许的命令直接返回 False。
        """
        line = line.strip()
        if not line:
            return False, "empty command"
        if not is_command_allowed(line):
            return False, f"command not allowed: {line}"
        if not self._transport.is_connected():
            return False, "transport not connected"
        with self._io_lock:
            try:
                self._transport.write((line + "\n").encode())
                # 等 ASCII 回复 — 二进制帧会 decode 成乱码所以自然被过滤掉
                deadline = time.monotonic() + 1.0
                buf = bytearray()
                while time.monotonic() < deadline:
                    chunk = self._transport.read(size=2048, timeout=0.1)
                    if not chunk:
                        continue
                    buf += chunk
                    # 只取以可打印 ASCII 开头的那几行（固件回复始终以
                    # INFO / OK / ERR / STATUS 等开头，不会是 0xA5 'R' 这样的二进制）
                    text = bytes(buf).decode("utf-8", "replace")
                    lines = text.splitlines()
                    for ln in lines:
                        if not ln:
                            continue
                        # 二进制帧 decode 后第一个非空字符应该是 0x00-0x1F 或 0x80+
                        # 固件回复行首必定是大写字母
                        first = ln[0]
                        if first.isalpha() and first.isupper():
                            # 找到一条回复 — 如果行太长就截断（避免携带后续帧）
                            # 固件回复最多 ~200 字节
                            if len(ln) > 400:
                                ln = ln[:400] + "..."
                            return True, ln
                # 超时 — 把所有可打印部分拼起来返回
                text = bytes(buf).decode("utf-8", "replace")
                printable = "\n".join(
                    ln for ln in text.splitlines()
                    if ln and ln[0].isalpha() and ln[0].isupper()
                )
                return False, printable or "no reply"
            except Exception as exc:
                return False, f"transport error: {exc}"

    # --- 主循环 -------------------------------------------------------

    def _run_loop(self) -> None:
        """线程入口：connect → handshake → read。失败按重连策略走。"""
        while not self._stop_event.is_set():
            try:
                if not self._connect_and_handshake():
                    if not self._reconnect_enabled:
                        self._set_state(DeviceState.ERROR, "handshake failed")
                        return
                    self._enter_reconnect_backoff()
                    continue
                self._read_loop()
            except Exception as exc:
                self._set_state(DeviceState.ERROR, str(exc))
            # 读循环意外退出也可能触发重连
            if self._stop_event.is_set():
                break
            if not self._reconnect_enabled:
                return
            self._enter_reconnect_backoff()

        # 线程退出
        try:
            self._transport.close()
        except Exception:
            pass

    def _connect_and_handshake(self) -> bool:
        """打开 Transport 并完成 PROTOCOL MONITOR → INFO。"""
        self._set_state(DeviceState.CONNECTING)
        try:
            self._transport.open()
        except Exception as exc:
            self._set_state(DeviceState.ERROR, f"open failed: {exc}")
            return False

        # 先发 PROTOCOL MONITOR
        self._set_state(DeviceState.HANDSHAKING)
        try:
            self._transport.write(b"PROTOCOL MONITOR\n")
            time.sleep(0.1)
            self._transport.read(size=4096, timeout=0.2)  # 丢弃 OK\n

            self._transport.write(b"INFO\n")
            deadline = time.monotonic() + 1.0
            buf = bytearray()
            info_line: Optional[str] = None
            while time.monotonic() < deadline:
                chunk = self._transport.read(size=4096, timeout=0.1)
                if not chunk:
                    continue
                buf += chunk
                text = buf.decode("utf-8", "replace")
                for ln in text.splitlines():
                    if ln.startswith("INFO widscan58|"):
                        info_line = ln
                        break
                if info_line is not None:
                    break

            if info_line is None:
                self._set_state(DeviceState.ERROR, "no INFO reply")
                return False

            from .protocol import parse_info_line

            parsed = parse_info_line(info_line)
            if parsed is None:
                self._set_state(DeviceState.ERROR, f"INFO parse failed: {info_line}")
                return False

            self._info = parsed
            self._frame_stream = FrameStream()
            self._set_state(DeviceState.ONLINE)
            self._bus.publish(
                Event(
                    type="device.online",
                    device_id=self._device_id,
                    data={
                        "info": parsed,
                        "transport": self._transport.get_info().__dict__,
                    },
                )
            )
            return True

        except Exception as exc:
            self._set_state(DeviceState.ERROR, f"handshake exception: {exc}")
            try:
                self._transport.close()
            except Exception:
                pass
            return False

    def _read_loop(self) -> None:
        """循环读数据 → 喂 FrameStream → 解码 → publish。"""
        assert self._frame_stream is not None
        stream = self._frame_stream
        while not self._stop_event.is_set():
            try:
                with self._io_lock:
                    chunk = self._transport.read(size=8192, timeout=self._read_timeout)
            except Exception as exc:
                self._set_state(DeviceState.ERROR, f"read error: {exc}")
                break

            if not chunk:
                # 超时 → 继续循环
                if not self._transport.is_connected():
                    self._set_state(DeviceState.ERROR, "transport disconnected")
                    break
                continue

            self._metrics.bytes_in += len(chunk)
            self._metrics.last_seen_ts = time.time()

            items = stream.push(chunk)
            self._metrics.crc_errors = stream.crc_errors
            self._bus.publish(
                Event(
                    type="device.stream_stats",
                    device_id=self._device_id,
                    data={
                        "bytes_in": self._metrics.bytes_in,
                        "crc_errors": stream.crc_errors,
                        "resync_bytes": stream.resync_bytes,
                        "frames_in": self._metrics.frames_decoded,
                    },
                )
            )

            for item in items:
                if item.kind == "frame":
                    self._metrics.frames_decoded += 1
                    decoded = decode_frame(item.type, item.payload)
                    # Phase 07.3 / 07.4：在 Agent 接收事件的这一刻生成
                    # agent_received_at 与 event_id，之后全链路原样传递。
                    sequence = coerce_sequence(decoded)
                    device_timestamp_ms = coerce_device_timestamp_ms(decoded)
                    self._bus.publish(
                        Event(
                            type="device.frame",
                            device_id=self._device_id,
                            data={
                                "type_code": item.type,
                                "decoded": decoded,
                                "event_id": make_event_id(
                                    self._device_id,
                                    self.device_boot_id,
                                    item.type,
                                    sequence,
                                    device_timestamp_ms,
                                ),
                                "agent_received_at": utc_now_iso(),
                                "sequence": sequence,
                                "device_timestamp_ms": device_timestamp_ms,
                            },
                        )
                    )
                elif item.kind == "text":
                    self._metrics.lines_decoded += 1
                    self._bus.publish(
                        Event(
                            type="device.line",
                            device_id=self._device_id,
                            data={"line": item.line},
                        )
                    )

    def _enter_reconnect_backoff(self) -> None:
        """指数退避 sleep — 线程循环继续，下一迭代就重连。"""
        self._reconnect_attempt += 1
        delay = min(
            self._reconnect_initial_delay * (self._reconnect_multiplier ** (self._reconnect_attempt - 1)),
            self._reconnect_max_delay,
        )
        self._set_state(DeviceState.RECONNECTING, error="backoff")
        self._metrics.reconnections += 1
        self._bus.publish(
            Event(
                type="device.reconnect_scheduled",
                device_id=self._device_id,
                data={
                    "attempt": self._reconnect_attempt,
                    "delay": delay,
                },
            )
        )
        # Event.wait 可以被 stop_event 提前打断
        self._stop_event.wait(timeout=delay)
