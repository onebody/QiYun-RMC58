"""DeviceManager — 用 simulator 注入多台设备。"""

import time

import pytest

from widscan_agent.device import DeviceState
from widscan_agent.events import EventBus
from widscan_agent.manager import DeviceManager
from widscan_agent.transports.simulator import SimulatorTransport


@pytest.fixture
def manager():
    bus = EventBus()
    mgr = DeviceManager(bus=bus, poll_interval=999)  # 关掉 USB 轮询
    mgr.start()
    yield mgr
    mgr.stop()


class TestManagerInjection:
    def test_add_simulator(self, manager: DeviceManager):
        sim = SimulatorTransport(device_id="mgr-sim-001")
        d = manager.add_device(sim, device_id="mgr-sim-001")
        assert d is not None
        assert d.device_id == "mgr-sim-001"
        # 等 ONLINE
        for _ in range(40):
            if d.state == DeviceState.ONLINE:
                break
            time.sleep(0.1)
        assert d.state == DeviceState.ONLINE

    def test_list_devices(self, manager: DeviceManager):
        for i in range(3):
            sim = SimulatorTransport(device_id=f"mgr-sim-{i:03d}")
            manager.add_device(sim, device_id=f"mgr-sim-{i:03d}")
        devices = manager.list_devices()
        assert len(devices) == 3

    def test_get_device(self, manager: DeviceManager):
        sim = SimulatorTransport(device_id="mgr-sim-get")
        manager.add_device(sim, device_id="mgr-sim-get")
        d = manager.get("mgr-sim-get")
        assert d is not None
        assert d.device_id == "mgr-sim-get"
        assert manager.get("nope") is None

    def test_remove_device(self, manager: DeviceManager):
        sim = SimulatorTransport(device_id="mgr-sim-remove")
        manager.add_device(sim, device_id="mgr-sim-remove")
        assert manager.remove_device("mgr-sim-remove")
        assert manager.get("mgr-sim-remove") is None
        assert not manager.remove_device("nope")

    def test_duplicate_device_id(self, manager: DeviceManager):
        sim1 = SimulatorTransport(device_id="dup-001")
        manager.add_device(sim1, device_id="dup-001")
        sim2 = SimulatorTransport(device_id="dup-001")
        d2 = manager.add_device(sim2, device_id="dup-001")
        # 已存在 → 返回现有那个
        assert d2.device_id == "dup-001"
        assert len(manager.list_devices()) == 1
