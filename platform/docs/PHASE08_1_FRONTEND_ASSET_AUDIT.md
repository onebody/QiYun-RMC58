# Phase 08.1 — 现有前端资产审计

> 阶段：Phase 08 Platform UI Integration / 子阶段 08.1
> 性质：**只读审计，不修改任何代码**
> 日期：2026-11-05
> 审计范围：`repo/web`、`repo/web-desktop`、`repo/rftrack/frontend/workbench`、`repo/rftrack/backend`（页面挂载部分）、`site-server`（是否提供页面）、`app/mobile`（是否依赖平台接口）

---

## 0. 审计结论摘要

1. 仓库内存在 **两套互不相同的“前端平台”**：
   - **原始 widscan58 Web Console**：`repo/web`，Vanilla TypeScript + Vite，单页多模式，通过 Web Serial / WebUSB / `serve.mjs` 桥接直连设备。
   - **RF Track Workbench**：`repo/rftrack/frontend/workbench`，无构建 Vue 3 + 原生 DOM 组件，挂在 **rftrack backend** 的 `/workbench/` 下，读取 rftrack 的 `/api/*`。
2. **Phase 07 的 Site Server（`site-server`）没有任何前端页面，也没有静态文件挂载**。`site-server/src/widscan_site/main.py` 只注册 REST + Agent WebSocket；`main.py` 文件头声称存在 `/ws/v1/site` 前端事件流，**实际代码未实现**（已核对全部 `@app.*` 装饰器）。
3. 因此任务书中“Site Workbench”**不是现成可复用的页面**：
   - rftrack Workbench 的数据模型是 `site → relay → gateway → node`，后端是 `repo/rftrack/backend`；
   - Phase 07 平台的数据模型是 `Site → Agent → Device → Measurement`，后端是 `site-server`；
   - 两者字段、端点、主键语义均不同，**不能直接把 rftrack Workbench 当作 site-server 的管理界面**。
4. 推荐路径：**新增一个独立的 Platform Shell 静态前端，由 site-server 同源托管**；原始 Console 保持现有构建与运行链路不变，仅通过导航链接接入。该建议需要人工确认后才能进入 08.2/08.4。

---

## 1. 现有前端项目结构

### 1.1 原始 widscan58 Web Console（`repo/web`）

```
repo/web/
├── index.html                 # 单入口，<div id="app">，加载 src/main.ts
├── vite.config.ts             # base="./" + 自定义 PWA Service Worker 生成插件
├── serve.mjs                  # LAN 静态服务 + 串口桥 + /api/endpoints + /api/mode
├── package.json               # dev/build/preview/serve/test/e2e
├── src/
│   ├── main.ts                # 975 行：页面装配、模式切换、连接/录制/回放/烧录
│   ├── model/                 # app / store / channelPlan / timer / laps / power / rhServer
│   ├── serial/                # device(WebSerial/WebSocket) / recorder(Replay) / flasher / parser / webusb / rhLink
│   ├── views/                 # panorama / zoom / channelGrid / history / rawiq / controls / power / timer / debug / 各类 Dialog
│   ├── proto/                 # 设备帧与 RotorHazard 协议
│   ├── util/                  # canvas / speech / 其他工具
│   ├── mock/                  # 演示数据（本地模拟，明确与真实设备分离）
│   └── i18n.ts
├── e2e/                       # headless / layout-stability / chrome-resolver
└── dist/                      # 构建产物（被 rftrack backend 可选用作 /app/）
```

关键特征：

| 项 | 现状 |
| --- | --- |
| 框架 | 无 React/Vue；原生 DOM + TypeScript |
| 构建 | Vite（`npm run build` 先 `tsc --noEmit`） |
| 路由 | **无路由库**，用 `UiSettings.uiMode = monitor / timer / power` 切换三个主页面 |
| 状态 | 类实例 + localStorage（`widscan58.settings`）；无全局 store 框架 |
| 样式 | 页面内 CSS（`src/style.css` 一类），深色为主 |
| 数据连接 | 浏览器 Web Serial / Android WebUSB / `ws://host/serial?ep=` / 录制回放 / RotorHazard HTTP 轮询 |
| PWA | `sw.js` 由构建插件按产物 hash 生成 |

### 1.2 Electron 桌面壳（`repo/web-desktop`）

- 入口 `main.mjs`：自定义 `app://` 协议加载 `web/dist`，`webSecurity: false`（允许访问局域网明文 HTTP 的 RotorHazard）。
- 提供 Electron 不自带的串口/USB 选择器 UI；菜单、自动更新等。
- 打包产物 `release/`（0.8.0 / 0.9.0 dmg、zip、nsis）。
- **与 site-server 无任何耦合**，只依赖 `web/dist`。

### 1.3 RF Track Workbench（`repo/rftrack/frontend/workbench`）

```
repo/rftrack/frontend/workbench/
├── index.html                 # 无构建；Vue 从三个来源之一注入，最后回落到 vendor/vue.global.prod.js
├── styles.css
├── app.js                     # 1037 行：Vue 根实例、动作层、栅格拖拽/缩放
├── api.js                     # 198 行：前缀探测、ApiError 归一化、调用审计 audit()
├── context.js                 # 325 行：四级层级 + 结构化状态栏 + 视图记忆
├── live.js                    # 507 行：整页一条 /ws/live
├── store.js                   # 658 行：布局/格子状态机
├── registry.js                # 组件注册表（10 个可用 + 3 个 PLANNED）
├── grid.js / chart.js
├── components/                # 13 个 Vue 组件
├── test/                      # node --test 单测
└── vendor/vue.global.prod.js  # Vue 运行时仓库副本
```

挂载方式（`repo/rftrack/backend/app/main.py`）：

- `/workbench/` → `workbench_dir`（默认 `frontend/workbench`）
- `/app/` → `console_dir`（默认 `repo/web/dist`，可缺失，缺失不算故障）
- 两个挂载都同源，因此不需要 CORS。

### 1.4 Site Server（`site-server`）

- **没有 `web/`、`static/`、`templates/` 目录**；`main.py` 只导入 `FileResponse` 但未使用。
- 无 CORS 中间件，无 REST 认证依赖。
- 已注册端点（全部在 `site-server/src/widscan_site/main.py`）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 存活 |
| GET/PATCH | `/api/v1/site` | Site 读取/更新 |
| GET | `/api/v1/agents` | Agent 列表 |
| POST | `/api/v1/agents/pairing-codes` | 生成配对码 |
| POST | `/api/v1/agents/pair` | Agent 配对 |
| POST | `/api/v1/agents/{agent_id}/revoke` | 吊销凭证 |
| GET | `/api/v1/devices` | Device 列表 |
| GET | `/api/v1/devices/{device_id}` | Device 详情（含 endpoints/sessions） |
| POST | `/api/v1/devices/{device_id}/adopt` | 认领 |
| PATCH | `/api/v1/devices/{device_id}` | 改名/状态 |
| DELETE | `/api/v1/devices/{device_id}` | 删除 |
| POST | `/api/v1/devices/{device_id}/transfer-owner` | 迁移 Owner |
| GET | `/api/v1/devices/{device_id}/measurements` | Measurement 列表（支持 `frame_type/limit/offset`） |
| GET | `/api/v1/measurements/stats` | 总量/最后时间 |
| GET | `/api/v1/diagnostics` | 计数/在线连接/ingestion 统计 |
| WS | `/ws/v1/agents/connect` | **Agent 反向连接**（非前端事件流） |

**缺失**：`/ws/v1/site`（文件头注释声称存在，代码中不存在）、任何静态页面托管、REST 鉴权、CORS。

### 1.5 Android 客户端（`app/mobile`）

- Kotlin 原生 App；`Widscan58Activity` + USB CDC（`usb/UsbCdc.kt`、`usb/UsbSerialLink.kt`）。
- 仅与 GitHub Releases 通信（`GitHubReleases.kt`），**不依赖 site-server 或 rftrack 平台接口**。
- 本阶段不涉及 Android UI 重构。

---

## 2. 已实现页面与功能清单

### 2.1 Console 三个主页面

| 模式 | 页面内容 | 代码 |
| --- | --- | --- |
| Monitor | 全景频谱（band panorama）+ Waterfall、Zoom（单频道频谱）、频道格（8 频道/40 频点按固件能力）、RAW IQ、历史曲线、控制面板、日志、GPIO、烧录 | `views/panorama.ts`、`views/zoom.ts`、`views/channelGrid.ts`、`views/rawiq.ts`、`views/history.ts`、`views/controls.ts`、`views/debug.ts`、`views/flashDialog.ts`、`views/gpioDialog.ts` |
| Lap timer | 计时席位、计时曲线、检测事件、设置、语音播报、RotorHazard 服务器只读轮询 | `views/timer.ts`、`views/timerEventsDialog.ts`、`views/rhServerDialog.ts`、`model/rhServer.ts` |
| Power meter | 功率计校准/平滑/衰减设置、以功率模式复用 monitor 扫描协议 | `views/power.ts`、`model/power.ts` |

附加能力：

- 录制/回放：`serial/recorder.ts`（`Recorder` + `ReplayTransport`），replay 只读。
- 固件烧录：`serial/flasher.ts` + esptool-js；`firmware/index.json` 版本清单。
- 通道计划/跳频/增益：`model/channelPlan.ts` + `views/channelPlanDialog.ts`。
- 断线恢复：`main.ts` 中 3 s 无数据提示、`WebSerialTransport` 可恢复错误继续读、连接/重连、屏幕常亮、语音。
- PWA 离线：`sw.js` 构建生成。

### 2.2 Workbench 组件

| 组件 | 数据源 | 说明 |
| --- | --- | --- |
| channel-matrix | `/ws/live` | 当前板每档电平/EMA/SNR/活动/更新时间 |
| channel-detail | `/ws/live` | 单频道全部量 |
| combined-spectrum | `/ws/live` | 多节点离散频道电平分线（**不是连续频谱**） |
| rssi-trend | REST `/api/samples/summary` | 摘要桶趋势 |
| signal-events | `/ws/live` | 断流/恢复事件 |
| node-status | REST `/api/nodes` | 接入机/节点连接与能力 |
| site-tree | REST 层级 | 场地→位置→接入机→节点 |
| channel-activity-heatmap | REST 摘要 | 频道×时间活动 |
| target-inspector | REST `/api/targets` | 候选目标证据/时间线/复核 |
| alert-feed | REST `/api/alerts` | 告警处置 |
| spectrum-view | — | **PLANNED**：无真实频率分辨数据 |
| waterfall-view | — | **PLANNED**：无 RAW IQ/频谱帧 |
| node-map | — | **PLANNED**：无可靠位置数据 |

---

## 3. 各页面依赖的后端接口

### 3.1 Console（不依赖 site-server）

| 功能 | 依赖 |
| --- | --- |
| USB 有线连接 | 浏览器 Web Serial（`navigator.serial`）/ Android WebUSB（`navigator.usb`） |
| 无 Web Serial 浏览器 | `serve.mjs`：`GET /api/endpoints` 列端口 → `ws://host/serial?ep=<id>` 桥接 |
| 板卡模式切换（扫描/RH） | `serve.mjs`：`GET/POST /api/mode`（需 `--rh-stop-cmd/--rh-start-cmd`） |
| RotorHazard 只读数据 | 用户在 UI 中填写的 `http(s)://` RH Server JSON API（`model/rhServer.ts`，2 s 轮询） |
| 录制回放 | 本地文件（`Recorder`/`ReplayTransport`） |
| 固件清单/烧录 | `firmware/index.json` + Web Serial/WebUSB |

**结论：原始 Console 没有任何站点/Agent/Measurement 平台接口依赖，可完全离线单机运行。**

### 3.2 Workbench（依赖 rftrack backend，不是 site-server）

- `GET /api/health`、`/api/settings/schema`、`/api/settings/global`、`/api/settings/site/{id}`
- `GET /api/sites`、`/api/relays`、`/api/gateways`、`/api/nodes`、`/api/endpoints`
- `POST /api/sites`、`/api/relays`、`/api/gateways`、`/api/gateways/{id}/probe`
- `GET /api/samples/summary`、`/api/events`、`/api/events/{id}`
- `GET /api/targets`、`/api/targets/{id}`、`POST /api/targets/{id}/review`
- `GET /api/alerts`、`/api/alerts/{id}`、`POST /api/alerts/{id}/ack|silence|resolve`
- `GET /api/layouts`、`POST/PATCH/... /api/layouts/*`
- `WS /ws/live`

### 3.3 平台管理页面（Phase 08 目标，当前不存在）

期望数据来自 site-server 的 `/api/v1/*`（见 §1.4）。**实时事件流接口缺失**，只能先轮询；若 08.5 要求 WebSocket，需要新增 `/ws/v1/site`（属于本轮新增能力，不能改冻结协议）。

---

## 4. 原始 Console 功能保留清单（不可回归）

| 能力 | 保留要求 |
| --- | --- |
| 本地单机运行 | `npm run dev` / `npm run build` + 静态打开，不依赖 site-server |
| 设备连接 | Web Serial、WebUSB、`serve.mjs` 串口桥、`?bridge=ws://...`（serial_bridge.py） |
| 频谱显示 | 全景 + 单频道 Zoom + Waterfall（Canvas） |
| 40 频点 | 以固件能力为准；`channelGrid` 按 profile 渲染，不得假设固定 40 |
| RAW IQ | 抓取/显示/保存逻辑保持 |
| Recording / Playback | 录制、停止、回放只读 |
| 固件烧录 | esptool-js 流程保持 |
| Lap timer / Power meter | 模式切换、语音、RotorHazard 只读 |
| Electron | `app://` 协议、串口/USB 选择器、局域网明文 HTTP 能力 |
| PWA | `sw.js` 生成与离线固件缓存 |
| 设备协议 | 不改二进制帧、命令白名单、`PROTOCOL` 语义 |

---

## 5. Workbench 当前完成度

| 维度 | 完成度 | 证据 |
| --- | --- | --- |
| 页面壳 | 完成（无构建 Vue3） | `index.html`、`app.js` |
| 场地层级 | 完成（四级） | `context.js`、`components/site-tree.js` |
| 实时数据 | 完成（单条 `/ws/live`） | `live.js` |
| 组件库 | 10 可用 + 3 明确未实现 | `registry.js` |
| 布局管理 | 完成（REST 持久化） | `store.js`、`api.js` |
| 告警/目标 | 完成 | `components/alert-feed.js`、`target-inspector.js` |
| 真实频谱/瀑布 | **未实现（PLANNED，且在页面上如实显示原因）** | `registry.js` |
| 与 site-server 对接 | **不存在** | site-server 无挂载、无对应 API 适配 |

**结论：Workbench 是 rftrack 产品的完成品，但不是 Phase 07 平台的 Workbench。** 它的“不把未实现功能包装成可用”的纪律、`api.audit()`、能力声明、空状态处理可以借鉴，但业务层不可直接复用。

---

## 6. 可以复用的组件

### 可直接复用（低风险）

| 资产 | 复用方式 |
| --- | --- |
| `repo/rftrack/frontend/workbench/vendor/vue.global.prod.js` | 新平台 UI 若无构建，可复制同版本 Vue 运行时 |
| `workbench/api.js` 的 `detectApiBase` / `ApiError` / `audit()` 设计 | 以“同源 + 可覆盖前缀 + 错误归一化”为蓝本重写站内 client |
| `workbench/styles.css` 的视觉 token（色彩/间距/表格/状态色） | 作为 08.3 设计规范的起点，按 RF 监测平台风格调整 |
| `workbench/components/parts.js` 的空状态/错误/来源角标模式 | 平台页沿用同一表达 |
| `workbench/grid.js` 的栅格几何 | 仅当平台需要可拖拽仪表盘时复用 |
| `repo/web/src/util/canvas.ts` 的绘图工具 | RF Console 已在用，平台侧不复制 |
| `repo/web/src/i18n.ts` | 若平台需要中英双语可参考；不强制引入 |

### 不可直接复用（数据模型不匹配）

- `workbench/store.js`、`context.js`、`live.js`、全部 `components/*`：绑定 `site/relay/gateway/node/sample` 与 rftrack API。
- `repo/web` 的 `main.ts`、`views/*`、`serial/*`：与设备串口协议强耦合，不能嵌入平台表格页。

---

## 7. 需要新增的组件

| 组件 | 用途 | 依赖接口 |
| --- | --- | --- |
| Platform Shell（主导航 + 顶部状态栏） | Overview / Sites / Gateways / Devices / Measurements / RF Console / System | 无（静态） |
| Overview 卡片/统计 | Site、Agent、Device 在线离线、最近 Measurement、系统状态 | `/api/v1/site`、`/api/v1/agents`、`/api/v1/devices`、`/api/v1/measurements/stats`、`/api/v1/diagnostics` |
| Sites 列表/详情 | Site 配置、下属 Agent、数据状态 | `/api/v1/site`、`/api/v1/agents?site_id=` |
| Gateways 列表/详情 | Agent 在线、心跳、版本、关联 Device | `/api/v1/agents`、`/api/v1/devices` |
| Devices 列表/详情 | 身份、类型、固件、在线、所属 Agent、最后上报 | `/api/v1/devices`、`/api/v1/devices/{id}` |
| Measurements 列表/详情 | 三类时间戳、event_id、完整性状态、过滤 | `/api/v1/devices/{id}/measurements`（**当前无跨设备/时间范围过滤**） |
| System | Site Server/DB/Agent 连接/ingestion 统计/版本 | `/api/v1/diagnostics`、`/health` |
| RF Console 入口 | 打开原始 Console，不内嵌业务数据 | 仅导航链接 |
| 实时状态组件 | Agent/Device 在线变化 | **需轮询或新增 `/ws/v1/site`**（当前缺失） |
| 未实现状态组件 | 明确显示 `PLANNED`/接口缺口 | 本地静态声明 |

**需要但当前接口不支持的页面能力（接口缺口，不得改冻结协议）：**

1. Measurement 跨设备查询与时间范围过滤（现只有按 device 查询 + limit/offset）。
2. Measurement 列表未返回 `agent_received_at`（Phase 07.3 已入库但 REST 未暴露）。
3. 无前端实时事件流 `/ws/v1/site`。
4. `/api/v1/devices` 的 `transport_type` 入参声明了但未使用（`main.py` 中未传给 `list_devices`）。
5. 无单条 Measurement 详情端点（列表已带 `payload`，可先用列表）。
6. Diagnostics 不含版本号/启动时间等运维字段。

---

## 8. 不能合并或不能破坏的模块

| 模块 | 原因 |
| --- | --- |
| `repo/web` 的构建与 `serve.mjs` | 单机 Console 主链路；不得引入 site-server 依赖 |
| `repo/web-desktop`（Electron） | `app://` 协议与 `webSecurity:false` 是为设备/RH 服务的；不得改成加载平台页面 |
| `app/mobile`（Android） | USB CDC 原生链路，本阶段不重构 |
| ESP32-C5 二进制帧协议/命令白名单 | Phase 07 冻结；平台 UI 不得直连设备协议 |
| site-server 数据模型与 Alembic | 08 阶段不得为 UI 改 schema |
| rftrack backend/workbench | 独立产品；不得为归一导航改其 API 或数据模型 |
| 原始 Console 的频谱/Waterfall 显示面积与操作效率 | 不得为了平台视觉统一压缩/嵌入导致操作回归 |

**统一导航 ≠ 统一数据连接层**：平台 Shell 只做导航与平台数据展示；RF Console 继续用原有设备链路。

---

## 9. 当前前端技术债

| # | 技术债 | 影响 | 证据 |
| --- | --- | --- | --- |
| 1 | site-server 无任何前端托管 | 08 必须新增页面与挂载 | `site-server/src/widscan_site/main.py` |
| 2 | `main.py` 声称的 `/ws/v1/site` 不存在 | 无法做真实实时推送，只能轮询 | 同上，全部 `@app.*` |
| 3 | site-server REST **无鉴权** | 平台 UI 上线后管理端点裸露（生产风险） | 无 `Depends`、无中间件 |
| 4 | site-server 无 CORS | 跨源部署会被浏览器拦截；同源托管可规避 | `create_app()` 未加 `CORSMiddleware` |
| 5 | `config/site.example.yaml` 使用 `agent:` 段，`ServerConfig` 期望 `site:` | 示例配置的站点字段被 Pydantic 忽略（现仅因默认值相同而未暴露） | `load_config('config/site.example.yaml')` → `site-001/WIDSCAN58 Site` |
| 6 | `api/`、`measurements/`、`diagnostics/`、`profiles/`、`websocket/` 空目录 | 结构误导，实际逻辑全在 `main.py` | 目录列表 |
| 7 | Measurement REST 未暴露 `agent_received_at` | 08.5 三类时间戳页面只能显示两类 | `main.py` 列表响应字段 |
| 8 | 两套“平台”概念重名（rftrack gateway/node/sample vs site-server agent/device/measurement） | 文档与导航易混淆 | `workbench/registry.js` vs `site-server/main.py` |
| 9 | Console 无路由、全局类实例、localStorage 直读写 | 若强行嵌入平台需大改，违背“不破坏 Console” | `src/main.ts`、`src/model/app.ts` |
| 10 | Console 单元测试仅 56 个、无组件级测试 | UI 回归依赖手工/e2e | `repo/web` vitest 结果 |
| 11 | rftrack 既有失败 `test_info_line`（5 字段 vs 实际 7 字段） | 与 Phase 08 无关但会污染全量回归基线 | `repo/rftrack` pytest |
| 12 | `websockets` 旧版 `ws_handler` 弃用告警 | 未来升级风险 | pytest 警告 |

---

## 10. 推荐的集成方式

### 方案 A（推荐）：新增独立 Platform Shell，由 site-server 同源托管

```
site-server/
└── web/                     # 新增：无构建静态页面（Vue3 运行时副本或原生 TS 产物）
    ├── index.html
    ├── app.js / styles.css
    └── vendor/vue.global.prod.js
```

- site-server 挂载：
  - `/` 或 `/platform/` → 平台 Shell
  - `/app/`（可选）→ `repo/web/dist` 的 Console 构建产物（同 rftrack 的 `mount_console` 模式）
- 优点：
  - 同源，无 CORS；不改变 Console 链路；不依赖 rftrack；
  - 平台 Shell 与 Console 各自独立，导航只做链接；
  - 生产可直接由 site-server 单进程提供“管理 + Console”。
- 约束：
  - 无构建或最小构建，避免“大规模更换技术栈”；
  - 生产模式禁止 Mock，未实现接口显示 `NOT IMPLEMENTED`；
  - 实时能力先用轮询，`/ws/v1/site` 作为明确缺口记录，08.5 再决定是否新增。

### 方案 B（不推荐）：扩展现有 rftrack Workbench

- 需要为 site-server 写一整套 API 适配层、改造 `context/store/components` 的实体模型；
- 会同时影响 rftrack 产品，超出“最小修改”；
- 且 Workbench 的栅格组件与 Site/Gateway/Device 表格页目标不一致。

### 方案 C（不推荐）：把平台页面并入 `repo/web`

- Console 是设备直连优先的单页应用，无路由；引入平台数据会让单机模式产生外部依赖；
- 违反“本地 Console 不得强制依赖 Site Server”。

### 方案 A 的具体落地边界

1. **只读优先**：08.4 先做 Overview / Sites / Gateways / Devices / Measurements / System 的只读展示；写操作（adopt/rename/transfer-owner）在 08.6 验收清单未要求，暂不暴露按钮或标注“未启用”。
2. **接口缺口显式展示**：如 Measurement 时间范围过滤无后端支持，则 UI 不提供假筛选，显示“后端未提供”。
3. **RF Console 入口**：顶部导航“RF Console”打开 Console（同源 `/app/` 或用户配置的地址），不在平台内嵌 iframe，保持操作效率。
4. **视觉规范**：08.3 定义 token；不复制 rftrack 的栅格交互到管理页。
5. **Electron 兼容**：不修改 `web-desktop/main.mjs`；平台页面不放进 Electron 包。

---

## 11. 测试基线（2026-11-05，本轮实际执行）

| 套件 | 命令 | 结果 |
| --- | --- | --- |
| Console 单元测试 | `cd repo/web && npx vitest run` | **56 passed / 10 files**（466 ms） |
| Workbench 单测 | `cd repo/rftrack/frontend/workbench && node --test` | **215 pass / 0 fail / 0 skipped**（324 ms） |
| Gateway Agent | `cd gateway-agent && PYTHONPATH=src python3 -m pytest tests -q` | **95 passed**（42.72 s） |
| Site Server | `cd site-server && PYTHONPATH=src python3 -m pytest tests -q` | **69 passed**（6.54 s） |
| RF Track | `cd repo/rftrack && python3 -m pytest -q` | **649 passed / 1 failed / 1 skipped**（93.70 s） |

说明：

- RF Track 的 1 个失败为既有失败：`tests/test_info_line.py::test_the_current_info_line_yields_all_five_identity_fields` 期望 5 字段，实际多出 `mac: None`、`uptime_ms: None`。本轮未修改 RF Track，如实保留，不计入通过。
- 以上数字均为本轮重新执行的结果，未沿用 Phase 07 历史数字。
- 08.1 为审计阶段，未执行前端构建；构建验证安排在 08.4/08.6。

---

## 12. 进入 08.2 前需要人工确认的问题

1. **平台 Shell 的归属**：确认采用“方案 A：site-server 新增静态 `web/` 并同源托管”。（推荐）
2. **Console 的接入方式**：确认采用“导航外链/同源 `/app/`”，不做 iframe 内嵌。
3. **实时能力**：08.5 是否允许新增 `/ws/v1/site`（只读事件推送），还是本轮只用轮询并把 WebSocket 列为缺口。
4. **写操作范围**：Overview/Sites/Gateways/Devices/Measurements/System 是否只做只读；adopt/rename/transfer-owner 是否本轮不暴露。

**未获确认前，不进入 08.2，不修改任何代码。**
