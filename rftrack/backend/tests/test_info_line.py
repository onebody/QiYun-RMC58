"""设备身份进库：不新增固件命令，也不许把"没解析"写成"板子没说"。

三条口径：

- **身份的唯一来源是 INFO 文本行**（`firmware/src/proto/commands.c:426` 那句
  `INFO widscan58|<target>|<proto>|<fw>|<commit>|<phy>`）。采集层今天把文本行只计数就丢掉
  （`services/capture.py:626-627`），所以这是**后端解析缺口**，不是设备协议缺口——本期不申请
  任何新命令。
- **认不出的一律不写**：不是 INFO、字段不够、版本号不是数字，都不许"补一个默认值"进库。
  `Node.protocol_version` 里躺着一个软件猜的 `1`，比 `NULL` 难查得多。
- **同一份身份只写一次**：文本行会随每次连接、每次 `INFO` 再来一遍，UPDATE 只在真变化时发生。
  写放大是这条路上唯一会被规模咬到的地方。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import Gateway, Node
from app.services.capture import CapturePipeline
from app.services.frame_parser import parse_info_line
from app.services.ingest import reconcile

SERIAL_EP = "serial:/dev/widscan58-A"
SERIAL_ADDR = "/dev/widscan58-A"

#: 固件现形：target / proto / fw / HEAD 时间 / PHY（PHY 里可能带空格）。
INFO_CURRENT = "INFO widscan58|esp32-c5|1|0.6.0|2026-11-01T02:03:04Z|ESP32-C5 SPH 8.1.2"
#: 老固件的空格分隔形，只有五段且没有 HEAD 时间。
INFO_LEGACY = "INFO widscan58 esp32-c5 1 0.5.2 8.1.1"


# --- 纯解析：一份行文本，不含任何 I/O -----------------------------------------


def test_the_current_info_line_yields_all_five_identity_fields():
    info = parse_info_line(INFO_CURRENT)
    assert info == {
        "target": "esp32-c5",
        "protocol_version": 1,
        "fw_version": "0.6.0",
        "commit_time": "2026-11-01T02:03:04Z",
        "phy_version": "ESP32-C5 SPH 8.1.2",
    }


def test_the_phy_version_keeps_its_spaces():
    """PHY 版本自带空格（固件注释明说了），所以它只能放在最后一格并整段留下。"""
    assert parse_info_line("widscan58|c5|1|0.6.0|t|ESP32-C5 SPH 8.1.2 build 77")["phy_version"] == (
        "ESP32-C5 SPH 8.1.2 build 77"
    )


def test_the_legacy_space_form_has_no_commit_time():
    info = parse_info_line(INFO_LEGACY)
    assert info["fw_version"] == "0.5.2"
    assert info["phy_version"] == "8.1.1"
    assert info["commit_time"] is None, "老固件那一格的第 5 段是 PHY，不是 HEAD 时间"


def test_the_info_keyword_prefix_is_optional():
    """流里给的可能是整行 `INFO widscan58|...`，也可能是剥掉关键字的载荷。"""
    assert parse_info_line(INFO_CURRENT) == parse_info_line(INFO_CURRENT.removeprefix("INFO "))


@pytest.mark.parametrize(
    "line",
    [
        "OK",
        "",
        "STATUS: scanning",
        "widscan58|esp32-c5|1",  # 缺版本号那一格
        "widscan58|esp32-c5|1|",
        "probe|1|2|3|4|5",
        "INFO widscan58",
    ],
)
def test_a_line_that_is_not_an_identity_returns_none_instead_of_a_half_truth(line):
    assert parse_info_line(line) is None


def test_a_non_numeric_protocol_field_is_dropped_not_invented():
    """协议号必须是数字才作数；`x` 那种回串宁可留 `None`，不许落成 0 或 1。"""
    assert parse_info_line("widscan58|c5|x|0.6.0|t|phy")["protocol_version"] is None


def test_an_empty_phy_or_commit_stays_none_rather_than_becoming_empty_string():
    info = parse_info_line("widscan58|c5|1|0.6.0||")
    assert info["fw_version"] == "0.6.0"
    assert info["commit_time"] is None
    assert info["phy_version"] is None


# --- 采集层落库：一块真板子的形状 ----------------------------------------------


class FakeResolver:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def addrs_for(self, gateway_id, eps):
        table = self.tables.get(gateway_id, {})
        return {ep: table[ep] for ep in eps if ep in table}


@pytest.fixture
def chain(client):
    """一个场地、一台接入机、一块板：身份写进的就是这一行 `Node`。"""
    site = client.post("/api/sites", json={"name": "主场", "slug": "main"}).json()
    relay = client.post(
        "/api/relays", json={"site_id": site["site_id"], "name": "主场塔"}
    ).json()
    gateway = client.post(
        "/api/gateways",
        json={"relay_id": relay["relay_id"], "name": "pi-main", "kind": "usb-bridge"},
    ).json()
    with client.app.state.db.scope() as session:
        reconcile(
            session,
            session.get(Gateway, gateway["gateway_id"]),
            [
                {
                    "id": SERIAL_EP,
                    "kind": "serial",
                    "label": "widscan58-A",
                    "path": SERIAL_ADDR,
                    "online": True,
                    "busy": False,
                }
            ],
            Settings(max_nodes_per_gateway=8),
        )
    row = client.get(f"/api/nodes?gateway={gateway['gateway_id']}").json()[0]
    assert row["transport"]["addr"] == SERIAL_ADDR
    return {
        "site_id": site["site_id"],
        "gateway_id": gateway["gateway_id"],
        "node_id": row["node_id"],
        "ep": SERIAL_EP,
        "addr": SERIAL_ADDR,
    }


def make_pipeline(client):
    settings = Settings(
        db_path=client.app.state.settings.db_path,
        dialer_enabled=False,
        retention_purge_enabled=False,
        capture_silence_s=5.0,
    )
    return CapturePipeline(client.app.state.db, settings, resolver=FakeResolver())


def node_row(client, node_id):
    with client.app.state.db.scope() as session:
        return session.execute(
            select(Node.fw_version, Node.protocol_version).where(Node.node_id == node_id)
        ).one()


# --- 落库的三条行为 -----------------------------------------------------------


def test_an_info_line_fills_the_node_version_columns(client, chain):
    pipeline = make_pipeline(client)
    chunk = f"{INFO_CURRENT}\n".encode()
    pipeline.feed(
        chain["gateway_id"], chain["ep"], chunk, transport_addr=chain["addr"]
    )
    pipeline.drain_all()
    pipeline.tick()

    assert node_row(client, chain["node_id"]) == ("0.6.0", 1)


def test_a_repeated_identical_info_line_writes_the_row_once(client, chain):
    pipeline = make_pipeline(client)
    chunk = f"{INFO_CURRENT}\n".encode()
    for _ in range(3):
        pipeline.feed(chain["gateway_id"], chain["ep"], chunk, transport_addr=chain["addr"])
        pipeline.drain_all()
        pipeline.tick()

    assert pipeline.identity_writes == 1
    assert node_row(client, chain["node_id"]) == ("0.6.0", 1)


def test_a_non_info_text_line_leaves_the_identity_columns_untouched(client, chain):
    pipeline = make_pipeline(client)
    pipeline.feed(
        chain["gateway_id"], chain["ep"], b"MODE GROUP\nOK\n", transport_addr=chain["addr"]
    )
    pipeline.drain_all()
    pipeline.tick()

    assert node_row(client, chain["node_id"]) == (None, None)
    assert pipeline.identity_writes == 0


def test_a_malformed_info_line_is_counted_as_unparsed_not_as_a_version(client, chain):
    pipeline = make_pipeline(client)
    pipeline.feed(
        chain["gateway_id"], chain["ep"], b"INFO widscan58|esp32-c5|1\n",
        transport_addr=chain["addr"],
    )
    pipeline.drain_all()
    pipeline.tick()

    assert node_row(client, chain["node_id"]) == (None, None)
    assert pipeline.text_lines == 1


# --- `identity_source`：这个数是谁说的 ---------------------------------------

#: 人工 `PUT` 与设备 `INFO` 都会填 `fw_version`，光看值分不出来。`identity_source` 那一格
#: 记的就是"这一行的版本是谁说的"——手打的会过期，板子不会。
INFO_FULL = {
    "identity_source": "info_line",
    "identity_target": "esp32-c5",
    "commit_time": "2026-11-01T02:03:04Z",
    "phy_version": "ESP32-C5 SPH 8.1.2",
    "fw_version": "0.6.0",
    "protocol_version": 1,
}


def identity_row(client, node_id) -> dict:
    with client.app.state.db.scope() as session:
        node = session.get(Node, node_id)
        session.refresh(node)
        return {
            "identity_source": node.identity_source,
            "identity_target": node.identity_target,
            "commit_time": node.commit_time,
            "phy_version": node.phy_version,
            "fw_version": node.fw_version,
            "protocol_version": node.protocol_version,
        }


def feed_info(client, chain, line: str = INFO_CURRENT):
    pipeline = make_pipeline(client)
    pipeline.feed(
        chain["gateway_id"], chain["ep"], f"{line}\n".encode(), transport_addr=chain["addr"]
    )
    pipeline.drain_all()
    pipeline.tick()
    return pipeline


def test_an_info_line_records_that_the_device_said_it(client, chain):
    feed_info(client, chain)
    assert identity_row(client, chain["node_id"]) == INFO_FULL


def test_a_hand_typed_declaration_is_marked_as_hand_typed(client, chain):
    client.put(
        f"/api/nodes/{chain['node_id']}", json={"fw_version": "0.4.0", "protocol_version": 1}
    )
    row = identity_row(client, chain["node_id"])
    assert row["identity_source"] == "manual"
    assert row["fw_version"] == "0.4.0"
    #: 人工那一格填不出 target/commit/phy——端点表与 `PUT` 都没有这些事实，留 NULL 不补空串。
    assert (row["identity_target"], row["commit_time"], row["phy_version"]) == (None,) * 3


def test_the_device_wins_over_what_someone_typed_in(client, chain):
    """手打的版本串会过期，板子不会：INFO 一到就顶掉 `manual`，并留下新的来源。"""
    client.put(f"/api/nodes/{chain['node_id']}", json={"fw_version": "0.4.0"})
    feed_info(client, chain)

    row = identity_row(client, chain["node_id"])
    assert row["identity_source"] == "info_line"
    assert row["fw_version"] == "0.6.0"


def test_the_read_api_exposes_where_the_identity_came_from(client, chain):
    feed_info(client, chain)
    out = client.get(f"/api/nodes/{chain['node_id']}").json()
    assert out["identity_source"] == "info_line"
    assert out["identity_target"] == "esp32-c5"
    assert out["phy_version"] == "ESP32-C5 SPH 8.1.2"
    assert out["time_sync_quality"] == "unsynced"



def test_a_capabilities_only_declaration_leaves_the_devices_claim_alone(client, chain):
    """只声明能力的那次 PUT 里**没有"版本"这件事**，把来源翻成 `manual` 就是替操作员
    指认"这个版本号是我打的"——他没说过。"""
    feed_info(client, chain)
    client.put(f"/api/nodes/{chain['node_id']}", json={"capabilities": {"rawiq": True}})
    assert identity_row(client, chain["node_id"])["identity_source"] == "info_line"
