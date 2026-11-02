# 烧录后链路自检（Link Check）设计

日期：2026-11-01
状态：已确认（方案 A：后端服务 + 工作台按钮 / 一次一只端点 / 不抢占写权 / 报告不落库）
需求来源：用户在 S3+C5 双板真机接入实测（任务 #99）之后要求「烧录后全链路测试验证一个」。

## 1. 目标

烧完固件之后，在工作台里按一次就能知道**这块板在这条链上活到了第几环**，并且拿得到逐项证据。

八个环节一条链跑完：固件身份 → 桥可见 → 后端有节点 → MONITOR 有流 → 帧解得干净 → 点落进库 → 判定/告警/目标在算 → 板子回到 RH。

它替代的是这次实测里的手工序列（起桥 → 起后端 → 建拓扑 → 开一条 WS 持有端点 → 发 `PROTOCOL MONITOR` → 收 60 s → 离线解帧 → 手工还原）。这类"每次都要按同样六下才得出一个证据"的手法按既有纪律做成产品内入口，不留在 `/tmp` 脚本里。

非目标：

- **不代做烧录**。烧录仍走原 `web/src/serial/flasher.ts`（Web Serial/esptool-js）或本机 esptool。自检假定"已经烧完"，第一环用 `INFO` 反证烧进去的那份确实起来了。
- **不改 `web/serve.mjs` 去声明能力位**。节点行的 `levels/spectrum/rawiq` 与 `fw_version` 仍是 `undeclared`/null，这是方案 §7 S2 偏差①，属另行阶段。
- **不做多端点批量**（一次一只，两块板点两次）、**不把报告写进数据库**、**不给 rf-track 线加刷写入口**。
- `web/` 与 `firmware/` 保持零改动。

## 2. 决策摘要

| 决策 | 选择 | 理由 |
|---|---|---|
| 入口形态 | 后端服务 + 工作台「链路自检」面板 tab | 只有产品内入口能让现场的人自己点出来；纯 CLI 日常按不到 |
| 串口通路 | **复用 `BridgeHub` 现有上游**，自检作为一条"内部页"加入 | 桥对同一端点只放一把钥匙；另开一条上游必然 `busy`，且自检看到的字节就该是页面看到的字节 |
| 写权冲突 | **不抢占**：写权已被别的页占着就如实 `fail: writer_busy` | 抢占能"一次点成功"，但会静默打断别人正在做的事 |
| 时长模型 | 异步：`POST` 立刻回 `run_id`，`GET` 读逐项进度 | 逐项进度就是这功能的价值；同步等 30 s 只能给一坨结果，卡在第 4 环时看不出来 |
| 每环状态 | 四态 `pass / fail / gap / skipped` | `gap` 是已申报缺口（不许当新 bug 追），`skipped` 是前序 fail 传染（不许留空当通过） |
| 报告留存 | 进程内有界队列（最近 10 份）+ 每次一行 LOG | 不落库就不需要迁移，也就不与 `signal_event.source` 只有 `real/mock/demo` 的口径打架；代价（重启即清）在报告里明写 |
| 时间窗 | 请求参数 `seconds`，硬边界 2–30，越界 422 | 短于 2 s 采不满一个摘要桶，长于 30 s 就是让板子离开计时太久 |

## 3. 八个环节与判据

`overall` 只看必需环：任一必需环 `fail` → `overall=fail`；`gap` 与 `skipped` 不改判但必须显示。

| # | key | 必需 | 判据（`pass` 的条件） | 证据字段 |
|---|---|---|---|---|
| 1 | `firmware_identity` | 是 | 收到以 `INFO widscan58|` 开头的文本行，且 `PROTOCOL?` 有应答 | `info`（原样串）、`protocol_before` |
| 2 | `bridge_endpoint` | 是 | `/api/endpoints?gateway=` 的表里该端点 `online=true` | 表里的整行 |
| 3 | `node_present` | 是 | `/api/nodes?site=` 里能按 `(gateway_id, transport_addr)` 找到对应节点行 | `node_id`、档位号 |
| 4 | `monitor_stream` | 是 | 发 `PROTOCOL MONITOR` 后 ≤2 s 内从自己队列收到 ≥1 块二进制 | `chunks`、`bytes`、`bytes_per_s` |
| 5 | `frame_parse` | 是 | 该端点 `crc_errors` 与 `resync_bytes` 差值均为 0，且帧类型计数 ≥2 种非零 | 按类型计数（metrics/spectrum/telemetry/…） |
| 6 | `storage` | 是 | 该端点 `points_stored` 差值 > 0，且 `/api/samples/summary` 该节点出现本次窗口的新桶 | `points_delta`、`buckets_new` |
| 7 | `rules_chain` | 是 | 插件运行时 `errors` 差值为 0；启用插件的 `findings` 差值 > 0 | 插件/告警/目标三节计数 |
| 8 | `restored` | 是 | `PROTOCOL?` 回 `PROTOCOL RH`，且 hub `status()` 里这条端点会话已不在 | `ack`、`sessions_after` |

三条固定的 `gap`（常驻显示，不参与 `overall`）：

- **能力位与版本未申报**：`levels/spectrum/rawiq = undeclared`、`fw_version = null`。桥的端点表就是这么设计的（§7 S2 偏差①），第一环读到的 `INFO` 串是本次自检给的替代证据，但它**不回写节点行**。
- **连接窗口归属丢失**：`unattributed` / `pending_dropped` 非零是 `capture._attribute()` 的既有语义（有界），本次跑出的数值只报不判。
- **告警与目标为 0**：只在**该场地没有任何 `alert_rule`** 时报 `gap`（并给出建规则的提示），有规则却 0 条则按 `fail` 追。

## 4. 架构

### 4.1 `BridgeHub` 的内部页句柄（唯一需要改既有模块的地方）

`rftrack/backend/app/services/ws_bridge.py` 现在只把 `WebSocket` 当读者（`_Session.readers` 的值只被 `_fan_out` 用 `send_bytes` 调一次，ws_bridge.py:307–316）。加一条 duck-type 的同款接口，不动三条既有口径（一条端点一条上游、只读扇出、文本帧不透传）：

```python
@dataclass
class EndpointHandle:
    """自检用的内部页：加入同一条上游，收这块板的字节，发命令走同一把写者规则。"""
    page: int
    frames: asyncio.Queue[bytes]
    async def send_bytes(self, payload: bytes) -> None   # _fan_out 唯一的调用面
    async def send_command(self, payload: bytes) -> None # 先 claim 写权，再送上游
    async def recv(self, timeout: float) -> bytes        # 超时抛 ReadTimeout
    async def release(self) -> None                      # 等价于"这一页走了"

async def open_handle(self, link: Link) -> EndpointHandle: ...
```

要点：

- `open_handle` 复用 `_join`（ws_bridge.py:233）——首连串行、跨端点并行那套语义不变；句柄存在期间这条上游就是**唯一**上游，页面若此时连进来只会成为同一上游的另一个读者，不会 `busy`。
- `send_command` 用与 `_client_loop` 完全相同的写者判据（ws_bridge.py:260–263）：`writer is None` 才 claim，否则抛 `Refused(BUSY)`。**不加"强制接管"参数**。
- `release()` 走 `_leave`（ws_bridge.py:321）：最后一页离开才关上游、才撤 observer、才把串口还给桥并由桥起回 RH。自检成功与否都必须经过这里，见 §7。
- 句柄不进 `readers` 的失败即摘除逻辑之外：它是内存队列，`send_bytes` 不会失败；若队列积压（下游消费者被卡）按**有界队列丢最旧**并记 `frames_dropped`，绝不反压上游。

改动规模预计 +40/−0，且 `web/serve.mjs` 一行不改。

### 4.2 `services/link_check.py`（新）

```python
class LinkCheck:
    def __init__(self, db, settings, hub, capture, plugins, alerts, targets, transport): ...
    async def run(self, req: LinkCheckRequest, report: LinkCheckReport) -> LinkCheckReport
```

- 每一步只做三件事：取证据 → 写 `report.steps[key]` → 记时间。任何一步抛异常都进 `except`，把那一步记 `fail` 并把后续未跑的记 `skipped`，然后进 `finally` 执行还原。
- 环 5/6 的计数取自 `capture` 的**按端点**运行态（`_Endpoint.stream` 的 `crc_errors/resync_bytes/bytes_in`，capture.py:147），跑前跑后各读一次做差值。差值出现负数说明期间会话重建、流已换新，此时该环记 `gap` 并写 `counters_source: "stream_reset_mid_run"`，不拿一个看起来像 0 的数糊过去。全局计数只作为 `evidence.global_counters` 附上，不当判据。
- `plugins/alerts/targets` 三个引擎只**读** `status()`，不启动、不停止、不改配置。
- 无迁移、无新表、无新配置键（`seconds` 是请求参数不是配置项——这是"跑一次"的动作参数，不是场地的长期设定）。

### 4.3 `routers/link_check.py`（新）+ 挂载

三个端点，挂法与 S9 的 archive router 一样只在 `routers/__init__.py` 与 `main.py` 各加两行：

| 端点 | 作用 | 角色闸 |
|---|---|---|
| `POST /api/link-check` | 起跑，立刻回 `{run_id}`；同一端点已在跑则 409 `already_running` | aggregator → 405（与那 21 个写端点同口径） |
| `GET /api/link-check/{run_id}` | 读单项报告（含进行中态） | 读，任意角色 |
| `GET /api/link-check?site=` | 读最近 ≤10 份的摘要列表 | 读，任意角色 |

`site` 归属由 `gateway_id → relay → site` 推出，跨场地取别人的报告按现有口径 404。错误 `reason` 全部走全站统一的 `{"reason": …, "detail": …}`：新增只有 `endpoint_required`、`seconds_out_of_range`、`already_running` 三个，`gateway_not_found`（ws_bridge.py:140）与 `aggregator_read_only`（S4 那 21 个写端点已在用）复用既有词，不另发明同义词。

## 5. 报告数据结构

```jsonc
{
  "run_id": "lc-20261101-153012-11201",
  "site_id": 1, "gateway_id": 1, "endpoint": "serial:/dev/cu.usbmodem11201",
  "node_id": 6, "seconds": 10,
  "started_at": "…", "finished_at": "…", "elapsed_s": 12.4,
  "overall": "pass",                      // pass | fail | running
  "restored": true,                        // 与环 8 同值，单列出来给状态栏用
  "steps": [
    {"key": "firmware_identity", "label": "固件身份", "state": "pass",
     "evidence": {"info": "widscan58|esp32c5|1|…", "protocol_before": "PROTOCOL RH"},
     "note": null}
  ],
  "gaps": [{"key": "capabilities_undeclared", "label": "能力位未申报", "since": "§7 S2 偏差①"}],
  "counters": {
    "chunks": 3671, "bytes": 369880, "bytes_per_s": 36988.0,
    "frames": {"metrics": 2390, "spectrum": 480, "telemetry": 40},
    "crc_errors_delta": 0, "resync_bytes_delta": 0,
    "points_delta": 19120, "buckets_new": 10,
    "plugin_errors_delta": 0, "findings_delta": 34,
    "alerts_created": 2, "targets_created": 3,
    "unattributed_delta": 1240, "pending_dropped_delta": 0,
    "counters_source": "per_endpoint_stream_delta"
  },
  "retention": "进程内最近 10 份，实例重启即清"
}
```

`steps[].state` 只有 `pending / running / pass / fail / gap / skipped` 六个值，前端按值配色，**`gap` 不得长得像 `fail`**（既有的 `unsupported` 口径延伸）。

## 6. 前端入口

`rftrack/frontend/workbench/app.js` 右侧面板现在是两个 tab（「属性 / 布局默认」，app.js:665–670）。加第三个「链路自检」：

1. 接入机下拉（`api.gateways(site)`）→ 端点下拉（`api.endpoints(gatewayId)`，app.js:156）→ 秒数（默认 10，边界与后端一致）。
2. 「开始自检」先前置确认（文案见 §8），确认后 `POST`，然后 2 s 一轮 `GET` 刷八行。
3. 每行：状态徽标 + 一句话判据 + 可展开的证据（`INFO` 串、计数、原始 ack）。
4. 底部：`overall` 一行结论 + 「复制报告 JSON」。
5. 面板只显示**所选端点这一次**（或它上次跑完的）报告，不做时间线堆叠；`GET /api/link-check?site=` 那份有界历史是给 `curl` 取证与阶段报告用的，面板不渲染它（两块板点两次就够，多份对比属 YAGNI）。

面板不是栅格组件：不进布局、不参与 `refresh_ms` 节拍、不接 `/ws/live`。所以 `registry.js` 与组件测试的既有对账不受影响。

`api.js` 新增三行方法（`linkCheckStart` / `linkCheckRun` / `linkCheckList`），`store.js` 不动。

## 7. 还原纪律（最要紧的一条正确性要求）

自检把板子从 RH 计时里拉出来，所以**任何出口都必须还原**，包括异常出口：

```python
handle = await hub.open_handle(link)
try:
    …发 MONITOR、收数、判各环…
finally:
    try:
        await handle.send_command(b"PROTOCOL RH\r\n")
        report.steps["restored"] = ack 里出现 "PROTOCOL RH" 才 pass
    finally:
        await handle.release()   # 关上游 → 桥释放串口 → 桥起回 RH
```

- `release()` 在 `finally` 的最内层，`send_command` 抛异常也走得到。
- 环 8 `fail`（`restore_failed`）时报告顶部红字写"这块板可能还停在 MONITOR，请手动发 `PROTOCOL RH` 或重插 USB"，并**照常**给出已取得的证据——不能因为还原失败就把前面七环的证据扣掉。
- 变异门必须能抓住"把 `try/finally` 拆平"和"写权改成无条件覆盖"两种走形，见 §9。

## 8. 前置确认文案（三条，点自检必然看见）

1. **这块板会离开 RotorHazard 计时 2–30 s**——正在计时的比赛里别点。
2. 期间产生的是**真实**采样，会照常进摘要桶与保留期（不是 DEMO/MOCK）。
3. `serial:` 原生 USB 靠固件 `PROTOCOL MONITOR/RH` 切换；`uart:` 端点由 `serve.mjs` 连带停/起 RH（serve.mjs:484）。两条机制不同，报告会分别写明本次走的是哪条。

## 9. 测试与验收

**基线（不许退步）**：`pytest` 587 passed/1 skipped · workbench `node --test` 214 · 浏览器冒烟 65。

- **后端** `tests/test_link_check.py`，复用 `tests/test_ws_bridge.py:126` 的假上游工厂，脚本化一条假桥：答 `INFO`/`PROTOCOL?`、切 MONITOR 后按节奏吐合成帧块。逐条判据：八环各自 pass、`writer_busy` 时第 1 环 fail 且**不动**上游、`seconds` 越界 422、aggregator 405、`already_running` 409、`skipped` 传染、`gap` 不拉低 `overall`、零规则时 `rules_chain=gap`、计数出现负差值时 `counters_source=stream_reset_mid_run`、留存队列有界（第 11 份挤掉第 1 份）、**中途抛异常仍还原并 release**。
- **变异门** 三条：拆掉 `try/finally`、写权改成无条件覆盖、把差值判据改成 `>= 0`（让"会话重建"蒙混成 pass）——三条都必须被现有用例抓住。
- **前端** `node --test`：八行渲染、六态配色类名区分（`gap` 与 `fail` 不同类）、进行中不重复提交、复制 JSON 内容与 `overall` 一致。
- **冒烟** `rftrack/frontend/e2e/workbench.mjs` 加 1–2 条：夹具环境里没有真桥，点自检断言报告渲染出来且第 2 环如实 `fail: bridge_endpoint`（这条同时证明"自检不会在有桥之外谎报"）。
- **真机一轮**：C5 与 S3 现在就在本机（11201 / 11301），各跑一次，取 `overall=pass` 且八环齐全的报告作「已通过真实硬件测试」的证据；产物落 `/tmp`，报告文本附在阶段报告里。

诚实口径照旧六档：本轮改动完成度按「已通过软件测试 / 已通过真实硬件测试」分别申报，**没真机跑过就不写"已通过真实硬件测试"**。

## 10. 文件清单

| 文件 | 动作 | 职责 |
|---|---|---|
| `rftrack/backend/app/services/ws_bridge.py` | 改 | `EndpointHandle` + `open_handle`，写者判据共用 |
| `rftrack/backend/app/services/link_check.py` | 新建 | 八环执行器、报告装配、有界留存 |
| `rftrack/backend/app/routers/link_check.py` | 新建 | 三端点 + 角色闸 + reason 枚举 |
| `rftrack/backend/app/routers/__init__.py`、`app/main.py` | 改 | 挂 router、`app.state.link_check` |
| `rftrack/backend/tests/test_link_check.py` | 新建 | §9 的后端条目 |
| `rftrack/frontend/workbench/app.js` | 改 | 面板第三 tab |
| `rftrack/frontend/workbench/api.js` | 改 | 三个调用口 |
| `rftrack/frontend/workbench/test/*.test.mjs` | 改/新建 | 面板判定 |
| `rftrack/frontend/e2e/workbench.mjs` | 改 | 冒烟 1–2 条 |
| `docs/rf-track/RF_Track_落地实施方案与计划.md` | 改 | 进度头加一句；§7 阶段表在 S9 之后**新增一行「S9-6 烧录后链路自检」**（出口判据=§9 的真机一轮）；§9 风险登记新增 **R30**（现最大号是 R29，已核对）：自检类功能会短暂占用单写者端点，判据必须包含"异常出口也还原" |

## 11. 落地边界

- 分支 `rf-track`，**不合入 main**；`origin`（CapnBry/widscan58）永不推。推 `fork` 前按对象逐 blob 脱敏扫描，正则含 `192\.168\.31`；本设计文档与实现里的样例网关地址一律 `192.168.99.*`（`rftrack/**`）或 `203.0.113.x`（`docs/**`）。
- 三份 `docs/*-ChatGPT采集.md` 保持未跟踪，不进任何提交。
- 三门槛从仓库根跑（后端测试在 `rftrack/backend/`）。
- 实现完成后：先给阶段报告（11 项模板），等确认再谈提交与推送。

相关：`docs/rf-track/RF_Track_落地实施方案与计划.md`、`docs/architecture/target-stack-landing.md`、`docs/superpowers/specs/2026-10-26-rh-uart-bridge-design.md`（`uart:` 端点停/起 RH 的出处）。
