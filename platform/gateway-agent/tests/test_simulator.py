"""Simulator 帧必须和 frame_parser 的 decoder 完全对得上 — 这是端到端测试的基石。"""

import pytest

from widscan_agent.protocol import FrameStream, decode_frame, parse_info_line
from widscan_agent.transports.simulator import (
    build_metrics_frame,
    build_spectrum_frame,
    build_telemetry_frame,
    _frame,
    FRAME_METRICS,
    FRAME_SPECTRUM,
    FRAME_TELEMETRY,
    FRAME_RAWIQ,
    FRAME_LOG,
    FRAME_LEVELS,
)


class TestSimulatorFrames:
    def test_all_frames_decode(self):
        stream = FrameStream()
        for builder in [
            build_metrics_frame(seq=1),
            build_metrics_frame(seq=9999, nch=4),
            build_telemetry_frame(t_ms=5000, rotations=120, hops_total=480),
            build_spectrum_frame(seq=1, n=256),
            build_spectrum_frame(seq=2, n=512),
        ]:
            items = stream.push(builder)
            assert len(items) >= 1
            for it in items:
                assert it.kind == "frame"
                decoded = decode_frame(it.type, it.payload)
                assert decoded is not None, f"type={it.type} decode returned None"

    def test_frame_sizes_match_firmware_static_assert(self):
        """固件 frames.h 有 _Static_assert 的几个尺寸：
        frame_metrics_t = 34 字节
        frame_channel_t = 26 字节
        frame_telemetry_t = 59 字节
        frame_levels_t = 32 字节
        frame_level_t = 4 字节
        frame_rawiq_t = 14 字节

        frame_spectrum_t 没有 static assert — frame_parser.py 用的是
        Struct("<IIiIHBB") = 20 字节 header。
        """
        # METRICS 帧：6 (header) + 34 (hdr) + 8×26 (channels) + 4 (crc) = 252
        mf = build_metrics_frame(seq=1, nch=8)
        assert len(mf) == 252

        # TELEMETRY：6 + 59 + 4 = 69
        tf = build_telemetry_frame()
        assert len(tf) == 69

        # SPECTRUM：6 + 20 (hdr) + 256 (bins) + 4 = 286
        sf = build_spectrum_frame(seq=1, n=256)
        assert len(sf) == 286

    def test_incremental_stream(self):
        """分片推送 — FrameStream 必须正确拼回。"""
        mf = build_metrics_frame(seq=42)
        stream = FrameStream()
        # 把帧切成 3 段
        items = []
        for i in range(0, len(mf), 37):
            items.extend(stream.push(mf[i : i + 37]))
        assert len(items) == 1
        assert items[0].kind == "frame"
        assert items[0].type == FRAME_METRICS
        decoded = decode_frame(items[0].type, items[0].payload)
        assert decoded is not None
        assert decoded["seq"] == 42

    def test_info_line_parser(self):
        """INFO 行竖线格式 — 跟固件 commands.c 的回复一致。"""
        line = "INFO widscan58|esp32c5|1|sim-0.1.0|2026-11-01T00:00:00Z|esp32c5 2.5.0"
        parsed = parse_info_line(line)
        assert parsed is not None
        assert parsed["target"] == "esp32c5"
        assert parsed["protocol_version"] == 1
        assert parsed["fw_version"] == "sim-0.1.0"
        assert parsed["phy_version"] == "esp32c5 2.5.0"

    def test_info_line_rejects_non_widscan(self):
        assert parse_info_line("INFO rh-node|36|rh-0.7|...") is None

    def test_info_line_rejects_empty(self):
        assert parse_info_line("") is None
        assert parse_info_line("INFO") is None

    def test_command_whitelist(self):
        from widscan_agent.protocol import is_command_allowed

        # 肯定在白名单里的
        for ok in [
            "INFO", "PROTOCOL?", "PROTOCOL MONITOR", "PROTOCOL RH",
            "GAIN?", "GAIN MANUAL 45", "METRICS FULL", "GAIN HARDWARE",
            "FFT 1024", "FFT 256", "SPEC 0 256 40000000 MAX",
            "REBOOT", "BOOTLOADER", "SCAN START", "SCAN STOP",
            "STATUS?", "CONFIG?", "RAWIQ 5658000 2048",
            "CHANNELS R1=5658 A8=5725",
            "LEVELFILTER MEDIAN", "THROTTLE 60", "PHYIDLE?",
        ]:
            assert is_command_allowed(ok), f"expected allowed: {ok}"

        # 肯定不在白名单里的
        for bad in [
            "erase_flash", "custom_command", "rm -rf /", "",
            "UNKNOWN", "flash.py", "python3 -c",
        ]:
            assert not is_command_allowed(bad), f"expected denied: {bad}"
