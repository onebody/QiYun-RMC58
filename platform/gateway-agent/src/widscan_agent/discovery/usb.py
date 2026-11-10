"""USB 自动发现。

不解析 USB 硬件描述符；用 serial.tools.list_ports 拿到候选列表后，
真正的"这是不是 WIDSCAN58"靠握手 (PROTOCOL MONITOR → INFO)。

这跟 Web Serial 的发现策略一致：Web Serial 也只看 "USB serial 设备"，
然后靠 INFO 里的 `widscan58|...` 魔数确认。

两个核心事实决定了实现方式：
1. pyserial 的 list_ports.comports() 在 macOS 上给不出 USB 序列号（cp210x 会
   但 cdc_acm 不会），所以 identity 的第一选择是 INFO 里的 fw_version，
   如果那也拿不到才退而用 tty 路径并标记 low_confidence。
2. 握手阻塞：打开端口 → 发 PROTOCOL MONITOR → 读 INFO → 关。整个过
   程需要大约 50ms。上层 DeviceManager 用一个后台循环轮询 poll_interval。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from ..transports.base import TransportInfo
from ..transports.usb_serial import UsbSerialTransport


@dataclass
class UsbCandidate:
    """一次 list_ports 扫描的候选端口。"""
    path: str
    description: str = ""
    manufacturer: str = ""
    hwid: str = ""
    serial: str = ""


def list_usb_serial_candidates() -> list[UsbCandidate]:
    """列出所有串口候选。依赖 pyserial。"""
    try:
        from serial.tools.list_ports import comports
    except Exception:
        return []

    out: list[UsbCandidate] = []
    for p in comports():
        if not p.device:
            continue
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if "bluetooth" in desc or "bth" in hwid:
            continue
        if p.device.startswith("/dev/cu."):
            continue
        out.append(
            UsbCandidate(
                path=p.device,
                description=p.description or "",
                manufacturer=p.manufacturer or "",
                hwid=p.hwid or "",
                serial=p.serial_number or "",
            )
        )
    return out


@dataclass
class HandshakeResult:
    """握手结果。"""
    ok: bool
    path: str = ""
    info: Optional[dict] = None
    device_id: Optional[str] = None
    device_boot_id: Optional[str] = None  # V1.1.2: C5 启动周期标识
    transport_info: Optional[TransportInfo] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    confidence: str = "low"  # "high" (MAC-based) | "low" (path hash fallback)


def handshake_once(
    path: str,
    baudrate: int = 921600,
    timeout: float = 1.0,
) -> HandshakeResult:
    """在指定路径上尝试一次 PROTOCOL MONITOR + INFO 握手（阻塞）。"""
    from ..protocol.adapter import parse_info_line

    result = HandshakeResult(ok=False, path=path)
    start = time.monotonic()
    transport = UsbSerialTransport(port=path, baudrate=baudrate, timeout=0.1)
    try:
        transport.open()
        time.sleep(0.05)
        transport.read(size=8192, timeout=0.1)

        transport.write(b"PROTOCOL MONITOR\n")
        time.sleep(0.1)
        transport.read(size=4096, timeout=0.1)

        transport.write(b"INFO\n")
        deadline = time.monotonic() + timeout
        buf = bytearray()
        got_info: Optional[str] = None
        while time.monotonic() < deadline:
            chunk = transport.read(size=4096, timeout=0.1)
            if not chunk:
                continue
            buf += chunk
            text = buf.decode("utf-8", "replace")
            for line in text.splitlines():
                if line.startswith("INFO widscan58|"):
                    got_info = line
                    break
            if got_info is not None:
                break

        if got_info is None:
            result.error = "no INFO reply"
            result.duration_ms = (time.monotonic() - start) * 1000
            return result

        parsed = parse_info_line(got_info)
        if parsed is None:
            result.error = f"INFO parse failed: {got_info}"
            result.duration_ms = (time.monotonic() - start) * 1000
            return result

        fw = parsed.get("fw_version") or "unknown"
        mac = parsed.get("mac")  # 可选：新固件 2026-11+ 提供 eFuse MAC
        serial_path = transport.get_info().path

        # device_id 优先级：MAC（硬件唯一）> 持久化映射 > hash(path) 降级
        if mac:
            # 1. 有 MAC → 硬件稳定身份（跨 USB 口、固件升级、Agent 重启都不变）
            mac_clean = mac.replace(":", "")
            result.device_id = f"c5-{mac_clean}"
            confidence = "high"
        else:
            # 2. 无 MAC（老固件）→ 用 USB 路径 hash 降级，标记 low confidence
            result.device_id = f"c5-{fw}-{abs(hash(serial_path)) % 10000:04x}"
            confidence = "low"

        result.info = parsed
        ti = transport.get_info()
        result.transport_info = ti.with_id(result.device_id)
        result.ok = True
        result.duration_ms = (time.monotonic() - start) * 1000
        result.confidence = confidence  # 新增：identity confidence
        return result

    except Exception as exc:
        result.error = f"handshake exception: {exc}"
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    finally:
        try:
            transport.close()
        except Exception:
            pass


def discover_once(
    baudrate: int = 921600,
    timeout: float = 1.0,
) -> list[HandshakeResult]:
    """一次完整扫描：列出候选端口 → 逐个握手 → 返回成功列表。"""
    candidates = list_usb_serial_candidates()
    results: list[HandshakeResult] = []
    for cand in candidates:
        try:
            r = handshake_once(cand.path, baudrate=baudrate, timeout=timeout)
            if r.ok:
                results.append(r)
        except Exception as exc:
            results.append(HandshakeResult(ok=False, path=cand.path, error=str(exc)))
    return results
