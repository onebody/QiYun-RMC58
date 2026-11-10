"""三个 Transport 类的 open/close/read/write 行为。"""

import time

import pytest

from widscan_agent.transports.simulator import SimulatorTransport
from widscan_agent.transports.base import TransportState
from widscan_agent.transports.tcp import TcpTransport


class TestSimulatorTransport:
    def test_open_close(self):
        sim = SimulatorTransport(device_id="sim-001")
        assert sim.state == TransportState.DISCONNECTED
        sim.open()
        assert sim.state == TransportState.CONNECTED
        sim.close()
        assert sim.state == TransportState.DISCONNECTED

    def test_is_connected(self):
        sim = SimulatorTransport(device_id="sim-002")
        assert not sim.is_connected()
        sim.open()
        assert sim.is_connected()
        sim.close()
        assert not sim.is_connected()

    def test_write_read_command(self):
        sim = SimulatorTransport(device_id="sim-003")
        sim.open()
        sim.write(b"INFO\n")
        time.sleep(0.2)
        buf = b""
        start = time.time()
        while time.time() - start < 1.0:
            buf += sim.read(size=4096, timeout=0.2)
            if b"INFO widscan58" in buf:
                break
            time.sleep(0.01)
        assert b"INFO widscan58" in buf

    def test_read_timeout_returns_empty(self):
        sim = SimulatorTransport(device_id="sim-004", metrics_per_sec=0.001)
        sim.open()
        # 读完 INFO 回复后 drain
        sim.write(b"INFO\n")
        time.sleep(0.2)
        sim.read(size=4096, timeout=0.2)
        sim.read(size=4096, timeout=0.2)
        # 现在应该很快返回空 bytes
        start = time.monotonic()
        data = sim.read(size=4096, timeout=0.2)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15  # 接近 timeout
        assert isinstance(data, (bytes, bytearray))

    def test_get_info(self):
        sim = SimulatorTransport(device_id="sim-005")
        info = sim.get_info()
        assert info.kind == "simulator"
        assert info.device_id == "sim-005"
        assert "sim" in info.path


class TestTcpTransport:
    def test_open_unknown_host_error(self):
        """连接不上的 host 应该 raise 并标记 ERROR 状态。"""
        tcp = TcpTransport(host="127.0.0.1", port=1, timeout=0.5)
        with pytest.raises(Exception):
            tcp.open()
        assert tcp.state == TransportState.ERROR
        assert tcp.error is not None
        tcp.close()

    def test_get_info(self):
        tcp = TcpTransport(host="example.com", port=80)
        info = tcp.get_info()
        assert info.kind == "tcp"
        assert "example.com" in info.label
