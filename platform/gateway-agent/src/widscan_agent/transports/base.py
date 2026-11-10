"""Transport 抽象层。只关心字节流，不知道设备协议，不知道 Web UI。

所有 I/O 是同步的 — 上层（Device 类）通过 asyncio.to_thread 或 run_in_executor
把它们包进事件循环。Transport 自己绝不 import asyncio，这样在不跑 async 的
Python 进程里也能用它做一次性连接（发现流程）。
"""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class TransportState(str, enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class TransportInfo:
    """Transport 自报身份。"""
    kind: str                    # "usb_serial", "tcp", "simulator"
    label: str                   # 人类可读，例如 "/dev/ttyACM0" 或 "sim:demo"
    path: str = ""               # 物理/逻辑定位键（USB 路径、host:port、sim id）
    device_id: Optional[str] = None   # 握手后填入，由上层设置
    extra: Dict[str, Any] = field(default_factory=dict)

    def with_id(self, device_id: str) -> "TransportInfo":
        return TransportInfo(
            kind=self.kind, label=self.label, path=self.path,
            device_id=device_id, extra=dict(self.extra),
        )


class Transport(abc.ABC):
    """所有 Transport 必须实现这 6 个方法。不要在子类里加额外的 async 版本。"""

    def __init__(self) -> None:
        self._state = TransportState.DISCONNECTED
        self._error: Optional[str] = None

    # --- 状态 ----------------------------------------------------------

    @property
    def state(self) -> TransportState:
        return self._state

    @property
    def error(self) -> Optional[str]:
        return self._error

    # --- 必须由子类实现 ------------------------------------------------

    @abc.abstractmethod
    def open(self) -> None:
        """打开传输通道。阻塞直到成功或抛异常。"""

    @abc.abstractmethod
    def close(self) -> None:
        """关闭传输通道。允许已关闭时调用。"""

    @abc.abstractmethod
    def read(self, size: int = 4096, timeout: Optional[float] = None) -> bytes:
        """读至多 size 字节。timeout=None 用 Transport 默认。空 bytes 代表超时。"""

    @abc.abstractmethod
    def write(self, data: bytes) -> int:
        """写 bytes，返回写入字节数。"""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """健康检查 — 底层资源还活着吗？"""

    @abc.abstractmethod
    def get_info(self) -> TransportInfo:
        """返回 TransportInfo。"""
