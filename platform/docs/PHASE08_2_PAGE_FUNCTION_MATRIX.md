# Phase 08.2 — 页面功能矩阵

> 项目：QiYun / widscan58
> 阶段：Phase 08.2 — Platform Information Architecture / 配套文档 ②
> 性质：设计审计文档，不代表新增实现
> 日期：2026-11-05
> 依据：`docs/PHASE08_1_FRONTEND_ASSET_AUDIT.md`、Phase 07 五份验收文档、`site-server/web`、`repo/web`、`repo/web-desktop` 实际代码

## 0. 状态口径

| 标记 | 含义（严格按当前仓库代码判定） |
|---|---|
| `EXISTING` | 已实现且可用；能在 `site-server/web` 或 `repo/web` 中指认到对应代码 |
| `PARTIAL` | 部分实现：主路径可用，但字段缺失、过滤缺失、或依赖列表接口推导 |
| `NEW` | 设计中存在、代码中不存在，且**后端已具备或只需少量补齐**即可实现 |
| `NOT SUPPORTED` | 后端/协议/安全边界明确不提供；UI 已显式标注 `未实现 / 未提供` |

判定原则：

1. 以真实代码与真实 API 为准，不以设计图为准。
2. `NOT SUPPORTED` 不等于“以后不做”，而是“本阶段及其现网后端不可用”。
3. 任何依赖 `GET /api/v1/sites`、`GET /api/v1/agents/{id}`、`GET /api/v1/measurements`、`GET /api/v1/measurements/{event_id}`、`/ws/v1/site` 的功能，一律不得标为 `EXISTING`。

---

## 1. Platform Shell（全局壳）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Shell | 侧栏 7 项导航（Overview/Sites/Gateways/Devices/Measurements/RF Console/System） | EXISTING | 静态 HTML | 无 | `site-server/web/index.html` 的 `.pf-nav` + `data-route` | 7 项可点击；当前项 `aria-current="page"` |
| Shell | 顶栏当前 Site（名称 + ID） | EXISTING | `GET /api/v1/site` | `/api/v1/site` | `app.js` 写 `#pf-site-label` | 名称来自接口，非硬编码 |
| Shell | Site Server 连接徽标（已连接/不可达/状态未知） | EXISTING | 最近一次 REST 结果 | 全部 REST | `updateServerBadge()` | 停服后 5–15 s 内变为“不可达” |
| Shell | 最近刷新时间 | EXISTING | 前端本地时钟 | 无（由成功请求触发） | `updateRefreshLabels()` | 每次成功请求后更新时间 |
| Shell | RF Console 顶栏入口 + 可达性探测 | EXISTING | `HEAD /app/` | `/app/` 静态挂载 | `initConsoleLink()` | 未挂载时降级为 `#/console` 且文案“未就绪” |
| Shell | 底部状态条（数据来源 / 刷新 / WebSocket / 版本） | PARTIAL | 静态 + 实际刷新时间 | 无 | `index.html` `.pf-statusbar` | WebSocket 如实显示“未实现（轮询）”；版本显示“未提供” |
| Shell | 用户 / 会话上下文（登录态、当前用户、角色） | NOT SUPPORTED | 无 | 无认证端点 | UI 不呈现 | 页面不出现虚假用户信息 |
| Shell | 通知中心 / 告警入口 | NOT SUPPORTED | 无 | 无告警端点 | UI 不呈现 | 不出现假红点、假未读计数 |

---

## 2. Overview（平台总览）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Overview | Site 统计卡（当前实例 = 1） | EXISTING | `GET /api/v1/site` | `/api/v1/site` | `statCard()` → `#/sites` | 数字与接口一致；点击进入 Sites |
| Overview | Gateway Agent 在线 / 总数卡 | EXISTING | `GET /api/v1/agents`（失败时回退 `counts`） | `/api/v1/agents` | `statCard()` → `#/gateways` | 与 Agents 列表头数一致 |
| Overview | Device 在线 / 总数卡 | EXISTING | `GET /api/v1/devices`（失败时回退 `counts`） | `/api/v1/devices` | `statCard()` → `#/devices` | 与 Devices 列表头数一致 |
| Overview | 待认领 Device 卡 | EXISTING | `counts.devices_pending` | `/api/v1/site` | `statCard()` → `#/devices?only_adopted=false` | 与 Devices 未认领过滤结果一致 |
| Overview | Measurement 总量 + 最近落库时间 | EXISTING | `GET /api/v1/measurements/stats` | `/api/v1/measurements/stats` | `card()` + `formatUTC()` | 数值随实时写入增长 |
| Overview | 系统状态卡（Site server / DB 路径 / Agent 连接 / 重复事件 / 完整性错误） | EXISTING | `GET /api/v1/diagnostics` | `/api/v1/diagnostics` | `kv()` | 字段与 diagnostics 响应一致 |
| Overview | 数据接入异常指标 | PARTIAL | `diagnostics.ingestion` 进程内累计计数 | `/api/v1/diagnostics` | `kv()` 两行 | 只显示计数，无趋势、无阈值、无告警 |
| Overview | 最近事件列表 | NOT SUPPORTED | 无（`SiteEvent` 表存在但无 REST 端点） | 无 | 卡片显示 `未实现` | 页面明确标注“未实现（Site Server 未提供告警接口）” |
| Overview | 告警 / 通知 | NOT SUPPORTED | 无 | 无 | 卡片显示 `未实现` | 不伪造告警条目 |

---

## 3. Sites（站点）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Sites 列表 | 单站点列表（当前实例 1 行） | PARTIAL | `GET /api/v1/site` | `/api/v1/site` | `dataTable()` | 仅 1 行；显式说明“多站点列表接口未提供” |
| Sites 列表 | 字段：名称 / Site ID / 状态 / Agent 在线·总 / Device 在线·总 / Measurement 累计 | PARTIAL | 同上 + `counts` | `/api/v1/site` | 列定义在 `pageSites` | 字段与 `counts` 一致；**无“最近活动时间”字段**（接口未提供） |
| Sites 列表 | 位置 / 时区 | EXISTING | `site.location` / `site.timezone` | `/api/v1/site` | `valueCell()` | 空值显示 `—`，不显示假地点 |
| Site 详情 | `#/sites/:siteId` 基础信息（ID/名称/描述/位置/时区/状态/创建·更新时间） | PARTIAL | `GET /api/v1/site` | `/api/v1/site` | `kv()` | 任意 `siteId` 都读同一实例；不存在的 id 只显示“Site 不存在” |
| Site 详情 | 下属 Gateway Agent 列表 | EXISTING | `GET /api/v1/agents?site_id=` | `/api/v1/agents` | `dataTable()` + 行链接 | 与 Gateways 列表一致；行可跳转 Agent 详情 |
| Site 详情 | 统计卡（Agent/Device/待认领/Measurement） | EXISTING | `counts` | `/api/v1/site` | `statCard()` | 与 Overview 同源一致 |
| Site 详情 | Site 级最近 Measurement | NOT SUPPORTED | 无站点级列表端点 | 无（仅 `/devices/{id}/measurements`） | 显示 `未实现` | 必须声明“站点级列表未实现” |
| Site 详情 | Site 运行状态（在线率、接入异常、最近活动） | PARTIAL | `status` + `counts` | `/api/v1/site` | `valueCell()` | 只有静态 `status`，无时间序列 |
| Sites | 多站点列表 + Site 切换器 | NEW | 需要 `GET /api/v1/sites` | `GET /api/v1/sites`（404） | 设计占位：URL `?site=` | 代码中**不得**出现假切换器 |

---

## 4. Gateways（Gateway Agent）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Gateways 列表 | Agent 列表（ID/名称/状态/版本/平台/OS/最后心跳/Device 数） | EXISTING | `GET /api/v1/agents` | `/api/v1/agents` | `dataTable()` | 与 Site 详情下属列表一致 |
| Gateways 列表 | 状态过滤（全部/在线/离线） | EXISTING | 服务端 `status` 参数 | `/api/v1/agents?status=` | URL `?status=` | 切换后 URL 变化且结果变化 |
| Gateways 列表 | Site 过滤 | PARTIAL | 服务端 `site_id` 参数 | `/api/v1/agents?site_id=` | URL `?site=`（无 UI 控件） | 只能由 URL 驱动；无下拉框 |
| Gateways 列表 | 心跳超时提示（后端仍 online 但 >45 s） | EXISTING | `last_seen_at` 与本地时间 | `/api/v1/agents` | `agentBadge()` | 45 s 后显示“心跳超时”徽标 |
| Gateway 详情 | `#/gateways/:agentId` 身份信息（ID/名称/Site/状态/版本/平台/OS/心跳/注册时间） | PARTIAL | 从列表接口过滤 | `/api/v1/agents` | 列表内查找 + `kv()` | 无 `GET /api/v1/agents/{id}`，找不到显示“Agent 不存在” |
| Gateway 详情 | 凭证状态 / 凭证过期时间 | NOT SUPPORTED | 无端点 | 无 | `unknown()` 标注 | 显示“未提供（无 REST 端点）” |
| Gateway 详情 | 关联 Device（按 `owner_agent_id`） | EXISTING | `GET /api/v1/devices` | `/api/v1/devices` | 前端过滤 + `dataTable()` | 与 Devices 列表的 Owner 字段一致 |
| Gateway 详情 | 最近事件 / 错误信息 | NOT SUPPORTED | 无 | 无 | 不呈现 | 不伪造错误日志 |
| Gateways | 远程命令 / 连接控制 / 重启入口 | NOT SUPPORTED | Agent 本地 API（仅本机） | **禁止**映射到平台 | 不提供任何入口 | 平台页面调用链中不得出现 Agent 命令端点 |

---

## 5. Devices（设备）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Devices 列表 | 设备列表（ID/名称/硬件/固件/状态/Owner/所有权/最后上报/端点数量） | EXISTING | `GET /api/v1/devices` | `/api/v1/devices` | `dataTable()` | 字段与响应一致；行可进详情 |
| Devices 列表 | 状态过滤（online/pending/offline/error/disabled/identity_conflict） | EXISTING | 服务端 `status` | `/api/v1/devices?status=` | URL `?status=` | 六种状态均可选 |
| Devices 列表 | 认领过滤（已认领/未认领） | EXISTING | 服务端 `only_adopted` | `/api/v1/devices?only_adopted=` | URL `?only_adopted=` | 与 `adopted_at` 语义一致 |
| Devices 列表 | `transport_type` 过滤 | NOT SUPPORTED | 后端接受参数但未参与查询 | `/api/v1/devices?transport_type=` | 显示 `未实现` | UI 明确标注“后端入参未生效” |
| Device 详情 | `#/devices/:deviceId` 身份（Device ID/显示名/硬件/固件/协议/PHY/状态/身份置信/首末次发现/能力） | EXISTING | `GET /api/v1/devices/{id}` | `/api/v1/devices/{id}` | `kv()` | 与详情响应一致 |
| Device 详情 | 所有权（Owner Agent / ownership_status / 分配·更新·认领时间） | EXISTING | 同上 | `/api/v1/devices/{id}` | `kv()` | 认领前后状态可区分 |
| Device 详情 | 端点列表（Agent / transport_type / endpoint_identifier / status / last_connected_at） | EXISTING | 详情响应 `endpoints` | `/api/v1/devices/{id}` | `dataTable()` | 端点数为 0 时显示空表提示 |
| Device 详情 | 连接会话（Session / Agent / **boot_id** / 连接·断开时间 / 原因 / 状态） | EXISTING | 详情响应 `sessions`（最近 20） | `/api/v1/devices/{id}` | `dataTable()` | `boot_id` 列明确为设备启动周期标识 |
| Device 详情 | 最近 Measurement（20 条，Frame / Site Received / Sequence / Event ID） | PARTIAL | `GET /devices/{id}/measurements?limit=20` | `/api/v1/devices/{id}/measurements` | `dataTable()` + 链接到全量 | 未展示 `device_timestamp_ms` / `agent_received_at` |
| Device 详情 | 历史事件（device.online / state_change / 错误历史） | NOT SUPPORTED | 无事件查询端点 | 无 | 不呈现 | 不伪造事件时间线 |
| Device 详情 | `agent_boot_id` 展示 | NOT SUPPORTED | 无（Agent 内部变量，未进入 REST） | 无 | 不呈现 | 文档明确：`agent_boot_id` 仅用于 Agent 诊断，不暴露给平台 |
| Device 详情 | 写操作（认领 / 重命名 / 转移所有权 / 删除） | NOT SUPPORTED | 后端有端点但无平台鉴权 | `POST adopt`、`PATCH`、`DELETE`、`POST transfer-owner` | 平台 UI 不提供 | 前端无写按钮；不通过 URL 参数触发写操作 |

---

## 6. Measurements（测量数据）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| Measurements | Device 选择查询 | EXISTING | `GET /api/v1/devices` 填充下拉；数据来自设备级列表 | `/api/v1/devices`、`/api/v1/devices/{id}/measurements` | URL `?device=` | 未选设备显示空状态，不发数据请求 |
| Measurements | 实时数据列表（自动刷新） | PARTIAL | 无轮询；仅手动“刷新”/翻页 | `/api/v1/devices/{id}/measurements` | `pageHead` 刷新按钮 | 页面不自动增长；需手动刷新才能看到新数据 |
| Measurements | Frame Type 过滤（METRICS/SPECTRUM/TELEMETRY/LEVELS/RAWIQ/LOG） | EXISTING | 服务端 `frame_type` | `/api/v1/devices/{id}/measurements?frame_type=` | URL `?frame=` | 切换后 URL 与结果同步 |
| Measurements | 分页（`limit=100` + `offset`，上一页/下一页） | EXISTING | 服务端 `limit/offset` | 同上 | `?offset=` + `pf-pager` | 本页条数 < limit 时禁用“下一页” |
| Measurements | 时间范围过滤 | NOT SUPPORTED | 后端无时间参数 | 无 | 标注 `未实现` | 不出现无效时间输入框 |
| Measurements | Site / Agent 过滤 | NOT SUPPORTED | 后端仅设备级查询 | 无 | 标注 `未实现` | 不伪造过滤控件 |
| Measurements | Event ID 查询 | NOT SUPPORTED | 无查询端点/参数 | 无 | 不提供输入框 | 明确记录为缺口 |
| Measurements | 时间字段展示（Device Timestamp / Agent Received / Site Received） | PARTIAL | `device_timestamp_ms`、`created_at` 可用；`agent_received_at` **接口未返回** | `/api/v1/devices/{id}/measurements` | 三列独立展示，Agent Received 列显示 `—` + 表头注释 | 三个时间语义不合并；不得把 `created_at` 当 Agent 接收时间 |
| Measurements | Device / Boot ID / Agent 标识区分 | PARTIAL | `device_boot_id`、`agent_id` 已返回；`agent_boot_id` 未暴露；`device_id` 由已选设备上下文体现 | 同上 | 独立列 `Boot ID` / `Agent` + 页头已选设备 | 不把 Agent 启动标识当设备身份；行内不重复 device_id 列 |
| Measurements | Payload 行内展开 | EXISTING | `payload_json` | 同上 | `payloadButton()` + `pf-json` | 展开/收起不破坏表格 |
| Measurements | 独立事件详情页 `#/measurements/:eventId` | NOT SUPPORTED | 无单条详情端点 | 无 | 设计改为行内展开 | 不新增假路由 |

---

## 7. RF Console（原始射频工作台）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| RF Console | 独立入口 `/app/`（新标签页） | EXISTING | `repo/web/dist` 静态挂载 | `/app/`（可选挂载） | 直接复用原构建产物 | 未构建时不挂载，链路降级为说明页 |
| RF Console | 平台内说明页 `#/console`（入口、能力边界、不注入数据） | EXISTING | 静态 | 无 | `pageConsole()` | 明确“平台不内嵌、不注入数据” |
| RF Console | 设备连接 / 断开（Web Serial） | EXISTING | 浏览器串口授权 | 无（浏览器 API） | `WebSerialTransport` | 授权后可连接真实 C5 |
| RF Console | WebUSB（设备选择 / 烧录） | EXISTING | 浏览器 USB 授权 | 无 | `webusb.ts` / `flasher.ts` | 设备列表来自浏览器授权 |
| RF Console | WebSocket 串口桥 | EXISTING | `ws://` 桥接服务 | 桥接服务（用户自备） | `WebSocketTransport` | 可连接已运行桥接 |
| RF Console | 录制 / 回放（`.widscan58`） | EXISTING | 原始字节流 | 无 | `Recorder` + `ReplayTransport` | 录制文件可回放，回放只读 |
| RF Console | 频谱 / 频点 / Waterfall / RAWIQ | EXISTING | 设备帧 | 无 | `views/panorama`、`views/rawiq`、`views/channelGrid` | Canvas 局部重绘，不重渲染整页 |
| RF Console | 三模式（monitor / timer / power） | EXISTING | `UiSettings.uiMode` | 无 | 原 `main.ts` 模式切换 | 模式切换不丢失设备连接 |
| RF Console | 平台 ↔ Console 设备身份共享 | NOT SUPPORTED | 无映射表 | 无 | 不实现 | 两平面数据不互相注入 |
| RF Console | Console 内嵌平台导航 | NOT SUPPORTED | 无 | 无 | 不修改 Console | 保持独立入口与原 UI |

---

## 8. System（系统管理）

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| System | 服务健康（`ok` + Site ID） | EXISTING | `GET /health` | `/health` | `kv()` + `badge()` | 停服后显示“不可用” |
| System | Site ID / 数据库路径 / 活跃 Agent 连接 | EXISTING | `GET /api/v1/diagnostics` | `/api/v1/diagnostics` | `kv()` | 与 diagnostics 一致 |
| System | Measurement 总量 + 最近数据时间 | EXISTING | `GET /api/v1/measurements/stats` | `/api/v1/measurements/stats` | `kv()` | 随实时写入变化 |
| System | 站点计数（Agent/Device/待认领/Measurement） | EXISTING | `diagnostics.counts` | `/api/v1/diagnostics` | `kv()` | 与 Overview 一致 |
| System | Ingestion 统计（落库/重复/非法 event_id/完整性错误等） | EXISTING | `diagnostics.ingestion` | `/api/v1/diagnostics` | 横向条形 `pf-bars` | 进程内累计值；重启归零需说明 |
| System | 数据库运行状态 | PARTIAL | 仅 `db_path` | `/api/v1/diagnostics` | `kv()` | 无连接健康检查、无查询延迟 |
| System | 版本信息（Site Server / Agent / DB schema） | NOT SUPPORTED | 无版本端点 | 无 | 显示“未提供” | 不硬编码版本号 |
| System | 系统日志 | NOT SUPPORTED | 无日志端点 | 无 | 显示“未实现” | 不在前端伪造日志 |
| System | 运行配置（端口、保留策略、WebSocket 参数） | NOT SUPPORTED | 无 | 无 | 显示“未提供” | 不读取未授权本地配置 |
| System | 告警 / 错误中心 | NOT SUPPORTED | 无 | 无 | 不呈现 | 不伪造告警 |
| System | 运维操作（重启 / 清理 / 备份） | NOT SUPPORTED | 无 | 无 | 不提供 | 平台不引入运维命令入口 |

---

## 9. 跨页面与全局能力

| 页面 | 功能 | 当前实现状态 | 数据来源 | API 依赖 | 复用方式 | 验收方式 |
|---|---|---|---|---|---|---|
| 全局 | Hash 路由 + 刷新恢复 | EXISTING | `location.hash` | 无 | `parseHash()` / `hashchange` | 刷新后停留在同一页面 |
| 全局 | 深链接（`device` / `frame` / `offset` / `status` / `only_adopted`） | EXISTING | URL query | 相应端点 | URLSearchParams | 复制链接可复现同一视图 |
| 全局 | 404 页面 | EXISTING | 未知路由 | 无 | `pageNotFound()` | 未知路由显示 404 空状态 |
| 全局 | 空状态（接口成功返回空） | EXISTING | 响应空数组 | 任意列表端点 | `dataTable()` 空行 | 文案“无数据（接口成功返回空）” |
| 全局 | 错误条 + 重试 + HTTP 状态标签 | EXISTING | 失败响应 | 任意端点 | `errorBar()` | 401/403/404/5xx/NET 分类正确 |
| 全局 | 失败保留 stale 数据并标注 | EXISTING | 上次成功缓存 | 任意端点 | `load()` + `pf-stale` | 断服后仍显示旧值并提示 |
| 全局 | 轮询（Overview 10 s / Gateways·Devices 5 s / Sites·System 15 s） | EXISTING | 各页面返回 `{ms}` | 各端点 | `schedulePoll()` | 页面切换后旧定时器被清除 |
| 全局 | 隐藏标签页暂停轮询、恢复立即刷新 | EXISTING | `document.hidden` | 无 | `visibilitychange` | 切回前台数据立即更新 |
| 全局 | 认证 / 会话 / RBAC | NOT SUPPORTED | 无 | 无 | 不呈现 | 前端不接受角色参数 |
| 全局 | 前端 WebSocket 事件流 | NOT SUPPORTED | 无 | 无 `/ws/v1/site` | 轮询替代 | 状态条如实显示“未实现（轮询）” |
| 全局 | 写操作后端授权校验 | NOT SUPPORTED | 后端写端点无鉴权 | 无 | 平台不提供写 UI | 不通过前端参数提权 |

---

## 10. 覆盖率统计（口径：矩阵行数）

见本文末统计；统计脚本口径为 `grep -c "| EXISTING |"` 等，只统计表格数据行（不含本表表头）。

| 状态 | 条目数 | 占比 |
|---|---|---|
| EXISTING | 51 | 56.0% |
| PARTIAL | 13 | 14.3% |
| NEW | 1 | 1.1% |
| NOT SUPPORTED | 26 | 28.6% |
| 合计 | 91 | 100% |

说明：

1. `NEW` 仅 1 项（多站点列表 + 切换器）；其余未实现能力均受后端/协议缺失限制，归入 `NOT SUPPORTED`。
2. `NOT SUPPORTED` 是**设计结论**，不是缺陷统计；多数已在 UI 中显式标注。
3. 本矩阵不统计 Console（`repo/web`）内部页面与 Electron 壳的细粒度能力，仅统计平台集成面。

---

## 11. 结论

- 平台 7 页面的主路径全部有真实实现，且只依赖 Site Server 现有 REST 端点。
- 所有“接口未提供”的能力在 UI 中均有显式标注，不存在伪造数据或假控件。
- 未实现清单已同步至 `docs/PHASE08_2_PLATFORM_INFORMATION_ARCHITECTURE.md` §10 与 `docs/PHASE08_2_ARCHITECTURE_DECISIONS.md`。
