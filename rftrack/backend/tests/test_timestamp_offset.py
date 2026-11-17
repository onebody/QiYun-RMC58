"""出参的时刻一律带偏移，不给裸 ISO 串。

起因是一条实测：工作台 node-status 的"最近"那格显示 `22:40:41`，而 `GET /api/nodes` 回的是
`06:40:41`——秒位完全一致，差整整 16 小时。链条是这样的：

- SQLite 的 `DATETIME(timezone=True)` 读回来是 **naive**（`models.to_utc` 的注释钉住这条实测）；
- Pydantic 对 naive datetime 序列化出**不带偏移**的 ISO，`"2026-11-11T06:40:41"`；
- `new Date("2026-11-11T06:40:41")` 按**本地时区**解释，在 UTC+8 上就是前一天的 22:40 UTC；
- 页面再按 UTC 印出来 → `22:40:41`。

口径本身早就立过（`services/history.iso`：「统一带偏移：不带就会被浏览器按本地时区解释」），
漏的是出参模型——`alerts`/`events`/`targets` 三个 router 手工走过 `history.iso`，而靠
`from_attributes` 直接映射的 `SiteOut`/`RelayOut`/`GatewayOut`/`NodeOut`/`LayoutOut` 没走。
所以修在类型上（`schemas.IsoUtc` 一个定义覆盖这五个模型的九个字段），而不是往每个 router
再补一遍手抄的换算。
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from app.schemas import IsoUtc

SITE = {"name": "主场", "slug": "main"}
#: 库里真实躺着的样子：naive 的 UTC 墙上时间，微秒补零与 `utcnow()` 写进去的一致。
WALL = "2026-11-11 06:40:41.000000"


@pytest.fixture
def gateway(client):
    """场地 → 位置 → 接入机，三级各一行；节点由各个测试自己摆。"""
    site = client.post("/api/sites", json=SITE).json()
    relay = client.post("/api/relays", json={"site_id": site["site_id"], "name": "起点塔"}).json()
    created = client.post(
        "/api/gateways", json={"relay_id": relay["relay_id"], "name": "pi-1", "kind": "usb-bridge"}
    )
    return {"site": site, "relay": relay, "gateway": created.json()}


def test_node_last_seen_carries_an_offset(gateway, client, db_path):
    """就是页面上那一格：库里 06:40 UTC 的 `last_seen`，页面必须读出 06:40 UTC。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO node (node_id, gateway_id, display_name, transport_kind, transport_addr, "
            "status, stops_rh, last_seen) "
            "VALUES (1, ?, 'widscan58-1', 'usb-native', '/dev/widscan58-1', 'active', 0, ?)",
            (gateway["gateway"]["gateway_id"], WALL),
        )
        conn.commit()

    node = client.get("/api/nodes").json()[0]
    assert node["last_seen"] == "2026-11-11T06:40:41+00:00", node["last_seen"]
    # 页面读这一格走的是 Date.parse；带偏移之后，按 UTC 印出来的秒位与库里那行一致
    assert (
        datetime.fromisoformat(node["last_seen"]).astimezone(timezone.utc).strftime("%H:%M:%S")
        == "06:40:41"
    )


def test_a_node_that_has_never_been_seen_is_still_null(gateway, client, db_path):
    """没偏移 ≠ 没数据。`last_seen` 为空时还是 `null`，不能为了补偏移写成 1970。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO node (node_id, gateway_id, display_name, transport_kind, transport_addr, "
            "status, stops_rh) VALUES (1, ?, 'widscan58-1', 'usb-native', '/dev/widscan58-1', "
            "'active', 0)",
            (gateway["gateway"]["gateway_id"],),
        )
        conn.commit()

    assert client.get("/api/nodes").json()[0]["last_seen"] is None


def test_every_timestamp_on_the_api_carries_an_offset(gateway, client, db_path):
    """九个字段一个都不放过。

    判据按**形状**而不是按值：结尾是不是 `+00:00`。裸串与带偏移串在页面上差 16 小时，
    而这一格不会因此变空、也不会报错——只有形状断言拦得住它。
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE site SET last_sync_at = ? WHERE site_id = ?", (WALL, gateway["site"]["site_id"]))
        conn.execute(
            "UPDATE gateway SET last_seen = ? WHERE gateway_id = ?",
            (WALL, gateway["gateway"]["gateway_id"]),
        )
        conn.commit()

    layout = client.post(
        "/api/layouts", json={"site_id": gateway["site"]["site_id"], "name": "主屏"}
    ).json()
    sites = client.get("/api/sites").json()[0]
    relays = client.get("/api/relays").json()[0]
    gateways = client.get("/api/gateways").json()[0]

    seen = {
        "site.created_at": sites["created_at"],
        "site.updated_at": sites["updated_at"],
        "site.last_sync_at": sites["last_sync_at"],
        "relay.created_at": relays["created_at"],
        "relay.updated_at": relays["updated_at"],
        "gateway.last_seen": gateways["last_seen"],
        "layout.created_at": layout["created_at"],
        "layout.updated_at": layout["updated_at"],
    }
    for name, value in seen.items():
        assert isinstance(value, str), f"{name} = {value!r}"
        assert value.endswith("+00:00"), f"{name} 没有偏移：{value!r}"


def test_only_the_json_side_becomes_a_string():
    """python 侧（`model_dump()`）仍是 datetime：内部按时间比较的代码不该被这次改动牵到。"""

    class Sample(BaseModel):
        at: IsoUtc
        updated_at: IsoUtc

    moment = datetime(2026, 11, 11, 6, 40, 41)
    sample = Sample(at=moment, updated_at=moment)
    assert isinstance(sample.model_dump()["at"], datetime)
    assert json.loads(sample.model_dump_json())["at"] == "2026-11-11T06:40:41+00:00"
