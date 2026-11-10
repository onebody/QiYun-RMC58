# Phase 08.4 — 统一入口与导航集成

> 阶段：Phase 08 Platform UI Integration / 子阶段 08.4
> 性质：**代码实现**（只读平台界面 + 同源挂载）
> 日期：2026-11-05
> 前置决策：08.1/08.2/08.3 已确认（site-server 同源托管、Console 外链、轮询、只读）

---

## 1. 实现目标与边界

### 1.1 本轮完成

- 新增 Platform Shell：Overview / Sites / Gateways / Devices / Measurements / System / RF Console。
- 由 site-server 同源托管：`/platform/`；根路径 `/` 重定向到 `/platform/`。
- 可选同源托管原始 Console：`/app/`（仅当 `repo/web/dist` 或 `SITE_CONSOLE_DIR` 存在）。
- 平台页全部使用 **真实 REST 数据**；无 Mock、无假数据、无假图表。
- 只读：不调用任何 POST/PATCH/DELETE 端点。
- 原 Console、Electron、Android、rftrack Workbench **零修改**。

### 1.2 本轮明确不做

- 不新增 `/ws/v1/site`，实时能力采用轮询并如实标注。
- 不新增后端 API、不改数据库模型、不改设备协议。
- 不实现写操作（adopt / rename / transfer-owner / revoke）。
- 不做 iframe 内嵌 Console。
- 不新增构建工具或前端框架依赖。

---

## 2. 新增 / 修改文件

### 2.1 新增

| 文件 | 说明 |
| --- | --- |
| `site-server/web/index.html` | Platform Shell 结构、导航、顶部/底部状态栏、部署配置注入 |
| `site-server/web/styles.css` | 08.3 设计 Token 与全部组件样式（深色、桌面优先） |
| `site-server/web/app.js` | Hash 路由、API Client、6 个只读页面、轮询、错误/空/未实现状态 |
| `site-server/tests/test_phase08_platform_ui.py` | 5 个托管与回归测试 |

### 2.2 修改

| 文件 | 改动 |
| --- | --- |
| `site-server/src/widscan_site/main.py` | 新增 `os` / `RedirectResponse` / `StaticFiles`；在 `create_app()` 末尾挂载 `/platform`、`/` 重定向、可选 `/app` |

**未修改**：`repo/web/**`、`repo/web-desktop/**`、`app/mobile/**`、`repo/rftrack/**`、数据库模型、Alembic、Gateway Agent。

---

## 3. 入口与挂载行为

### 3.1 路径

| 路径 | 行为 |
| --- | --- |
| `GET /` | **307** → `/platform/` |
| `GET /platform` | **307** → `/platform/` |
| `GET /platform/` | 返回平台 `index.html` |
| `GET /platform/app.js` / `styles.css` | 静态资源 |
| `GET /app` | **307** → `/app/`（仅 Console dist 存在时注册） |
| `GET /app/` | 返回 `repo/web/dist/index.html`（可选） |

### 3.2 目录解析

```python
platform_dir = env SITE_PLATFORM_WEB_DIR or <site-server>/web
console_dir  = env SITE_CONSOLE_DIR        or <repo>/web/dist
```

- 目录不存在时不注册对应挂载，仅记录日志；**site-server 启动不受影响**。
- `/platform` 缺失时 `GET /` 不会重定向（该路由只在 platform 存在时注册）。

---

## 4. 页面实现明细

### 4.1 路由

`#/overview`、`#/sites`、`#/sites/:id`、`#/gateways`、`#/gateways/:id`、`#/devices`、`#/devices/:id`、`#/measurements`、`#/system`、`#/console`。

- 未知路由显示 404 页面（不静默跳转）。
- 过滤条件写入 hash query，刷新可恢复。
- 无 hash 时以 `history.replaceState` 写入 `#/overview`。

### 4.2 数据来源（全部为现有端点）

| 页面 | 端点 |
| --- | --- |
| Overview | `/api/v1/site`、`/api/v1/agents`、`/api/v1/devices`、`/api/v1/measurements/stats`、`/api/v1/diagnostics` |
| Sites | `/api/v1/site`、`/api/v1/agents?site_id=` |
| Gateways | `/api/v1/agents`（`status`/`site_id` 过滤）、`/api/v1/devices`（前端按 owner 过滤） |
| Devices | `/api/v1/devices`（`status`/`only_adopted`）、`/api/v1/devices/{id}`、`.../measurements?limit=20` |
| Measurements | `/api/v1/devices`、`/api/v1/devices/{id}/measurements`（`frame_type/limit/offset`） |
| System | `/health`、`/api/v1/diagnostics`、`/api/v1/measurements/stats` |

### 4.3 明确呈现的“未实现/未返回”

| 位置 | 呈现 |
| --- | --- |
| Overview“最近告警” | “当前无告警数据源：未实现（Site Server 未提供告警接口）” |
| Sites 多站点 | “未实现（GET /api/v1/sites 未提供）” |
| Gateway 凭证状态 | “未提供（无 REST 端点）” |
| Gateway 详情端点 | “未实现（无 GET /api/v1/agents/{id} 与凭证端点）” |
| Device `transport_type` 过滤 | “后端入参未生效，未提供” |
| Measurement Agent Received 列 | 表头标注“（接口未返回）”，单元格 `—` |
| Measurement 时间范围 | “未实现（后端未提供时间过滤）” |
| Measurement 总数 | 分页显示“后端未提供总数” |
| System 版本/日志/配置 | 分别标注“未实现/未提供” |
| 底部状态条 | “WebSocket: 未实现（轮询）”、“版本: 未提供” |

### 4.4 轮询

| 页面 | 间隔 | 依据 |
| --- | --- | --- |
| Overview | 10 s | 08.2 |
| Sites 列表/详情 | 15 s | 08.2 |
| Gateways 列表/详情 | 5 s | 08.2 |
| Devices 列表/详情 | 5 s | 08.2 |
| Measurements | 不轮询（手动刷新/筛选触发） | 08.2 |
| System | 15 s | 08.2 |

- 页面隐藏时暂停，恢复可见立即刷新（`visibilitychange`）。
- 轮询渲染为静默模式，不重播骨架。
- 请求失败保留上一次成功数据，并显示“显示的是上一次成功数据”。

### 4.5 状态语义

- Agent：`online` / `offline` / `心跳超时`（>45 s 且后端仍 online，文案注明“等待后端判定”）。
- Device：`online` / `pending` / `offline` / `error` / `disabled` / `identity_conflict`。
- 额外徽标：`未认领`、`只读`（`identity_confidence=low`）。
- 时间统一以 **UTC** 渲染并标注 `UTC`，tooltip 保留原始 ISO。
- `null/undefined/""` 统一显示 `—`，不显示 `null/None`。

### 4.6 访问安全

- 所有数据通过 DOM API 写入（`textContent`/`createElement`），不拼接 HTML，避免 XSS。
- `payload` 以 `JSON.stringify` 写入 `<pre>`，不语法高亮、不注入。
- 平台不读取/不展示任何 Token、凭证、环境变量。
- 平台只读，不提供写操作入口。

---

## 5. Console 接入

- 顶部栏 `RF Console ↗` 默认指向 `/app/`，`target="_blank" rel="noopener"`。
- 启动时用 `HEAD /app/` 探测：404 或网络失败 → 链接降级为 `#/console` 说明页（“未配置/未就绪”）。
- `#/console` 页面只说明入口与连接方式，不内嵌、不传数据。
- Console 构建产物由 `/app/` 同源提供；Vite `base="./"` 保证资源相对路径可用。

---

## 6. 测试与构建结果（本轮实际执行）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| Platform 新测试 | `cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase08_platform_ui.py -q` | **5 passed**（1.74 s） |
| Site Server 全量 | `cd site-server && PYTHONPATH=src python3 -m pytest tests -q` | **74 passed**（6.60 s） |
| Console 构建 | `cd repo/web && npm run build` | **成功**（`✓ built in 182ms`，含 `tsc --noEmit`） |
| Console 单测 | `cd repo/web && npx vitest run` | **56 passed / 10 files**（372 ms） |

未运行：Gateway Agent（本子阶段未改其代码）；RFTrack（未改）；Workbench node 测试（未改）。最终验收阶段将统一重跑。

新增测试覆盖：

1. `/platform/` 返回 200 且包含导航；
2. `/platform/app.js`、`styles.css` 可访问且内容非空；
3. `/platform` 与 `/` 307 重定向；
4. `/health`、`/api/v1/site`、`/api/v1/agents`、`/api/v1/diagnostics` 不受影响；
5. `SITE_CONSOLE_DIR` 存在时 `/app/` 可返回 Console 页面。

---

## 7. 已知限制

1. **单站点**：页面只展示一个 Site；多站点接口未提供。
2. **无实时推送**：轮询模式；状态更新延迟取决于间隔与后端心跳判定。
3. **Measurement 字段不完整**：`agent_received_at` 未由 REST 返回，页面显示 N/A。
4. **无写操作**：认领/改名/迁移/吊销均未暴露。
5. **REST 无鉴权**：平台数据仅靠网络边界保护；生产需反代鉴权/内网部署。
6. **Console 依赖构建产物**：未构建或未挂载时导航自动降级，不报错。
7. **Gateway 详情为列表拼装**：无独立详情端点，数据可能比列表稍旧（同一轮询内）。
8. **浏览器兼容**：目标为 Chromium/Firefox/Safari 桌面版；未验证移动端。

---

## 8. 进入 08.5 的条件

- 08.4 构建、单测、全量 Site Server 测试均通过。
- 待 08.5：启动真实 Site Server + 真实/明确标注的测试数据，逐项验证页面数据、断线、刷新恢复、Console 独立运行。
- 待 08.6：真实运行截图与验收报告。

**本子阶段完成，等待人工确认后进入 08.5。**
