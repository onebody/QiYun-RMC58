"""TCP (socket) Transport — 用于未来 Wi-Fi/TCP 固件固件场景。

当前不是主路径（主路径是 USB CDC），所以实现保持最小可用：阻塞 socket，
可设置读写超时。上层用 asyncio.to_thread 包住它。
"""

from __future__ import annotations

import socket
from typing import Optional

from .base import Transport, TransportInfo, TransportState


class TcpTransport(Transport):
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 2.0,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def open(self) -> None:
        try:
            s = socket.create_connection((self._host, self._port), timeout=self._timeout)
            s.settimeout(self._timeout)
            self._sock = s
            self._state = TransportState.CONNECTED
            self._error = None
        except Exception as exc:
            self._state = TransportState.ERROR
            self._error = f"tcp connect failed: {exc}"
            raise

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._state = TransportState.DISCONNECTED

    def read(self, size: int = 4096, timeout: Optional[float] = None) -> bytes:
        if self._sock is None:
            return b""
        try:
            if timeout is not None:
                self._sock.settimeout(timeout)
            data = self._sock.recv(size)
            if timeout is not None:
                self._sock.settimeout(self._timeout)
            if not data:
                # 对端关闭
                self._state = TransportState.DISCONNECTED
                self._error = "peer closed"
            return bytes(data)
        except socket.timeout:
            return b""
        except OSError as exc:
            self._state = TransportState.ERROR
            self._error = f"tcp error: {exc}"
            raise

    def write(self, data: bytes) -> int:
        if self._sock is None:
            return 0
        try:
            self._sock.sendall(data)
            return len(data)
        except Exception as exc:
            self._state = TransportState.ERROR
            self._error = f"tcp write error: {exc}"
            raise

    def is_connected(self) -> bool:
        if self._sock is None:
            return False
        try:
            self._sock.getpeername()
            return True
        except Exception:
            return False

    def get_info(self) -> TransportInfo:
        return TransportInfo(
            kind="tcp",
            label=f"tcp://{self._host}:{self._port}",
            path=f"{self._host}:{self._port}",
            extra={"host": self._host, "port": self._port},
        )
