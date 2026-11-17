"""节点级命令下发：站点工作台里那套"单模块接入点控制"的写侧。

这一轮是 C-8 明确留到后面的那一轮（`docs/superpowers/plans/2026-11-03-widscan58-p0-stage1-audit.md`
§4 第 19 条）。形状由五条口径决定，每条都在这里有一个专门的失败：

- **端点只能问桥，不许从地址猜**。uart 条目的 `id` 用未解析的 `--rh-serial`、`path` 是
  `realpath` 之后的 `ttyAMA0`（`web/serve.mjs:375`），所以 `ep = 前缀 + addr` 这种实现对
  ②a 永远连错板。这里钉的就是"桥说了什么我们用什么"。
- **接入会停 RotorHazard 的板，不许由控制 API 自己开上游**（R21）。已经有人挂着是另一回事：
  那时 RH 已经停了，再多一个使用者不增加副作用。
- **`PROTOCOL RH` 不走这条路**。它改的是板卡归属，必须经 `POST /api/board/mode`，
  否则平台对"这块板现在归谁"就没有账。
- **板子的拒绝原样交出去**。`ERR …` 是板子说的话，不是传输故障，不许翻成 502。
- **命令是串行的**。同一块板上两条命令交错等答，就会互相偷对方的回复。

帧字节一律取自 `tests/golden/frames-v1.json`（TS 侧权威导出）：手写一段"我觉得对的 RAWIQ"
等于把协议镜像的第二份实现塞进测试里。
"""

import asyncio
import json
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Gateway
from app.services.dialer import FetchResult
from app.services.frame_parser import FRAME_RAWIQ, parse_rawiq_chunk
from app.services.ingest import reconcile
from app.services.ws_bridge import UpstreamClosed

GOLDEN = Path(__file__).parent / "golden" / "frames-v1.json"
if not GOLDEN.exists():
    pytest.skip(
        "golden/frames-v1.json 不存在：先跑 "
        "`node --experimental-strip-types rftrack/tools/export_golden_frames.mjs`",
        allow_module_level=True,
    )

CASES = {case["name"]: case for case in json.loads(GOLDEN.read_text())["cases"]}

HOST = "192.168.99.21"
BASE_URL = f"http://{HOST}:8080"
SERIAL_EP = "serial:/dev/widscan58-A"
SERIAL_ADDR = "/dev/widscan58-A"
UART_EP = "uart:/dev/serial0"
UART_ADDR = "/dev/ttyAMA0"
REMOTE_EP = "node:52a1d8"

ENDPOINTS = [
    {
        "id": SERIAL_EP,
        "kind": "serial",
        "label": "widscan58-A",
        "path": SERIAL_ADDR,
        "online": True,
        "busy": False,
    },
    {
        "id": UART_EP,
        "kind": "uart",
        "label": "ttyAMA0",
        "path": UART_ADDR,
        "online": True,
        "busy": False,
    },
    {"id": REMOTE_EP, "kind": "remote", "label": "52a1d8", "online": True, "busy": False},
]

#: 假板子对查询的默认回答，按固件自己的格式手写（`commands.c:449` 的 INFO、`:467` 的
#: send_status、`:562` 的 GAIN）。**故意不从实现的 `QUERY_PREFIX` 推**：那样 matcher 漏一条
#: 前缀时夹具会跟着漏，测试就成了自证。设置类命令一律 `OK`，与固件一致。
DEFAULT_REPLIES = {
    "INFO": [b"INFO widscan58|esp32s3|1|v1.0.0|2026-11-01|phy 1.0|aa:bb:cc:dd:ee:ff|12345\n"],
    "STATUS?": [b'STATUS {"running": true, "gain": 45}\n'],
    "GAIN?": [b"GAIN MANUAL 45 0 82\n"],
}


def frame(type_code: int, payload: bytes) -> bytes:
    head = bytes([0xA5, 0x52, type_code, 1]) + len(payload).to_bytes(2, "little")
    return head + payload + (zlib.crc32(head + payload) & 0xFFFFFFFF).to_bytes(4, "little")


def rawiq_chunks(name: str) -> list[bytes]:
    """golden 的分块序列 → 整帧字节。`hex` 用 `:` 分块（与 test_frame_parity 同一口径）。"""
    return [
        frame(FRAME_RAWIQ, bytes.fromhex(part))
        for part in CASES[name]["hex"].split(":")
    ]


class FakeTransport:
    """桥的 HTTP 面：只回答端点表。`post` 一旦被调用就是实现走错了路。"""

    def __init__(self, endpoints=None, *, error: str | None = None) -> None:
        self.endpoints = ENDPOINTS if endpoints is None else endpoints
        self.error = error
        self.calls: list[str] = []

    def fetch(self, url: str, timeout_s: float) -> FetchResult:
        self.calls.append(url)
        if self.error:
            return FetchResult(None, None, 0, self.error)
        return FetchResult(200, {"endpoints": self.endpoints}, 1)

    def post(self, url, payload, timeout_s):  # pragma: no cover - 走到这里就是断言失败
        raise AssertionError(f"命令不该走 HTTP POST：{url}")


class ScriptedUpstream:
    """一条假桥：收到命令行按脚本回字节，脚本没写的回 `OK`。

    脚本的值是**字节块列表**，所以"一帧拆成两次到达"与"根本没有回复"都表达得出来。
    """

    def __init__(self, url: str, script: dict) -> None:
        self.url = url
        self.script = script
        self.sent: list[bytes] = []
        self.closed: tuple[int, str] | None = None
        self._in: asyncio.Queue = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def send(self, payload: bytes) -> None:
        self.sent.append(payload)
        for chunk in self.chunks_for(payload.decode("utf-8", "replace").strip()):
            self._post(chunk)

    def chunks_for(self, line: str) -> list[bytes]:
        # `script[line] = []` 是"这条命令没有回复"，与"没写脚本"是两件事，所以不能用 `or`。
        return self.script[line] if line in self.script else DEFAULT_REPLIES.get(line, [b"OK\n"])

    async def recv(self) -> bytes:
        item = await self._in.get()
        if isinstance(item, UpstreamClosed):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self._post(UpstreamClosed(code, reason))

    def push(self, payload: bytes) -> None:
        self._post(payload)

    def _post(self, item) -> None:
        if self._loop is None:
            self._in.put_nowait(item)
        else:
            self._loop.call_soon_threadsafe(self._in.put_nowait, item)


class FakeFactory:
    def __init__(self) -> None:
        self.created: list[ScriptedUpstream] = []
        self.script: dict[str, list[bytes]] = {}

    async def connect(self, url: str) -> ScriptedUpstream:
        link = ScriptedUpstream(url, self.script)
        link._loop = asyncio.get_running_loop()
        self.created.append(link)
        return link


@dataclass
class Rig:
    app: object
    factory: FakeFactory
    transport: FakeTransport
    nodes: dict = field(default_factory=dict)
    gateway_id: int = 0
    site_id: int = 0
    client: object = None

    def send(self, command: str, node: str = "serial", **kwargs):
        return self.client.post(
            f"/api/nodes/{self.nodes[node]['node_id']}/command",
            json={"command": command, **kwargs},
        )

    def sessions(self) -> list[dict]:
        return self.app.state.bridge.status()["sessions"]

    def upstream(self, index: int = 0) -> ScriptedUpstream:
        return self.factory.created[index]


def quiet_settings(db_path, **overrides) -> Settings:
    """后台循环全关：这一轮测的是命令通路，不是拨号/采集/保留自己的节拍。"""
    return Settings(
        db_path=db_path,
        dialer_enabled=False,
        retention_purge_enabled=False,
        capture_enabled=False,
        plugins_enabled=False,
        alert_engine_enabled=False,
        target_engine_enabled=False,
        **overrides,
    )


def make_rig(db_path, *, instance_role="site", transport=None, factory=None, **overrides) -> Rig:
    """层级与节点行由一个**站点角色**的 app 写进库，再用被测角色的 app 去读。

    与 `test_ws_bridge.make_rig` 同一条理由：汇聚实例对写请求一律 405，拿被测 app 自己
    搭脚手架会死在夹具里，那是角色闸门不是命令通路的问题。
    """
    transport = transport if transport is not None else FakeTransport()
    factory = factory if factory is not None else FakeFactory()
    seed = create_app(quiet_settings(db_path), transport=FakeTransport(), upstream_factory=FakeFactory())
    with TestClient(seed) as client:
        site = client.post("/api/sites", json={"name": "主场", "slug": "home"}).json()
        relay = client.post("/api/relays", json={"site_id": site["site_id"], "name": "起点塔"}).json()
        gateway = client.post(
            "/api/gateways",
            json={
                "relay_id": relay["relay_id"],
                "name": "pi-a",
                "kind": "usb-bridge",
                "base_url": BASE_URL,
            },
        ).json()
        with seed.state.db.scope() as session:
            reconcile(
                session,
                session.get(Gateway, gateway["gateway_id"]),
                ENDPOINTS,
                Settings(max_nodes_per_gateway=8),
            )
        listed = client.get(f"/api/nodes?gateway={gateway['gateway_id']}").json()

    app = create_app(
        quiet_settings(db_path, instance_role=instance_role, **overrides),
        transport=transport,
        upstream_factory=factory,
    )
    rig = Rig(app, factory, transport, gateway_id=gateway["gateway_id"], site_id=site["site_id"])
    for row in listed:
        kind = row["transport"]["kind"]
        key = {"usb-native": "serial", "uart-pl011": "uart", "node-ws": "remote"}[kind]
        rig.nodes[key] = row
    rig.client = TestClient(app)
    return rig


@pytest.fixture
def rig(db_path):
    made = make_rig(db_path)
    with made.client as client:
        made.client = client
        yield made


def wait_for(predicate, *, cap: float = 3.0) -> bool:
    """条件轮询。TestClient 的 portal 在另一条线程上跑，所以这里等的是真状态不是 sleep。"""
    started = time.monotonic()
    while not predicate():
        if time.monotonic() - started > cap:
            return False
        time.sleep(0.01)
    return True


def detail(response) -> dict:
    body = response.json()
    return body["detail"] if isinstance(body, dict) and "detail" in body else body


# --- 一条命令的来回 -----------------------------------------------------------


def test_a_query_comes_back_with_the_boards_own_reply_and_its_json(rig):
    payload = {"running": True, "gain": 45, "gain_max": 82}
    rig.factory.script["STATUS?"] = [f"STATUS {json.dumps(payload)}\n".encode()]

    response = rig.send("STATUS?")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["reply"] == f"STATUS {json.dumps(payload)}"
    assert body["data"] == payload
    assert body["node_id"] == rig.nodes["serial"]["node_id"]
    assert body["ep"] == SERIAL_EP
    assert body["command"] == "STATUS?"


def test_a_setter_reaches_the_board_verbatim_with_a_newline(rig):
    """固件按 `\\n` 才派发一行（`commands.c` 的 `line_byte`），少了它就是永远等不到回复。"""
    response = rig.send("GAIN MANUAL 45")

    assert response.status_code == 200, response.text
    assert rig.upstream().sent == [b"GAIN MANUAL 45\n"]
    body = response.json()
    assert (body["ok"], body["reply"], body["data"]) == (True, "OK", None)


def test_an_err_from_the_board_is_itself_not_a_transport_failure(rig):
    rig.factory.script["GAIN MANUAL 999"] = [b"ERR gain_range\n"]

    response = rig.send("GAIN MANUAL 999")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["reply"] == "ERR gain_range"


def test_a_command_with_no_answer_times_out_with_its_own_reason(rig):
    rig.factory.script["SCAN START"] = []

    response = rig.send("SCAN START")

    assert response.status_code == 504, response.text
    assert detail(response)["reason"] == "command_timeout"
    assert rig.upstream().sent == [b"SCAN START\n"]


def test_a_reply_split_across_reads_is_still_one_reply(rig):
    rig.factory.script["STATUS?"] = [b"STATUS {\"gain\"", b": 45}\n"]

    body = rig.send("STATUS?").json()

    assert body["data"] == {"gain": 45}


def test_unsolicited_frames_before_the_reply_do_not_end_the_command(rig):
    """板子在跑的时候 LEVELS 一直在出：把"第一帧"当回复会让每条命令都答非所问。"""
    levels = frame(6, bytes.fromhex(CASES["levels_mode_1"]["hex"]))
    rig.factory.script["RECAL"] = [levels, b"OK\n"]

    body = rig.send("RECAL").json()

    assert (body["ok"], body["reply"]) == (True, "OK")


# --- 端点只能问桥 -------------------------------------------------------------


def test_the_uart_endpoint_is_the_bridges_id_not_a_prefix_on_the_address(rig):
    """`addr=/dev/ttyAMA0` 而 `ep=uart:/dev/serial0`：猜前缀的实现会连到不存在的端点。"""
    response = rig.send("STATUS?", node="uart")

    assert response.status_code == 409, response.text  # 没人挂着 → 不许自己开（R21）
    assert detail(response)["reason"] == "rh_would_stop"
    assert rig.factory.created == [], "拒绝的时候不许已经开过上游"


def test_a_remote_node_is_addressed_by_its_own_id_without_asking_the_table(rig):
    response = rig.send("INFO", node="remote")

    assert response.status_code == 200, response.text
    assert response.json()["ep"] == REMOTE_EP
    assert parse_qs(urlsplit(rig.upstream().url).query) == {"ep": [REMOTE_EP]}
    assert rig.transport.calls == [], "node-ws 的地址就是端点 id，不必再敲一次端点表"


def test_an_address_the_bridge_does_not_list_is_refused_by_name(db_path):
    rig = make_rig(db_path, transport=FakeTransport(endpoints=[ENDPOINTS[1], ENDPOINTS[2]]))
    with rig.client:
        response = rig.send("STATUS?")

    assert response.status_code == 409, response.text
    body = detail(response)
    assert body["reason"] == "endpoint_not_found"
    assert body["addr"] == SERIAL_ADDR
    assert rig.factory.created == []


def test_a_gateway_without_base_url_keeps_the_existing_reason(db_path):
    rig = make_rig(db_path)
    with rig.client:
        with rig.app.state.db.scope() as session:
            session.get(Gateway, rig.gateway_id).base_url = None
        response = rig.send("STATUS?")

    assert response.status_code == 409, response.text
    assert detail(response)["reason"] == "no_base_url"


@pytest.mark.parametrize("error", ["dns", "refused", "timeout"])
def test_an_unreachable_bridge_says_which_transport_failed(db_path, error):
    rig = make_rig(db_path, transport=FakeTransport(error=error))
    with rig.client:
        response = rig.send("STATUS?")

    assert response.status_code == 502, response.text
    assert detail(response)["reason"] == error


def test_a_bridge_that_drops_the_upstream_says_so_with_its_own_reason(rig):
    class DroppingFactory(FakeFactory):
        async def connect(self, url):
            raise OSError("connection refused")

    rig.app.state.bridge.factory = DroppingFactory()

    response = rig.send("STATUS?")

    assert response.status_code == 502, response.text
    assert detail(response)["reason"] == "upstream_OSError"


# --- 拒绝：角色、协议归属、命令形状 -------------------------------------------


def test_the_aggregator_instance_refuses_to_command(db_path):
    rig = make_rig(db_path, instance_role="aggregator")
    with rig.client:
        response = rig.send("SCAN START")

    assert response.status_code == 405, response.text
    assert detail(response)["reason"] == "aggregator_read_only"
    assert rig.factory.created == []


def test_protocol_rh_is_refused_here_and_points_at_the_board_mode_endpoint(rig):
    response = rig.send("PROTOCOL RH")

    assert response.status_code == 409, response.text
    body = detail(response)
    assert body["reason"] == "protocol_rh_forbidden"
    assert "/api/board/mode" in body["detail"]
    assert rig.factory.created == []


def test_protocol_monitor_is_allowed(rig):
    assert rig.send("PROTOCOL MONITOR").status_code == 200


def test_an_unknown_node_is_404(rig):
    response = rig.client.post("/api/nodes/999999/command", json={"command": "INFO"})

    assert response.status_code == 404
    assert detail(response)["reason"] == "not_found"


@pytest.mark.parametrize(
    "command",
    ["", "   ", "INFO\nREBOOT", "INFO\rREBOOT", "INFO\x00", "INFO é", "INFO\t"],
)
def test_a_command_that_is_not_one_ascii_line_is_refused_before_the_board(rig, command):
    response = rig.send(command)

    assert response.status_code == 422, response.text
    assert detail(response)["reason"] == "command_invalid"
    assert rig.factory.created == []


def test_a_command_longer_than_the_firmware_line_buffer_is_refused(rig):
    """`cmd_port_t.line[128]` 且 `used + 1 < sizeof` → 126 字符是上限，超了固件回
    `ERR command_length`。在这里拦住是为了不白跑一趟，理由里要带上那个数。"""
    response = rig.send("CHANNELS " + "R1=5658 " * 20)

    assert response.status_code == 422, response.text
    body = detail(response)
    assert body["reason"] == "command_invalid"
    assert body["limit"] == 126
    assert rig.send("CHANNELS " + "R1=5658 " * 12).status_code == 200


def test_control_disabled_is_refused_with_its_own_reason(db_path):
    rig = make_rig(db_path, control_enabled=False)
    with rig.client:
        response = rig.send("INFO")

    assert response.status_code == 409, response.text
    assert detail(response)["reason"] == "control_disabled"


# --- RAWIQ：整快照，缺块作废 --------------------------------------------------


def test_rawiq_chunks_are_assembled_into_one_snapshot(rig):
    chunks = rawiq_chunks("rawiq_snapshot_assembled")
    rig.factory.script["RAWIQ 5677000 2500"] = chunks

    body = rig.send("RAWIQ 5677000 2500").json()

    assert body["ok"] is True
    assert body["rawiq"] == CASES["rawiq_snapshot_assembled"]["parsed"][-1]
    assert body["reply"] is None


def test_a_missing_rawiq_chunk_voids_the_whole_snapshot(rig):
    """最后一块照样到达，但中间少了一块：不许拼出半张混了旧数据的图。"""
    chunks = rawiq_chunks("rawiq_snapshot_assembled")
    assert parse_rawiq_chunk(chunks[-1][6:-4])["offset"] > 0
    rig.factory.script["RAWIQ 5677000 2500"] = [chunks[0], chunks[-1]]

    body = rig.send("RAWIQ 5677000 2500").json()

    assert body["ok"] is False
    assert body["reason"] == "rawiq_incomplete"
    assert body["rawiq"] is None


def test_rawiq_can_be_answered_by_an_err_like_any_other_command(rig):
    rig.factory.script["RAWIQ 1 2"] = [b"ERR rawiq_busy\n"]

    body = rig.send("RAWIQ 1 2").json()

    assert (body["ok"], body["reply"], body["rawiq"]) == (False, "ERR rawiq_busy", None)
    # 拒绝就是拒绝：不许被 rawiq_incomplete 抢走（那会说成"缺块、重抓"，误导运营者）。
    assert body.get("reason") != "rawiq_incomplete"
    assert "分块" not in (body.get("detail") or "")


def test_rawiq_err_uart_port_is_a_refusal_not_a_missing_chunk(rig):
    """无线/UART 接入的 head 带不了 RAWIQ：板子回 ERR uart_port，是干净拒绝而非缺块。"""
    rig.factory.script["RAWIQ 5899000 8192"] = [b"ERR uart_port\n"]

    body = rig.send("RAWIQ 5899000 8192").json()

    assert (body["ok"], body["reply"], body["rawiq"]) == (False, "ERR uart_port", None)
    assert body.get("reason") != "rawiq_incomplete"
    assert "分块" not in (body.get("detail") or "")


# --- 会话：与页面共用、串行、闲了交还 -----------------------------------------


def test_two_commands_to_one_board_reuse_a_single_upstream(rig):
    assert rig.send("STATUS?").status_code == 200
    assert rig.send("GAIN MANUAL 45").status_code == 200

    assert len(rig.factory.created) == 1, "一条命令开一把钥匙 = 每条命令复位一次板子"
    assert rig.upstream().sent == [b"STATUS?\n", b"GAIN MANUAL 45\n"]
    assert rig.sessions()[0]["holders"] == 1


def test_the_control_session_hands_the_board_back_when_it_goes_idle(db_path):
    """没人再发命令就把端点交还：桥靠这次断开释放串口并把 RotorHazard 起回来。"""
    rig = make_rig(db_path, control_idle_release_s=0.05, control_sweep_interval_s=0.02)
    with rig.client:
        assert rig.send("INFO").status_code == 200
        link = rig.upstream()
        assert wait_for(lambda: link.closed is not None), "闲了却没交还端点"
        assert link.closed[0] == 1000
        assert wait_for(lambda: rig.sessions() == [])


def test_a_command_keeps_the_session_alive_while_the_operator_is_using_it(db_path):
    rig = make_rig(db_path, control_idle_release_s=0.3, control_sweep_interval_s=0.02)
    with rig.client:
        for _ in range(4):
            assert rig.send("INFO").status_code == 200
            time.sleep(0.1)
        assert rig.upstream().closed is None
        assert len(rig.factory.created) == 1


def test_health_reports_the_control_section(rig):
    section = rig.client.get("/api/health").json()["control"]

    assert section["enabled"] is True
    assert section["holders"] == 0
    assert rig.send("INFO").status_code == 200
    after = rig.client.get("/api/health").json()["control"]
    assert after["holders"] == 1
    assert after["commands"] == 1


# --- 与页面同板：这一段必须在同一个事件循环里 ---------------------------------


def loop_rig(db_path, **overrides):
    """不进 TestClient 的一套：`Page` 直接驱动 ASGI，两条连接落在同一个循环里。"""
    return make_rig(db_path, **overrides)


class Page:
    """一个浏览器标签页（与 test_ws_bridge 同一手法，理由见那里的文件头）。"""

    def __init__(self, app, url: str) -> None:
        path, _, query = url.partition("?")
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "subprotocols": [],
            "root_path": "",
        }
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._app = app
        self._task = None

    async def __aenter__(self):
        self._task = asyncio.create_task(self._app(self.scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        return self

    async def __aexit__(self, *exc):
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, 3.0)

    async def send_bytes(self, payload: bytes) -> None:
        await self._to_app.put({"type": "websocket.receive", "bytes": payload})

    async def receive(self, cap: float = 3.0) -> dict:
        return await asyncio.wait_for(self._from_app.get(), cap)

    async def wait_accepted(self) -> None:
        assert (await self.receive())["type"] == "websocket.accept"


async def until(predicate, *, cap: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    started = loop.time()
    while not predicate():
        if loop.time() - started > cap:
            return False
        await asyncio.sleep(0.01)
    return True


def page_url(rig, ep: str) -> str:
    return f"/ws/bridge/{ep}?gateway={rig.gateway_id}"


def test_a_page_and_the_api_share_one_upstream_and_one_board(db_path):
    rig = loop_rig(db_path)

    async def scenario():
        control = rig.app.state.control
        async with Page(rig.app, page_url(rig, SERIAL_EP)) as page:
            await page.wait_accepted()
            assert await until(lambda: len(rig.factory.created) == 1)
            link = rig.upstream()
            assert await until(lambda: rig.sessions()[0]["subscribers"] == 1)
            rig.factory.script["STATUS?"] = [b"STATUS {\"gain\": 7}\n"]
            result = await control.send(rig.nodes["serial"]["node_id"], "STATUS?")
            assert result["data"] == {"gain": 7}
            assert len(rig.factory.created) == 1, "控制不许开第二条上游"
            # 页面照样收得到板子的字节：控制没有把透传挤掉。板子对命令的回复本身也扇出
            # 给页面（它也是这块板的读者），所以先收到那条回复，再收到后面推进来的字节。
            assert (await page.receive())["bytes"] == b'STATUS {"gain": 7}\n'
            link.push(b"\x42")
            assert (await page.receive())["bytes"] == b"\x42"
        # 页面走了，但 API 还握着这条端点：不许交还（交还就是关串口，开回来会复位板子）。
        assert link.closed is None
        await control.stop()
        assert link.closed == (1000, "no more pages")

    asyncio.run(scenario())


def test_a_uart_board_already_held_by_a_page_can_be_commanded(db_path):
    """RH 已经因为那个页面停了，再多一个使用者不增加副作用——这时不许再拒。"""
    rig = loop_rig(db_path)

    async def scenario():
        control = rig.app.state.control
        async with Page(rig.app, page_url(rig, UART_EP)) as page:
            await page.wait_accepted()
            assert await until(lambda: len(rig.factory.created) == 1)
            assert await until(lambda: rig.sessions()[0]["subscribers"] == 1)
            assert parse_qs(urlsplit(rig.upstream().url).query) == {"ep": [UART_EP]}
            result = await control.send(rig.nodes["uart"]["node_id"], "INFO")
            assert result["ok"] is True
            # 这一句钉的是**反查**：addr 是 /dev/ttyAMA0，而桥的端点 id 是 uart:/dev/serial0。
            # 拼前缀的实现会在这里给出另一个 ep，并为此开第二条上游。
            assert result["ep"] == UART_EP
            assert len(rig.factory.created) == 1
        await control.stop()

    asyncio.run(scenario())


def test_the_api_holding_a_board_does_not_let_a_leaving_page_reset_it(db_path):
    rig = loop_rig(db_path)

    async def scenario():
        control = rig.app.state.control
        await control.send(rig.nodes["serial"]["node_id"], "INFO")
        link = rig.upstream()
        async with Page(rig.app, page_url(rig, SERIAL_EP)) as page:
            await page.wait_accepted()
            assert await until(lambda: rig.sessions()[0]["subscribers"] == 1)
        assert await until(lambda: rig.sessions()[0]["subscribers"] == 0)
        assert link.closed is None, "API 还握着，页面走了不许交还端点"
        assert len(rig.factory.created) == 1
        await control.stop()

    asyncio.run(scenario())


def test_two_commands_in_flight_are_serialised_not_interleaved(db_path):
    """两条命令同时等答会互相偷回复：第二条必须等第一条**收完回复**才发出去。

    慢要慢在**板子回答**上，不能慢在 `send` 里：`send` 是在 `write_lock` 内跑的，
    在那里睡等于让写锁替命令锁把两条命令串行化了——去掉命令锁测试照样绿。
    只断言"发出的先后"同样是瞎的，所以把回复投递也记进同一条轨迹。
    """
    rig = loop_rig(db_path)

    async def scenario():
        control = rig.app.state.control
        trace: list[str] = []

        class SlowUpstream(ScriptedUpstream):
            async def send(self, payload):
                self.sent.append(payload)
                line = payload.decode().strip()
                trace.append(f"send:{line}")
                asyncio.get_running_loop().call_later(0.05, self._answer, line)

            def _answer(self, line):
                for chunk in self.chunks_for(line):
                    self._post(chunk)
                trace.append(f"reply:{line}")

        class SlowFactory(FakeFactory):
            async def connect(self, url):
                link = SlowUpstream(url, self.script)
                link._loop = asyncio.get_running_loop()
                self.created.append(link)
                return link

        slow = SlowFactory()
        rig.app.state.bridge.factory = slow
        rig.factory = slow  # 换了工厂就要跟着换读数的那只手，否则断言读的是空列表
        node_id = rig.nodes["serial"]["node_id"]
        first, second = await asyncio.gather(
            control.send(node_id, "STATUS?"), control.send(node_id, "GAIN?")
        )
        assert first["reply"] == 'STATUS {"running": true, "gain": 45}'
        assert second["reply"] == "GAIN MANUAL 45 0 82"
        assert trace == [
            "send:STATUS?",
            "reply:STATUS?",
            "send:GAIN?",
            "reply:GAIN?",
        ]
        assert rig.upstream().sent == [b"STATUS?\n", b"GAIN?\n"]
        await control.stop()

    asyncio.run(scenario())


def test_the_control_session_feeds_the_capture_observer(db_path):
    """控制会话也是采集的数据源：它开着的时候板子的字节同样要进采集层。"""
    rig = loop_rig(db_path)
    seen: list[bytes] = []

    class Observer:
        def tap(self, link, payload):
            seen.append(payload)

        def untap(self, link):
            pass

    rig.app.state.bridge.observer = Observer()

    async def scenario():
        control = rig.app.state.control
        rig.factory.script["INFO"] = [b"INFO widscan58 x\n"]
        await control.send(rig.nodes["serial"]["node_id"], "INFO")
        assert seen == [b"INFO widscan58 x\n"]
        await control.stop()
        assert await until(lambda: rig.sessions() == [])

    asyncio.run(scenario())


def test_stop_hands_back_every_board_the_api_holds(db_path):
    """收摊必须交还端点：进程停了而串口还被占着，RotorHazard 就再也起不来。"""
    rig = loop_rig(db_path)

    async def scenario():
        control = rig.app.state.control
        await control.send(rig.nodes["serial"]["node_id"], "INFO")
        await control.send(rig.nodes["remote"]["node_id"], "INFO")
        assert len(rig.factory.created) == 2
        await control.stop()
        assert all(link.closed is not None for link in rig.factory.created)
        assert rig.sessions() == []

    asyncio.run(scenario())