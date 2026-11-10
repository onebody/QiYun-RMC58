"""Simulator Transport — 完全在内存里生成真实二进制帧，用于测试和离线开发。

**关键约束**：生成的帧必须跟固件 `frames.h` 的 _Static_assert 尺寸完全匹配，
CRC 必须正确，这样上层 `FrameStream.push()` 才能把它们当真机数据解出来。
这样端到端测试不需要真机硬件。

不伪造 INFO / PROTOCOL MONITOR 的握手逻辑 — 那些 ASCII 回复在这里是硬编码。
"""

from __future__ import annotations

import os
import time
import zlib
from collections import deque
from typing import Optional
from struct import pack

from .base import Transport, TransportInfo, TransportState


# --- 帧构造辅助：镜像 firmware/src/proto/frames.h ---------------------------

FRAME_METRICS = 1
FRAME_SPECTRUM = 2
FRAME_TELEMETRY = 3
FRAME_RAWIQ = 4
FRAME_LOG = 5
FRAME_LEVELS = 6

SYNC0 = 0xA5
SYNC1 = ord("R")


def _frame(payload: bytes, type_code: int, version: int = 1) -> bytes:
    hdr = pack("<BBBBH", SYNC0, SYNC1, type_code, version, len(payload))
    crc = zlib.crc32(hdr + payload) & 0xFFFFFFFF
    return hdr + payload + pack("<I", crc)


def build_metrics_frame(
    seq: int = 1,
    lo_khz: int = 5658000,
    nch: int = 8,
    gains: list[int] | None = None,
) -> bytes:
    """构造一个 METRICS_FULL 帧。尺寸 = 34 + 8×26 = 242 字节。"""
    channels = []
    if gains is None:
        gains = [-6000, -5800, -6200, -5900, -6100, -5700, -6300, -5850]
    for i in range(nch):
        rssi = gains[i % len(gains)]
        ch = pack(
            "<BBhhhhhhiihh",
            i,                 # id
            0,                 # flags
            rssi,              # rssi_cdb
            rssi,              # rssi_ema_cdb
            rssi - 200,        # floor_cdb
            1800,              # snr_cdb
            -32768,            # clear_floor_cdb (NO_CLEAR_CHANNEL)
            rssi + 500,        # peak_cdb
            0,                 # peak_off_hz
            0,                 # centroid_off_hz
            0,                 # obw_lo_khz
            0,                 # obw_hi_khz
        )
        channels.append(ch)
    hdr = pack(
        "<IIIIIIHhhBBBB",
        seq,                # seq
        (seq * 16) % 1_000_000,  # t_ms
        lo_khz,             # lo_khz
        230,                # tune_us
        4000,               # capture_us
        3600,               # dsp_us
        0,                  # saturated
        -3000,              # floor_bin_cdb
        -500,               # dc_cdb
        45,                 # gain
        seq % 4,            # hop_index
        4,                  # hops
        nch,                # nch
    )
    return _frame(hdr + b"".join(channels), FRAME_METRICS)


def build_telemetry_frame(
    t_ms: int = 0,
    rotations: int = 0,
    hops_total: int = 0,
    running: bool = True,
) -> bytes:
    payload = pack(
        "<IIIIIIIIIIIHIIIB",
        t_ms,
        rotations,
        hops_total,
        60000,              # rotation_mhz (μHz → 60000 = 60 Hz)
        4000,               # hop_us_avg
        5000,               # hop_us_max
        230,                # tune_us_avg
        260,                # tune_us_max
        4000,               # capture_us_avg
        3600,               # dsp_us_avg
        4000,               # dsp_us_max
        50,                 # link_fill_pm (per mille)
        20000,              # heap_free
        0,                  # drops
        0,                  # capture_errors
        1 if running else 0,
    )
    return _frame(payload, FRAME_TELEMETRY)


def build_spectrum_frame(
    seq: int = 1,
    lo_khz: int = 5658000,
    n: int = 256,
) -> bytes:
    import math
    bins = bytearray(n)
    for i in range(n):
        # 模拟带噪频谱：中心附近有个信号
        dist = abs(i - n // 2)
        base = 127 - int(60 * math.exp(-dist * dist / (2 * 20 * 20)))
        bins[i] = max(0, min(255, base))
    hdr = pack("<IIiIHBB", seq, lo_khz, -(n // 2) * 20000, 20000, n, 0, 0)
    return _frame(hdr + bytes(bins), FRAME_SPECTRUM)


# --- Simulator Transport 本身 ----------------------------------------------


class SimulatorTransport(Transport):
    """
    行为：
    - open() 后立即就绪
    - 写进的字节进命令解析器（硬编码几个回复）
    - 读从输出队列拿帧 — read() 按一个合理间隔返回（模拟 60 Hz metrics）
    """

    def __init__(
        self,
        device_id: str = "sim-c5-001",
        target: str = "esp32c5",
        protocol_version: int = 1,
        fw_version: str = "sim-0.1.0",
        commit_time: str = "2026-11-01T00:00:00Z",
        phy_version: str = "esp32c5 2.5.0",
        metrics_per_sec: float = 60.0,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self._device_id = device_id
        self._target = target
        self._proto_ver = protocol_version
        self._fw = fw_version
        self._commit = commit_time
        self._phy = phy_version
        self._metrics_per_sec = metrics_per_sec
        self._rng_seed = seed
        self._out: deque[bytes] = deque()
        self._mode: str = "RH"          # boot default like firmware
        self._scan_running: bool = True
        self._seq = 0
        self._telemetry_tms = 0
        self._hops_total = 0
        self._rotations = 0
        self._last_metrics: float = 0.0
        self._last_telemetry: float = 0.0
        self._last_spectrum: float = 0.0
        self._baudrate: int = 0           # 模拟，不真限速
        self._closed: bool = False

    # --- Transport 接口 ------------------------------------------------

    def open(self) -> None:
        self._closed = False
        self._state = TransportState.CONNECTED
        self._error = None

    def close(self) -> None:
        self._closed = True
        self._out.clear()
        self._state = TransportState.DISCONNECTED

    def read(self, size: int = 4096, timeout: Optional[float] = None) -> bytes:
        if timeout is None:
            timeout = 0.05
        deadline = time.monotonic() + timeout

        # 等超时或队列里有东西
        while not self._out and time.monotonic() < deadline:
            self._pump_output()
            time.sleep(min(0.005, deadline - time.monotonic()))
            if self._closed:
                return b""

        # 如果队列空就返回（超时）
        if not self._out:
            return b""

        out = bytearray()
        while self._out and len(out) < size:
            out += self._out.popleft()
        return bytes(out)

    def write(self, data: bytes) -> int:
        self._handle_written(data)
        return len(data)

    def is_connected(self) -> bool:
        return not self._closed and self._state == TransportState.CONNECTED

    def get_info(self) -> TransportInfo:
        return TransportInfo(
            kind="simulator",
            label=f"sim:{self._device_id}",
            path=self._device_id,
            device_id=self._device_id,
            extra={
                "target": self._target,
                "fw_version": self._fw,
            },
        )

    # --- 内部 -----------------------------------------------------------

    def _pump_output(self) -> None:
        """根据 metrics_per_sec 定时往队列塞帧。"""
        if not self._scan_running:
            return
        now = time.monotonic()

        # METRICS
        interval = 1.0 / self._metrics_per_sec
        if now - self._last_metrics >= interval:
            self._last_metrics = now
            self._seq += 1
            self._hops_total += 4
            self._out.append(build_metrics_frame(seq=self._seq))

        # TELEMETRY 每 500ms
        if now - self._last_telemetry >= 0.5:
            self._last_telemetry = now
            self._telemetry_tms += 500
            self._rotations += 30
            self._out.append(
                build_telemetry_frame(
                    t_ms=self._telemetry_tms,
                    rotations=self._rotations,
                    hops_total=self._hops_total,
                    running=self._scan_running,
                )
            )

        # SPECTRUM 每 2s
        if now - self._last_spectrum >= 2.0:
            self._last_spectrum = now
            self._out.append(build_spectrum_frame(seq=self._seq))

    def _handle_written(self, data: bytes) -> None:
        """解析一行命令，返回硬编码回复。"""
        lines = data.decode("utf-8", "replace").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            reply = self._handle_line(line)
            if reply is not None:
                self._out.append(reply.encode() + b"\n")

    def _handle_line(self, line: str) -> Optional[str]:
        if line == "INFO":
            return (
                f"INFO widscan58|{self._target}|{self._proto_ver}|"
                f"{self._fw}|{self._commit}|{self._phy}"
            )
        if line == "PROTOCOL?":
            return f"PROTOCOL {self._mode}"
        if line == "PROTOCOL MONITOR":
            self._mode = "MONITOR"
            return "OK"
        if line == "PROTOCOL RH":
            self._mode = "RH"
            return "OK"
        if line == "SCAN START":
            self._scan_running = True
            return "OK"
        if line == "SCAN STOP":
            self._scan_running = False
            return "OK"
        if line == "GAIN?":
            return "GAIN MANUAL 45 82"
        if line.startswith("GAIN MANUAL "):
            return "OK"
        if line == "METRICS?":
            return "METRICS FULL"
        if line.startswith("METRICS "):
            return "OK"
        if line.startswith("FFT "):
            return "OK"
        if line.startswith("SPEC "):
            return "OK"
        if line == "STATUS?" or line == "CONFIG?":
            return (
                'STATUS {"protocol":"MONITOR","profile":"c5-race",'
                '"running":true,"enabled":255,"mode":"GROUP",'
                '"nfft":1024,"gain_mode":"MANUAL","gain":45,'
                '"metrics":"FULL","fft":"S16R"}'
            )
        if line == "PROFILE?":
            return (
                'PROFILE {"name":"c5-race","target":"esp32c5",'
                '"band_khz":[5645,5945],"base_khz":5658000,"channels":['
                '{"id":0,"name":"R1","khz":5658000},'
                '{"id":1,"name":"R2","khz":5695000},'
                '{"id":2,"name":"R3","khz":5732000},'
                '{"id":3,"name":"R4","khz":5769000},'
                '{"id":4,"name":"R5","khz":5806000},'
                '{"id":5,"name":"R6","khz":5843000},'
                '{"id":6,"name":"R7","khz":5880000},'
                '{"id":7,"name":"R8","khz":5917000}'
                "]}"
            )
        if line == "REBOOT":
            self._closed = True
            self._state = TransportState.DISCONNECTED
            return "OK REBOOT"
        if line == "BOOTLOADER":
            self._closed = True
            self._state = TransportState.DISCONNECTED
            return "OK BOOTLOADER"
        return f"ERR command {line}"
