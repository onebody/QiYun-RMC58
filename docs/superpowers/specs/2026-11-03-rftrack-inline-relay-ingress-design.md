# RF Track 内置 Wi-Fi 接入面（inline relay ingress）—— 设计

日期：2026-11-03 · 分支基线：`fw-wifi-relay` @ `7ba3f2f`（平台代码与 `rf-track` 同源） · 状态：**待用户审阅**（本文件落盘时实现代码零行）

## 0. 目标与两条缺口

用户 2026-11-02 原话：**「widscan58 原有的 web 页面的功能都没有啊？S3 也不是 WIFI 接入的啊，这两个都需要调整修改」**。

这两句各自指向一个真实缺口，且根因不同：

| 缺口 | 表象 | 根因（一手取证） |
|---|---|---|
| ① 原 web 页面功能缺失 | 页面由后端发出时端点列表永远为空、连不上任何板 | 页面对同源的三条假设全部成立在 `serve.mjs` 的形状上：`GET /api/endpoints` 不带参（`web/src/main.ts:691`）、`WS /serial?ep=`（`main.ts:793`）、条目形状 `{id,kind,label,online,busy,scannable?}`（`main.ts:652-661`）。后端这两条都不匹配：`/api/endpoints` 的 `?gateway=` 必填、缺参 422（`tests/test_bridge_proxy.py:89`），而页面里 `if (!r.ok) return;`（`main.ts:692`）把失败**静默吞掉**；`/serial` 路由后端不存在，只有要 `?gateway=` 的 `/ws/bridge/{ep}`（`resolve_link` 的 `gateway_required`）。 |
| ② 平台表达不出"这台是 Wi-Fi 接入" | 节点显示 `node-ws`、桥只报 `kind:"remote"`、`NET STATUS` 的 STA/head 事实进不了数据模型 | `TRANSPORT_BY_KIND` 把 `remote` 一律映射成 `node-ws`（`services/ingest.py:34-38`），介质信息在这一句被擦平；`NET STATUS` 只有被命令才答，且答的是 ASCII 文本（`firmware/src/net/`），模型里没有任何一张表能放它。 |

三轮澄清已把范围钉死（用户逐项选定）：

1. **后端自己收 Wi-Fi** —— ③ 成为一等接入形态，不再依赖手工起 `serve.mjs`。
2. **后端成为唯一入口** —— 原 web 页面也连后端；`serve.mjs` 退为只管 ① 本地 USB / ② UART。
3. **白名单准入** —— 未登记 id 直接 close 并留可查 reason。
4. **架构取方案 C（一台机器、两张脸）** —— 同一个后端进程既接节点上行（`/node`）也发页面与透传（`/app/` + `/serial?ep=`），字节管道复用现成的 `BridgeHub`。

本设计**只覆盖平台侧**（`repo/rftrack/backend/` + `repo/web/` 一行 URL 拼接 + `deploy/` 可选卷）。固件零改动：`firmware/src/net/ws_link.c:431` 的 `dial_and_handshake()` 用的 host/port/path 来自 NVS 里存的整条 URL，把中继从指 8011 改成指后端端口是一次 `NET URL` 配置写入，不重烧。

## 1. 数据模型与白名单准入

### 1.1 内置接入机：一行 seed，不放开约束

`Node.gateway_id` 是 NOT NULL + FK RESTRICT（`app/models.py:204-206`），`Gateway.relay_id` 同样是（`models.py:175-177`）。**不动约束**，改为启动时 seed 一条固定链：

| 层 | seed 值 | 备注 |
|---|---|---|
| site | 沿用部署首场地（现网 `site_id=1`） | 已存在 |
| relay | `name="本机无线接入"` | 一个进程只有一条，见下方 YAGNI |
| gateway | `name="wifi-relay-host"`、`kind="inline"`、`base_url=NULL` | `kind` 是裸 `String(16)` 无枚举约束（`models.py:179`），加值零迁移 |

`base_url=NULL` 今天会被 `probe_gateway` 打成 `no_base_url` 409（`services/dialer.py:165-166`），所以它必须走 §1.3 的分派而不是 HTTP probe——这是"不放开约束"的代价，也是它不该被 HTTP probe 的原因。

**YAGNI**：不做多台 inline gateway。一个后端进程只有一张 Wi-Fi 脸；多场地仍按已裁定的「每场地一套实例 + 局域网只读汇聚」走。

**seed 必须幂等**：重复启动/重复迁移不得重复插行（判据见 §4.2-10）。

### 1.2 准入：白名单 + 拒入留痕

两张新表，一次 Alembic 迁移。

`relay_enrollment` —— 谁是允许的节点

| 列 | 类型 | 说明 |
|---|---|---|
| `enroll_id` | PK | |
| `node_key` | `String(64)` NOT NULL unique | 中继 `?id=` 里那个词，也是 `transport_addr` 去掉 `node:` 前缀的部分 |
| `label` | `String(120)` | 对账后进 `Node.display_name` |
| `relay_id` | FK NOT NULL | 条目天生属于某个子场地，调用方不必每次传 |
| `enabled` | Boolean default true | 见 §3.4 的"能不能连 vs 能不能上屏" |
| `note` / `created_at` | | |

`relay_denial` —— 谁被挡在门外

| 列 | 类型 | 说明 |
|---|---|---|
| `denial_id` | PK | |
| `node_key` | `String(64)` | 空 id 也记，落 `missing_node_id` |
| `reason` | `String(32)` | 闭集：`unregistered_node` / `enrollment_disabled` / `missing_node_id` |
| `remote_addr` | `String(45)` | 见 §4.2-12 的脱敏口径 |
| `seen_at` | DateTime | |

写入策略：**每次连接尝试都记一行（append-only），但同一 `node_key` 在 60 s 窗口内只记一行。**

- 为什么要时间线而不是 upsert：中继重启或换 AP 后会反复重连，"第一次被拒"和"最后一次被拒"是两个不同的排查结论，做成 upsert 就丢了这条时间线。
- 为什么要在 60 s 处收口：`/node` 是无鉴权入口，未知 id 洪水会直接把这张表吹大。跨分钟的重连仍然逐窗口留痕，洪水被限住。
- 膨胀仍由既有 retention 清理兜底，不新写清理器。

API（**前缀定成 `/api/relay-nodes`**，因为 `/api/relays` 已被子场地三级 CRUD 占用——`routers/relays.py:27-70` 的 `GET/POST /relays`、`GET/DELETE /relays/{relay_id}` 是 site→relay→gateway 里的那个 relay，与 Wi-Fi 中继不是一回事，复用同名会做出两套语义的 `GET /api/relays/…`）：

- `GET/POST /api/relay-nodes`、`DELETE /api/relay-nodes/{node_key}` —— 白名单 CRUD
- `GET /api/relay-nodes/denied` —— 拒入时间线
- `POST /api/relay-nodes/{node_key}/status` —— 手动催一次 `NET STATUS`（§2.3）

替代方案（env 里配逗号分隔名单）**否掉**：页面上看到拒绝记录要能直接点成"给它登记上"，env 名单不能回写、还多一次重启。

### 1.3 端点表怎么喂进 `reconcile()`：不另发明状态机

新文件 `services/relay_hub.py` 在内存维护 registry，并**按 `serve.mjs` `/api/endpoints` 的原形状**产出：

```python
{"id": f"node:{node_key}", "kind": "relay", "label": label,
 "online": connected, "busy": writer_held, "gateway_id": INLINE_GATEWAY_ID}
```

三条口径：

1. **白名单里但没连上的条目照样列出，`online=false`。** `reconcile()` 已有的三态 `active/offline/lost`（`ingest.py:41-43`）就足以表达"待认领 / 已认领"，不新增状态位；且因为它每轮都在表里，不会被 `lost = [addr for addr in known if addr not in seen]`（`ingest.py:150`）误判成 `lost`。
2. **`dialer._probe()` 加分派**：`gateway.kind == "inline"` 时不调 HTTP，直接取 `relay_hub.endpoints()`，仍走同一个 `classify()` / `reconcile()`。`ingest.py` 开头那句"端点表是本系统唯一的节点事实来源"保持不变——换的是实现，不是契约。
3. **`TRANSPORT_BY_KIND` 加 `"relay": "wifi-relay"`**（`ingest.py:34-38`）；旧的 `remote → node-ws` **原样保留**，`serve.mjs` 那条 ①/② 路不受影响，历史行不变。`stops_rh` 实现时核对 `capabilities.stops_rotorhazard()` 按介质名的判法，预期 `wifi-relay` 为 False。

### 1.4 Wi-Fi 事实进数据模型：独立小表，不塞进 `Node`

`relay_link`（1 行 = 1 个已登记节点的最新状态）：`node_key` PK、`sta_state`、`head_baud`、`head_bytes_up`、`head_lost_down`、`out_drops`、`queue_permille`、`uptime_s`、`sampled_at`。

不写进 `Node` 的理由：这些数一秒一变，混进 `reconcile()` 会把 `updated` 流水账刷爆（`ingest.py:137-139` 是逐字段比较），还会把"声明"（寿命=小时）和"遥测"（寿命=秒）焊在一张表上。

IP / BSSID **不落库**（脱敏约束）；要表达可达性就用 `sta_state` + `sampled_at`。

读侧：`GET /api/nodes` 只对 `transport_kind="wifi-relay"` 的行附带一份 `relay_link`，工作台与 `/app/` 页面各自渲染成 NET 卡片。

## 2. 上行管道与遥测

### 2.1 `/node` 监听落在后端进程里

新增 WS 路由 `GET /node?id=<key>`，由 FastAPI 自己接。语义照抄 `web/serve.mjs:393-425` 的 `attachNode`，四条不变：

| 情况 | 行为 | 关闭理由 |
|---|---|---|
| `id` 不合 `^[A-Za-z0-9_.:-]{1,64}$` | 先 accept 再关 | `bad-id` |
| 未登记 / 已禁用 | 先 accept 再关 + 落 `relay_denial` | `unregistered_node` / `enrollment_disabled` |
| 同 id 重复接入 | 旧的收 `replaced`，旧页面收 `node reconnected` | — |
| 节点断开 | 扇出侧的页收 `node offline` | — |

"先 accept 再带理由关"是桥写明的做法（`serve.mjs:371-373` 注释：握手被拒在页面只剩一句"连不上桥"，而关闭理由能原样显示）。`REASON_LIMIT=100` 沿用 `ws_bridge.py:43`。

固件侧代价为零（见 §0 末段）。

### 2.2 字节管道复用 `BridgeHub`，只换上游的另一端

`Upstream` 协议只有三件事——`send` / `recv` / `close`（`ws_bridge.py:68-75`），而 `_pump`（273）、`_fan_out`（307）、写权锁（`_client_loop:260-263`）、`observer.tap`（289-293）全都只跟这三个方法打交道。所以：

- `relay_hub` 把已经握住的中继 socket 包成 `RelayUpstream`：收侧由 WS 接收循环喂一个内部队列供 `recv()`，发侧直接写 socket。
- `_join` 加分派：`link.url` 以 `inline://` 开头时不叫 `factory.connect()`，改用 `relay_hub` 交出的这条上游（`ws_bridge.py:240`）。
- `resolve_link()` 对 inline 接入机产出 `Link(gateway_id=<seed 那行>, ep="node:<key>", url="inline://node:<key>")`（`ws_bridge.py:143`）。

**一处必须改的行为**：inline 会话在节点 dial-in 那一刻就打开并挂上 capture 观察者，`_leave` 对这类会话**不因"最后一页走了"而拆上游**（`_Session` 加 `sticky` 位；对照 `ws_bridge.py:326` 那条注释——它拆上游是为了让桥释放串口并把 RotorHazard 起回来）。这与 BridgeHub 类注释里"观察者不开第二条上游、也不握着端点"相抵，理由要说清：那条规则服务于 ②a 不能把 RH 一直停着（R21）；③ 中继不占串口、不停 RH，握着它没有副作用，而"平台自己就是接入机、不依赖有人挂着页"正是这次改动的目的。

顺带解决现场看到的别扭局面：现在采集要靠手工跑 `m2_holder.py` 占住管道，M2 的 L2 窗口就是被这种手工性打断的。inline 之后 0 页也有采集。

两条路并存、**不去重**：`serve.mjs` 的 `/node` 保留（①/② 桥与模拟节点仍走它）。同一块物理板从两条路各来一次会落成两行——身份键是 `(gateway_id, transport_addr)`，两条链路就是两个事实，**不是 bug**，但要在文档里写明别按显示名合并（`ingest.py:6-7` 同一条理由）。

### 2.3 遥测采样：写权冲突的落点

`NET STATUS` 只有被命令才答，而命令通道就是那把写权钥匙（`_client_loop:260-263`：谁先发二进制帧谁是写者；`_leave:323-324`：写者走时释放）。三个候选：

| 方案 | 结论 |
|---|---|
| **A. 进程内的采样客户端** | ✅ 采用。后端每 `relay_status_poll_s`（默认 **10 s**）检查 `session.writer is None`；空闲时以**普通页身份**接进 `BridgeHub`（与今天 holder 走同一条代码路径），发 `NET STATUS`、把回读 ASCII 交给解析、然后主动断开释放写权。**零新锁语义。** 10 s 的依据：`NET STATUS` 是一问一答，命令回环真机实测 ≤500 ms（M2 L4），采样占空比 <5%，与 `dial_poll_interval_s` 默认 5.0 s 同数量级。 |
| B. 采样器常驻持写权 | ❌ 页面从此吃 `busy`，等于为了遥测把界面变成只读。 |
| C. 固件改成周期性主动打印 | ❌ 理由具体：中继管道是**裸字节流**，往里面插 ASCII 会让帧解析器 resync，而 `resync_bytes` / `crc_errors` 正是 M2 L2 判据在数的东西。为省一次轮询去污染门生指标不划算，且要改固件重烧两块板。 |

A 的真实代价必须摆在桌面上：**页面长期持有写权时，Wi-Fi 遥测会停更。** 处理不是兜底而是显式化——`relay_link.sampled_at` 保持旧值，UI 渲染成"上次采样 42 s 前（写权在页面上）"，并提供 `POST /api/relay-nodes/{key}/status` 让运营者手动重试（无人持写权时才成功）。这与 S11 的 R19「不得把陈旧值画成实时」是同一条口味。

### 2.4 准入在这一节的最后一道

白名单管**能不能连上**，`enabled` 管**能不能上屏**：一个 id 可以登记但暂不上屏，此时连接照样被拒（`enrollment_disabled`），但在 `/api/relay-nodes` 里可见、在 `/denied` 里有记录。这样"我登记了怎么没出现"有第二个答案（登记了但没启用 vs 根本没连进来），与拨号侧 `refused` 名单（`dialer.py:209-214`）同一个设计口味。

## 3. 页面别名——原 web 页面直接连后端

### 3.1 把"功能都没有"改成可定位的因果

页面功能一个都没少，是它问的那台机器答非所问：

| 页面的假设 | 出处 | 后端今天的答复 | 后果 |
|---|---|---|---|
| 同源有 `GET /api/endpoints`，无需参数 | `main.ts:691` | 422（`?gateway=` 必填） | `if (!r.ok) return` 静默 → 列表永远空 → 只显示"服务器没有板也没有中继节点" |
| 同源有 `WS /serial?ep=<id>` | `main.ts:793` | 404 | 桥接路径整条不通 |
| 条目形状 `{id,kind,label,online,busy,scannable?}` | `main.ts:652-661` | 字段更多但不缺这些 | 无需改页面即可渲染；`kind:"remote"` 正好渲染成「无线节点 <label>」（`main.ts:667`） |

第三行是好消息：③ 天生落在页面已有的 `remote` 语义上，`canScan = online && (kind!=='uart' || scannable)`（`main.ts:661`）对 `remote` 不要求 `scannable`。所以本节几乎不动页面逻辑，只补服务端两条别名。

### 3.2 两条别名

**`GET /api/endpoints`（不带 `?gateway=`）= 全场聚合**：每台非 inline 接入机各取自己那份，加上 inline 那行的 `relay_hub.endpoints()`，每条过一遍现成的 `advertise()`（`ingest.py:165-178`，补 `gateway_id` / `medium` / `stops_rh`）。显式带 `?gateway=` 时保持今天的严格 422/404 不变——只有"完全不带参"才新增聚合语义。

**`WS /serial?ep=<id>&gateway=<n>` = `/ws/bridge/{ep}` 的别名**：内部直接叫同一个 `BridgeHub.serve()`，不复制管道。

> 这里改了上一稿的想法，理由值得留档：原本想让"只有一台接入机时自动归属"，但 `resolve_link` 的注释就是为"默认第一台"写的禁令——多接入机并跑时同一个 `/dev/widscan58-2` 在两台机器上是两块不同的板，猜等于安静连错板（`ws_bridge.py:124-127`）。**列表可以多解，连接不能**：连错就是拿别人的板做写操作。所以定成：
> - 页面从 `advertise()` 的条目里本来就拿到了 `gateway_id`，`connectBridge()` 拼 URL 时带上它——**这是页面侧唯一改动，一行**（`main.ts:793`）；
> - 条目没有 `gateway_id`（同源是 `serve.mjs` 的老部署）时不带参，走 `serve.mjs` 原路不变。

### 3.3 页面从哪儿发出来

对称于已有的 `RFTRACK_WORKBENCH_DIR` → `/workbench`（`main.py:64-81`，含"没挂不算故障"的那节健康检查），新增 `RFTRACK_WEB_DIR` → 挂 `/app`。不占 `/`，两套页面互不覆盖；`deploy/` 加一个可选 `web/` 卷。

页面一旦由后端发出，`location.host` 就是后端，§3.2 的两条别名自动生效；`?bridge=`（`main.ts:925-930`）保持原义（指向另一台机器），GitHub Pages 上那份发布拓扑完全不动。

### 3.4 一个附带就修掉的真实体验差

`serve.mjs` 对同一个节点**只放一把钥匙**：`if (pages.has(ep)) return void socket.end(wsCloseFrame("busy"))`（`serve.mjs:382`）。这正是浏览器里看到「占用中」、以及必须手工跑 holder 才能保住采集的根因。走 inline + `BridgeHub` 之后是 N 读者 + 1 写者，"平台在采集"与"原页面在操作同一块板"第一次能同时成立。

### 3.5 `serve.mjs` 退到哪条线为止

| 留在 `serve.mjs` | 为什么不留给后端 |
|---|---|
| ① `serial:` 开本机串口（`bridgeSerial` + `SerialPort`） | Python 再写一套 pyserial，"谁在动串口"就变成两处事实 |
| ② `uart:` PL011 + 停/复 RotorHazard | 那是那台物理机上的命令串；形态已裁定（②a 直连、②b 适配器与 Z0 取消） |
| 浏览器 Web Serial 直连 | 本来就不经过服务端 |

**`/node` 与 `remote` 条目在 `serve.mjs` 保留不删**：真板改连后端，但模拟节点与旧部署还在用它；本设计要能回滚（§4.1），一刀切掉老路会让 ③ 一夜间断链。数据侧的区分靠 §1.3——`serve.mjs` 报的仍是 `node-ws`，inline 报的才是 `wifi-relay`。

## 4. 回滚与验收判据

### 4.1 一闸三断：`RFTRACK_RELAY_ENABLED`（默认 1）

开关只切三处，这样回滚是一个变量而不是三处改动：

| 切断电 | 效果 |
|---|---|
| `/node` 路由不注册 | 中继连不进来 |
| inline 三元组不 seed | `relay_hub.endpoints()` 无人消费，`_probe` 分派不会命中 |
| `GET /api/endpoints` 聚合剔除 inline 条目 | 页面回到"只见 `serve.mjs` 的世界" = 改动前行为 |

`/serial?ep=` 别名与 `RFTRACK_WEB_DIR` 挂载**不跟着关**：它们是既有 hub 与既有静态挂载的加法路径，跟着关只会让回滚后剩下的东西更难解释。

默认取 1，与 `dialer_enabled` / `capture_enabled` / `plugins_enabled`（`config.py:86,110,171`）全套现有旗标同风格；未登记任何 `node_key` 时它等于空转（列表里多一条"待认领"的 `offline` 行）。

判据：`RFTRACK_RELAY_ENABLED=0` 起一次实例 → 连接被拒、无 `wifi-relay` 行、§4.3 的五套门槛全绿，即"关掉就回到今天"。

### 4.2 新增判据（逐条可执行，全部进后端 pytest）

| # | 判据 | 怎么测 |
|---|---|---|
| 1 | 未登记 id 被拒且可查 | TestClient 连 `/node?id=ghost` → close reason `unregistered_node`；`GET /api/relay-nodes/denied` 有该行 |
| 2 | `id` 缺失/非法 | `missing_node_id` / `bad-id`，同样留痕 |
| 3 | 登记但禁用 | `enabled=false` → `enrollment_disabled` |
| 4 | 待认领是 `offline` 不是 `lost` | 登记后不连，跑两轮 reconcile：行存在、`status=offline`、不进 `lost` |
| 5 | 已认领 | 假中继 fixture dial-in → 下一轮 `active`、`transport_kind="wifi-relay"` |
| 6 | **0 页也有采集** | 节点在线、无任何 bridge/serial 客户端 → `/api/health` 的 `capture.frames` 仍增长 |
| 7 | N 读者 + 1 写者 | 两个 `/serial?ep=node:x` 同在不吃 `busy`；第三方发命令才吃 `busy` |
| 8 | 列表兼容 | 无参 `/api/endpoints` 每条含 `{id,kind,label,online,busy}`；带 `?gateway=` 仍严格 422/404 |
| 9 | 遥测让位可见 | 写权被占时 `sampled_at` 不许变新，`/api/nodes` 里陈旧位为真 |
| 10 | seed 幂等 | 同一空库连续两次启动/lifespan，`site/relay/gateway` 各只多一行 |
| 11 | 拒入限流 | 同 `node_key` 在 60 s 内洪水 → 只落一行；跨窗口再来才第二行 |
| 12 | 脱敏 | `/api/relay-nodes`、`/denied`、日志三处无 SSID/口令/公网 IP；`remote_addr` 只在局域网段且按既有规则渲染 |
| 13 | 回滚闸 | `RFTRACK_RELAY_ENABLED=0` 下 §4.1 那三条断言成立，其余门槛不受影响 |

### 4.3 五套门槛 + 两道真机门槛

- 数字只增不减：后端 **618 passed / 1 skipped**、工作台 **214**、浏览器冒烟 **65**、`web` **56**、`pio test -e native` **34**。**不许用 skip 换绿**，`pytest -rs` 复核那条 skip 的 reason 原样还在。
- 浏览器冒烟加一条真路径（"缺口① 修好了"唯一的直接证据）：打开后端发出的 `/app/` → 端点下拉出现「无线节点 <label>」→ 连上后**频谱必须真的画出来**、`badFrames` 不增长。
- 诚实边界：手测看到的"原页面连中继 → 等待频谱帧 + CRC/resync 增长"，假设是它由 `serve.mjs` 单消费者锁造成（holder 与页面抢同一条管道），inline 的多读者扇出应连带修掉。**若重跑仍复现，它是独立缺陷**：届时如实写"缺口① 只修了一半"并单开一条，不混进本次判据蒙过去。
- **L2 重跑**：上一轮窗口被 SIGTERM 打断，`m2-l2-t1.json` 随 backend 一起丢了，只能整轮重来；且必须等本次平台改动落地后再跑（任何重启都毁窗口）。判据不变：窗口 ≥1800 s，`endpoint_stats` 里 `node:s3-relay` 的 `crc_errors` 与 `resync_bytes` **增量为 0**，KiB/s 记进收尾实况。inline 之后不再需要 holder，窗口内唯一变量就是中继自己。
- **L3 断网重连**仍需把 AP 断电 60 s；**L6 真机回退**仍未执行。

### 4.4 明确不做

- `gateway_id` 自动归属——理由见 §3.2。
- 固件周期主动上报 `NET STATUS`——理由见 §2.3-C。
- 多台 inline 接入机、per-node 鉴权令牌：`node_key` 就是凭据位；要真鉴权另开一轮，不混进这次。
- `serve.mjs` 的 `/node` 与 `remote` 条目下线：见 §3.5。

## 5. 落地后的文件清单（供实现计划拆任务）

| 动作 | 文件 |
|---|---|
| 新增 | `rftrack/backend/app/models.py`（`relay_enrollment` / `relay_denial` / `relay_link` 三张表） |
| 新增 | `rftrack/backend/alembic/versions/007_inline_relay.py`（现有最大号为 `006_targets`，`007_aggregate` 属 S11 尚未建） |
| 新增 | `rftrack/backend/app/services/relay_hub.py`（`/node` 侧：准入、registry、`RelayUpstream`、采样客户端） |
| 修改 | `rftrack/backend/app/services/ws_bridge.py`（`inline://` 分派、`_Session.sticky`、`resolve_link` 认 inline） |
| 修改 | `rftrack/backend/app/services/dialer.py`（`_probe` 对 `kind=="inline"` 走 `relay_hub.endpoints()`） |
| 修改 | `rftrack/backend/app/services/ingest.py`（`TRANSPORT_BY_KIND` += `"relay": "wifi-relay"`） |
| 修改 | `rftrack/backend/app/routers/bridge.py`（无参 `GET /api/endpoints` 聚合） |
| 新增 | `rftrack/backend/app/routers/relay_nodes.py`（`/api/relay-nodes` 白名单 CRUD + `/denied` + `/{key}/status`；前缀理由见 §1.2——`/api/relays` 已被 site→relay→gateway 的子场地 CRUD 占用） |
| 新增 | `rftrack/backend/app/routers/nodes.py` 附带 `relay_link` |
| 修改 | `rftrack/backend/app/main.py`（`/node` WS、seed、`RFTRACK_WEB_DIR` → `/app`） |
| 修改 | `rftrack/backend/app/config.py`（`relay_enabled`、`relay_status_poll_s`、`RFTRACK_WEB_DIR`） |
| 修改 | `web/src/main.ts:793`（拼 URL 带上条目里的 `gateway_id`，一行） |
| 新增 | `rftrack/backend/tests/test_inline_relay_ingress.py`（§4.2 逐条） |
| 修改 | `rftrack/frontend/e2e/workbench.mjs`（`/app/` 真路径那条冒烟） |
| 修改 | `rftrack/deploy/`（可选 `web/` 卷 + `RFTRACK_RELAY_ENABLED` 说明） |

## 6. 判据档位标注

| 条目 | 档位 |
|---|---|
| §0 两条缺口的根因 | **[已由源码确认]**（逐条 file:line） |
| §1.1/§1.2/§1.4/§2.1/§2.2/§2.3/§3.2/§3.5 的行为断言 | **[根据源码推断]**——设计期，实现尚未存在 |
| §3.4「原页面连 `serve.mjs` 时显示占用中」 | **[已由真机实测]**（2026-11-02 浏览器手测） |
| §4.4 假设"频谱帧缺失由单消费者锁造成" | **[当前无法确认]**，须在 §4.3 那条浏览器冒烟里判定 |
| §4.3 L2/L3/L6 | **未执行**，等本设计落地后重跑 |
