# Phase 08.2.2 — Security Asset Audit（安全资产审计）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2 — Security Architecture Review
> 性质：**安全审计文档。未修改任何产品代码、数据库、API、WebSocket 协议或固件。**
> 日期：2026-11-05
> 关联：`docs/PHASE08_2_1_*`（全部仍为 PROPOSED）

---

## 0. 前置：Phase 08.2.1 决策审批状态核查

### 0.1 核查方法

- 逐份读取 `docs/PHASE08_2_1_MULTI_SITE_ADR.md`、`PHASE08_2_1_EVENT_STREAM_ADR.md`、`PHASE08_2_1_SECURITY_PERMISSION_ADR.md`、`PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md`、`PHASE08_2_1_IMPLEMENTATION_DEPENDENCY_MATRIX.md`。
- 检索 `APPROVED / REJECTED / DEFERRED / UNKNOWN / PROPOSED / 审批记录`。
- 仅以**明确人工审批记录**作为 APPROVED 依据；不根据对话推荐、默认建议或执行代理结论推断。

### 0.2 核查结果

| 文档 | 实际 ADR 编号 | 任务书中引用名 | 文档声明状态 | 是否存在人工审批记录 | 本次认定 |
|---|---|---|---|---|---|
| `PHASE08_2_1_MULTI_SITE_ADR.md` | **ADR-08.2.1-01** | 任务书称 `ADR-08-021` | PROPOSED（待人工审批） | 无 | **PROPOSED** |
| `PHASE08_2_1_EVENT_STREAM_ADR.md` | **ADR-08.2.1-02** | 任务书称 `ADR-08-022` | PROPOSED（待人工审批） | 无 | **PROPOSED** |
| `PHASE08_2_1_SECURITY_PERMISSION_ADR.md` | **ADR-08.2.1-03** | 任务书称 `ADR-08-023` | PROPOSED（待人工审批） | 无 | **PROPOSED** |
| `PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md` | 跨架构审查（非 ADR） | — | PROPOSED | 无 | **PROPOSED** |
| `PHASE08_2_1_IMPLEMENTATION_DEPENDENCY_MATRIX.md` | WP-01–WP-14 | — | 全部 PROPOSED | 无 | **PROPOSED** |

- 无任何 APPROVED / REJECTED / DEFERRED / UNKNOWN 记录。
- 任务书中的 `ADR-08-021/022/023` 与实际文件名 `ADR-08.2.1-01/02/03` 编号不一致，此处按实际文档编号记录，避免引用歧义。
- **结论（VERIFIED）**：08.2.1 全部决策仍为 PROPOSED。本阶段继续进行独立安全审计，提出安全建议，但**不将任何决策标记为批准**，也不执行依赖这些决策的开发任务。

### 0.3 对本次审计的约束

- 多站点、前端 WS 订阅、设备命令控制均视为“未获批准、未实施”。
- 本审计对上述能力只做**前置条件设计**，不将其视为现有能力。

---

## 1. 审计方法与证据分级

### 1.1 证据标签

| 标签 | 含义 |
|---|---|
| **VERIFIED** | 已通过当前代码或测试直接验证 |
| **DOCUMENTED** | 现有文档明确记录 |
| **INFERRED** | 根据代码结构推断，未运行时验证 |
| **PROPOSED** | 本阶段新提出的架构建议 |
| **UNKNOWN** | 当前无法确认 |

### 1.2 审计范围

| 组件 | 审计内容 |
|---|---|
| Site Server | REST 路由、认证、授权、CORS、Host/Origin、Agent WS、DB 访问边界、错误与日志、未授权读写路径 |
| Gateway Agent | 本地 REST、本地认证、Owner/Observer、Site Connector、凭证存储/生命周期、身份混淆 |
| Platform UI | 浏览器凭证、请求头、Token 泄漏、XSS、CSRF、WS 认证、本地存储、越权入口 |
| RF Console | `/app/` 入口、Web Serial/WebUSB、WS 桥、Replay、是否绕过平台权限 |

---

## 2. Site Server 安全资产审计

### 2.1 REST 端点清单与认证状态

代码位置：`site-server/src/widscan_site/main.py`（`create_app` 起始于 L87）。

| 方法 | 路径 | 代码行 | 认证 | 授权 / Site Scope | 备注 |
|---|---|---|---|---|---|
| GET | `/health` | L151-153 | 无 | 无 | 返回 `site_id` |
| GET | `/api/v1/site` | L157-165 | 无 | 无（`site_id` 来自 query） | 任意 `site_id` 可查 |
| PATCH | `/api/v1/site` | L167-174 | 无 | 无（`site_id` 来自 query） | **可创建任意 Site**（`upsert_site` 不存在则建） |
| GET | `/api/v1/agents` | L178-188 | 无 | 无（`site_id` 来自 query） | 可枚举其他 Site Agent |
| POST | `/api/v1/agents/pairing-codes` | L190-203 | 无 | 无 | **可生成配对码 + 明文凭证** |
| POST | `/api/v1/agents/pair` | L205-221 | 无 | 无 | 用配对码换 Agent 身份 |
| POST | `/api/v1/agents/{agent_id}/revoke` | L223-238 | 无 | 无 Site 校验 | 可吊销任意 Agent |
| GET | `/api/v1/devices` | L242-258 | 无 | 无（`site_id` 来自 query） | 可枚举 |
| GET | `/api/v1/devices/{device_id}` | L260-270 | 无 | **完全无 site 过滤** | 按 ID 直接读 |
| POST | `/api/v1/devices/{device_id}/adopt` | L272-284 | 无 | 有 site 校验（`adopt_device`） | 但 `site_id` 由调用方提供 |
| PATCH | `/api/v1/devices/{device_id}` | L286-300 | 无 | **完全无 site 过滤** | 可改任意设备名/状态 |
| DELETE | `/api/v1/devices/{device_id}` | L302-308 | 无 | **完全无 site 过滤** | 可删任意设备 |
| POST | `/api/v1/devices/{device_id}/transfer-owner` | L310-332 | 无 | 有同 site 校验 | 可改归属 |
| GET | `/api/v1/devices/{device_id}/measurements` | L336-366 | 无 | **完全无 site 过滤** | 可按任意 device 读历史 |
| GET | `/api/v1/measurements/stats` | L368-383 | 无 | 无（`site_id` 来自 query） | 统计可跨站点 |
| GET | `/api/v1/diagnostics` | L387-399 | 无 | 无 | **泄漏 `db_path` 与在线 Agent 列表** |
| WS | `/ws/v1/agents/connect` | L403-405 | 首条消息 auth | `site_id` 由服务端 Agent 记录绑定 | 见 §2.4 |
| GET | `/platform` `/platform/*` | L411-415 | 无 | 无 | 静态文件 |
| GET | `/app` `/app/*` | L428-432 | 无 | 无 | 静态文件（可选） |

**认证现状（VERIFIED）**：全仓库 `site-server` 内不存在 `Depends(` 调用、不存在认证中间件、不存在 `CORSMiddleware`、不存在 `TrustedHostMiddleware`、不存在 `add_middleware`。检索结果仅命中 `main.py` 文档字符串中的 `/ws/v1/site` 字样。

**补充事实（INFERRED）**：Site Server 默认 host 实际来自 `ServerConfig.api.host = "127.0.0.1"`（`config.py` L26），但 `main.py` L539 注释写“默认绑定 0.0.0.0”，属注释与配置不一致；以配置值为准。`--host` 可覆盖，且仅打印警告（L540）。

### 2.2 认证 / 授权 / 网络边界

| 检查项 | 结论 | 证据 |
|---|---|---|
| REST 身份认证 | **不存在** | 全部路由无依赖注入（VERIFIED） |
| REST 授权（角色/RBAC） | **不存在** | 无角色模型、无权限判定（VERIFIED） |
| Site Scope 强制 | **不存在** | `site_id` 直接取 query，`sid = site_id or cfg.site.id`（L159/169/181/193/207/249/319/372）；设备与测量端点无 site 过滤（VERIFIED） |
| CORS | **未配置** | 无 `CORSMiddleware`（VERIFIED） |
| Host / Origin 校验 | **未配置** | 无 `TrustedHostMiddleware`、无 Origin 检查（VERIFIED） |
| CSRF | **无防护** | 当前无 Cookie 会话，因此暂无 CSRF 场景；但一旦引入 Cookie 认证必须补（VERIFIED + PROPOSED） |
| API 文档暴露 | **默认暴露** | 未设置 `docs_url=None / openapi_url=None`，`/docs`、`/redoc`、`/openapi.json` 可用（INFERRED，代码未关闭） |
| 速率限制 | **不存在** | 无中间件、无计数（VERIFIED） |
| 请求体大小限制 | **应用层无** | `body: dict` 端点（L168、L311）接受任意 JSON；Pydantic 字符串字段无长度上限（VERIFIED） |
| 审计日志身份 | **无真实用户身份** | `AuditLog.actor` 硬编码为 `system` / `admin`（`devices/__init__.py` L68/95/194/262/290/304；`auth/__init__.py` L99/160）（VERIFIED） |
| DB 文件保护 | **应用未设置权限/加密** | `sqlite:///./data/site.db`（`config.py` L31）；无文件权限设置、无加密（INFERRED） |
| 错误信息泄漏 | **部分泄漏** | `diagnostics` 返回 `db_path`（L398）；常规 404/400 文案较克制（VERIFIED） |
| 日志凭证泄漏 | **未发现** | Agent WS URL 有 `_redact_ws_url` 脱敏 `credential=`（`connection.py` L46-53/L137-139）；服务端只存凭证 SHA256（VERIFIED） |

### 2.3 未授权读写路径（重点是“任何 HTTP 客户端”视角）

不从“前端只读、内网、无 CORS”推断安全。以同网段主机、本机其他进程、被入侵主机为攻击者：

| 编号 | 路径 | 能力 | 影响 |
|---|---|---|---|
| SS-01 | `GET /api/v1/devices/{id}/measurements` | 读取任意设备历史测量 | 数据泄露（跨 Site 亦无过滤） |
| SS-02 | `PATCH/DELETE /api/v1/devices/{id}` | 修改或删除任意设备 | 完整性破坏 |
| SS-03 | `PATCH /api/v1/site?site_id=X` | 创建/修改任意 Site | 完整性破坏、伪造站点 |
| SS-04 | `POST /api/v1/agents/pairing-codes` | 生成配对码与明文凭证 | **攻击者可自助注册伪 Agent**，向平台注入数据 |
| SS-05 | `POST /api/v1/agents/{id}/revoke` | 吊销任意 Agent | 拒绝服务 |
| SS-06 | `GET /api/v1/diagnostics` | 读取 db 路径、在线 Agent | 侦察、为后续攻击铺路 |
| SS-07 | `GET /docs` `/openapi.json` | 获取完整接口契约 | 侦察 |
| SS-08 | 任意端点 | 无速率限制 | 批量拉取、资源耗尽 |

> 说明：`adopt_device` 与 `transfer_device_owner` 内部有 `site_id` 一致性校验（`devices/__init__.py` L184-185、L231-232），属于**数据一致性保护**，不是身份认证，不能作为安全边界。

### 2.4 Agent WS 接入（现有唯一带认证的链路）

代码：`site-server/src/widscan_site/agents/connection.py`。

| 检查项 | 结论 | 证据 |
|---|---|---|
| 连接前认证 | 先 `accept()`，再等首条 `auth` | L134、L156-184 |
| 认证超时 | 10 s（可配） | L131、L159-164；`config.py` L58 |
| 首条消息必须为 auth | 是，否则 4401 关闭 | L170-176 |
| URL credential | 生产默认禁止 | L145-150；`config.py` L57 |
| 凭证校验 | SHA256 比对 + `agent_id` 绑定 + 吊销/过期校验 | `auth/__init__.py` L166-183 |
| Site 绑定 | `site_id = agent.site_id`（服务端记录） | L200-201 |
| Site 缺失兜底 | **回落到 `"site-001"`** | L201（潜在缺陷） |
| 连接后逐消息授权 | 全部写入使用连接的 `agent_id` + 绑定 `site_id` | `_handle_agent_message` L270-307 |
| 命令授权 | 调用 `authorize_device_command`，且不执行远端分发 | L310-345（授权判定 L334） |
| 凭证撤销对活动连接 | **不会主动断开已建立连接** | `revoke_credential`（`auth/__init__.py` L186-198）仅改状态，无 registry 关闭（VERIFIED） |
| 长期连接凭证复核 | **无** | 认证只在建立时发生（VERIFIED） |
| 重放防护 | 无 nonce/challenge；凭证为 bearer 首消息 | INFERRED |
| 传输加密 | 取决于 `ws://` 或 `wss://`，默认示例为 `ws://` | `site_connector.py` L140、L795 |
| 反向恶意流量 | 无消息频率/大小限制 | INFERRED |

### 2.5 `/ws/v1/site` 状态

- `main.py` 文档字符串 L11 声明“前端 Web Workbench 事件流”，但代码中**不存在该路由**（VERIFIED，检索无实现）。
- 平台前端只做轮询，无 WS 订阅（VERIFIED，`site-server/web/app.js` L1065-1068、L1099）。
- **不能把文档字符串当作已实现能力。**

---

## 3. Gateway Agent 安全资产审计

### 3.1 本地 REST 端点与认证

代码：`gateway-agent/src/widscan_agent/api/__init__.py`（`build_app` L87）。

| 方法 | 路径 | 行号 | 认证 | 说明 |
|---|---|---|---|---|
| GET | `/api/v1/agent` | L148 | **无** | Agent 元信息 |
| GET | `/api/v1/devices` | L158 | **无** | 设备列表与指标 |
| GET | `/api/v1/devices/{id}` | L172 | **无** | 单设备详情 |
| POST | `/api/v1/devices/{id}/connect` | L186 | **有** | `dependencies=auth_dep` |
| POST | `/api/v1/devices/{id}/disconnect` | L194 | **有** | 同上 |
| POST | `/api/v1/devices/{id}/reconnect` | L202 | **有** | 同上 |
| POST | `/api/v1/devices/{id}/commands` | L210 | **有** | 同上 |
| GET | `/api/v1/diagnostics` | L222 | **无** | 返回事件摘要 + 安全状态（Token 已 mask） |
| GET | `/api/v1/site-connector` | L244 | **无** | Connector 诊断 |
| POST | `/api/v1/site-connector/enable` | L250 | **有** | 可注入 endpoint + credential |
| POST | `/api/v1/site-connector/disable` | L260 | **有** | |
| POST | `/api/v1/site-connector/pair` | L267 | **有** | 接收 `plain_credential` |
| GET | `/health` | L336 | **无** | |
| WS | `/ws/v1/events` | L345 | **无** | 单向事件流，无认证 |

**关键结论（VERIFIED）**：本地**写/命令类**端点受 Token 保护；**读类端点、诊断、健康检查、事件 WS 全部无认证**。默认只绑定 `127.0.0.1`，因此当前风险受限于本机；一旦外部绑定（需认证+Token）开放，读端点仍无认证。

### 3.2 本地认证机制

| 检查项 | 结论 | 证据 |
|---|---|---|
| 凭证来源 | `Authorization: Bearer` 或 `X-API-Token` | L45-50 |
| 比较方式 | `hmac.compare_digest` 常量时间 | L127 |
| 未配置 Token | 命令端点返回 **503**（宁可拒绝服务） | L119-125 |
| 认证失败 | 401 + `WWW-Authenticate` | L135-138 |
| 认证是否替代命令白名单 | **不替代**，仍需 `is_command_allowed` | L14、L216-219 |
| Token 日志 | 不记录内容，仅记录 client/path | L128-133 |
| 诊断输出 | `mask_secret` 返回 `***` | L40-43、L234-238 |
| 绑定地址 | 默认回环；外部监听需 `require_auth=True` 且 Token 已配置 | `config.py` L122-144 |
| 可关闭认证 | 环境变量 `GATEWAY_API_REQUIRE_AUTH` 可关闭 | `config.py` L113-116 |

### 3.3 Owner / Observer 授权

代码：`site-server/src/widscan_site/devices/authorization.py`。

| 检查项 | 结论 | 证据 |
|---|---|---|
| 角色来源 | 服务端 Device/Agent 记录 | `resolve_agent_role` L89-117 |
| 未绑定 Owner 设备 | 视为 observer（只读） | L102-103 |
| 非 Owner Agent | observer，不能管理 | L115-117 |
| Observer 管理动作 | 拒绝 | `_ALLOWED_ROLES` L38-44、L152-154 |
| Site Admin | 由**可信服务端调用方**声明 | L130-138 |
| 低置信度设备 | 非读动作拒绝 | L157-158 |
| 不可命令状态 | pending/offline/error/disabled/identity_conflict | L46-48、L155-156 |
| 是否信任客户端自报 owner | **不信任** | L6-8、`connection.py` L314-316 |

**结论（VERIFIED）**：Agent 平面的 Owner/Observer 授权已实现且有测试（`site-server/tests/test_phase07_authorization.py`、`test_device_ownership.py`）。但它是**机器身份授权**，不构成平台用户认证。

### 3.4 Agent 凭证存储与生命周期

| 检查项 | 结论 | 证据 |
|---|---|---|
| 服务端存储 | 仅 SHA256 哈希 | `auth/__init__.py` L21-22、L92 |
| 明文生成 | 48 字节随机 base64url | L30-34 |
| 有效期 | 720 小时（30 天） | L96、L153 |
| 配对码 | 8 位、一次性、默认 300 s | L25-27、L94；`config.py` L35-38 |
| 吊销 | `revoked_at` + Agent 置 `disabled` | L186-198 |
| 轮换 | **无轮换 API** | 无实现（VERIFIED） |
| Agent 侧存储 | **YAML 明文 `credential` 字段** | `gateway-agent/config.py` L63、`save_config` L147-163 |
| 内存存储 | 保留于 `self._credential` | `site_connector.py` L172 |
| 认证失败后行为 | 清空内存凭证并停止重连 | `site_connector.py` L767-769 |
| 日志打印凭证 | 未发现 | 全文件检索无凭证日志 |
| 环境变量注入 | 仅本地 API Token 支持；**Site credential 无 env 覆盖** | `config.py` L108-116 |

### 3.5 身份混淆与冒用

| 检查项 | 结论 | 证据 |
|---|---|---|
| 同一凭证绑定单一 `agent_id` | 是 | `verify_credential` L181-182 |
| Agent 复制（复制 YAML 到另一台机器） | **可以冒用**，无设备指纹/证书绑定 | INFERRED |
| 两个 Agent 使用同一 `agent_id` | 后连接者覆盖 registry 条目，前连接不被主动关闭 | `AgentConnectionRegistry.register` L88-91（覆盖写入） |
| Agent 上报任意 `device_id` | **服务端不校验归属**，首次上报即成为 Owner | `devices/__init__.py` L44-71、`connection.py` L374-398 |
| 重连快照 | 会重放设备快照，可能重复触发 Owner 认领 | `site_connector.py` 快照逻辑 + `upsert_device_from_agent` L88-99 |

---

## 4. Platform UI 安全资产审计

代码：`site-server/web/{index.html,app.js,styles.css}`，同源挂载 `/platform/`。

| 检查项 | 结论 | 证据 |
|---|---|---|
| 浏览器是否保存凭证 | **否** | 无 `localStorage` / `sessionStorage` / `indexedDB`（VERIFIED，检索 0 命中） |
| 请求是否携带认证信息 | **否** | `fetch` 仅带 `Accept`、`cache: no-store` | `app.js` L117 |
| Token 是否可能经 URL 泄漏 | **无 Token 机制** | 无认证参数 |
| XSS 注入面 | **低** | 无 `innerHTML` / `outerHTML` / `insertAdjacentHTML` / `eval` / `new Function`（VERIFIED，检索 0 命中） |
| CSRF | **当前无会话，暂无场景**；未来 Cookie 会话必须补 | INFERRED + PROPOSED |
| WebSocket 认证方式 | **无前端 WS** | 无 `/ws/v1/site` |
| 本地存储 | 未使用 | VERIFIED |
| 错误/调试输出 | 有错误条与 stale 标记，不含凭证 | `app.js` 轮询与错误处理 |
| 前端越权入口 | **无写操作 UI** | 仅 GET 列表/详情，无命令/删除按钮（VERIFIED） |

**结论**：Platform UI 在“无认证”的前提下反而是**最安全的一层**——它不持有凭证、不写数据。但这不能反推后端安全；任何其他 HTTP 客户端都可绕过 UI 直接调用 REST。

---

## 5. RF Console 安全边界审计

| 检查项 | 结论 | 证据 |
|---|---|---|
| 入口 | 独立 `/app/`（可选挂载 `repo/web/dist`） | `main.py` L428-432 |
| 是否调用 Site Server API | **否** | `repo/web` 内无 `/api/v1` 调用；数据平面独立（DOCUMENTED，08.2 已确认） |
| Web Serial / WebUSB | 浏览器原生权限，需用户手势授权 | DOCUMENTED |
| WS 桥接 | Console 自有 WS bridge，与平台无关 | DOCUMENTED |
| Replay | 本地回放，不访问后端 | DOCUMENTED |
| 本地存储 | 主题、i18n、定时器阈值、通道计划、RH Server URL | `repo/web/src/main.ts` L50/58、`i18n.ts` L11/35、`model/timer.ts` L80/97、`model/channelPlan.ts` L62/77、`model/rhServer.ts` L145/160 |
| 是否存凭证 | **否**（仅存 RH Server `url` + `on`） | `rhServer.ts` L145-160 |
| 与 Platform 共享认证状态 | **无共享**（平台无认证） | VERIFIED |
| 能否绕过平台权限直接访问后端 | **不能访问 Site Server**；可直接操作本地设备（物理/浏览器权限） | VERIFIED |
| 静态托管是否受平台鉴权保护 | **否**，`/app/` 与 `/platform/` 均无鉴权 | `main.py` L419/L432 |

**结论**：Console 的“绕过”风险本质是**设备本地物理访问**，不是平台数据越权。不得为了统一认证破坏 Web Serial/WebUSB。未来若对 `/app/` 加认证，必须保证 Electron 与本地 `file`/`app://` 入口仍可用。

---

## 6. 已有保护措施汇总（不得否定）

| 编号 | 措施 | 位置 | 性质 |
|---|---|---|---|
| P-01 | Agent WS 首条消息认证 + 10 s 超时 | `agents/connection.py` L156-184 | 认证 |
| P-02 | URL credential 生产默认禁止 | `config.py` L57 | 认证 |
| P-03 | 凭证 SHA256 存储、绑定 agent_id、吊销/过期校验 | `auth/__init__.py` L166-183 | 认证 |
| P-04 | Agent 本地命令 API Token（Bearer / X-API-Token，常量时间比较） | `api/__init__.py` L115-138 | 认证 |
| P-05 | 未配置 Token 时命令 API 503 拒绝 | 同上 L119-125 | 失败关闭 |
| P-06 | 外部监听需认证 + Token，否则回退回环 | `config.py` L122-144 | 网络边界 |
| P-07 | Owner/Observer 服务端授权矩阵 | `devices/authorization.py` L38-159 | 授权 |
| P-08 | 命令白名单（`is_command_allowed`） | `api/__init__.py` L216-219 | 输入约束 |
| P-09 | 命令消息不信任客户端 owner 声明 | `connection.py` L314-316 | 授权 |
| P-10 | WS URL 凭证脱敏 | `connection.py` L46-53 | 日志安全 |
| P-11 | 诊断 Token 掩码 | `api/__init__.py` L40-43 | 日志安全 |
| P-12 | event_id 唯一约束幂等 | `ingestion/__init__.py` L245-254；`models.py` L274 | 数据完整性（**非认证**） |
| P-13 | `agent_received_at` 缺失保持 NULL | `ingestion/__init__.py` L214-223 | 数据完整性（**非认证**） |
| P-14 | 默认绑定回环（Agent） | `config.py` L122-144 | 网络边界 |

### 6.1 Phase 07 修复归类（不得混为一谈）

| 修复 | 归属 | 说明 |
|---|---|---|
| B-1 Agent 命令 API 安全 | **认证** | 仅覆盖 Agent 本地写端点 |
| B-2 Device Owner 授权 | **授权** | 机器身份平面，不是用户认证 |
| B-3 `agent_received_at` | **数据完整性** | 不提供身份保证 |
| B-4 `event_id` 幂等 | **数据完整性** | 不提供身份保证 |

---

## 7. 当前安全缺口清单

| 编号 | 缺口 | 组件 | 严重度 | 证据标签 |
|---|---|---|---|---|
| G-01 | REST 全量无认证 | Site Server | CRITICAL | VERIFIED |
| G-02 | 无用户/角色/站点授权模型 | Site Server | CRITICAL | VERIFIED |
| G-03 | `site_id` 由客户端自由指定，设备/测量端点无 Site 过滤 | Site Server | HIGH（多站点下 CRITICAL） | VERIFIED |
| G-04 | 可自助生成配对码与明文凭证 | Site Server | CRITICAL | VERIFIED |
| G-05 | 无 CORS / Host / Origin 校验 | Site Server | MEDIUM | VERIFIED |
| G-06 | 无速率限制 / 无请求体大小限制 | Site Server | MEDIUM | VERIFIED |
| G-07 | `/docs`、`/openapi.json` 默认暴露 | Site Server | LOW | INFERRED |
| G-08 | `diagnostics` 泄漏 `db_path` 与在线 Agent | Site Server | MEDIUM | VERIFIED |
| G-09 | 审计 `actor` 硬编码，无真实身份 | Site Server | HIGH | VERIFIED |
| G-10 | Agent WS 撤销不主动断开活动连接 | Site Server | HIGH | VERIFIED |
| G-11 | Agent WS 建立后不复核凭证有效期 | Site Server | MEDIUM | VERIFIED |
| G-12 | Agent WS Site 缺失回落 `"site-001"` | Site Server | MEDIUM | VERIFIED |
| G-13 | 无重放防护、无 TLS 强制 | Agent ↔ Server | HIGH（公网） | INFERRED |
| G-14 | Agent 凭证 YAML 明文、无轮换 API | Gateway Agent | HIGH | VERIFIED |
| G-15 | Agent 读端点 / 诊断 / 事件 WS 无认证 | Gateway Agent | MEDIUM（回环）/ HIGH（外绑） | VERIFIED |
| G-16 | 服务端不校验 Agent 上报设备归属 | Site Server | HIGH | VERIFIED |
| G-17 | 无设备指纹/证书绑定，Agent 可被复制冒用 | 双端 | HIGH | INFERRED |
| G-18 | SQLite 明文文件，无权限/加密保证 | Site Server | HIGH | INFERRED |
| G-19 | 前端 WS 不存在（功能缺口，暂不构成漏洞） | Site Server / UI | INFO | VERIFIED |
| G-20 | `/platform/`、`/app/` 静态托管无鉴权 | Site Server | LOW | VERIFIED |

---

## 8. 审计结论

1. **Site Server REST 层在当前状态下不具备生产可用的安全边界**（G-01/G-02/G-03/G-04）。不能因“UI 只读、无 CORS、内网部署”而认定安全。
2. **Agent WS 与 Agent 本地写端点已有真实认证与授权**（P-01–P-09），但其覆盖面有限，且存在撤销不即时、凭证明文存储、无设备绑定等缺口。
3. **Platform UI 与 RF Console 不持有平台凭证、不越权访问后端**，其风险主要是静态资源与本地设备物理访问，而非数据越权。
4. **08.2.1 决策全部 PROPOSED**，本阶段不据此实施任何开发。
5. 所有结论均为审计事实；修复方案见 `PHASE08_2_2_AUTHENTICATION_ARCHITECTURE.md`、`PHASE08_2_2_AUTHORIZATION_MODEL.md`、`PHASE08_2_2_WEBSOCKET_SECURITY.md`、`PHASE08_2_2_IMPLEMENTATION_DEPENDENCY_MATRIX.md`。

---

## 9. 变更声明

- 本阶段未修改任何产品代码、数据库、API、WebSocket 协议、固件或测试。
- 本文档状态：**PROPOSED（审计结论，待人工审批）**。
