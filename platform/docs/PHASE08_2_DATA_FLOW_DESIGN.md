# Phase 08.2 — 数据流设计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2 — Platform Information Architecture / 配套文档 ④
> 性质：基于**现有实现**的数据流记录；虚线框表示未实现
> 日期：2026-11-05

## 0. 总览：两个数据平面，物理隔离

```text
┌──────────────────────────── 平台平面（HTTP / 轮询） ────────────────────────────┐
│                                                                                │
│  Gateway Agent ──WS(agent.event)──▶ Site Server ──SQL──▶ SQLite                 │
│                                          │                                     │
│                                          └──REST──▶ Platform Web (www)          │
│                                                       │                        │
│                                                       └── fetch/poll──▶ 7 页面 │
└────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── 设备平面（浏览器/桌面直连） ─────────────────────────┐
│                                                                                │
│  C5 设备 ──USB/串口/WS桥──▶ RF Console(Web) ──▶ 解析/渲染 ──▶ Spectrum/Waterfall │
│                              （/app/ 或 Electron app://）                       │
└────────────────────────────────────────────────────────────────────────────────┘
```

**关键结论**：两个平面在前端**没有任何运行时数据共享**。平台从不打开串口/USB，Console 从不调用 Site REST。

---

## 1. API 数据流（平台 → Site Server）

### 1.1 请求链路（实测实现）

```text
页面函数 (pageOverview/pageSites/...)
      │  load(key, path, params)
      ▼
app.js api(path, params)
      │  fetch(path, { cache: "no-store", Accept: application/json })
      ▼
FastAPI 路由（同源，无鉴权）
      │  get_session()（SQLAlchemy Session）
      ▼
SQLite（site.db）
      │
      ▼
序列化 dict（显式白名单字段）
      │
      ▼
state.cache.set(key, {data, ts})  ──▶ 渲染 DOM
```

### 1.2 各页面的 API 组合（真实调用）

| 页面 | 并行请求 | 轮询 |
|---|---|---|
| Overview | `GET /api/v1/site`、`/agents`、`/devices`、`/measurements/stats`、`/diagnostics` | 10 s |
| Sites 列表 | `GET /api/v1/site` | 15 s |
| Site 详情 | `GET /api/v1/site`、`GET /api/v1/agents?site_id=<id>` | 15 s |
| Gateways 列表 | `GET /api/v1/agents?status=&site_id=` | 5 s |
| Gateway 详情 | `GET /api/v1/agents`、`GET /api/v1/devices` | 5 s |
| Devices 列表 | `GET /api/v1/devices?status=&only_adopted=` | 5 s |
| Device 详情 | `GET /api/v1/devices/{id}`、`GET /api/v1/devices/{id}/measurements?limit=20` | 5 s |
| Measurements | `GET /api/v1/devices`（下拉）+ `GET /api/v1/devices/{id}/measurements?frame_type=&limit=&offset=` | 无 |
| System | `GET /health`、`GET /api/v1/diagnostics`、`GET /api/v1/measurements/stats` | 15 s |
| Console 说明页 | 无 | 无 |

### 1.3 缓存与陈旧策略

| 机制 | 行为 | 代码位置 |
|---|---|---|
| 请求缓存 | `state.cache: Map<key, {data, ts}>` | `app.js` |
| 成功 | 覆盖同 key 缓存 | `load()` |
| 失败 | 返回 `{data: 上次成功值, error, stale: true}` | `load()` |
| 渲染 | 错误条 + “显示的是上一次成功数据” | `errorBar()` |
| 缓存键 | `site`、`agents`、`devices`、`stats`、`diag`、`device:<id>`、`meas:device:<id>` 等 | 各页面 |
| 清理 | 不主动清理；同 key 覆盖；进程内存随页面数量线性有界 | — |

### 1.4 前端数据边界

- 页面只消费 REST 返回值，**不做本地推算统计**（除简单的 `filter(...).length` 计数）。
- 任何 `undefined` 字段 → `—` 或“未提供”，不猜测默认值。
- 不做前端聚合（无跨设备求和、无趋势图）。

---

## 2. 实时事件数据流（后端真实链路）

### 2.1 上行完整路径

```text
C5 设备
  │  USB CDC / UART
  ▼
Gateway Agent 传输层（serial / webusb / ws bridge / simulator）
  │  原始字节
  ▼
帧解析 proto/frames.ts 等价实现（Python）
  │  decoded = {type_code, sequence, ...}
  ▼
Agent EventBus（device.online / device.frame / device.state_change / ...）
  │
  ▼
site_connector._to_site_envelope()
  │  device_boot_id = 当前设备启动周期标识
  │  event_id       = 上游已有的原样转发；缺失才派生
  │  agent_received_at = 上游已有或 utc_now_iso()
  │  RAWIQ(type_code=4) 默认跳过
  ▼
WebSocket  ws://<site-server>/ws/v1/agents/connect
  │  首条消息 = auth（agent_id + credential）
  │  心跳 heartbeat ⇄ heartbeat.ack
  ▼
Site Server agents/connection.py::agent_connect_ws
  │  _handle_agent_message
  ▼
_handle_agent_message → _process_agent_event
  │  device.online / discovered → upsert Device、写 last_seen_at
  │  device.frame → ingestion.record_device_frame
  │  device.state_change → 更新 status / session
  ▼
ingestion.persist_measurement
  │  event_id 校验；非法直接拒写
  │  唯一键冲突 → duplicate_events（幂等丢弃）
  │  其它完整性错误 → integrity_errors
  │  agent_received_at 缺失/非法 → 保持 NULL
  ▼
SQLite: measurements / devices / device_endpoints / connection_sessions / site_events
```

> 以上为**已实现**链路，08.5 实测 Measurement 累计 > 35,000 条，`agent_received_at` 100% 有值，因果倒置 0 条，重复 event_id 0 条。

### 2.2 前端实时事件（缺失）

```text
Site Server ──（应有 /ws/v1/site）──▶ Frontend Event Client ──▶ Workbench State ──▶ 页面
                    ╳ 未实现（NOT SUPPORTED）
```

当前替代方案：**REST 轮询**。

| 页面 | 间隔 | 隐藏时 |
|---|---|---|
| Gateways / Devices / Device 详情 | 5 s | 暂停（`document.hidden`），恢复时立即刷新 |
| Overview | 10 s | 同上 |
| Sites / System | 15 s | 同上 |
| Measurements | 无自动刷新 | 手动“刷新”按钮 |

**结论**：页面数据最多滞后 5–15 s；不做增量 patch；无事件推送。

### 2.3 Agent 侧心跳与离线判定

| 项 | 值 / 行为 | 位置 |
|---|---|---|
| 心跳间隔 | Agent 侧配置（默认秒级） | `site_connector` |
| 心跳超时 | `HEARTBEAT_TIMEOUT_SECONDS = 45` | site-server |
| 前端提示 | 后端仍 `online` 但 `last_seen_at` > 45 s → “心跳超时”徽标 | `agentBadge()` |
| Device `last_seen_at` | **只**随 `device.online` / `device.discovered` 更新，不随帧更新 | 后端 |

---

## 3. RF Console 数据流（设备平面）

```text
C5 设备
  │
  ├── Web Serial ─────────────┐
  ├── WebUSB（设备/烧录） ─────┤
  ├── WebSocket 串口桥 ────────┤──▶ Transport 抽象
  └── Replay（.widscan58） ───┘
                                 │  原始字节
                                 ▼
                        Parser（proto/frames、proto/rh）
                                 │  解码帧
                                 ▼
                        App 状态（model/app、model/store）
                                 │
                     ┌───────────┼───────────┬────────────┐
                     ▼           ▼           ▼            ▼
                 panorama     channelGrid   rawiq       timer / power
                 （频谱）      （频点）      （IQ）       （计时/功率）
                     │           │           │            │
                     └───────────┴─────┬─────┴────────────┘
                                       ▼
                                  Canvas 局部重绘
```

### 3.1 录制 / 回放

```text
原始字节流 ──Recorder──▶ .widscan58 文件
                            │
                            ▼
                     ReplayTransport（按时间轴回放，只读）
```

### 3.2 与平台的关系

| 项 | 结论 |
|---|---|
| 是否经过 Site Server | **否**（浏览器/Electron 直连设备） |
| 是否进入平台状态 | **否** |
| 是否受平台轮询影响 | 否（独立应用实例） |
| 是否共享设备连接 | 否（平台不建设备连接） |

---

## 4. 共享数据流设计

### 4.1 现状：无运行时共享

| 数据类型 | 平台可见 | Console 可见 | 共享方式 |
|---|---|---|---|
| 设备身份（ID/名称） | 来自 Site DB（`device.id`、`display_name`） | 来自设备固件/协议（串口信息） | **无自动映射**，仅人能对照 |
| 设备在线状态 | `devices.status` | 串口连接状态 | 无共享 |
| 频谱 / Waterfall / RAWIQ | 不显示 | 显示 | 无共享 |
| Measurement | 显示历史 | 不显示 | 无共享 |
| 事件流 | 无 | 无（Console 自有链路） | 无共享 |

### 4.2 设计原则（必须遵守）

1. **可以共享**：设备身份字符串、设备在线与否的“人读语义”。
2. **不得重复建立物理连接**：平台永不打开串口/USB；Console 永不被平台自动拉起。
3. **高频数据不入全局状态**：RAWIQ/频谱帧绝不进入 `state.cache`、绝不进入平台 DOM。
4. **切页不影响连接**：Console 是独立标签页/独立窗口，平台路由切换不会触碰其连接。
5. **Workbench 不依赖 Console 内部实现**：平台不得 import `repo/web` 模块、不得读取其 `localStorage` 键、不得内嵌 iframe。

### 4.3 未来可选项（NEW，不在本阶段实现）

只读“设备名册”共享：平台提供设备 ID/名称给 Console 用于**显示**，Console 仍需用户手动选择串口，平台不发起连接。此方案需要：Site Server 暴露稳定 `device_id` 映射到串口标识（当前**无**该映射），故列为规划项，不得视作已实现。

---

## 5. 数据边界

| 数据 | 唯一来源 | 落点 | 允许进入平台全局状态 |
|---|---|---|---|
| Site 基本信息 / counts | Site Server `GET /api/v1/site` | 页面缓存 | 是（低频、小对象） |
| Agent 列表 | `GET /api/v1/agents` | 页面缓存 | 是 |
| Device 列表 / 详情 | `GET /api/v1/devices[/{id}]` | 页面缓存 | 是 |
| Measurement 列表（≤100/页） | `GET /devices/{id}/measurements` | 页面局部 | 是（按页，不累积） |
| `agent_received_at` | DB 有值，但 **REST 未返回** | — | 否（当前不可见） |
| `agent_boot_id` | Agent 进程内存 | 不传输 | 否 |
| `device_boot_id` | Site DB / Agent envelope | REST 返回 | 是 |
| Ingestion 计数 | 进程内存 | diagnostics | 是（只读展示） |
| 频谱 / Waterfall / RAWIQ | C5 设备 | Console 内存/Canvas | **否（硬性禁止）** |
| Console 连接会话 | 浏览器/Electron | Console 内存 | 否 |

---

## 6. 异常恢复流程

### 6.1 Site Server 不可达

```text
fetch 抛错
  ▼
state.reachable = false → 顶栏徽标“Site Server 不可达”
  ▼
load() 返回上次缓存 + stale=true
  ▼
页面显示错误条（NET）+ “显示的是上一次成功数据”
  ▼
固定间隔轮询继续（5/10/15 s）；用户可点“重试”
  ▼
服务恢复 → 下一次请求成功 → 徽标恢复“已连接”、错误条消失
```

> 实测：停服后页面不崩溃、数据保留；恢复后自动回连。**无指数退避**，无服务端主动通知。

### 6.2 单端点失败（部分降级）

- Overview 的 5 个请求相互独立：任一失败只影响对应卡片，其余照常渲染。
- `errorBars()` 按 `path:status` 去重，避免重复堆叠。
- `counts` 与列表接口不一致时（列表失败），Overview 回退到 `site.counts`。

### 6.3 Agent 掉线

```text
Agent 进程停止 / 网络断
  ▼
Site Server 心跳超时（45 s）
  ▼
Agent status = offline；前端下一轮询显示“离线”
  ▼
Device 状态由 Agent 上报的 state_change 决定；`last_seen_at` 不随帧更新
```

前端另有“心跳超时”徽标：防止后端尚未落 offline 时给出误导性“在线”。

### 6.4 Agent 重连

- `site_connector` 自动重连（含认证握手）。
- 重连后 Agent 重新上报设备快照（`agent.devices_snapshot`），Site Server 重新对齐设备状态。
- 08.5 实测：Agent 重启后恢复在线，Measurement 继续增长。

### 6.5 页面切换 / 标签页隐藏

| 事件 | 处理 |
|---|---|
| `hashchange` | `renderPage` 清空页面、递增 token、清除旧定时器 |
| 旧请求晚到 | token 不匹配 → 丢弃，不写 DOM |
| `document.hidden = true` | 轮询跳过本轮（重新排下一轮），不请求 |
| 回到前台 | `renderPage({quiet:true})` 立即刷新一次 |

### 6.6 Console 异常（独立）

| 场景 | 行为 |
|---|---|
| 串口断开 | Console 内提示并允许重连；平台不感知 |
| 回放结束 | `onClose("replay finished")` |
| Electron 渲染进程崩溃 | `main.mjs` 重建窗口，不退出应用 |
| Console 静态资源缺失 | 平台 `/app/` 无挂载；顶栏入口降级为说明页 |

### 6.7 长时运行

| 风险 | 现状 | 约束 |
|---|---|---|
| 缓存无限增长 | 缓存按固定 key 覆盖，不随轮询累积 | 不得按事件 ID 缓存 |
| DOM 无限增长 | 每页整页重建；Measurement 每页 100 行 | 不得前端无限追加 |
| 定时器泄漏 | `clearTimeout(pollTimer)` + token 校验 | 新页面必须先清旧定时器 |
| Console 内存 | 由原实现负责（环形缓冲/Canvas） | 平台不得注入高频数据 |

---

## 7. 验收清单

| # | 检查项 | 期望 |
|---|---|---|
| 1 | 页面数据全部来自 REST | 无 mock、无硬编码数字 |
| 2 | 断服 | 错误条 + stale + 不崩溃 |
| 3 | 恢复 | 自动恢复，无需刷新页面 |
| 4 | 三个时间字段 | `device_timestamp_ms` / `agent_received_at`（缺）/ `created_at` 语义分离 |
| 5 | `device_boot_id` | 与 `device_id` 分列，不混用 |
| 6 | `agent_boot_id` | 平台不展示（未暴露） |
| 7 | 轮询清理 | 切页后旧定时器不再触发 |
| 8 | 隐藏标签页 | 不产生请求 |
| 9 | Console 独立 | 关闭 Site Server 后仍可用 |
| 10 | RAWIQ | 不出现在平台任何请求/DOM |
