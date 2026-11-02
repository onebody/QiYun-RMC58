"""`GET /api/nodes`：推导链、身份键与"缺能力就报 unsupported"。

三条都是硬口径，不是风格问题：
- 节点上不存 `site_id`，场地/位置一律经 gateway → relay → site 推导（避免三处双写不一致）。
- 身份键是 `(gateway_id, transport_addr)`，同名的两块板分属两台接入机时不能合并。
- 能力只按声明渲染；**没声明 = unsupported**，有界能力少了上限也当没声明——
  把有界渲染成无界，和把它渲染成 unsupported 一样是伪造。
"""

import json
import sqlite3

import pytest

NODE = (
    "INSERT INTO node (node_id, gateway_id, display_name, transport_kind, transport_addr, "
    "capabilities_json, status, stops_rh) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)"
)


@pytest.fixture
def chain(client, db_path):
    """一个场地两个位置，各一台接入机；另有一块同名板挂在第二台上。"""
    home = client.post("/api/sites", json={"name": "主场", "slug": "home"}).json()
    away = client.post("/api/sites", json={"name": "客场", "slug": "away"}).json()
    relays = {}
    gateways = {}
    for site, relay_name, gw_name in (
        (home, "起点塔", "pi-1"),
        (home, "看台", "pi-2"),
        (away, "计时区", "pi-1"),
    ):
        relay = client.post("/api/relays", json={"site_id": site["site_id"], "name": relay_name}).json()
        relays[relay_name] = relay
        gw = client.post(
            "/api/gateways",
            json={"relay_id": relay["relay_id"], "name": gw_name, "kind": "usb-bridge"},
        ).json()
        gateways[relay_name] = gw

    def add(relay_name, node_id, name, kind, addr, caps=None, stops_rh=False):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                NODE,
                (
                    node_id,
                    gateways[relay_name]["gateway_id"],
                    name,
                    kind,
                    addr,
                    json.dumps(caps) if caps is not None else None,
                    1 if stops_rh else 0,
                ),
            )
            conn.commit()

    return {
        "sites": {"home": home, "away": away},
        "gateways": gateways,
        "relays": relays,
        "add": add,
        "db_path": db_path,
    }


def test_a_node_carries_its_whole_derived_chain(chain, client):
    chain["add"]("起点塔", 1, "widscan58-1", "usb-native", "/dev/widscan58-1")
    node = client.get("/api/nodes").json()[0]
    assert node["site_id"] == chain["sites"]["home"]["site_id"]
    assert node["relay_id"] == chain["relays"]["起点塔"]["relay_id"]
    assert node["gateway_id"] == chain["gateways"]["起点塔"]["gateway_id"]
    assert node["transport"] == {"kind": "usb-native", "addr": "/dev/widscan58-1"}


def test_filtering_by_site_returns_only_that_site(chain, client):
    chain["add"]("起点塔", 1, "主场的板", "usb-native", "/dev/a")
    chain["add"]("计时区", 2, "客场的板", "usb-native", "/dev/b")
    home_id = chain["sites"]["home"]["site_id"]
    listed = client.get(f"/api/nodes?site={home_id}").json()
    assert [n["node_id"] for n in listed] == [1]
    assert {n["site_id"] for n in listed} == {home_id}


def test_filtering_by_gateway(chain, client):
    chain["add"]("起点塔", 1, "a", "usb-native", "/dev/a")
    chain["add"]("看台", 2, "b", "usb-native", "/dev/b")
    gw_id = chain["gateways"]["看台"]["gateway_id"]
    listed = client.get(f"/api/nodes?gateway={gw_id}").json()
    assert [n["display_name"] for n in listed] == ["b"]


def test_same_board_name_on_two_gateways_stays_two_nodes(chain, client):
    """同名两块板分属两台接入机是完全正常的，按显示名全局去重会串场地。"""
    chain["add"]("起点塔", 1, "widscan58-2", "usb-native", "/dev/widscan58-2")
    chain["add"]("看台", 2, "widscan58-2", "usb-native", "/dev/widscan58-2")
    listed = client.get("/api/nodes").json()
    assert len(listed) == 2
    assert {n["gateway_id"] for n in listed} == {
        chain["gateways"]["起点塔"]["gateway_id"],
        chain["gateways"]["看台"]["gateway_id"],
    }


def test_a_node_with_no_declaration_at_all_is_all_unsupported(chain, client):
    chain["add"]("起点塔", 1, "没声明的板", "usb-native", "/dev/a")
    caps = client.get("/api/nodes").json()[0]["capabilities"]
    for name in ("levels", "spectrum", "rawiq"):
        assert caps[name] == {"supported": False, "reason": "undeclared"}


def test_declared_false_is_reported_as_declared_not_missing(chain, client):
    chain["add"](
        "起点塔",
        1,
        "a",
        "usb-native",
        "/dev/a",
        {"levels": True, "spectrum": {"supported": False}, "rawiq": {"supported": False}},
    )
    caps = client.get("/api/nodes").json()[0]["capabilities"]
    assert caps["levels"] == {"supported": True}
    assert caps["spectrum"] == {"supported": False, "reason": "declared_unsupported"}
    assert caps["rawiq"] == {"supported": False, "reason": "declared_unsupported"}


def test_a_bounded_capability_without_its_limit_is_not_rendered_as_supported(chain, client):
    """UART 上的 spectrum 是被节流的有界能力：只说"支持"不说上限，等同伪造。"""
    chain["add"]("起点塔", 1, "a", "uart-pl011", "/dev/serial0", {"spectrum": True})
    caps = client.get("/api/nodes").json()[0]["capabilities"]
    assert caps["spectrum"] == {"supported": False, "reason": "missing_limits"}


def test_the_limits_are_passed_through_verbatim(chain, client):
    chain["add"](
        "起点塔",
        1,
        "a",
        "uart-pl011",
        "/dev/serial0",
        {"spectrum": {"supported": True, "max_bins": 512, "paced_rotation_hz": 3.5}},
    )
    caps = client.get("/api/nodes").json()[0]["capabilities"]
    assert caps["spectrum"] == {
        "supported": True,
        "max_bins": 512,
        "paced_rotation_hz": 3.5,
    }


def test_an_unknown_declaration_is_shown_instead_of_dropped(chain, client):
    chain["add"]("起点塔", 1, "a", "usb-native", "/dev/a", {"telepathy": True})
    caps = client.get("/api/nodes").json()[0]["capabilities"]
    assert caps["telepathy"] == {
        "supported": None,
        "reason": "unknown_capability",
        "declared": True,
    }


def test_a_uart_pl011_node_always_says_it_stops_rotorhazard(chain, client):
    """②a 会停掉该接入机的计时——这是介质决定的事实，不许有一行数据把它写成 False。"""
    chain["add"]("起点塔", 1, "a", "uart-pl011", "/dev/serial0", stops_rh=False)
    node = client.get("/api/nodes").json()[0]
    assert node["stops_rh"] is True


def test_a_usb_node_does_not_claim_to_stop_rotorhazard(chain, client):
    chain["add"]("起点塔", 1, "a", "usb-native", "/dev/a")
    assert client.get("/api/nodes").json()[0]["stops_rh"] is False


def test_a_single_node_can_be_fetched(chain, client):
    chain["add"]("起点塔", 1, "a", "usb-native", "/dev/a")
    assert client.get("/api/nodes/1").json()["display_name"] == "a"
    assert client.get("/api/nodes/4242").status_code == 404


def test_malformed_capability_json_degrades_to_unsupported_not_a_500(chain, client):
    """capabilities_json 是文本列，坏值只能在库层面构造出来。"""
    chain["add"]("起点塔", 1, "a", "usb-native", "/dev/a")
    with sqlite3.connect(chain["db_path"]) as conn:
        conn.execute("UPDATE node SET capabilities_json = ? WHERE node_id = 1", ("{不是 JSON",))
        conn.commit()
    response = client.get("/api/nodes")
    assert response.status_code == 200
    node = response.json()[0]
    assert node["capabilities"]["levels"] == {"supported": False, "reason": "undeclared"}
    assert node["declaration_error"] == "malformed_json"


def test_put_declares_the_three_columns_the_ui_has_always_rendered_unsupported(chain, client):
    """`levels/spectrum/rawiq` 与 `fw_version`/`protocol_version` 自 001 就在表里，
    页面上恒是 unsupported——因为今天没有任何写路径。M2 补的就是这一条。
    spectrum 是有界能力：只有 921600 的下行才配得上 992 bins（line_pace 的上限）。"""
    chain["add"]("起点塔", 7, "中继头的板", "node-ws", "node:fake-head")
    before = client.get("/api/nodes/7").json()["capabilities"]
    assert before["spectrum"] == {"supported": False, "reason": "undeclared"}

    row = client.put(
        "/api/nodes/7",
        json={
            "fw_version": "Web-v0.10",
            "protocol_version": 1,
            "capabilities": {
                "levels": True,
                "spectrum": {"supported": True, "max_bins": 992},
                "rawiq": {"supported": False},
            },
        },
    )
    assert row.status_code == 200, row.text
    out = row.json()
    assert out["declaration_error"] is None
    assert out["capabilities"]["levels"] == {"supported": True}
    assert out["capabilities"]["spectrum"] == {"supported": True, "max_bins": 992}
    assert out["capabilities"]["rawiq"] == {
        "supported": False,
        "reason": "declared_unsupported",
    }
    assert out["fw_version"] == "Web-v0.10"
    assert out["protocol_version"] == 1
    # 形状没被发明：响应就是 GET 的那一套键
    assert set(out) == set(client.get("/api/nodes/7").json())


def test_a_declaration_survives_the_next_endpoint_sweep(chain, client):
    """`reconcile()` 每轮都会重跑。它要是把声明冲掉，操作员填一次就永远白填——
    而现象是"能力位过一会儿自己变回 unsupported"，最难倒推。"""
    from app.services.ingest import reconcile
    from app.config import Settings
    from app.models import Gateway

    chain["add"]("起点塔", 8, "扫频头", "node-ws", "node:head-8")
    client.put(
        "/api/nodes/8",
        json={"capabilities": {"levels": True, "spectrum": {"supported": True, "max_bins": 512}}},
    )
    with client.app.state.db.scope() as session:
        gateway = session.get(Gateway, chain["gateways"]["起点塔"]["gateway_id"])
        reconcile(
            session,
            gateway,
            [
                {
                    "id": "node:head-8",
                    "kind": "node",
                    "label": "扫频头",
                    "online": True,
                    "busy": False,
                    # 端点表里塞一份"能力"：平台一个字都不许从这里取
                    "capabilities": {"levels": False, "rawiq": True},
                    "fw_version": "从端点表偷来的",
                }
            ],
            Settings(max_nodes_per_gateway=8),
        )
    out = client.get("/api/nodes/8").json()
    assert out["fw_version"] is None, "端点表里那句 fw_version 一个字都不许进账"
    assert out["capabilities"]["rawiq"]["supported"] is False
    assert out["capabilities"]["spectrum"]["max_bins"] == 512


def test_put_rejects_a_declaration_that_is_not_an_object(chain, client):
    """不是对象的声明会渲染成 `not_an_object` 且全部 unsupported——那就写进去一份
    读不出来的东西。入侧就拒，比让页面显示"声明有问题"诚实。"""
    chain["add"]("起点塔", 9, "a", "usb-native", "/dev/a")
    assert client.put("/api/nodes/9", json={"capabilities": [1, 2]}).status_code == 422


def test_the_aggregator_cannot_declare(chain, client, db_path):
    """声明是改这台实例看到的东西，只在场地实例上写（与另外 21 个写端点同口径）。"""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    chain["add"]("起点塔", 10, "a", "usb-native", "/dev/a")
    reader = TestClient(
        create_app(Settings(db_path=db_path, dialer_enabled=False, instance_role="aggregator"))
    )
    row = reader.put("/api/nodes/10", json={"capabilities": {"levels": True}})
    assert row.status_code == 405, (row.status_code, row.text)
    assert row.json()["detail"]["reason"] == "aggregator_read_only"
