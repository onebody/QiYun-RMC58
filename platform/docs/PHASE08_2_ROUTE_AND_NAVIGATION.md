# Phase 08.2 — 路由与导航设计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2 — Platform Information Architecture / 配套文档 ③
> 性质：设计文档；路由以 `site-server/web/app.js` **现有实现**为准
> 日期：2026-11-05

## 0. 结论先行

1. 平台使用 **Hash 路由**（`#/...`），不是 History API；静态托管无需服务端 fallback。
2. 路由解析只有 1 个入口：`parseHash()`；`hashchange` 触发整页重建。
3. 导航 7 项与路由 1:1 对应；RF Console 是**独立应用入口**（`/app/`），不是平台路由。
4. 页面上下文只通过 URL query 传递，不使用 localStorage、不使用全局选中状态。
5. 未提供的页面（多站点、事件详情、站点级 Measurement）**不设假路由**。

---

## 1. 路由树

### 1.1 平台路由（Hash）

```text
#/overview                        平台总览（空 hash 与 #/ 的默认页）
#/sites                           站点列表（单站点实例，1 行）
#/sites/:siteId                   Site 详情（siteId 经 decodeURIComponent）
#/gateways                         Gateway 列表（?status=online|offline）
#/gateways/:agentId               Gateway 详情（从列表接口推导）
#/devices                          Device 列表（?status=、?only_adopted=true|false）
#/devices/:deviceId               Device 详情
#/measurements                     Measurement 查询（?device=&frame=&offset=）
#/system                           System 状态
#/console                          RF Console 说明页（非 Console 本体）
#/<其它>                           404 页面（pageNotFound）
```

### 1.2 静态挂载（非平台路由）

| 路径 | 来源 | 行为 |
|---|---|---|
| `/` | `site-server` | 307 → `/platform/` |
| `/platform` | `site-server` | 307 → `/platform/` |
| `/platform/` | `StaticFiles(html=True)` | 平台 Shell + Workbench |
| `/app` | `site-server`（可选） | 307 → `/app/` |
| `/app/` | `StaticFiles(html=True)` | 原始 RF Console（`repo/web/dist`） |
| `/health` | FastAPI | JSON 健康检查 |
| `/docs`、`/openapi.json`、`/redoc` | FastAPI | 自动 API 文档 |

挂载条件：

- 平台：`SITE_PLATFORM_WEB_DIR` 或 `site-server/web` 存在。
- Console：`SITE_CONSOLE_DIR` 或 `repo/web/dist` 存在；不存在则**不挂载**，仅日志提示。

### 1.3 明确不存在的路由（不得在设计中补造）

| 路由 | 原因 |
|---|---|
| `/sites`（多站点列表） | 后端无 `GET /api/v1/sites` |
| `#/measurements/:eventId` | 后端无单条 Measurement 详情端点 |
| `#/events`、`#/events/:id` | 无事件查询端点（`SiteEvent` 表未开放） |
| `#/alerts` | 无告警端点 |
| `#/login`、`#/users` | 无认证体系 |

---

## 2. 路由解析规则（与代码一一对应）

| 规则 | 实现 | 说明 |
|---|---|---|
| 空 hash / `#/` | `raw.slice(1) || "/overview"`，`pathPart === "/"` → `/overview` | 首次打开落在 Overview |
| 首次进入补 hash | `history.replaceState(null, "", "#/overview")` | 不产生历史记录噪声 |
| 路径切分 | `pathPart.split("/").filter(Boolean)` | `#/devices/abc` → `["devices","abc"]` |
| 查询参数 | `new URLSearchParams(queryPart)` | 所有过滤均走 query |
| 路由匹配 | `PAGES[segments[0]] || pageNotFound` | 只匹配一级；二级由页面函数自行分派 |
| 页面切换 | `hashchange` → `renderPage({})` | 整页重建 + 骨架屏 |
| 定时轮询 | `renderPage` 返回 `{ms}` → `schedulePoll` | 切页时 `clearTimeout` 旧定时器 |
| 竞态保护 | 自增 `renderToken` | 旧请求返回后丢弃 |

### 2.1 为什么用 Hash

- 平台由 FastAPI `StaticFiles(html=True)` 托管，**没有** SPA fallback 路由；History API 的 `/devices/abc` 会 404。
- 刷新可恢复（hash 随 URL 保留）。
- 深链接可分享（过滤参数在 hash query 中）。
- 与 Console 的 `/app/` 静态入口互不干扰。

（对比与取舍见 `docs/PHASE08_2_ARCHITECTURE_DECISIONS.md` ADR-08-02。）

---

## 3. 导航结构

### 3.1 侧栏（220 px，固定）

| 顺序 | 导航项 | `data-route` | href | 分组语义 |
|---|---|---|---|---|
| 1 | Overview | `overview` | `#/overview` | 总览 |
| 2 | Sites | `sites` | `#/sites` | 资源：站点 |
| 3 | Gateways | `gateways` | `#/gateways` | 资源：Agent |
| 4 | Devices | `devices` | `#/devices` | 资源：设备 |
| 5 | Measurements | `measurements` | `#/measurements` | 数据 |
| 6 | RF Console | `console` | `#/console` | 独立工作台（入口说明页） |
| — | 间隔 | `.pf-nav__gap` | — | 视觉分组 |
| 7 | System | `system` | `#/system` | 运维 |

激活态：`setActiveNav(route)` 仅给当前 `data-route` 设置 `aria-current="page"`，其余移除。

### 3.2 顶栏

| 元素 | 行为 | 说明 |
|---|---|---|
| 品牌 WIDSCAN58 / Platform | 静态 | 不是首页链接，避免误跳 |
| Site 名称（`#pf-site-label`） | 由 `GET /api/v1/site` 写入 | 初始 `—` |
| Server 徽标（`#pf-server-badge`） | 已连接 / 不可达 / 状态未知 | 由最近一次 REST 结果驱动 |
| 最近刷新（`#pf-last-refresh`） | `HH:MM:SS` | 本地时钟 |
| RF Console ↗（`#pf-console-link`） | `target="_blank"` → `/app/` | `HEAD /app/` 失败时降级为 `#/console` 且文案“未就绪” |

### 3.3 底部状态条

| 字段 | 文案 | 真实性 |
|---|---|---|
| 数据来源 | 真实 API | 平台只读 Site Server REST |
| 刷新 | 最近成功请求时间 | 实测值 |
| WebSocket | 未实现（轮询） | 如实标注 |
| 版本 | 未提供 | 无版本端点 |

### 3.4 导航与登录态

无认证、无用户菜单、无角色切换器。**不得**添加“登录 / 头像 / 角色”等假 UI（见 ADR-08-07）。

---

## 4. 页面跳转关系

| 来源 | 触发 | 目标 | 携带上下文 |
|---|---|---|---|
| Overview | Site 卡 | `#/sites` | 无 |
| Overview | Agent 卡 | `#/gateways` | 无 |
| Overview | Device 卡 | `#/devices` | 无 |
| Overview | 待认领卡 | `#/devices?only_adopted=false` | 认领过滤 |
| Sites 列表 | Site ID 单元格 | `#/sites/:siteId` | `siteId` |
| Site 详情 | Agent ID 单元格 | `#/gateways/:agentId` | `agentId` |
| Site 详情 | Agent 总数卡 / Device 总数卡 | `#/gateways`、`#/devices` | 无（不带 site 过滤） |
| Gateways 列表 | Agent ID 单元格 | `#/gateways/:agentId` | `agentId` |
| Gateway 详情 | Device ID 单元格 | `#/devices/:deviceId` | `deviceId` |
| Devices 列表 | Device ID / Owner Agent 单元格 | `#/devices/:deviceId`、`#/gateways/:agentId` | 对应 ID |
| Device 详情 | “查看该设备全部 Measurement →” | `#/measurements?device=:deviceId` | `device` |
| Measurements | 选择 Device | `#/measurements?device=…` | 重置 `frame`、`offset` |
| Measurements | 选择 Frame | `#/measurements?device=…&frame=…` | 保留 device |
| Measurements | 翻页 | `#/measurements?device=…&frame=…&offset=…` | 三参数 |
| Measurements | Payload “展开” | 无跳转 | 行内展开（不新增路由） |
| 任意页面 | 侧栏导航 | 对应一级路由 | 清空旧 query（新 hash 覆盖） |
| 任意页面 | 顶栏 RF Console ↗ | `/app/`（新标签页） | 无（独立应用） |

### 4.1 跳转约束

1. 所有跨页跳转均为**同标签页 hash 跳转**，唯一例外是 Console 新标签页。
2. 跳转只携带“可枚举的小参数”，不携带对象或状态快照。
3. 详情页不存在的 ID：显示空状态（`Site 不存在` / `Agent 不存在` / `Device 不存在`），**不报错、不空白**。
4. 不实现“返回上一页”按钮，依赖浏览器返回。

---

## 5. 上下文传递

| 上下文 | 载体 | 生命周期 | 是否跨页面保持 |
|---|---|---|---|
| 当前 Site | `GET /api/v1/site` 常量 + 顶栏展示 | 进程（每次请求刷新） | 是（单实例天然唯一） |
| 选中 Site（详情） | `#/sites/:siteId` 路径段 | 路由 | 否（离开即失效） |
| 选中 Agent（详情） | `#/gateways/:agentId` 路径段 | 路由 | 否 |
| 选中 Device（详情） | `#/devices/:deviceId` 路径段 | 路由 | 否 |
| Measurement 查询条件 | `?device=&frame=&offset=` | 路由 | 否（可分享、可刷新恢复） |
| 列表过滤 | `?status=`、`?only_adopted=`、`?site=` | 路由 | 否 |
| 表格滚动位置 / 展开行 | 无持久化 | 页面 | 否（刷新即重置） |
| Console 连接设备 | Console 进程内状态 | Console 会话 | 与平台无关 |

### 5.1 Site 上下文是否跨页面保持

当前 Site Server **单实例单站点**，`cfg.site.id` 唯一，`GET /api/v1/site` 只返回一条。因此：

- Site 上下文等价于全局常量，无需切换器。
- 设计保留 `?site=` 参数位（Gateways 列表已可读），为未来多站点预留。
- **不实现** Site 下拉切换器：无多站点 API 时它只能是假控件（ADR-08-08）。

### 5.2 为什么不用 localStorage

- URL 已是唯一真相来源，localStorage 会造成两套状态。
- 深链接必须可复现；localStorage 会让同一 URL 呈现不同结果。
- 无登录态，无需持久化用户偏好（08.3 再评估 UI 偏好存储）。

---

## 6. 独立 Console 入口

| 入口形态 | 路径 / 方式 | 依赖 Site Server | 状态 |
|---|---|---|---|
| 平台顶栏 | `/app/`（新标签页） | 静态挂载（可选） | EXISTING |
| 平台导航 | `#/console` → 说明页 + 链接 | 否 | EXISTING |
| 直接访问 | `http://<host>:<port>/app/` | 否（但需该服务托管构建产物） | EXISTING（构建后） |
| Electron 桌面端 | `app://widscan58/index.html` | 否 | EXISTING（`repo/web-desktop`） |
| 本地静态 | 直接打开 `repo/web/dist/index.html` | 否 | EXISTING |

### 6.1 Console 入口降级链

1. `window.__PLATFORM_CONFIG__.consoleUrl` 为空 → 顶栏链接改为 `#/console`，文案“RF Console（未配置）”。
2. `consoleUrl` 已配置但 `HEAD` 非 2xx / 抛错 → 顶栏链接改为 `#/console`，文案“RF Console（未就绪）”。
3. 静态挂载失败时 Site Server 正常启动，平台其余 7 页不受影响。

### 6.2 Console 独立性约束

- Console **不读取**平台 hash、不调用 Site REST。
- 平台**不内嵌** Console（无 iframe）、不注入数据、不覆盖 `UiSettings`。
- Console 的设备连接、频谱、Waterfall、RAWIQ、录制回放全部在其自身入口内完成。

---

## 7. 错误页与空状态

### 7.1 路由级

| 场景 | 呈现 | 代码 |
|---|---|---|
| 未知路由 | 标题“页面不存在” + 未知 path + `404` 空状态 | `pageNotFound()` |
| 空 hash | 静默落到 Overview | `history.replaceState` |

### 7.2 数据级

| 场景 | 呈现 | 代码 |
|---|---|---|
| 接口失败（HTTP 4xx/5xx） | 错误条：`HTTP <code>` + 路径 + 中文原因 + “重试” | `errorBar()` |
| 网络不可达 | 错误条 `NET` + “网络不可达”；顶栏徽标转“不可达” | `api()` 捕获 |
| 有旧数据且失败 | 错误条附“显示的是上一次成功数据” + `pf-stale` | `load()` |
| 接口成功但空数组 | 表格空行“无数据（接口成功返回空）” | `dataTable()` |
| 未选 Device（Measurements） | 空状态“请选择 Device”，不发数据请求 | `pageMeasurements` |
| Site 读取失败 | 空状态“无法读取 Site” | `pageSites` |
| 详情 ID 不存在 | 空状态“Site / Agent / Device 不存在” | 各详情函数 |
| Console 未配置 | 空状态“Console 地址未配置” | `pageConsole` |
| 能力未实现 | 行内标注“未实现 / 未提供（原因）” | `unknown()` |
| 页面渲染异常 | 错误条 `ERR` + 异常消息 | `renderPage` catch |
| 首屏加载 | 3 行骨架屏（`aria-busy`） | `skeleton()` |

### 7.3 空状态设计原则

1. 空状态必须说明**为什么空**（未选条件 / 接口返回空 / 能力未提供），不能只写“暂无数据”。
2. 未实现能力必须写明**缺什么**（如“后端未提供时间过滤”“无 GET /api/v1/agents/{id}”）。
3. 不使用插图占位或“即将上线”营销文案。

---

## 8. 路由 ↔ API ↔ 轮询 对照

| 路由 | 主要 API | 轮询间隔 | 可空状态 |
|---|---|---|---|
| `#/overview` | `/api/v1/site`、`/agents`、`/devices`、`/measurements/stats`、`/diagnostics` | 10 s | 接口失败逐项降级 |
| `#/sites` / `#/sites/:id` | `/api/v1/site`、`/api/v1/agents?site_id=` | 15 s | 是 |
| `#/gateways` / `#/gateways/:id` | `/api/v1/agents`、`/api/v1/devices` | 5 s | 是 |
| `#/devices` / `#/devices/:id` | `/api/v1/devices`、`/devices/{id}`、`/devices/{id}/measurements` | 5 s | 是 |
| `#/measurements` | `/api/v1/devices`、`/devices/{id}/measurements` | 无（手动刷新 / 翻页） | 是（未选设备） |
| `#/system` | `/health`、`/api/v1/diagnostics`、`/api/v1/measurements/stats` | 15 s | 是 |
| `#/console` | 无 | 无 | 是（未配置） |

> “无轮询”的页面在 `document.hidden` 恢复时仍会触发一次 `renderPage({quiet:true})`。

---

## 9. 验收清单

| # | 检查项 | 期望 |
|---|---|---|
| 1 | 首屏无 hash | 落在 `#/overview` |
| 2 | 7 项导航 | 全部可点击，激活态唯一 |
| 3 | 刷新任意详情页 | 停留在同一路由 |
| 4 | 深链接 `?device=&frame=&offset=` | 复制到新标签可复现 |
| 5 | 未知路由 | 404 空状态，不白屏 |
| 6 | Console 未构建 | 顶栏降级，平台其他页正常 |
| 7 | 详情 ID 不存在 | 对应空状态 |
| 8 | Site Server 停服 | 错误条 + stale 数据，不崩溃 |
| 9 | Console 独立打开 | 不依赖 Site Server，原能力可用 |
| 10 | 无假路由 | 不存在 `#/measurements/:eventId`、`#/alerts`、`#/login` |
