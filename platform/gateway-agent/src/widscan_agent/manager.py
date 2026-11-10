"""DeviceManager — 管 Device 的集合。

职责：
1. 维护 device_id → Device 的 registry
2. 周期性扫描 USB 候选，把新端口拉起来，把消失的设备停掉
3. 统一对外暴露 REST/WebSocket 的查询入口
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from .device import Device, DeviceState
from .discovery import discover_once, handshake_once
from .events import Event, EventBus
from .transports.base import Transport
from .transports.usb_serial import UsbSerialTransport


class DeviceManager:
    def __init__(
        self,
        bus: EventBus,
        poll_interval: float = 5.0,
        reconnect_enabled: bool = True,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        reconnect_multiplier: float = 2.0,
        read_timeout: float = 0.2,
        baudrate: int = 921600,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._reconnect_enabled = reconnect_enabled
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._reconnect_multiplier = reconnect_multiplier
        self._read_timeout = read_timeout
        self._baudrate = baudrate

        self._devices: Dict[str, Device] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False

    # --- 生命周期 ------------------------------------------------------

    def start(self) -> None:
        """启动后台轮询线程。也立即做一次 discover。"""
        if self._started:
            return
        self._started = True
        # 立即一次
        self._discover_and_add()
        # 后台循环
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="DeviceManager-poll", daemon=True
        )
        self._thread.start()
        self._bus.publish(Event(type="agent.started", data={"devices": len(self._devices)}))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            devices = list(self._devices.values())
        for d in devices:
            d.stop()
        with self._lock:
            self._devices.clear()
        self._bus.publish(Event(type="agent.stopped"))

    # --- 外部注入（测试 / simulator 场景） -------------------------------

    def add_device(self, transport: Transport, device_id: Optional[str] = None,
                   identity_confidence: str = "high") -> Device:
        """手动添加一台设备（比如 simulator）。

        Args:
            transport: 传输层实例
            device_id: 设备唯一身份（MAC-based 最稳定）
            identity_confidence: "high" (MAC-based) | "low" (path hash fallback)
        """
        with self._lock:
            if device_id and device_id in self._devices:
                return self._devices[device_id]
            did = device_id or f"dev-{abs(hash(transport.get_info().path)) % 100000:05x}"
            d = Device(
                transport=transport,
                bus=self._bus,
                device_id=did,
                identity_confidence=identity_confidence,
                reconnect_enabled=self._reconnect_enabled,
                reconnect_initial_delay=self._reconnect_initial_delay,
                reconnect_max_delay=self._reconnect_max_delay,
                reconnect_multiplier=self._reconnect_multiplier,
                read_timeout=self._read_timeout,
            )
            self._devices[did] = d
            d.start()
            self._bus.publish(
                Event(
                    type="device.registered",
                    device_id=did,
                    data={"transport": transport.get_info().__dict__},
                )
            )
            return d

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            d = self._devices.pop(device_id, None)
        if d is None:
            return False
        d.stop()
        self._bus.publish(Event(type="device.removed", device_id=device_id))
        return True

    # --- 查询 ---------------------------------------------------------

    def get(self, device_id: str) -> Optional[Device]:
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self) -> List[Device]:
        with self._lock:
            return list(self._devices.values())

    # --- 轮询 ---------------------------------------------------------

    def _poll_loop(self) -> None:
        """定期扫描 USB 候选，处理热插拔。"""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._poll_interval)
            if self._stop_event.is_set():
                break
            try:
                self._discover_and_add()
                self._prune_disconnected()
            except Exception as exc:
                self._bus.publish(
                    Event(type="agent.error", data={"phase": "poll", "error": str(exc)})
                )

    def _discover_and_add(self) -> None:
        """跑一次 discover，新设备加进来，已存在的不动。"""
        from .discovery import list_usb_serial_candidates

        try:
            results = discover_once(baudrate=self._baudrate)
        except Exception as exc:
            self._bus.publish(
                Event(type="agent.error", data={"phase": "discover", "error": str(exc)})
            )
            return

        # 先查已存在设备的 path
        existing_paths: set[str] = set()
        with self._lock:
            for d in self._devices.values():
                existing_paths.add(d.transport_info.path)

        for r in results:
            if r.device_id is None or not r.ok:
                continue
            with self._lock:
                if r.device_id in self._devices:
                    # 已存在设备，检查 uptime 是否有变化（设备重启检测）
                    existing = self._devices[r.device_id]
                    uptime = r.info.get("uptime_ms") if r.info else None
                    if existing.check_device_boot(uptime):
                        self._bus.publish(Event(
                            type="device.boot_changed",
                            device_id=r.device_id,
                            data={"device_boot_id": existing.device_boot_id,
                                  "uptime_ms": uptime},
                        ))
                    continue
            # 把它加进来
            transport = UsbSerialTransport(port=r.path, baudrate=self._baudrate)
            d = self.add_device(transport, device_id=r.device_id,
                                identity_confidence=r.confidence)
            # 初始化 device_boot_id（第一次看到 uptime）
            uptime = r.info.get("uptime_ms") if r.info else None
            d.check_device_boot(uptime)

    def _prune_disconnected(self) -> None:
        """把长时间没 seen 且状态为 DISCONNECTED/ERROR 的设备清理掉。"""
        now = time.time()
        to_remove: list[str] = []
        with self._lock:
            for did, d in self._devices.items():
                m = d.metrics
                # 如果从来没 seen 过 (last_seen_ts=0) 且启动超过 10 秒
                if m.last_seen_ts == 0.0:
                    if now - m.connect_time > 10 and d.state in (
                        DeviceState.DISCONNECTED, DeviceState.ERROR,
                    ):
                        to_remove.append(did)
                    continue
                # 已连接过但最后 seen 超过 60 秒且已不在 ONLINE
                if now - m.last_seen_ts > 60 and d.state != DeviceState.ONLINE:
                    to_remove.append(did)
        for did in to_remove:
            self.remove_device(did)
