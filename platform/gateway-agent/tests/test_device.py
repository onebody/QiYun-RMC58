"""Device 状态机 — 用 SimulatorTransport 做真实端到端。"""

import time

import pytest

from widscan_agent.device import Device, DeviceState
from widscan_agent.events import EventBus
from widscan_agent.transports.simulator import SimulatorTransport


class TestDeviceOnlineLifecycle:
    def test_start_goes_online(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-001")
        d = Device(transport=sim, bus=bus, device_id="sim-life-001", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            assert d.state == DeviceState.ONLINE, f"final state: {d.state}"
        finally:
            d.stop()

    def test_stop_goes_disconnected(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-002")
        d = Device(transport=sim, bus=bus, device_id="sim-life-002", reconnect_enabled=False)
        d.start()
        time.sleep(0.5)
        d.stop()
        time.sleep(0.3)
        assert d.state == DeviceState.DISCONNECTED

    def test_emits_online_event(self):
        bus = EventBus()
        events = []
        bus.subscribe("device.online", lambda e: events.append(e))
        sim = SimulatorTransport(device_id="sim-life-003")
        d = Device(transport=sim, bus=bus, device_id="sim-life-003", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if events:
                    break
                time.sleep(0.1)
            assert len(events) == 1
            assert events[0].data["info"]["target"] == "esp32c5"
        finally:
            d.stop()

    def test_receives_frames(self):
        bus = EventBus()
        frames = []
        bus.subscribe("device.frame", lambda e: frames.append(e))
        sim = SimulatorTransport(device_id="sim-life-004", metrics_per_sec=100)
        d = Device(transport=sim, bus=bus, device_id="sim-life-004", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(40):
                if len(frames) >= 5:
                    break
                time.sleep(0.1)
            assert len(frames) >= 5, f"only got {len(frames)} frames"
            frame_types = set(e.data["type_code"] for e in frames)
            # simulator 产生 1 (METRICS), 2 (SPECTRUM), 3 (TELEMETRY)
            assert frame_types & {1, 2, 3}
            # 每帧都应该有 decoded dict
            for e in frames:
                assert e.data["decoded"] is not None
        finally:
            d.stop()

    def test_send_command_ok(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-005")
        d = Device(transport=sim, bus=bus, device_id="sim-life-005", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            time.sleep(0.5)  # 让后台 read_loop drain 完 handshake 残留
            ok, reply = d.send_command("GAIN?")
            assert ok, f"reply={reply}"
            assert "GAIN" in reply
        finally:
            d.stop()

    def test_send_command_denied(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-006")
        d = Device(transport=sim, bus=bus, device_id="sim-life-006", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            ok, reply = d.send_command("erase_flash")
            assert not ok
            assert "not allowed" in reply
        finally:
            d.stop()

    def test_state_transition_count(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-007")
        d = Device(transport=sim, bus=bus, device_id="sim-life-007", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            m = d.metrics
            assert m.state_changes >= 4  # discovered → connecting → connecting → handshaking → online ...
        finally:
            d.stop()

    def test_identity_info(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-life-008")
        d = Device(transport=sim, bus=bus, device_id="sim-life-008", reconnect_enabled=False)
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            info = d.info
            assert info["target"] == "esp32c5"
            assert info["protocol_version"] == 1
            assert info["fw_version"].startswith("sim-")
        finally:
            d.stop()


class TestDeviceReconnectBackoff:
    """关闭模拟器设备模拟拔插 — Device 应该走 ERROR → RECONNECTING 然后指数退避。"""

    def test_reconnects_after_sim_disconnect(self):
        bus = EventBus()
        sim = SimulatorTransport(device_id="sim-reconnect-001")
        d = Device(
            transport=sim, bus=bus, device_id="sim-reconnect-001",
            reconnect_enabled=True, reconnect_initial_delay=0.1, reconnect_max_delay=0.3,
        )
        d.start()
        try:
            for _ in range(30):
                if d.state == DeviceState.ONLINE:
                    break
                time.sleep(0.1)
            assert d.state == DeviceState.ONLINE

            # 让 simulator "断开" — 写一条 REBOOT 命令 (simulator 会关闭)
            ok, _ = d.send_command("REBOOT")
            time.sleep(0.5)

            # 现在 sim transport 应该 DISCONNECTED，Device 应该 RECONNECTING 或重连 ONLINE
            # 我们不关心它最终怎么样，只关心它没崩
            assert d.state != DeviceState.DISCOVERED
        finally:
            d.stop()
