"""Phase 07.2 — Site Server command.request 授权拦截（单元级）。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager

from widscan_site.agents import connection as conn_module
from widscan_site.database.models import Agent, Device


class _FakeConn:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _agent(session, agent_id):
    a = Agent(id=agent_id, site_id="site-001", name=agent_id, status="online")
    session.add(a)
    session.commit()
    return a


def _owned_device(session, device_id, owner):
    session.add(Device(
        id=device_id, site_id="site-001", display_name=device_id, status="online",
        owner_agent_id=owner, ownership_status="owned", identity_confidence="high",
    ))
    session.commit()


def _patch_session(monkeypatch, session):
    @contextmanager
    def _cm():
        yield session

    monkeypatch.setattr(conn_module, "get_session", _cm)


def _send(session, monkeypatch, *, agent_id, msg):
    _patch_session(monkeypatch, session)
    conn = _FakeConn()
    asyncio.run(conn_module._handle_inbound_command(
        msg, agent_id=agent_id, site_id="site-001", conn=conn))
    return conn.sent


def test_owner_command_authorized_but_dispatch_not_implemented(session, monkeypatch):
    _agent(session, "agent-A")
    _owned_device(session, "dev-1", "agent-A")
    sent = _send(session, monkeypatch, agent_id="agent-A", msg={
        "type": "command.request", "request_id": "r1",
        "device_id": "dev-1", "command": "INFO",
    })
    assert sent[0]["type"] == "command.response"
    assert sent[0]["ok"] is False
    assert sent[0]["reply"] == "remote command dispatch not implemented"


def test_observer_command_denied(session, monkeypatch):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _owned_device(session, "dev-1", "agent-A")
    sent = _send(session, monkeypatch, agent_id="agent-B", msg={
        "type": "command.request", "request_id": "r2",
        "device_id": "dev-1", "command": "INFO",
    })
    assert sent[0]["ok"] is False
    assert sent[0]["reply"] == "denied: observer_read_only"


def test_forged_owner_claim_in_message_is_ignored(session, monkeypatch):
    _agent(session, "agent-A")
    _agent(session, "agent-B")
    _owned_device(session, "dev-1", "agent-A")
    # B 在消息中伪造 owner_agent_id=agent-B，服务端只信已认证身份 + Device 记录
    sent = _send(session, monkeypatch, agent_id="agent-B", msg={
        "type": "command.request", "request_id": "r3",
        "device_id": "dev-1", "command": "INFO",
        "owner_agent_id": "agent-B", "requester_agent_id": "agent-B",
    })
    assert sent[0]["ok"] is False
    assert "denied" in sent[0]["reply"]


def test_unauthenticated_agent_identity_denied(session, monkeypatch):
    _agent(session, "agent-A")
    _owned_device(session, "dev-1", "agent-A")
    sent = _send(session, monkeypatch, agent_id="agent-ghost", msg={
        "type": "command.request", "request_id": "r4",
        "device_id": "dev-1", "command": "INFO",
    })
    assert sent[0]["ok"] is False
    assert "agent_unknown_or_disabled" in sent[0]["reply"]


def test_command_for_unknown_device_denied(session, monkeypatch):
    _agent(session, "agent-A")
    sent = _send(session, monkeypatch, agent_id="agent-A", msg={
        "type": "command.request", "request_id": "r5",
        "device_id": "dev-missing", "command": "INFO",
    })
    assert sent[0]["ok"] is False
    assert "device_not_found" in sent[0]["reply"]


def test_missing_fields_rejected(session, monkeypatch):
    sent = _send(session, monkeypatch, agent_id="agent-A", msg={
        "type": "command.request", "request_id": "",
        "device_id": "", "command": "INFO",
    })
    assert sent[0]["ok"] is False
    assert "invalid command request" in sent[0]["reply"]
