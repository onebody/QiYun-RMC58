"""REST API — FastAPI TestClient + httpx。"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from widscan_agent.events import EventBus
from widscan_agent.manager import DeviceManager
from widscan_agent.api import build_app
from widscan_agent.transports.simulator import SimulatorTransport


TEST_API_TOKEN = "test-local-token"


@pytest.fixture
def api_client():
    bus = EventBus()
    mgr = DeviceManager(bus=bus, poll_interval=999)
    mgr.start()
    sim = SimulatorTransport(device_id="api-sim-001")
    mgr.add_device(sim, device_id="api-sim-001")
    app = build_app(
        manager=mgr, bus=bus, agent_id="test-agent", frame_parser_path="/tmp/fp.py",
        api_token=TEST_API_TOKEN, require_auth=True,
    )
    # 等 simulator ONLINE
    time.sleep(0.5)
    with TestClient(app, headers={"X-API-Token": TEST_API_TOKEN}) as c:
        yield c
    mgr.stop()


class TestRESTEndpoints:
    def test_health(self, api_client: TestClient):
        r = api_client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "agent": "test-agent"}

    def test_get_agent(self, api_client: TestClient):
        r = api_client.get("/api/v1/agent")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "test-agent"
        assert data["version"] == "0.1.0"
        assert data["frame_parser_path"] == "/tmp/fp.py"
        assert "uptime" in data

    def test_list_devices(self, api_client: TestClient):
        r = api_client.get("/api/v1/devices")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        assert len(arr) >= 1
        api_sim = next(x for x in arr if x["device_id"] == "api-sim-001")
        assert api_sim["state"] in ("online", "connecting", "handshaking")

    def test_get_device(self, api_client: TestClient):
        r = api_client.get("/api/v1/devices/api-sim-001")
        assert r.status_code == 200
        data = r.json()
        assert data["device_id"] == "api-sim-001"
        assert "state" in data
        assert "transport_info" in data

    def test_get_device_404(self, api_client: TestClient):
        r = api_client.get("/api/v1/devices/nonexistent")
        assert r.status_code == 404

    def test_send_command_ok(self, api_client: TestClient):
        r = api_client.post(
            "/api/v1/devices/api-sim-001/commands",
            json={"command": "GAIN?"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        assert "GAIN" in data["reply"]

    def test_send_command_denied(self, api_client: TestClient):
        r = api_client.post(
            "/api/v1/devices/api-sim-001/commands",
            json={"command": "erase_flash"},
        )
        assert r.status_code == 403

    def test_send_command_empty(self, api_client: TestClient):
        r = api_client.post(
            "/api/v1/devices/api-sim-001/commands",
            json={"command": ""},
        )
        # FastAPI 会校验 min_length=1
        assert r.status_code == 422

    def test_diagnostics(self, api_client: TestClient):
        r = api_client.get("/api/v1/diagnostics")
        assert r.status_code == 200
        data = r.json()
        assert data["agent"] == "test-agent"
        assert "devices" in data
        assert "events_last_10" in data
