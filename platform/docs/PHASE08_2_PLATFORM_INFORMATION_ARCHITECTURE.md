# Phase 08.2 — Platform Information Architecture

> 项目：QiYun / widscan58
> 阶段：Phase 08.2 — Platform Information Architecture
> 性质：**设计与映射文档，不修改产品代码**
> 日期：2026-11-05
> 输入：`docs/PHASE08_1_FRONTEND_ASSET_AUDIT.md`、Phase 07 五份验收文档、仓库实际代码
> 配套文档：
> - `docs/PHASE08_2_PAGE_FUNCTION_MATRIX.md`
> - `docs/PHASE08_2_ROUTE_AND_NAVIGATION.md`
> - `docs/PHASE08_2_DATA_FLOW_DESIGN.md`
> - `docs/PHASE08_2_ARCHITECTURE_DECISIONS.md`

---

## 0. 前置检查结论

| 检查项 | 结论 |
|---|---|
| Phase 08.1 审计文档 | 存在且完整（12 节，含技术债与推荐集成方式），可直接引用 |
| Phase 07 四项修复 | B-1 Agent 命令 API 安全 / B-2 Device Owner 授权 / B-3 时间戳完整性 / B-4 Event ID 幂等，**均 PASS** |
| Phase 07 整体验收 | **CONDITIONAL PASS**，无 BLOCKED |
| Phase 07 遗留安全项 | REST 无鉴权、无 CORS；`allow_url_credential` 默认 false。对平台设计的影响：**平台页面只能按“只读、可信网络内”设计** |
| 数据库迁移 | Phase 07 无新增迁移；当前 schema 与模型一致；Site Server 启动有缺失列自检告警 |
| 测试可复现 | Site Server 74 / Gateway Agent 95 / Console 56 / 平台 UI 专项 5 均通过（08.5 / 08.6 实测） |

> 因无 BLOCKED 项，可进入正式设计。

### 0.1 与当前代码的一致性说明（必须前置说明）

本仓库在 Phase 08 后续子阶段中**已经落地**同源 Platform Shell 与 7 个页面（`site-server/web/`，挂载于 `/platform/`，Console 挂载于 `/app/`）。因此本文与配套矩阵中的「实现状态」是按**当前仓库真实代码**标注的：

- 已存在于 `site-server/web` 的页面能力 → `EXISTING`
- 只有部分字段/交互、或缺后端支撑 → `PARTIAL`
- 设计中但代码不存在 → `NEW`
- 后端与协议层面明确不提供 → `NOT SUPPORTED`

本文不把目标架构描述为“已完成”，也不假设 08.3 之后才出现的代码。

---

## 1. 平台整体信息架构

### 1.1 分层（目标结构 × 实际状态）

```text
QiYun / widscan58 Platform
│
├── Platform Shell ......................... EXISTING（site-server/web/index.html）
│   ├── Global Navigation .................. EXISTING（7 项，侧边栏）
│   ├── Global Status ...................... EXISTING（顶栏 + 底部状态条）
│   ├── User / Session Context ............. NOT SUPPORTED（无认证体系）
│   └── Notification Center ................ NOT SUPPORTED（无告警接口）
│
├── Site Workbench ......................... EXISTING（site-server/web/app.js，7 页面）
│   ├── Overview / Sites / Gateways
│   ├── Devices / Measurements
│   └── System
│
├── RF Console ............................. EXISTING（repo/web，独立构建，挂载 /app/）
│   ├── Device Connection (Web Serial / WebUSB / WS bridge / Replay) ... EXISTING
│   ├── Spectrum / Waterfall / RAWIQ / Recording / Playback ........... EXISTING
│   └── 独立运行入口（不依赖 Site Server） .......................... EXISTING
│
└── Shared Infrastructure
    ├── API Client ......................... EXISTING（app.js 内轻量 fetch 封装 + 缓存）
    ├── Event Client ....................... NOT SUPPORTED（无 /ws/v1/site）
    ├── Device Connection Adapter .......... EXISTING（仅在 Console 内，平台不复用）
    ├── State Management ................... EXISTING（页面级局部状态 + 请求缓存 Map）
    └── Shared UI Components ............... EXISTING（app.js 内 el()/badge()/dataTable() 等）
```

### 1.2 设计原则

1. **信息架构优先于视觉统一**：平台页回答“有多少、谁在线、数据到没到”；Console 回答“这台设备此刻的频谱是什么”。
2. **两个数据平面物理隔离**：平台平面（HTTP → Site Server → DB），设备平面（串口/USB → Console）。二者不在前端共享连接。
3. **不伪造**：没有的统计、没有的过滤、没有的告警，一律显示 `unavailable / 未实现 / 未提供`，并可说明原因。
4. **单站点事实**：当前 Site Server 是单实例单站点，多站点为规划项，不作为已完成能力。
5. **只读平台**：不引入写操作与命令控制入口（Phase 07 的授权能力只在 Agent 本地 API 生效）。

---

## 2. 导航结构

### 2.1 主导航（侧边栏，220 px）

| 顺序 | 导航项 | 目标 | 数据平面 |
|---|---|---|---|
| 1 | Overview | `#/overview` | 平台 |
| 2 | Sites | `#/sites` | 平台 |
| 3 | Gateways | `#/gateways` | 平台 |
| 4 | Devices | `#/devices` | 平台 |
| 5 | Measurements | `#/measurements` | 平台 |
| 6 | RF Console | `/app/`（新标签页） | **设备** |
| 7 | System | `#/system` | 平台 |

导航分组语义：1 = 总览；2–5 = 资源管理（Site → Agent → Device → 数据）；6 = 独立工作台；7 = 运维。

### 2.2 全局状态（顶栏 / 底部状态条）

| 位置 | 元素 | 真实来源 |
|---|---|---|
| 顶栏左 | 产品名 + 当前 Site（id + name） | `GET /api/v1/site` |
| 顶栏右 | Site Server 连接徽标（已连接/不可达/状态未知） | 最近一次 REST 结果 |
| 顶栏右 | 最近刷新时间（HH:MM:SS） | 前端本地时间 |
| 顶栏右 | RF Console 入口（`/app/`） | 配置 + 可达性探测 |
| 底栏 | 数据来源（真实 API） | 固定文案，不得显示“演示数据” |
| 底栏 | 轮询状态（`WebSocket: 未实现（轮询）`） | 如实标注 |
| 底栏 | 版本信息 | **未提供** |

### 2.3 当前 Site 上下文

- 本实例只有一个 Site，上下文 = 全局常量（来自 `GET /api/v1/site`）。
- 设计保留 `?site=` 参数与 Site 上下文占位，但**不实现 Site 切换器**（无多站点 API，切换器会是假控件）。
- 未来多站点时，切换器写入 URL query 并驱动各页面的 `site_id` 入参；届时再实现，不在本阶段。

---

## 3. 路由设计

完整路由树、跳转关系、上下文传递、错误与空状态见 **`docs/PHASE08_2_ROUTE_AND_NAVIGATION.md`**。摘要：

```text
#/overview
#/sites                 #/sites/:siteId
#/gateways              #/gateways/:agentId
#/devices               #/devices/:deviceId
#/measurements?device=&frame=&offset=
#/system
/app/                   （独立 Console，非平台路由）
```

关键决策：**沿用已有 Hash 路由**（静态托管无需服务端 fallback；刷新可恢复；深链接可分享），不迁移到 `/path` 风格。理由见 ADR-08-02。

---

## 4. 页面层级关系

```text
                ┌──────────────┐
                │  Overview    │
                └──────┬───────┘
       ┌───────────────┼────────────────┬────────────────┐
       ▼               ▼                ▼                ▼
 ┌──────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
 │ Sites    │   │ Gateways   │   │ Devices    │   │ Measurements│
 └────┬─────┘   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
      │               │                  │                ▲
      │  Site 详情 ──▶ │ Agent 详情       │                │
      │               │    │             │                │
      │               │    └── 关联 Device ──▶ Device 详情 │
      │               │                  │   └── 最近 Measurement ──┘
      ▼               ▼                  ▼
   counts/agents    身份/心跳/版本     身份/端点/会话
                                             │
                                             └── Measurement 行内展开（payload）

 ┌────────────────────────────────────────────────────────────┐
 │ RF Console（/app/，新标签页）——与上图无数据依赖，仅共享“设备名/ID”认知 │
 └────────────────────────────────────────────────────────────┘
```

跳转规则：

1. Overview 卡片 → 对应列表（携带状态过滤）。
2. Sites 详情 → Gateways（`?site=`）。
3. Gateways 详情 → Devices（`?owner=`）。
4. Devices 详情 → Measurements（`?device=`）。
5. Measurements 行 → 行内展开（**无独立事件详情页**，因无单条详情端点）。
6. Console 不做平台内嵌跳转，只做新标签页出口。

---

## 5. 功能模块映射与状态

| 模块 | 归属 | 状态 | 依据 |
|---|---|---|---|
| 导航壳、顶栏、状态条 | Platform Shell | EXISTING | `site-server/web/index.html` |
| Hash 路由与页面渲染 | Workbench | EXISTING | `site-server/web/app.js` |
| 统一视觉 Token | Shared UI | EXISTING | `site-server/web/styles.css` |
| Overview 统计 | Workbench | EXISTING（告警项 NOT SUPPORTED） | `/api/v1/site`、`/agents`、`/devices`、`/measurements/stats`、`/diagnostics` |
| Site 列表/详情 | Workbench | PARTIAL（单站点） | 仅 `GET /api/v1/site`，无 `/sites` |
| Gateway 列表/详情 | Workbench | PARTIAL（无详情端点/无凭证） | `GET /api/v1/agents` |
| Device 列表/详情 | Workbench | PARTIAL（无写操作、过滤不全） | `GET /api/v1/devices`、`/devices/{id}` |
| Measurement 查询 | Workbench | PARTIAL（仅按 Device、无时间范围、无 agent_received_at） | `/devices/{id}/measurements`、`/measurements/stats` |
| System 状态 | Workbench | PARTIAL（无日志/版本/配置） | `/health`、`/diagnostics` |
| RF Console | 独立模块 | EXISTING（全量原能力） | `repo/web` + `/app/` 挂载 |
| Electron 桌面端 | 独立模块 | EXISTING（未改动） | `repo/web-desktop` |
| 实时事件流 | Shared Infra | NOT SUPPORTED | `/ws/v1/site` 不存在 |
| 认证/角色 | Shared Infra | NOT SUPPORTED | REST 无鉴权 |
| 通知中心 | Platform Shell | NOT SUPPORTED | 无告警端点 |

**不可合并 / 不可破坏**

- `repo/web`（Console 源码、构建产物、`UiSettings.uiMode` 三模式、Web Serial 链路、Canvas 渲染）
- `repo/web-desktop`（Electron 入口与 `app://` 协议）
- `repo/rftrack/frontend/workbench`（另一产品，数据模型 site→relay→gateway→node，与本站 site→agent→device 不同）
- 设备协议 / 帧格式 / event_id 规则 / 数据库模型

---

## 6. 模块边界

### A. Platform Shell vs Workbench

| 归属 | 内容 |
|---|---|
| Shell 负责 | 导航、当前 Site 展示、连接状态、最近刷新、Console 出口、全局错误提示位置 |
| Workbench 负责 | 各页面的数据获取、过滤、表格、详情、空/错误状态 |
| Shell 不负责 | 任何业务数据缓存、任何写操作、任何设备连接 |
| 全局状态栏显示 | Site Server 可达性、数据来源、轮询模式、版本（未提供） |
| 当前 Site | 单实例下为全局常量；保留 URL 上下文位置，不实现切换器 |
| 跨页面保持 | **仅** Site 标识与用户所选过滤条件（写入 hash query）；不保持表格滚动位置等会话态 |

### B. RF Console

| 问题 | 结论 |
|---|---|
| 是否独立路由 | 是，但为**独立应用入口**（`/app/`），不进入平台 Hash 路由 |
| 是否独立状态管理 | 是。Console 内 `UiSettings` / 设备实例 / 帧解析器完全自持，平台不读写 |
| 无 Site Server 能否运行 | 能。直接打开 `repo/web/dist` 或 Electron 即可；平台只是“多一个入口” |
| Web Serial/USB 权限 | 保持不变（同源 `/app/` 或 `file`/`app://` 下由浏览器/Electron 授权） |
| 与 Workbench 如何共享设备信息 | **不共享运行时数据**；共享仅限于“人读层面的设备 ID/名字”（用户在两个页面各自识别） |
| 如何避免重复连接 | Console 与 Workbench 数据平面隔离：Workbench 从不打开串口；Console 从不拉 Site API |

### C. API 与实时事件

| 问题 | 结论 |
|---|---|
| 统一 API Client | 部分有：`app.js` 内单一 `api()/load()`（同源 fetch、缓存、错误归一）。未抽成独立包 |
| Site/Device API 组织 | 均走同一 `api(path, params)`；无版本前缀抽象，路径直写 `/api/v1/...` |
| WebSocket 统一连接管理 | **不存在**（无前端事件流）；Console 内有自己的 WS bridge，与平台无关 |
| 页面切换避免重复订阅 | 轮询按页面 arm/clear；隐藏页暂停；同端点共享缓存键 |
| 实时事件如何更新列表 | 当前为轮询覆盖式刷新；未来 WS 到位后按 key 增量 patch |
| 断线恢复 | 请求失败保留上次成功数据 + 陈旧标记；维持固定间隔轮询；手动“重试”；恢复后自动清除错误。**无指数退避**（未实现） |
| 区分 API 数据 vs 本地设备数据 | 平台只显示 API 数据；Console 只显示设备数据；**禁止跨平面填充** |

### D. 状态管理（责任划分）

| 状态 | 归属 | 生命周期 |
|---|---|---|
| 当前 Site | Shell（常量 + hash 占位） | 进程 |
| 当前 Agent / Device（选中项） | URL query | 路由 |
| 列表数据与请求缓存 | Workbench（`state.cache` Map） | 页面（跨刷新保留至下一次成功） |
| Measurement 历史 | Workbench（按 device 查询结果） | 页面 |
| 实时事件 | **无**（未实现） | — |
| Console 连接设备 / 频谱 / Waterfall / RAWIQ | Console（`UiSettings` + 设备实例 + 渲染器） | Console 会话 |
| 系统连接状态 | Shell（最近一次 API 结果） | 进程 |
| UI 偏好（如列宽/主题） | 未实现（存储方案待 08.3 定） | — |

明确**不使用单一全局 Store**：平台侧数据按页面局部持有；高频射频数据永不下沉到平台状态。

---

## 7. 角色与权限

### 7.1 现状（真实能力）

| 能力 | 现状 |
|---|---|
| 平台页面读取 | **无任何认证**：任何能访问该端口的人都能读（同源内网） |
| 平台写操作 | UI 不提供；后端存在写端点但无鉴权 |
| Device 命令授权（Phase 07.2） | 在 **Gateway Agent 本地 API** 生效（`X-Agent-Token` + owner/observer 语义） |
| Agent 本地命令 API | 仅绑定本机（默认 `127.0.0.1`），有 Bearer/Token 校验 |

### 7.2 角色矩阵（设计 vs 实现）

| 角色 | 设计权限 | 当前实现 |
|---|---|---|
| Read Only（Observer） | 浏览 Overview/Sites/Gateways/Devices/Measurements/System，打开 Console | **可用**（但无身份区分，等价于“任何访问者”） |
| Operator | 设备认领/重命名/连接测试等 | **NOT SUPPORTED**：平台无写 UI，后端无平台级鉴权 |
| Site Admin | Site 配置、Agent 配对/吊销 | **NOT SUPPORTED**（仅 REST 直接调用，无 UI、无鉴权） |
| System Admin | 系统配置/日志/重启 | **NOT SUPPORTED** |

### 7.3 安全边界（硬性）

1. 隐藏按钮不等于权限控制；所有写操作必须后端校验。
2. Observer 默认只读；`identity_confidence=low` 的设备在平台侧只读标注。
3. 不通过前端参数提权；平台不接受任何角色参数。
4. **不得在浏览器暴露 Agent 本地命令凭证**（`X-Agent-Token` 只存在于 Agent 侧配置/环境变量）。
5. **不得把 Agent 本地 API 映射为公网前端 API**（其 `/devices/{id}/commands`、`/connect` 等禁止出现在平台页面调用链）。
6. 当前未实现的权限能力一律标注 `NOT SUPPORTED`，不虚构角色。

---

## 8. 数据流（摘要，详见专项文档）

| 数据流 | 真实路径 | 状态 |
|---|---|---|
| Site 管理 | `Site Server REST` → `app.js api()` → 页面 | EXISTING |
| HTTP → 上行 | `Gateway Agent(site_connector)` → WS `/ws/v1/agents/connect` → Ingestion → SQLite | EXISTING |
| 前端实时事件 | `Site Server` → **（缺失前端事件流）** → 前端 | NOT SUPPORTED（轮询替代） |
| RF Console | `C5 设备` → 串口/USB/WS bridge → 解析器 → Canvas 渲染 | EXISTING |
| 共享数据 | 无运行时共享；仅“人读语义” | 设计约束 |

---

## 9. 性能与可用性要求

| 项 | 要求 | 现状 |
|---|---|---|
| 高频频谱不重渲染整页 | 仅 Canvas 局部重绘 | Console 已满足（独立应用） |
| RAWIQ 不入全局 Store | 平台状态不接收射频数据 | 满足（物理隔离） |
| 大量 Measurement | 分页（`limit/offset`，默认 100） | 满足；无 total 故不显示总数 |
| 实时订阅与清理 | 页面 arm/clear 轮询；隐藏页暂停 | 满足 |
| WS 断线重连 | 未来事件流需指数退避 + 快照补齐 | 未实现（当前只有固定间隔轮询 + 手动重试） |
| 页面切换不重复建连 | 平台不建设备连接 | 满足 |
| 长时运行内存 | 请求缓存按 key 覆盖、不累积 | 08.6 观察无明显增长 |
| 两模块互不影响 | 独立应用 + 独立构建 | 满足 |
| 布局 | 主内容 ≥1280 px，目标 1920×1080，笔记本可用 | 仅 1512×724 CSS 实测；**1920×1080 为栅格推演**（08.2.1 WP-02） |

---

## 10. 尚未实现的功能（规划项，不在本阶段实施）

1. 多站点（`GET /api/v1/sites`、Site 切换器、跨站聚合）
2. 前端事件流 `/ws/v1/site` + 断线补快照
3. `agent_received_at` 在 Measurement 响应中暴露
4. 站点级 Measurement 列表、时间范围过滤、分页 total
5. Agent 详情端点、凭证状态、Agent 级 ingestion
6. 告警/通知中心
7. 系统日志、版本、运行配置端点
8. 任何写操作（认领、重命名、配对、吊销、转移所有权）
9. 平台级认证与角色（RBAC）
10. 设备 `transport_type` 服务端过滤

---

## 11. 对 Phase 08.3 的输入

08.3 必须基于本文实现：

1. 7 页 × 4 类状态（正常 / 空 / 错误 / 未实现）的视觉规范。
2. 徽标语义表（in §7 of 08.2 earlier / 见 `PHASE08_2_ROUTE_AND_NAVIGATION.md` 状态语义）。
3. 布局栅格：侧栏 220 px、顶栏 48 px、底栏 28 px、主内容自适应。
4. 表格规范：sticky 表头 + sticky 首列、行高 36 px、长标识单行省略 + title。
5. 禁止：频谱/Waterfall 的缩略预览、假图表、装饰性动画。
6. Console 视觉不做统一改造，仅共享色彩语义。

---

## 12. 结论

- 信息架构、页面职责、模块边界、路由、数据流、状态归属、权限边界、缺口清单均已明确。
- 设计与实际代码**无矛盾**：所有 EXISTING 项均能在 `site-server/web`、`repo/web` 中指认；所有 NOT SUPPORTED 项均已在 UI 中显式标注。
- 无需要立即修改的后端 API / 数据库 / 冻结协议；所有缺口仅记录。

**Phase 08.2 状态：CONDITIONAL PASS**（设计完整；待确认项：多站点定位、事件流优先级、权限模型范围）。

**本阶段结束，不进入 Phase 08.3，等待人工审核。**

---

## 13. Phase 08.2.1 补充约束（PROPOSED）

本文的结论已被 Phase 08.2.1 的代码审计约束进一步收紧，新增文档：

- `docs/PHASE08_2_1_MULTI_SITE_ADR.md`（ADR-08.2.1-01）
- `docs/PHASE08_2_1_EVENT_STREAM_ADR.md`（ADR-08.2.1-02）
- `docs/PHASE08_2_1_SECURITY_PERMISSION_ADR.md`（ADR-08.2.1-03）
- `docs/PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md`（F-01–F-16 证据清单与交叉依赖）
- `docs/PHASE08_2_1_IMPLEMENTATION_DEPENDENCY_MATRIX.md`（WP-01–WP-14）

关键补充：

1. 多站点：数据库 `site_id` 基础已存在，但**能力未实现**；设备跨 Site 规则未定；不得做切换器或 Mock。
2. 事件流：当前只有 Agent 双队列（各 128）与前端轮询；**无逐事件 ACK、无持久化、无快照**；协议冻结。
3. 安全：REST 无鉴权/无 CORS；CORS 缺失不构成安全措施；生产部署前必须完成 Phase 08.2.2。
4. 08.3 的唯一硬门禁是 **1920×1080 真实浏览器验收**（WP-02）。

以上均为 **PROPOSED**，等待人工审批；本轮未修改业务代码、数据库、API 或协议。
