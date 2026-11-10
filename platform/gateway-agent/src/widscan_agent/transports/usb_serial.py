"""USB CDC serial Transport — pyserial 的薄封装。"""

from __future__ import annotations

from typing import Optional

from .base import Transport, TransportInfo, TransportState


class UsbSerialTransport(Transport):
    """
    所有 serial 配置构造时固定；open 只是打开端口。

    默认配置来自固件 UART pace 设计（921600 8N1，无流控），和 Web UI 保持一致。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 921600,
        bytesize: int = 8,
        parity: str = "N",
        stopbits: float = 1.0,
        timeout: float = 0.2,
        write_timeout: float = 0.5,
    ) -> None:
        super().__init__()
        self._port = port
        self._baudrate = baudrate
        self._bytesize = bytesize
        self._parity = parity
        self._stopbits = stopbits
        self._timeout = timeout
        self._write_timeout = write_timeout
        self._serial = None

    # --- Transport 接口 ------------------------------------------------

    def open(self) -> None:
        import serial  # 延迟导入，避免没装 pyserial 的场景崩溃

        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=self._bytesize,
                parity=self._parity,
                stopbits=self._stopbits,
                timeout=self._timeout,
                write_timeout=self._write_timeout,
            )
            self._state = TransportState.CONNECTED
            self._error = None
        except Exception as exc:
            self._state = TransportState.ERROR
            self._error = f"open failed: {exc}"
            raise

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        self._state = TransportState.DISCONNECTED

    def read(self, size: int = 4096, timeout: Optional[float] = None) -> bytes:
        if self._serial is None:
            return b""
        try:
            if timeout is not None:
                original = self._serial.timeout
                self._serial.timeout = timeout
            data = self._serial.read(size)
            if timeout is not None:
                self._serial.timeout = original
            return bytes(data)
        except Exception as exc:
            self._state = TransportState.ERROR
            self._error = f"read error: {exc}"
            raise

    def write(self, data: bytes) -> int:
        if self._serial is None:
            return 0
        try:
            return self._serial.write(data)
        except Exception as exc:
            self._state = TransportState.ERROR
            self._error = f"write error: {exc}"
            raise

    def is_connected(self) -> bool:
        if self._serial is None:
            return False
        try:
            # pyserial 的 is_open 在端口拔插后会抛异常
            return bool(self._serial.is_open)
        except Exception:
            return False

    def get_info(self) -> TransportInfo:
        extra: dict = {}
        if self._serial is not None:
            try:
                extra["baudrate"] = self._serial.baudrate
                extra["port"] = self._serial.port
            except Exception:
                pass
        return TransportInfo(
            kind="usb_serial",
            label=self._port,
            path=self._port,
            extra=extra,
        )
