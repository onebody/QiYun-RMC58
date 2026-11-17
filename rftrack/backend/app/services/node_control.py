"""节点级命令下发：站点工作台里那套"单模块接入点控制"的写侧。

C-8 把这一轮留到了 sticky 单写者之后（`docs/superpowers/plans/2026-11-03-widscan58-p0-stage1-audit.md`
§4 第 19 条）。这里就是那一轮，形状由五条口径决定：

- **不自己开第二条上游**。命令走 `BridgeHub` 已有的那条会话（`acquire`/`release`），
  所以页面与 API 共用一把钥匙、共用一份字节流，采集层也照样看得见。开 USB tty 会复位
  板子，"一条命令开一次"等于每按一个按钮就重启一次板。
- **端点只能问桥**。`transport_addr` 与桥的 `ep` 不是同一个串：uart 条目的 `id` 用未解析的
  `--rh-serial`、`path` 是 `realpath` 之后的 `ttyAMA0`（`web/serve.mjs:375`）。拼前缀猜地址
  对 ②a 永远连错板，所以这里反查桥自己的端点表。
- **会停 RotorHazard 的板不由自己开上游**（R21）。已经有人挂着是另一回事：那时 RH 已经停了，
  再多一个使用者不增加副作用。
- **板子的拒绝原样交出去**。`ERR …` 是板子说的话，不是传输故障；`PROTOCOL RH` 是唯一被这里
  拦下的命令，因为它改的是板卡归属，必须经 `POST /api/board/mode` 才有账。
- **一块板上的命令串行**。两条命令同时等答会互相偷回复，所以每端点一把命令锁；它与
  `write_lock` 不是一回事——后者管"一次 send 完整发完"，前者管"一问一答不许交错"。

命令本身不做动词白名单：固件自己会用 `ERR command` 回答不认识的字（`commands.c`），
而"对端说了什么比我们猜的可靠"正是 `ws_bridge` 已经在用的那条判据。这里只拦**形状**
（单行、可打印 ASCII、不超过固件行缓冲）与那一条改归属的命令。
"""

from __future__ import annotations

import asyncio
import json
import logging
from time import monotonic
from typing import Any, Callable

from fastapi import HTTPException, status

from ..config import Settings
from ..database import Database
from ..models import Gateway, Node
from .capabilities import stops_rotorhazard
from .dialer import ENDPOINTS_PATH, bridge_url, classify
from .frame_parser import (
    FRAME_RAWIQ,
    RAWIQ_HDR,
    FrameStream,
    RawIqAssembler,
    StreamItem,
    parse_rawiq_chunk,
)
from .hierarchy import conflict, not_found, unprocessable
from .ingest import endpoint_addr
from .ws_bridge import BridgeHub, Link, Refused, UpstreamClosed, bridge_ws_url

LOG = logging.getLogger("rftrack.node_control")

#: 固件行缓冲是 `char line[128]` 且 `used + 1 < sizeof`（`commands.c` 的 `cmd_port_t`），
#: 所以 126 字符是上限，超了它回 `ERR command_length`。在这里拦住是为了不白跑一趟。
COMMAND_LIMIT = 126

#: 查询类命令的回复前缀。镜像 `web/src/serial/device.ts` 的 `replyMatcher`，另外补上
#: `LEVELFILTER?`/`FFTTYPE?`/`PHYIDLE?` 三条——TS 那张表没有它们，而固件确实按这些前缀
#: 回答（`commands.c:490/667/698`），漏了就是每条查询白等一个超时。
QUERY_PREFIX = {
    "INFO": "INFO ",
    "PROFILE?": "PROFILE ",
    "STATUS?": "STATUS ",
    "CONFIG?": "CONFIG ",
    "GAIN?": "GAIN ",
    "TIMING?": "TIMING ",
    "SPURS?": "SPURS ",
    "ENABLE?": "ENABLE ",
    "PROTOCOL?": "PROTOCOL ",
    "METRICS?": "METRICS ",
    "GPIO?": "GPIO ",
    "LEVELFILTER?": "LEVELFILTER ",
    "FFTTYPE?": "FFTTYPE ",
    "PHYIDLE?": "PHYIDLE ",
    "SYNC": "SYNC ",
}


def validate_command(raw: str) -> str:
    """形状校验。**不做归一化**：发出去的就是操作员打的那一行加一个 `\\n`。

    固件按 `\\n` 才派发一行、遇到 `\\r` 直接丢（`commands.c` 的 `line_byte`），而中继镜像
    还会把非 ASCII 的整帧当二进制透传给 head——所以控制字符与非 ASCII 必须在这里就拦住。
    """
    limit = COMMAND_LIMIT
    if not raw or not raw.strip():
        raise unprocessable("command_invalid", detail="命令不能为空", limit=limit)
    if len(raw) > limit:
        raise unprocessable(
            "command_invalid",
            detail=f"命令最长 {limit} 字符（固件行缓冲），超了板子回 ERR command_length",
            limit=limit,
            length=len(raw),
        )
    if any(not 0x20 <= ord(ch) <= 0x7E for ch in raw):
        raise unprocessable(
            "command_invalid", detail="命令必须是单行可打印 ASCII", limit=limit
        )
    if raw.split()[:2] == ["PROTOCOL", "RH"]:
        raise conflict(
            "protocol_rh_forbidden",
            detail="板卡归属切换走 POST /api/board/mode?gateway=…，不走命令通路",
        )
    return raw


def reply_matcher(command: str) -> Callable[[StreamItem], bool]:
    """哪一条回复算这条命令的答复。内容前缀判定，与 TS 侧同一套。"""
    word = command.split(" ")[0]

    def is_err(item: StreamItem) -> bool:
        return item.kind == "text" and item.line.startswith("ERR")

    if word == "RAWIQ":
        def rawiq(item: StreamItem) -> bool:
            if item.kind == "frame" and item.type == FRAME_RAWIQ:
                if len(item.payload) < RAWIQ_HDR.size:
                    return False
                chunk = parse_rawiq_chunk(item.payload)
                return chunk["offset"] + chunk["count"] >= chunk["n"]
            return is_err(item)

        return rawiq
    if word == "NET":
        # NET 一族两种回复形状：查询答 `NET …`，设置答 OK/ERR。给设置等 `NET ` 会每条白等 3 s。
        query = command in ("NET STATUS", "NET SCAN")

        def net(item: StreamItem) -> bool:
            if item.kind != "text":
                return False
            if query:
                return item.line.startswith("NET ") or is_err(item)
            return item.line == "OK" or item.line.startswith("OK ") or is_err(item)

        return net
    prefix = QUERY_PREFIX.get(word)
    if prefix:
        return lambda item: (item.kind == "text" and item.line.startswith(prefix)) or is_err(item)
    return lambda item: item.kind == "text" and (
        item.line == "OK" or item.line.startswith("OK ") or item.line.startswith("ERR")
    )


def json_payload(line: str) -> Any:
    """`STATUS {…}` / `CONFIG {…}` / `NET STATUS {…}` 里那段 JSON。认不出就是 None，不猜。"""
    start = line.find("{")
    if start < 0:
        return None
    try:
        return json.loads(line[start:])
    except ValueError:
        return None


class NodeControl:
    """命令通路的调度核心：一块板一条会话、一把命令锁、一个闲了交还的持有者。"""

    def __init__(self, db: Database, settings: Settings, hub: BridgeHub, transport) -> None:
        self.db = db
        self.settings = settings
        self.hub = hub
        self.transport = transport
        self.commands = 0
        self.timeouts = 0
        self.failures = 0
        self.last_error: str | None = None
        self._held: dict[tuple[int, str], Any] = {}
        self._used: dict[tuple[int, str], float] = {}
        self._locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._tables: dict[int, tuple[list[dict], float]] = {}
        self._task: asyncio.Task | None = None

    # --- 起停 ----------------------------------------------------------------

    async def start(self) -> None:
        if not self.settings.control_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        """收摊必须交还每一块板：进程停了而串口还占着，RotorHazard 就再也起不来。"""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for key, session in list(self._held.items()):
            self._held.pop(key, None)
            self._used.pop(key, None)
            await self.hub.release(session)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.control_sweep_interval_s)
            try:
                await self.release_idle()
            except Exception as exc:  # 收摊失败不许带走整个循环，下一拍再试
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                LOG.warning("control idle sweep failed: %s", exc)

    async def release_idle(self) -> None:
        now = monotonic()
        for key, session in list(self._held.items()):
            lock = self._locks.get(key)
            if lock is not None and lock.locked():
                continue  # 正在发命令的端点不算闲
            if now - self._used.get(key, now) < self.settings.control_idle_release_s:
                continue
            self._held.pop(key, None)
            self._used.pop(key, None)
            await self.hub.release(session)

    def status(self) -> dict:
        return {
            "enabled": self.settings.control_enabled,
            "holders": len(self._held),
            "commands": self.commands,
            "timeouts": self.timeouts,
            "failures": self.failures,
            "last_error": self.last_error,
            "idle_release_s": self.settings.control_idle_release_s,
        }

    # --- 一条命令 -------------------------------------------------------------

    async def send(self, node_id: int, command: str) -> dict:
        if not self.settings.control_enabled:
            raise conflict(
                "control_disabled",
                detail="这台实例关掉了命令下发（RFTRACK_CONTROL_ENABLED=0）",
            )
        line = validate_command(command)
        with self.db.scope() as session:
            node = session.get(Node, node_id)
            if node is None:
                raise not_found("node", node_id)
            gateway = session.get(Gateway, node.gateway_id)
            if gateway is None:
                raise not_found("gateway", node.gateway_id)
        ep = self._endpoint_for(node, gateway)
        key = (gateway.gateway_id, ep)
        if stops_rotorhazard(node.transport_kind, node.stops_rh) and self.hub.session_for(*key) is None:
            raise conflict(
                "rh_would_stop",
                node_id=node.node_id,
                gateway_id=gateway.gateway_id,
                ep=ep,
                detail="接入这块板会停掉这台接入机的 RotorHazard 计时：先在单机 Console 里挂上它，"
                "或换用不停计时的接入方式",
            )
        link = Link(gateway_id=gateway.gateway_id, ep=ep, url=bridge_ws_url(gateway, ep))
        lock = self._locks.setdefault(key, asyncio.Lock())
        started = monotonic()
        async with lock:
            try:
                session = await self._session_for(key, link)
                self._used[key] = monotonic()
                item, snapshot = await self._exchange(session, line)
            except HTTPException as exc:
                self.failures += 1
                self.last_error = str(exc.detail)
                raise
            except Refused as refused:
                # 连不上桥。理由用桥/工厂自己的那一个（`upstream_OSError`），不翻成"失败"。
                self.failures += 1
                self.last_error = refused.reason
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    {
                        "reason": refused.reason,
                        "detail": f"接不上这块板的上游：{refused.reason}",
                        "node_id": node.node_id,
                        "ep": ep,
                    },
                ) from None
            except UpstreamClosed as closed:
                self.failures += 1
                self.last_error = closed.reason or "upstream_gone"
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    {
                        "reason": "upstream_gone",
                        "detail": closed.reason or "上游在命令途中断开",
                        "node_id": node.node_id,
                        "ep": ep,
                    },
                ) from None
            finally:
                self._used[key] = monotonic()
        self.commands += 1
        return self._result(node, gateway, ep, line, item, snapshot, started)

    async def _session_for(self, key, link: Link):
        """复用已有的那条会话；没有（或上游自己没了）才握一个新的持有者。"""
        current = self.hub.session_for(*key)
        session = self._held.get(key)
        if session is not None and session is current:
            return session
        session = await self.hub.acquire(link)
        self._held[key] = session
        return session

    async def _exchange(self, session, line: str) -> tuple[StreamItem | None, dict | None]:
        stream = FrameStream()
        assembler = RawIqAssembler()
        inbox: asyncio.Queue = asyncio.Queue()
        snapshot: dict | None = None

        def tap(payload: bytes) -> None:
            nonlocal snapshot
            for item in stream.push(payload):
                if item.kind == "frame" and item.type == FRAME_RAWIQ:
                    assembled = assembler.add(item.payload)
                    if assembled is not None:
                        snapshot = assembled
                inbox.put_nowait(item)

        tap_id = self.hub.add_tap(session, tap)
        try:
            async with session.write_lock:
                try:
                    await session.upstream.send(line.encode("ascii") + b"\n")
                except UpstreamClosed:
                    raise
                except Exception as exc:
                    raise Refused(f"upstream_{type(exc).__name__}") from exc
            match = reply_matcher(line)
            deadline = monotonic() + self.settings.control_timeout_s
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise self._timeout(line, session)
                try:
                    item = await asyncio.wait_for(inbox.get(), remaining)
                except asyncio.TimeoutError:
                    raise self._timeout(line, session) from None
                if match(item):
                    return item, snapshot
        finally:
            self.hub.remove_tap(session, tap_id)

    def _timeout(self, line: str, session) -> HTTPException:
        self.timeouts += 1
        return HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            {
                "reason": "command_timeout",
                "detail": f"板子在 {self.settings.control_timeout_s:g} s 内没有回答 {line!r}",
                "command": line,
                "ep": session.link.ep,
                "timeout_s": self.settings.control_timeout_s,
            },
        )

    def _result(self, node, gateway, ep, line, item, snapshot, started) -> dict:
        reply = item.line if item is not None and item.kind == "text" else None
        out = {
            "node_id": node.node_id,
            "gateway_id": gateway.gateway_id,
            "ep": ep,
            "command": line,
            "ok": True,
            "reply": reply,
            "data": json_payload(reply) if reply else None,
            "rawiq": snapshot,
            "elapsed_ms": round((monotonic() - started) * 1000),
        }
        if reply is not None and reply.startswith("ERR"):
            # 板子明确拒绝（如 UART 接入回 ERR uart_port）：这是拒绝，不是缺块。
            # 必须先于 rawiq_incomplete 判，否则会把"这条通路带不了 RAWIQ"说成
            # "分块丢了、重抓一次"——而重抓在同一条通路上永远不可能成功。
            out["ok"] = False
        elif line.split(" ")[0] == "RAWIQ" and snapshot is None:
            # 最后一块到了但拼不出整快照：缺块就作废，绝不交半张混了旧数据的图。
            # 判据是**快照在不在**，不是"有没有文本回复"——抓成功时本来就没有文本回复。
            out["ok"] = False
            out["reason"] = "rawiq_incomplete"
            out["detail"] = "RAWIQ 分块缺了中间一块，整快照已作废：重抓一次"
        return out

    # --- 端点反查 -------------------------------------------------------------

    def _endpoint_for(self, node: Node, gateway: Gateway) -> str:
        if node.transport_kind == "node-ws":
            return node.transport_addr  # ③ 的远端节点：地址就是桥的端点 id
        entries = self._endpoint_table(gateway)
        found = self._match_addr(entries, node.transport_addr)
        if found is None:
            # 手里这份表答不出这条地址：立刻重取一次，不等 TTL。板子刚插回来时
            # 干等 30 s 就是"点了没反应"，与 capture 的 `BridgeEndpointResolver` 同一条理由。
            entries = self._endpoint_table(gateway, force=True)
            found = self._match_addr(entries, node.transport_addr)
        if found is None:
            raise conflict(
                "endpoint_not_found",
                node_id=node.node_id,
                gateway_id=gateway.gateway_id,
                addr=node.transport_addr,
                detail="桥的端点表里没有这个地址：板子可能拔了，或桥重启后换了名字",
            )
        return found

    @staticmethod
    def _match_addr(entries: list[dict], addr: str) -> str | None:
        for entry in entries:
            try:
                if endpoint_addr(entry) == addr:
                    return str(entry["id"])
            except HTTPException:
                continue  # 一条认领不了不许带走整张表
        return None

    def _endpoint_table(self, gateway: Gateway, *, force: bool = False) -> list[dict]:
        cached = self._tables.get(gateway.gateway_id)
        if cached is not None and not force:
            if monotonic() - cached[1] < self.settings.capture_endpoint_ttl_s:
                return cached[0]
        result = self.transport.fetch(
            bridge_url(gateway, ENDPOINTS_PATH), self.settings.probe_timeout_s
        )
        ok, reason, detail, endpoints = classify(result)
        if not ok:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                {"reason": reason, "detail": detail, "gateway_id": gateway.gateway_id},
            )
        self._tables[gateway.gateway_id] = (endpoints, monotonic())
        return endpoints