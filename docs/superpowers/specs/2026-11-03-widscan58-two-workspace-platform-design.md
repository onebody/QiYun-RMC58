# widscan58 双工作区平台（Single Node Console + Site Monitoring）—— 架构设计与功能差异清单

> 编写：2026-11-03 · 分支基线：`fw-wifi-relay` @ `cd451bf`（平台代码与 `rf-track` 同源）
> 状态：**待用户审阅**；本文件落盘时实现代码零行。
> 标注：**[已由源码确认]** / **[已由真机实测]** / **[根据源码推断]** / **[需要实机验证]** / **[推算，非实测]**
> 上篇：`docs/architecture/multi-node-spectrum.md`（A1–A6 裁定，本文服从）、`docs/architecture/device-protocol-v2.md`（协议面）、`docs/superpowers/specs/2026-11-03-rftrack-inline-relay-ingress-design.md`（下称**内连接入设计**，本文 §1 替代其 §1.1–§1.4 的 seed 形态、准入模型与遥测表归属，其余沿用）、`docs/rf-track/RF_Track_落地实施方案与计划.md`（S0–S9 已完成）。
> 本轮实测基线（同日跑）：后端 **618 passed / 1 skipped**（88.3 s）· 工作台 **214 / 0 fail** · `web/` **56 / 56** · `git diff --stat 03eb498..HEAD -- web` **为空**。
> **先读 §12**（你 §一 要求的 16 项逐项差异清单，含"只有 1 项是路由问题"的结论），再按 §0 → §8 看架构与排期。

## 0. 五条裁定（用户 2026-11-03 逐项选定）

| # | 裁定 | 后果 |
|---|---|---|
| R-1 | **P0 走「原 Console 作 Single Node 工作区」** | 16 项单机功能不重写；DSP/绘图/协议解析零重复开发；`web/` 侧只改一行（连接时带上条目的 `gateway_id`） |
| R-2 | **S3 Wi-Fi 中继归为 `Gateway.kind="wifi-relay"`**（一台中继 = 一行 Gateway，幂等键 `wifi_key`）；它的扫描头 = 该 Gateway 下的一条端点 | 四层数据模型不动、`Relay` 继续只表示物理位置；`kind` 是裸 `String(16)` 无枚举约束，**加值零迁移** **[已由源码确认]** [models.py:179](../../../rftrack/backend/app/models.py#L179)。落地形态与它对内连接入设计的三处改动见 §3.4 |
| R-3 | **发现 ≠ 认领：候选表 + `adopt` 才建 Node** | 推翻现状的自动入库（[ingest.py:114-134](../../../rftrack/backend/app/services/ingest.py#L114) 当场 `Node(...)`）；迁移要**回填**，不许把已入库的板子打回未认领 |
| R-4 | **频谱数据面按栅格化 bins 入库** | 跨节点 MAX/AVG/MEDIAN/PER_NODE 全部可由栅格行算出（融合是**跨节点同频点同时刻**，不是跨时间重采样）；逐帧原样入库不做 |
| R-5 | **“Ethernet Discovery Adapter” = 经 HTTP 的接入机发现** | 不做 mDNS/扫段（也不违反"UDP 广播不得作认证/注册"）；三个适配器共用一条 `DiscoveryAdapter` 契约 |

## 1. 与内连接入设计的关系（替代其 §1.1–§1.4，其余沿用）

内连接入设计的 §2（`/node` 落后端）、§3（`/api/endpoints` 无参聚合 + `WS /serial?ep=` 别名 + `RFTRACK_WEB_DIR` 挂 `/app`）、§3.4（N 读者 + 1 写者取代 serve.mjs 的单页锁 [serve.mjs:382](../../../web/serve.mjs#L382)）、§1.5 三处连带点、§4 的回滚闸与 16 条判据**全部沿用**，它是 P0 的实施正文。§1.5 里"`kind == "inline"` 分派"那一处例外：分派条件按 R-2 换成 `kind == "wifi-relay"`，判据与理由不变。

**其 §1.1–§1.4 被本文替代**（根因是 R-2 与 R-3 两条裁定改变了那一节的前提）：

| 原文条目 | 处置 | 替代形态 |
|---|---|---|
| §1.1 启动时 seed 一条 `site→relay→gateway(kind="inline")` 固定链 | **取消** | 中继不是"全场共用的那一张脸"，而是**一台中继 = 一行 Gateway**（`kind="wifi-relay"` + `wifi_key`，§3.1）。这行由运营者登记（或从 `denied_relay` 一键生成草稿），**不由拨入自动建行** |
| §1.2 表 `relay_enrollment`（登记即认领） | **取消** | 准入白名单的载体换成"`kind='wifi-relay'` 且 `status='active'` 的 Gateway 行"本身；节点层的"登记 ≠ 拥有"由 `discovery_candidate` + `POST /api/candidates/{id}/adopt` 承担（R-3） |
| §1.2 表 `relay_denial` | **保留**，语义收窄 | 改名 `denied_relay`，只表达"未被允许上行的中继"（安全闸，与认领无关）；`reason` 闭集换成 `unregistered_relay`（没有对应 Gateway 行）/ `relay_disabled`（有行但 `status != "active"`）/ `missing_relay_id`；60 s 限流与 append-only 口径原样保留 |
| §1.3 `relay_hub.endpoints()` 喂 `reconcile()` 直接建/更新 Node | **改喂候选表** | `reconcile()` 的三态与 `lost` 语义、`max_nodes_per_gateway` 硬闸都保留，但写的是 `discovery_candidate`；建 `Node` 只发生在 `adopt`（§3.1、§8 P1） |
| §1.4 表 `relay_link`（`node_key` PK） | **保留并改归属** | 改名 `gateway_wifi_link`，PK = `gateway_id`（R-2：一台中继 = 一行 Gateway，遥测按接入机归属）；列不变，IP/BSSID 仍不落库 |
| §1.2 API `/api/relay-nodes`（白名单 CRUD + `/denied` + `/{key}/status`） | **拆成两组** | 白名单 CRUD → `/api/candidates` + `/adopt`；拒入时间线 → `GET /api/denied-relays`；手动催 `NET STATUS` → `POST /api/gateways/{id}/verify-link`（§6） |
| §4.2 第 10 条判据"seed 幂等" | **改写** | 拆成三条（未登记 key 不建行只留痕 / 已登记 key 重拨不重复行 / 登记本身幂等），逐条见 §3.4-3 |

一句话口径：**能不能连**（`denied_relay` 闸）、**是不是资产**（candidate→adopt）、**能不能上屏**（`Node.status` + `adoption_state`）三道闸分开，不再挤在 `enabled` 一个布尔里。

## 2. 总体架构

```
浏览器（Single Node /app，原 Console，零 DSP 重写）
   ├─ Web Serial 直连 ─────────────────────────► ESP32-C5 ①      （无任何服务端 = 约束 10 的退化路径）
   └─ WS /serial?ep=&gateway= ──► 后端(每场地一套) │
                                     ├─ /api/endpoints 聚合 ─► serve.mjs ①serial:/②uart: ─► 本机板
                                     ├─ WS /node?id=  ◄────  ESP32-S3 中继 ③（NVS 里存整条 URL，主动外拨）
                                     │                              └─ HEAD 透传 ─► ESP32-C5 扫描头
                                     └─ 采集/栅格/事件/插件/告警/目标 ─► SQLite
浏览器（Site Monitoring /workbench，Vue3 无构建）── 同一后端的 REST + WS
Android app/ 与 Electron web-desktop/ 继续按现协议消费，不受本设计影响
```

三条不变式：

1. **传输身份与业务归属分离**：`(gateway_id, transport_addr)` 仍是节点唯一键（[models.py:195-205](../../../rftrack/backend/app/models.py#L195)），但"谁在链路对面"只进候选表；进场地是人的动作。
2. **测量时间与接收时间各自存到底**（A4 双时间戳），禁止用接收时间当测量时间。
3. **原始帧永远优先于派生视图**：栅格行是可删可重算的派生物；原样 `.widscan58` 录制仍在单机侧，平台侧只数不存（R-4 的代价，数值见 §3.2 的体积账）。

## 3. 数据模型

### 3.1 新表与增列

**`discovery_candidate`** —— 唯一的"发现"落点，三个适配器共享。

| 列 | 类型 | 说明 |
|---|---|---|
| `candidate_id` | PK | |
| `gateway_id` | FK NOT NULL RESTRICT | 在哪台接入机上看见的 |
| `transport_addr` | `String(191)` NOT NULL | 与 `Node` 同一套寻址口径；`(gateway_id, transport_addr)` 复合唯一 |
| `medium` | `String(24)` | `usb-native` / `uart-pl011` / `wifi-relay`（沿用 [ingest.py:34-38](../../../rftrack/backend/app/services/ingest.py#L34) 的名字，不新造词） |
| `label` / `endpoint_json` | | 端点原样留一份，排查时不必回桥上看 |
| `seen_state` | `String(16)` | `online` / `offline` / `lost` —— **复用 `reconcile()` 现成的三态**（[ingest.py:40](../../../rftrack/backend/app/services/ingest.py#L40)），不新增第四态 |
| `fw_version` / `protocol_version` / `capabilities_json` | | 读得到就填；读不到 `NULL` → 渲染 `undeclared`（既有口径） |
| `device_key` | `String(12)` NULL | `DEVICE?` 的 MAC（§6）；`NULL` = 老固件没答 |
| `boot_id` | `String(8)` NULL | 同上 |
| `first_seen_at` / `last_seen_at` / `last_probe_at` / `last_probe_reason` | | 部分失败要留痕，沿用 `dialer` 的 6 类 `dial_reason` |
| `adopted_node_id` | FK NULL SET NULL + 部分唯一 | 认领后置；**唯一约束保证一块板不会被两个 Node 认领** |

**`node` 增列**（全部可空或带默认，不放开任何既有约束）：`adoption_state`（`draft`/`provisioned`/`active`/`suspended`/`unbound`）、`install_note`（安装位置的文字）、`position_x` / `position_y`（`Float` NULL，**归一化 0–1 坐标**，口径见本节末尾 `site_plan` 那一段）、`device_key` / `boot_id`、`desired_json` + `desired_revision`（期望态与配置版本）、`activated_at` / `unbound_at`。

**`gateway` 增列**（R-2 的落点）：`wifi_key String(64)` NULL + **部分唯一索引**（`WHERE wifi_key IS NOT NULL`）——它就是中继拨入时 `?id=` 里那个词。既有 `kind String(16)` 不加枚举约束，`"wifi-relay"` 直接写值 **[已由源码确认]** [models.py:179](../../../rftrack/backend/app/models.py#L179)。

**白名单的载体就是 Gateway 行本身**：`/node?id=<key>` 只接受"已存在且 `status='active'` 的 `kind='wifi-relay'` 行"（`status` 已是现成的开关，`dialer` 在 `gateway.status != "active"` 时本来就不拨 **[已由源码确认]** [dialer.py:308](../../../rftrack/backend/app/services/dialer.py#L308)），`key` 匹配不上就 close + 落 `denied_relay`。因此**不需要另一张准入表**（这是本文取消内连接入设计 §1.2 `relay_enrollment` 的具体机制，而不只是换个名字），也**不在拨入时自动建行**——那等于把"发现即入库"从节点层搬到接入机层，仍然违背 R-3 的精神。代价是新中继要先登记一行 Gateway；缓解 = `GET /api/denied-relays` 里被拒的 key 可一键生成 `kind='wifi-relay'` 的 Gateway 草稿行（与内连接入设计"看到拒绝记录要能直接点成登记上"是同一条口味）。

**`gateway_wifi_link`** —— 1 行 = 1 台在线中继的最新网络事实：`gateway_id` PK、`sta_state`、`head_baud`、`head_bytes_up`、`head_lost_down`、`out_drops`、`queue_permille`、`uptime_s`、`sampled_at`。IP/BSSID **不落库**（脱敏约束）。不写进 `Node` 的理由与原文一致：这些数一秒一变，混进逐字段比较的 `reconcile()` 会把 `updated` 流水账刷爆，也会把"声明"（寿命=小时）和"遥测"（寿命=秒）焊在一张表上。

**`denied_relay`** —— `relay_key` / `reason`（闭集 `unregistered_relay` / `relay_disabled` / `missing_relay_id`）/ `remote_addr` / `seen_at`；append-only，同 key 60 s 窗口内只落一行（防洪），清理走既有 retention。

**R-2 的容量后果（必须现在就说清，不然 P0 上线当天才发现）**：`max_gateways` 默认 **8**（[config.py:68](../../../rftrack/backend/app/config.py#L68)），它同时是 dialer 的并发闸和内存的分母——`queue_limit=256` 帧是**每接入机一条**、不随网关数摊薄（[config.py:69-71](../../../rftrack/backend/app/config.py#L69)），`ring_depth_per_slot=300` 点按 `(网关, 端点, 槽位)` 计（[config.py:74-76](../../../rftrack/backend/app/config.py#L74)）。于是一台中继吃一格 = **多一份 256 帧队列 + 8 槽 × 300 点的环形缓冲**，内存随网关数线性涨；S9-4 本地 4 节点 300 s 压测的 RSS 70–80 MB **[已由压测实测]** 就是这个乘法的当前刻度。真机后果：**8 台中继 + 场地内 serve.mjs 并存时会撞 `max_gateways` 上限**，届时是调这个键并同步复核内存（不是把中继塞回一台 Gateway 下做多端点——那会把 R-2 刚分清的归属又焊回去）。判据：`/api/health` 的网关容量账显式报 `gateways_used / max_gateways`，超限的现象是 adopt/probe 409 点名，绝不静默丢数据（与 `max_nodes_per_gateway` 同一条口味 [config.py:61-65](../../../rftrack/backend/app/config.py#L61)）。

**`spectrum_capture`**（P3）—— 一行 = 一块节点 × 一个频率栅格 × 一个时间栅格：

| 列 | 说明 |
|---|---|
| PK `(node_id, grid_id, ts_bucket)` | 复合主键，同格重写幂等（沿用 `sample_summary` 的取舍与理由 [models.py:431-489](../../../rftrack/backend/app/models.py#L431)） |
| `site_id` / `gateway_id` | 冗余下来给"按场地过滤不走全表扫"用，同 `sample_summary` |
| `grid_id` | 频率栅格版本名（`v1` = 下表） |
| `center_freq_hz` / `bin_spacing_hz` / `bin_count` | **真实频率坐标**，来自 SPECTRUM 帧的 `loKhz/startOffHz/binHz/n`（[frame_parser.py:254-263](../../../rftrack/backend/app/services/frame_parser.py#L254)） |
| `bins` | BLOB，`bin_count` × u8 dB 码（帧里本来就是这个形状，不膨胀成 f32） |
| `detector` | `MAX`/`MEAN`，混 detector 的格子不得并入同一栅格 |
| `capture_t_ms` / `capture_wall_ms` / `receive_time` | 帧内里程表原样存 + 换算出的墙钟 + 接收时刻（§4） |
| `capture_seq` / `hop_seq` | 频谱帧 `seq` / METRICS `hopIndex`（同 `seq` 不可跨帧类型对齐，缺口只在同类内查） |
| `sync_quality` | `measured` / `assumed` / `unsynced` —— **对齐误差是数据属性，不是一个全局"已同步"徽标**（架构 A4/§3.3） |
| `rssi_db` / `floor_db` / `snr_db` / `peak_db` / `obw_lo_hz` / `obw_hi_hz` / `gain` | 你 §六 要求的度量：来自同节点同时刻的 METRICS/LEVELS 帧（[frames.ts:25-40](../../../web/src/proto/frames.ts#L25) 已有全部字段），**不是从 bins 反推** |
| `sample_rate_hz` | 仅当该格由 RAWIQ 派生时填；SPECTRUM 帧**没有**采样率 **[已由源码确认]** —— 字段允许 `NULL`，不许造假 |
| `source` | `real` / `mock` / `demo`，与 `sample_summary.source` 同词表 |

**`node_clock_map`**（P3）—— 每节点每 boot 一行：`node_id`+`boot_id` PK、`uptime_ms`、`wall_ms`、`offset_ms`、`rtt_ms`、`drift_ppm`、`calibrated_at`、`quality`。

**`site` 增列 + `site_plan` 新表**（P4）：`site.location_text`（`String(200)`）；`site_plan(plan_id, site_id, revision, image_path, width_m NULL, north_deg NULL, active BOOL, created_at)` —— 一场地可留多版平面图，`active` 唯一性沿用 `layouts.py` 那套"先批量清旧再置新"的做法（S5 撞出的同批 flush 顺序坑）。坐标一律归一化 0–1，**米制只作为可选比例尺**（`width_m`），这样换图不重标节点、有比例尺时才能谈距离。

### 3.2 频率栅格 `v1`

| 项 | 取值 | 依据 |
|---|---|---|
| 范围 | 5 600 – 5 950 MHz | Band 全段，含 R9/R8 上沿（5 917/5 933 MHz 可达性见项目记忆） |
| `bin_spacing_hz` | 1 MHz → 350 bins | 帧内 `binHz` 常为 100 kHz 量级 **[已由源码确认]**，粗化 10× 是"能跨节点合并"的最小代价 |
| 每格码 | u8（帧原生 dB 码） | 不放大成 f32 |
| 合并规则 | 同 (node, 栅格, 桶) 内**同 detector** 取逐 bin 最大 | 与固件侧"SPEC 的 MAX 只是显示抽取时每 bin 取最大"同语义（A5）。一帧只覆盖**一个 LO 段**（`loKhz ± n×binHz`），所以 1 s 桶内是多帧并一次栅格，不是一帧一行 |
| 每帧宽度 | 默认 **256 bins**（帧账里那 256 字节 **[已由源码确认]** `frames.ts:192` 读 `n`），②a 的 UART 出口被固件钳到 **512** **[已由源码确认]** [frame_parser.py:255](../../../rftrack/backend/app/services/frame_parser.py#L255) | 256 × 100 kHz = 25.6 MHz ⇒ **一帧只占 350 格里的约 26 格**，整条 Band 靠 8 个 LO 段 + 多帧并桶拼出来；缺段必须在栅格里数得出来（`bin_count` 与实填格数之差 = 盲区），绝不平滑补齐 |
| `bin_count` 取法 | 帧头第 5 项 `n`；解析器**没把它放进返回字典**（只回 `db`），实现时 `len(db)` 即 `n` **[已由源码确认]** [frame_parser.py:257](../../../rftrack/backend/app/services/frame_parser.py#L257) | 不新增键就够用；要新增键则是**第四处**三份镜像同改点，本期不动 |
| 时间栅格 | 新键 `spectrum_bucket_ms`，默认 **1 000 ms** | 与 `summary_bucket_s>=1` 同一条 SD 磨损纪律 [config.py:211-212](../../../rftrack/backend/app/config.py#L211) |

**体积账（推算，非实测）**：每节点每 1 s 桶**至多一行** 350 B ⇒ 上界 **0.35 KB/s·节点**（跳频只覆盖 8 个 LO 段时实际填充格数更少，按变长行存就更小；本文按上界算，免得换实现口径就重谈容量）。8 节点 ≈ 2.8 KB/s ≈ **168 KB/min ≈ 0.24 GB/天**。

对照"逐帧原样入库"：**单节点** SPECTRUM ≈ 282 B × ~62.5 帧/s ≈ **17.6 KB/s**（架构文档 §5 的账；按 `SPECTRUM_HDR` 20 B + 256 bins + 帧头/CRC 复算是 286 B，差 4 B，量级不受影响 **[已由源码确认]**），**× 8 节点 ≈ 141 KB/s ≈ 12 GB/天** —— 相差约 **50 倍**，这就是 R-4 否掉逐帧入库的数值理由。真实帧率与每帧 bin 数要在 P3 真机一轮里量出来替换本段（双板真机 ①USB 实测 37–52 KB/s、0 CRC **[已由真机实测]** 是当前的量级参照）。

### 3.3 上报目标（§五）

| 介质 | 服务器 URL 存在哪 | 平台侧对偶 |
|---|---|---|
| ① USB 原生 | **不需要**（约束：USB Node 不需要服务器 URL） | 那台机器的 `serve.mjs` 就是一行 `kind="usb-bridge"` 的 Gateway，`base_url` 指向桥；节点由该 Gateway 的端点表认领（现状不变） |
| ② UART PL011 | 不需要 | 同上（同一行 Gateway 下的另一种端点介质）；`stops_rh` 事实不变（R21） |
| ③ Wi-Fi 中继 | **中继自己的 NVS**：整条 `ws://host:port/node?id=<key>`，由 `NET URL` 写入；`ws_link.c` 主动外拨 **[已由源码确认]** | `gateway.kind="wifi-relay"` + `gateway.wifi_key`（= `?id=` 那个词，`String(64)` 部分唯一）：平台据此**幂等**认出"这就是那台接入机"，重复拨入走同一行 |
| 发现服务 vs 数据服务 | 发现 = `GET /api/endpoints`（无状态、只读、可多解）；数据 = `WS /serial?ep=&gateway=`（有写权、单写者） | 两条路由分开即满足"分离"，不引入第三个服务 |

服务器 URL 的**自动发现**：不猜。手动 URL 配置（页面上填 → 下发 `NET URL` 给已连上的中继）+ **连接验证**（`POST /api/gateways/{id}/verify`：当场看它是否已拨入、拨入后 `probe` 一次端点表）+ **重新注册**（改 key 后旧行 `unbound`，新行以新 `wifi_key` 落，历史不追改）+ **配置版本管理**（§6 的 `desired_revision`）。

### 3.4 中继接入的落地形态与对内连接入设计的三处结构改动

R-2 把"中继"从"后端的一张脸"改成"一行接入机记录"，内连接入设计里三处按单一 seed 写的地方必须跟着改；**行为判据不变，改的是归属键**（连带的字面量只有一处：`Link.url` 的 `inline://` 前缀改叫 `relay://`，回滚旗标仍叫 `RFTRACK_RELAY_ENABLED`）：

| # | 原文 | 本设计的形态 | 为什么不是小事 |
|---|---|---|---|
| 1 | `resolve_link()` / `_join` 对 `kind=="inline"` 走 `INLINE_GATEWAY_ID` 那**一个常量** | 按拨入的 `?id=<key>` 查 `wifi_key` 命中的**那一行** Gateway，`Link(gateway_id=<查到的行>, ep="node:<key>", url="relay://<key>")` | 单一常量意味着全场所有中继的扫描头都挂在同一行 Gateway 下 → 场地/位置维度丢失（"这台中继在看台还是起点塔"答不出来），且 `max_nodes_per_gateway` 把 8 台中继当 8 块板算，第 9 台直接 409 |
| 2 | §1.5a 的恒等映射分派条件 `kind == "inline"`（绕开 `no_base_url`） | 条件换成 `kind == "wifi-relay" AND wifi_key IS NOT NULL`，且**对任意多行成立** | `wifi-relay` 行天生没有 `base_url`（它主动外拨，不是被 HTTP 探测），所以 §1.5a 那条"不发 HTTP、走内存恒等映射"从"只服务 seed 那一行"变成服务**每一台**中继；漏改就是 N 台正常中继在采集健康里各刷一条永久 `no_base_url` 错误 |
| 3 | §4.2 第 10 条判据"同一空库连续两次启动，`site/relay/gateway` 各只多一行"（seed 幂等） | seed 没了，判据换成三条：**a)** 未登记 key 拨入 → Gateway 行数不变 + `denied_relay` 落一行（60 s 内洪水仍只一行）；**b)** 已登记 key 断开重拨 → 同一行 Gateway、`gateway_wifi_link` 只更新不增行、候选行不翻倍；**c)** 登记动作本身幂等：同 `wifi_key` 建第二行撞部分唯一索引 → 409 `wifi_key_exists` | 原判据防的是"重复启动刷出三元组"，新形态要防的是"重复拨入把接入机表/链路表/候选表三张都刷一遍"——**同一种回归保护，换了主语**；直接删掉它等于幂等键失去唯一的守卫 |

`_Session.sticky`（节点 dial-in 即开采集会话、最后一页走了也不拆上游）与 §2.3 的遥测采样客户端**原样保留**，只是作用域从"那一张脸"变成"每一行 `wifi-relay`"；`gateway_wifi_link` 的采样键随之从 `node_key` 换成 `gateway_id`（§3.1）。

## 4. 时间模型

1. `capture_t_ms` = 节点自己的开机毫秒（`t_ms = (uint32_t)(t0/1000)`，`t0` 来自 `esp_timer_get_time()`，[scanner.c:462-468](../../../firmware/src/scan/scanner.c#L462) **[已由源码确认]**），`uint32` 约 **49.7 天回绕** **[根据源码推断]**；`boot_id` 给出复位边界。
2. `node_clock_map` 用往返中点法标定（`CLOCK?`）：M1 实测 `INFO` 三次往返 **9 / 12 / 43 ms** **[已由真机实测]** ⇒ 误差下界 ≈ **±20 ms**（架构 §3.4）。一次标定 + 周期重标（新键 `clock_recalib_s`，默认 300）。
3. 老固件不答 `CLOCK?` ⇒ `sync_quality="unsynced"`，图上按"不可比"渲染，**绝不**把 `receive_time` 冒充测量时刻（你 §六 的禁令 = 本条判据）。
4. 跨节点对齐精度目标：**±20 ms**（架构 D3 已拍板方向）；辅助定位与轨迹推测不在本期（架构 D3 的结论是 ±20 ms 不够，本文 §11 沿用）。

## 5. 数据库迁移计划

现有最大修订号 `007_node_identity`（P0 阶段 3.1 已落地）。**号位安排有一处要改内连接入设计**：原文 §5 把 S11 的汇聚迁移预留成 `007_aggregate`，而 P0 就要建表——预留号撞车。本文按阶段顺序发号，**S11 的汇聚迁移顺延为 `012_aggregate`**（它还没落地，改预留不产生迁移）。每条可单独 `downgrade`：

> **2026-11-03 号位改判（用户裁定：号位 = 执行顺序）**：身份迁移抢在准入迁移之前落地，占 `007_node_identity`（`node` 的身份四列 + 启动会话三列，一次做完），内连接入顺延为 `008_relay_ingress`。因此下表 P1–P4 那四行的号位**按原序作废**，等 P0 阶段 4–6 的 `009_summary_metrics`/`010_spectrum_grid`/`011_site_plan` 落完后，从 `012` 起重发；本文先把表头改成 P0 实际占用，P1+ 内容不动，只标注"号位待重发"。

| 修订 | 阶段 | 内容 | 回填与风险 |
|---|---|---|---|
| `007_node_identity` | **P0 ✅ 已落地** | `node` 追加七列：身份四列 `identity_source`/`identity_target`/`commit_time`/`phy_version` + 启动会话三列 `boot_id`/`boot_changed_at`/`time_sync_quality`（NOT NULL default `unsynced`） | 七列一律追加在末尾，列序与 `models.Node` 声明顺序一致（两条建库路径对拍的回归线）；旧行除 `unsynced` 外全 `NULL`，迁移内按 `PRAGMA table_info` 跳过已有列，`down()` 只撤这七列。写入方：`capture.py` 落 `info_line`，`PUT /api/nodes/{id}` 落 `manual`（只声明能力时不改来源） |
| `008_relay_ingress` | **P0** | `gateway.wifi_key` + 部分唯一索引、`gateway_wifi_link`、`denied_relay` | 空表起步；`kind="wifi-relay"` 无枚举约束，不需要 data migration。`007_inline_relay.py`（内连接入设计 §5 的文件名）按本文 §1 改名并去掉 `relay_enrollment`。**两处既有约束会咬人**：① `Gateway.relay_id` NOT NULL + RESTRICT **[已由源码确认]** [models.py:175-177](../../../rftrack/backend/app/models.py#L175) —— 登记一台中继必须先有 site→relay（位置）行，"从 `denied_relay` 一键建草稿"因此要连带问位置，不能只问 key；② `uq_gateway_relay_name` [models.py:172](../../../rftrack/backend/app/models.py#L172) —— 同一位置放两台中继必须不同 `name`，草稿行把 `name` 预填成 `wifi_key` 免得当场撞车 |
| `008_discovery`（号位待重发） | **P1** | `discovery_candidate`；`node` 增 `adoption_state`（默认 `active`）/`install_note`/**`position_x`/`position_y` 先建**（P1 的认领向导就要填安装位置，比等到 P4 更合理）/`device_key`/`boot_id` | **回填**：每个现存 `node` 造一条 candidate 并把 `adopted_node_id` 指回自己、`adoption_state='active'` —— 已入库的板子绝不退回未认领（真机验证过的 S3+C5 双板依赖这条） |
| `009_provisioning`（号位待重发） | **P2** | `node.desired_json/desired_revision/activated_at/unbound_at` + `config_apply_log` | 默认 `desired_revision=0`；既有 `ChannelPlan` 关系不动 |
| `010_spectrum`（号位待重发） | **P3** | `spectrum_capture`、`node_clock_map` | 空表起步；retention 挂既有清理器 |
| `011_site_plan`（号位待重发） | **P4** | `site.location_text`、`site_plan` | 无图时 `position_*` 仍可有值（文字安装位置） |

沿用既有两条建库路径对拍的迁移测试口径（`create_all`+stamp vs `upgrade head`，比较 `PRAGMA table_info/foreign_key_list/index_list` 语义事实而非 DDL 文本），并把**回填**做成迁移内的显式步骤 + 一条专门用例（"迁移后原 Node 仍在原场地、`adoption_state='active'`"）。**版本号不等于结构事实**：`migrate.missing_structure()` 自检 + `/api/health.schema_gaps` 已经在那儿，新增表都归它扫。

## 6. API 计划（全部沿用既有错误体 `{"reason":…}`、`?site=` 短名、aggregator 只读 405 三条口径）

| 组 | 端点 |
|---|---|
| 发现 | `GET /api/candidates?site=&gateway=&seen_state=`；`POST /api/candidates/{id}/rescan` |
| 认领 | `POST /api/candidates/{id}/adopt`（必填 `site`/`gateway`/`display_name`，可选 `install_note`/`position_x/y`/`device_type`/`channel_plan_id`）→ 201 + 新 `node` |
| 解绑 | `POST /api/nodes/{id}/unbind`（`adoption_state='unbound'`，candidate 的 `adopted_node_id` 置空，历史数据保留） |
| 配置 | `GET/PUT /api/nodes/{id}/desired`（期望态 = 频道方案 + `MODE`/`GAIN` + 度量档 + SPEC 参数；带 `expected_revision`，冲突 409）；`POST /api/nodes/{id}/apply`；`GET /api/nodes/{id}/apply-log` |
| 接入机 | `POST /api/gateways` 接受 `kind="wifi-relay"` + `wifi_key`（这就是准入白名单）；`POST /api/gateways/{id}/verify`；`GET /api/gateways/{id}/link`；`POST /api/gateways/{id}/verify-link`（手动催一次 `NET STATUS`，无人持写权时才成功——沿用内连接入设计 §2.3 的"让位要显式化"） |
| 拒入 | `GET /api/denied-relays?limit=`（时间线，可按 key 一键生成 Gateway 草稿行） |
| 频谱 | `GET /api/spectrum/latest?site=&grid=v1&nodes=1,2,3`；`GET /api/spectrum/history?…&from=&to=`；`WS /ws/spectrum?site=&grid=`（有界扇出，沿用 `store` 的丢最旧队列） |
| 融合 | `GET /api/spectrum/fused?site=&grid=v1&mode=…`，`mode ∈ {per_node, max, median, avg_power}`（P3 只开 `per_node` + `max`，`median`/`avg_power` 在被排入的阶段前一律 422 `mode_not_available`——**显式拒绝而不是悄悄回落**，符合"配置优先于代码兜底"） |
| 平面图 | `POST /api/sites/{id}/plan`（multipart）；`GET /api/sites/{id}/plan`；`GET /api/sites/{id}/plan/image?revision=` |
| P0 别名 | `GET /api/endpoints`（无参=全场聚合）；`WS /serial?ep=&gateway=` |

**设备协议扩展**：零帧改动（`device-protocol-v2.md` 的 P1 三份镜像纪律：`frames.h` / `frames.ts` / `frame_parser.py` 同改才算改）。新增 **`DEVICE?` / `CLOCK?` / `CAPS?`** 三条 ASCII + 回复行（定义已在 `device-protocol-v2.md` §1.2/§2.2/§4，含 flash<1 KB、堆 0、静态 RAM 数十字节的代价账 **[需要实机验证]**），一律**可选探测**：`ERR unknown` 就记 `undeclared` 并降级，**绝不因问不到身份而拒绝接入**。软件侧退化路径：`boot_id` 缺失时用"同类帧 `seq` 归零 + `t_ms` 大幅倒退"检测重启，标记 `restart_detected`。RH 白名单本期不开（Q3 推荐原样接受）。

## 7. UI 路由与组件复用

| 工作区 | 入口 | 技术栈 | 复用策略 |
|---|---|---|---|
| Single Node Console | `/app/`（后端挂载 `web/dist`）；Pages 那份发布拓扑**完全不动** | 原纯 TS SPA，零框架 | 16 项功能原地复用；唯一改动 = `main.ts:793` 拼 URL 时带上条目自带的 `gateway_id`（一行） |
| Site Monitoring | `/workbench/` | Vue3 无构建 | 新增 6 个组件，**不碰** `web/` 的解析与绘图代码 |

外壳层：两个入口互链，顶栏把"当前是哪块板 / 哪个场地"写进状态栏（你的既有偏好：不能只藏在 URL 或悬浮提示里）。

新组件（`rftrack/frontend/workbench/components/`）：`pending-devices.js`（候选表 + adopt 向导）、`node-compare.js`（PER_NODE 多迹线，同频点）、`spectrum-band.js`（真实频率轴一条带，栅格 bins）、`waterfall-multi.js`（N 节点 × 时间，共享时间栅格，逐行带 `sync_quality`）、`site-plan-view.js`（平面图 + 节点点位）、`gateway-link.js`（中继 NET 卡片）。

色阶表复用而不耦合：`web/src/util/canvas.ts:174` 有一张 256 项瀑布色表 **[已由源码确认]**；工作台侧在 `chart.js` 自建同表 + **一条对拍测试**读 `web/src/util/canvas.ts` 的字面量比对（两边都不许单方面改，改了当场红）。这是"复用绘图口径"与"`web/` 零改动纪律"（S0 起 `git diff 03eb498..HEAD -- web` 为空）唯一不打架的做法。

`registry.js` 里 `spectrum-view` / `waterfall-view` 从 PLANNED 升入 COMPONENTS 的时机 = P3 落库之后（[registry.js:134-158](../../../rftrack/frontend/workbench/registry.js#L134)）。那两条的 `reason` 句现在写着"频谱帧在采集层计入 skipped_frames 后整帧丢弃（`services/capture.py:573`）"——**那个行号已经过期**（实际丢弃点在 [capture.py:653](../../../rftrack/backend/app/services/capture.py#L653)），P3 落地时 reason 整句要换掉，别留一条指向不存在位置的过期解释。`node-map` 的 reason 同理（P4；它写的是"不做按 RSSI 猜坐标"——用户上传点位是另一件事，不推翻那条裁定）。

## 8. 阶段计划

> 门槛纪律升级：**五套全绿**才算完成 —— 后端 pytest / 工作台 `node --test` / 工作台浏览器冒烟 / `web/` vitest / `pio test -e native`。**数字只增不减，不许用 skip 换绿。**

### P0 — 原有单机控制台在新平台上完整可达（节点数据模型不动）

| 项 | 内容 |
|---|---|
| 正文 | 内连接入设计 §2–§4 原样执行（`/node`、`relay_hub`、两条别名、`RFTRACK_WEB_DIR`→`/app`、N 读者 1 写者、`RFTRACK_RELAY_ENABLED` 一闸三断），**其中 §1.1–§1.4 按本文 §1 的处置表替换、§3.4 的三处结构改动落地** |
| 文件 | 新增 `services/relay_hub.py`、`app/routers/relay_gateways.py`（准入 CRUD + `/denied-relays`）、`alembic/versions/008_relay_ingress.py`、`tests/test_inline_relay_ingress.py`；改 `ws_bridge.py`（`relay://` 分派 + `_Session.sticky`）、`dialer.py`（`kind=="wifi-relay"` 走内存端点表并写 `dial_state`）、`capture.py`（恒等映射，见 §3.4-2）、`ingest.py`（`TRANSPORT_BY_KIND += "relay": "wifi-relay"`）、`routers/bridge.py`、`main.py`、`config.py`、`web/src/main.ts:793`、`rftrack/frontend/e2e/workbench.mjs`、`rftrack/deploy/` |
| 迁移 | `008_relay_ingress`：`gateway.wifi_key` + `gateway_wifi_link` + `denied_relay`（§5）。**P0 不建候选表**——`discovery_candidate` 属 P1 |
| **过渡期如实申报** | P0 的端点仍走**现有 `reconcile()` 自动建 Node**（[ingest.py:114-134](../../../rftrack/backend/app/services/ingest.py#L114)），也就是"发现即入库"在这一步**仍然存在**，与 R-3 不符。理由：P0 的目标是"原 16 项功能可达"，把准入模型改造捆进来会让回滚闸变得不可解释。P1 的回填 + 候选表为其收口，**这一条必须在 P0 的收尾实况里写明，不能默认它已经成立** |
| 自动测试 | 该设计 §4.2 的 16 条逐条落 `pytest`，其中第 10 条按 §3.4-3 替换；冒烟加一条真路径：`/app/` 打开 → 端点下拉出现「无线节点 <label>」→ 连上后**频谱必须真的画出来**、`badFrames` 不增长 |
| 真机 | ①L2 窗口重跑（≥1 800 s，`node:s3-relay` 的 `crc_errors`/`resync_bytes` 增量为 0；上次窗口被 SIGTERM 打断，须整轮重来）；②L3 断网 60 s 重连；③L6 真机回退（关闸 → 回到今天的行为）；④浏览器手测单机 16 项全过一遍并截图存档 |
| 验收 | **§12 口径**：P0 只欠 #1 这条路由，修好后 `/app` 上 **16 项全部手测可达**（含 ⛔ 那三项——它们在单机侧本来就是可用的）；平台侧 #3/#4/#13 → P2、#5/#6/#7/#8/#9 → P3 **不算 P0 的失败**，要在收尾实况里逐条写明"仍未做、属哪阶段"。另两条硬指标：`?bridge=` 与 Pages 那份发布**不受影响**；`RFTRACK_RELAY_ENABLED=0` 后五套门槛全绿 |
| 回归确认 | `git diff --stat HEAD -- web` 只含 `main.ts` 一行；`web/` 56 条不动；固件零改动 |

### P1 — 场地 / 位置 / 接入机 / 节点管理与候选表（R-3 落地）

迁移 `008_discovery`。`reconcile()` 改写候选表、不再建 Node（保留三态与 `lost` 语义、保留 `max_nodes_per_gateway` 硬闸但改计**已认领**数，超限拒绝的是 adopt 而不是发现）。`site-tree.js` 向导补"待认领"一步；新 `pending-devices.js`。测试：候选幂等（同一端点重复轮询不增行）、重复认领 409、解绑后历史保留、认领后设备重启仍归同一 Node（按 `transport_addr`；有 `device_key` 时跨介质合并）。真机：把 S3+C5 双板那轮的板子先当未认领跑一轮（候选可见、无 summary 入库），再 adopt，确认数值与 M0 基线一致。

### P2 — Adoption / Provisioning（期望态与配置版本）

迁移 `009_provisioning`。`desired_json` 的字段面 = `web/` 那套"每次连接还原"的设备设置（频道方案、`MODE`、`GAIN`，[app.ts:56](../../../web/src/model/app.ts#L56)）+ `METRICS`/`SPEC`；下发走既有桥的写命令通道（同一把 asyncio 写锁，不新造并发语义）；`config_apply_log` 记 谁/何时/哪版/回执原文。验收判据含：**平台重启后期望态不丢**、节点重启后自动回到期望态、下发失败逐条点名（不允许半落地状态）。

### P3 — 频谱数据面与多节点瀑布（PER_NODE 优先，MAX 同批）

迁移 `010_spectrum`。改动集中在**一句**：[capture.py:648-655](../../../rftrack/backend/app/services/capture.py#L648) 的丢弃分支拆成"有 `channels` → 走摘要；是 SPECTRUM → 走栅格；是 RAWIQ/TELEMETRY → 仍只计数"。加 `services/spectrum_grid.py`（分箱/合并/detector 隔离/缺帧记账）、`routers/spectrum.py`、`/ws/spectrum`、`node_clock_map` 标定器。新组件 `spectrum-band.js` + `waterfall-multi.js` + `node-compare.js`。**第一版交付 `per_node` 与 `max`**（与你 §六 的排期一致），`median`/`avg_power` 显式 422。真机：4 节点同频道 30 min，判据 = 缺帧可解释、时间栅格误差可陈述（±20 ms）、拔一块板时图上出现**标注的盲区**而不是平滑补齐。

### P4 — 场地空间分析与高级融合

迁移 `011_site_plan`。`site_plan` 上传/多版/激活、`site-plan-view.js` 点位绘制、融合补 `median`/`avg_power`、跨时段对比。**明确不含**辅助定位与轨迹（架构 D3：±20 ms 不够，本期删掉该目标）。

## 9. 工程约束 → 本设计的落点自查

| 你的约束 | 落点 |
|---|---|
| 1 不动 C5 PHY/DSP | 固件侧只可能加 3 条 ASCII handler（P3 之后另开一轮，需你批 `device-protocol-v2.md` §7 的 Q1–Q4）；P0–P2 零固件改动 |
| 2 不破坏协议兼容 | 帧结构零改动、新命令一律可选（§6） |
| 3 RAWIQ 不进常规 Wi-Fi 数据面 | `rawiq_persist=false` 维持 [config.py:135](../../../rftrack/backend/app/config.py#L135)，栅格表只收 SPECTRUM |
| 4 不在 C5 引入网络管理框架 | 中继 M1/M2 已收口，本设计只在**后端**加 `/node` |
| 5 队列有界 | 复用 `store` 丢最旧 + `plugin_queue_limit`；`/ws/spectrum` 同风格 |
| 6 断线恢复 | `lost` 不删行 + 中继主动重拨 + L3 判据 |
| 7 身份 vs 启动会话 | `(gateway_id, transport_addr)` + `boot_id`；老固件走 `restart_detected` 退化 |
| 8 完整协议测试 | 帧对拍是唯一标准：`test_frame_parity.py` 与固件侧 `pio test -e native` 的字节基线不许因为平台改动而放宽；新增的栅格层**只读帧、不改帧**，因此对拍面不变 |
| 9 保留 USB/UART/RH | serve.mjs 的 `serial:`/`uart:` 与 `/node` 保留不删；②a 的 `stops_rh` 事实不变 |
| 10 单机不依赖 RF Track | Web Serial 直连 + Pages 发布拓扑不动，`/app` 只是多一个挂载点 |

## 10. 待你拍板（不答也能开工的是 P0，P1 之后会被卡住）

1. **`DEVICE?`/`CLOCK?`/`CAPS?` 是否本期实现**（要动固件、要重烧两块板）。不实现则 `boot_id`/真实 `capture_time` 长期停在 `unsynced`，PER_NODE 图能画但必须打"不可比"标签。**推荐**：P0–P3 先按软件退化路径走，P4 之前单开一轮固件。
2. **平面图图片来源**：场地上传照片/PNG（推荐，零外部依赖）还是接在线地图瓦片（引入第三方、且你的部署边界是"每场地一套实例 + 局域网"）。
3. **`median`/`avg_power` 是否坚持排到 P4**（你 §六 的原话）还是与 `max` 同批进 P3（多约 150 行，栅格行已经够算）。**推荐**：按你的原排期。
4. 既有 `sample_summary` 是否要扩出 `floor/snr/peak` 三列（现在只有 `rssi_*`/`ema_*` **[已由源码确认]**）——不扩则 §六 的 RSSI/NoiseFloor/SNR 只在新 `spectrum_capture` 行里有完整一套，两套口径要有一句话说明谁为准。**推荐**：P3 一并扩三列，因为告警与目标引擎读的是摘要。
5. **准入白名单的载体是否就定为 Gateway 行本身**（§3.1）：好处是不再有两张表表达同一件事、`status` 复用现成开关；代价是**一台中继要先有一行 Gateway 才能拨入**，现场多一步登记（缓解 = 从 `denied_relay` 一键生成草稿）。**推荐**：定为此——因为它同时保住了 R-2 的四层模型和 R-3 的"不自动入库"；替代形态是保留一张独立的准入表，但那会把本文 §1 的"取消 `relay_enrollment`"变成只是改名，三道闸又挤回一处。

## 11. 本期明确不做

节点间直接通信/自组网；IQ 级多节点融合；辅助定位与轨迹推测；UDP/mDNS 设备发现；per-node 鉴权令牌（`wifi_key` 就是凭据位，真鉴权另开一轮）；云端部署与多场地汇聚改造（`012_aggregate` 起号，见 §5）；把单机 16 项重写进 Vue；40+ 频道改造（另立固件分支 M3）。

## 12. 功能差异清单（你 §一 的 16 项逐条）

判定档位：**✅ 已达成** / **🟡 部分** / **❌ 缺口** / **⛔ 有意不做**。

**这张表有两个口径，别读成一个**（这是 §一"不许因为新增而弱化原有"能不能被验证的关键）：

- **`/app` 侧（Single Node Console）= 16 项全部 ✅ 存在**，因为 `web/` 那份代码一行没少 **[已由源码确认]**：`git diff --stat 03eb498..HEAD -- web` 为空、`web/` 56 条测试全绿。它今天"看起来什么都没有"的原因只有一个——#1 的连接路由，属于**可达性**而不是功能缺失。
- **下表"平台侧现状"与"判定 · 阶段"说的是 Site Monitoring（后端 + 工作台）**：同一件事在场地视图里能不能做。⛔ 三项指"平台侧不做，能力留在单机侧"，不是"这个项目没有"。

| # | 功能 | 原 Console 实现处 | 平台侧现状（证据） | 判定 · 阶段 |
|---|---|---|---|---|
| 1 | USB Serial 连接 | `web/src/serial/device.ts` + `main.ts:923-930`（`?bridge=`）| 后端发出页面时 `/api/endpoints` 422 被 `if (!r.ok) return` 静默吞掉（`main.ts:689-695`），`/serial` 路由不存在 | ❌ **纯路由缺口**，功能本身在 → P0 |
| 2 | 设备身份与固件信息 | `INFO` 六段（固件 `commands.c:421-428`）+ 页面头部板名 | 后端存 `Node.fw_version/protocol_version/capabilities_json`（[models.py:212-214](../../../rftrack/backend/app/models.py#L212)），工作台 `node-status.js` 渲染 | 🟡 缺**持久身份**（`DEVICE?` 的 mac/boot_id）→ §6 协议扩展；能力位 `undeclared` 是已申报缺口 |
| 3 | 频道及频率配置 | `views/channelPlanDialog.ts` + `PROFILE?`/`PROFILE SET` | 后端有 `ChannelPlan` + `ChannelPlanItem` 表（[models.py:223-246](../../../rftrack/backend/app/models.py#L223)）、工作台 `channel-matrix.js`/`channel-detail.js` **只读** | 🟡 **展示达成、下发缺**（平台不能改设备频道表）→ P2 `desired_json` |
| 4 | 扫描模式与 GROUP | `views/controls.ts:67-79`（`MODE <v>`，GROUP/SINGLE 两段）| 后端只把模式当能力位与日志读；`/api/mode` 是 RH 计时侧的口子，不是设备 `MODE` | 🟡 同上 → P2 |
| 5 | 实时频谱 | `views/panorama.ts:1`（LO 段拼接连续谱）| SPECTRUM 帧在采集层被丢弃计数（[capture.py:648-655](../../../rftrack/backend/app/services/capture.py#L648)，`FRAME_NAMES` 里 `spectrum`）；`combined-spectrum.js` 自述"离散频道线，不是连续频谱"；`spectrum-view` 在 PLANNED | ❌ **真缺口** → P3（R-4 栅格） |
| 6 | 实时瀑布图 | `views/panorama.ts:334` `renderWaterfall` + `util/canvas.ts:174` 256 项色表 | `waterfall-view` 在 PLANNED，理由同 #5 | ❌ → P3 |
| 7 | RSSI / Noise Floor / SNR | `frames.ts:25-40 ChannelMetrics`（floor/snr/clearFloor 全有）| 后端 `parse_levels` **带 `channels`** → LEVELS/METRICS 都进摘要与环 **[已由源码确认]**；但 `sample_summary` 只有 `rssi_mean/min/max/last` + `ema_*`，**没有 floor/snr/peak 列** | 🟡 原始帧里有、落库时丢了 → P3（待拍板 4） |
| 8 | 中心频率与 99% 占用带宽 | `frames.ts` `centroidOffHz/obwLoHz/obwHiHz` + `views/zoom.ts:133` 括号绘制 | 解析器有这些键，摘要与任何 API 都没这三列 | ❌ → P3 `spectrum_capture` 列（R-4） |
| 9 | 信号峰值与频道分析 | `frames.ts` `peak/peakOffHz` + `views/channelGrid.ts` | 同 #8：帧里有，落库没有 | ❌ → P3 |
| 10 | RAW IQ 抓取 | `views/rawiq.ts` + `FRAME_RAWIQ=4` | 后端 `parse_rawiq_chunk` 存在，采集层丢弃，`rawiq_persist=False` **[已由源码确认]** [config.py:135](../../../rftrack/backend/app/config.py#L135) | ⛔ **有意不做**（约束 3：连续 RAWIQ 不进常规 Wi-Fi 数据面）；单机侧 `/app` 原地可用 |
| 11 | LEVELS 显示 | `web/src/model/store.ts:194` `case FRAME_LEVELS` | 后端 `parse_levels`（[frame_parser.py:205-249](../../../rftrack/backend/app/services/frame_parser.py#L205)）产出 `channels`，与 METRICS 同路进摘要/环/`/ws/live`；`mode` 分 RSSI/SNR，"没测的量回 `None`"口径已在 | ✅ **达成**（原以为缺口，实测源码翻案：LEVELS 不在丢弃名单里） |
| 12 | 原始数据录制与回放 | `main.ts:846-851` `ReplayTransport` + `.widscan58` | 平台侧是另一层：归档/导入三层（S8，实测过 21 表逐字段往返） | ✅ 单机侧零改动保留；平台侧对等物已存在，**两者不合并**（§2 不变式 3） |
| 13 | 局部设置（设备侧） | `model/app.ts:56 deviceSettings` + `restoreCommands()` 每次连接还原 | 后端无"每节点期望配置"概念 | ❌ → P2（`desired_json` 的字段面就是这套） |
| 14 | 全局设置（界面） | `model/app.ts:13-31 UiSettings` + localStorage 7 键 | 工作台有自己的布局持久化（`002_layout`）| ✅ **两套 UI 设置服务两套界面，不合并**（合并是假需求） |
| 15 | 浏览器固件烧录 | `views/flashDialog.ts` + esptool-js | 平台侧没有，也不该有：远程/中继板不走 DFU | ⛔ **有意不做**；仍由单机 `/app` 与 Pages 那份承担 |
| 16 | VTX 功率计与计圈 | `views/power.ts`、`views/timer.ts`、`rhServerDialog.ts` | 平台侧只有告警/事件/目标；②a 的 RH 计时以 `stops_rh` 能力位表达（R21） | ⛔ 单机保留；平台侧的对应物是"多节点观测 + 告警"，**不是**移植功率计 |

**清单的三点结论**（决定 P0 的边界，也是"不许因为新增而弱化原有"的可验证形式）：

1. **16 项里只有 1 项是纯路由问题**（#1），修完它就一次性把 #2/#10/#11/#12/#14/#16 的单机可用性带回来——因为 `web/` 那份代码一行没少 **[已由源码确认]**：`git diff --stat 03eb498..HEAD -- web` 为空、`web/` 56 条测试全绿。
2. **真缺口集中在频谱数据面**（#5/#6/#8/#9 + #7 的列），根子是同一条 `capture.py:648-655` 的丢弃分支 → 全部由 P3 一次收口，不在 P0 夹带。
3. **三项标 ⛔ 的是裁定不做**，不是漏做；如果后面改变主意，要单独开设计，不塞进 P0–P4 的验收里。
