# Phase 08.2.2 — Security Decision Record（安全架构决策记录）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**架构决策记录（ADR）。本阶段未实施任何决策。**
> 日期：2026-11-05
> 状态：**全部 PROPOSED（等待人工审批）**

---

## 0. 决策状态总览

| ADR | 主题 | 状态 |
|---|---|---|
| ADR-08.2.2-01 | 用户认证载体：混合（Opaque + Cookie / Bearer） | **PROPOSED** |
| ADR-08.2.2-02 | 会话生命周期、CSRF 与 XSS 防护 | **PROPOSED** |
| ADR-08.2.2-03 | 不采用 JWT 作为默认方案 | **PROPOSED** |
| ADR-08.2.2-04 | Agent 凭证体系保留并补齐生命周期 | **PROPOSED** |
| ADR-08.2.2-05 | mTLS 仅用于跨互联网多站点 | **PROPOSED** |
| ADR-08.2.2-06 | RBAC 四角色模型 | **PROPOSED** |
| ADR-08.2.2-07 | Site Scope 服务端强制 | **PROPOSED** |
| ADR-08.2.2-08 | Resource Scope 默认关闭、可选启用 | **PROPOSED** |
| ADR-08.2.2-09 | 用户平面与机器平面严格分离 | **PROPOSED** |
| ADR-08.2.2-10 | 命令双平面二次授权链 | **PROPOSED** |
| ADR-08.2.2-11 | 前端 WS 短时票据、URL 不放长期凭证 | **PROPOSED** |
| ADR-08.2.2-12 | 网络隔离不替代应用安全；审计身份化与限流 | **PROPOSED** |

### 0.1 前置状态复核（不得误判）

| 前置 ADR | 文档编号 | 状态 | 是否可作为实施依据 |
|---|---|---|---|
| 多站点架构 | ADR-08.2.1-01 | **PROPOSED** | **否** |
| 事件流架构 | ADR-08.2.1-02 | **PROPOSED** | **否** |
| 权限模型 | ADR-08.2.1-03 | **PROPOSED** | **否** |

> 任务书中的 `ADR-08-021/022/023` 与实际 `ADR-08.2.1-01/02/03` 为同一组文档，编号差异已记录。
> **无任何人工审批记录。**

---

## ADR-08.2.2-01 用户认证载体

**Context**
Site Server 本地单机/局域网部署，当前 REST 完全没有认证（审计 G-01）。需要浏览器安全、可撤销、断网可用。

**Options**

| 方案 | 结论 |
|---|---|
| A. Session + Cookie | 浏览器好、撤销易，但 API/移动端不便 |
| B. JWT Access + Refresh | 撤销难、浏览器易落入 localStorage（与 T-05 冲突） |
| C. Opaque Token + 服务端 Session | 统一底座，撤销即时 |
| D. 反向代理认证 | 依赖外部 IdP，断网差 |
| E. 混合 | 浏览器 Cookie + API Bearer，统一会话表 |

**Decision**
采用 **E 混合架构**：
- 浏览器：Opaque 会话令牌 + `HttpOnly; SameSite=Strict` Cookie。
- API/移动端：同一会话表的 Bearer Opaque Token。
- 企业部署可选 D，但必须显式开启并仅信任回环/代理头。

**Consequences**
- 需要新增 `users` / `sessions` 表与登录端点（DB + API 变更）。
- 断网可用、撤销即时、XSS 不可读会话。
- 需要 CSRF 防护（见 ADR-02）。

**Status**：**PROPOSED**

---

## ADR-08.2.2-02 会话生命周期、CSRF 与 XSS

**Context**
Cookie 会话引入 CSRF；XSS 可能读取可被 JS 访问的存储。

**Options**
1. 仅 `SameSite=Strict`。
2. `SameSite=Strict` + Origin 校验。
3. 上述 + 双提交 CSRF Token。
4. 上述 + CSP。

**Decision**
采用 **3 + 4**：
- Cookie：`HttpOnly; SameSite=Strict; Secure`（TLS 时）。
- 写请求校验 `Origin/Referer` 白名单。
- 双提交 CSRF Token（`widscan_csrf` + `X-CSRF-Token`）。
- CSP：`default-src 'self'; script-src 'self'; frame-ancestors 'none'`。
- 空闲 30 min / 绝对 12 h；登出即时撤销。
- `permission_version` 变更使旧会话立即失效。

**Consequences**
- 所有写请求需带 CSRF 头；前端需改造（未来阶段）。
- 静态 UI 需保持无 `innerHTML`（现状已满足）。

**Status**：**PROPOSED**

---

## ADR-08.2.2-03 不采用 JWT 作为默认方案

**Context**
JWT 适合无状态跨服务，但本项目为本地单机，撤销与退出即时性优先。

**Options**
- JWT（无状态）
- Opaque 会话（有状态）

**Decision**
默认 **Opaque 会话**；JWT 仅在出现独立认证服务且明确需要无状态时才重新评估。

**Consequences**
- 需要会话表；无签名密钥管理负担。
- 撤销/登出即时生效。

**Status**：**PROPOSED**

---

## ADR-08.2.2-04 Agent 凭证体系保留并补齐

**Context**
现状已有每 Agent 独立凭证、SHA256 存储、首消息认证（P-01–P-03），但明文落盘、无轮换、撤销不即时。

**Options**

| 方案 | 结论 |
|---|---|
| 固定共享密钥 | 拒绝：不可区分、不可撤销 |
| **每 Agent 独立凭证（现状）** | **保留并增强** |
| 短期 Token | 拒绝：离线长运行易失效 |
| mTLS | 可选叠加（ADR-05） |
| 设备证书 | 拒绝：ESP32-C5 不承担该职责 |

**Decision**
保留每 Agent 独立凭证；新增：
- 环境变量优先（`GATEWAY_SITE_CREDENTIAL`）。
- 文件权限 0600。
- 轮换（新旧并行 24 h）。
- 撤销即关闭活动 WS。
- 连接期周期复核。
- 认证失败限流。

**Consequences**
- Agent 配置加载与 WS 管理变更。
- 无协议字段变更（除可选的 nonce，需单独批准）。

**Status**：**PROPOSED**

---

## ADR-08.2.2-05 mTLS 边界

**Context**
局域网 `ws://` 可接受；跨互联网存在中间人（T-10）。

**Options**
1. 始终 mTLS。
2. 仅跨主机强制 `wss://`。
3. 跨互联网 mTLS + 局域网 `ws/wss`。

**Decision**
采用 **3**：
- 回环 `127.0.0.1`：允许 `ws://`。
- 跨主机：强制 `wss://`。
- 跨互联网多站点：建议 mTLS（Agent 客户端证书）。
- 既有 `tls_ca_file` / `tls_verify` 字段复用（`site_connector.py`）。

**Consequences**
- 需要证书签发与轮换流程。
- 本地部署零改动。

**Status**：**PROPOSED**

---

## ADR-08.2.2-06 RBAC 四角色

**Context**
08.2.1 提出 Read Only / Operator / Site Admin / System Admin，但未批准、未实现。

**Decision**
采用四角色，角色与 Site 成员关系分离：
- Observer：站点只读。
- Operator：+ 设备认领/重命名/有条件命令。
- Site Admin：+ 删除/迁移/站点配置/Agent 配对与吊销。
- System Admin：全局跨站 + 用户与系统配置。

**Consequences**
- 新增 `site_memberships`；`system_admin` 为全局属性。
- 所有写路由必须挂角色依赖。

**Status**：**PROPOSED**（不因本文细化而视为批准）

---

## ADR-08.2.2-07 Site Scope 服务端强制

**Context**
当前 `site_id` 来自 query 且设备/测量端点无过滤（G-03）。

**Options**
1. 客户端传 `site_id`（现状）。
2. 服务端从 `AuthContext` 取 Scope。
3. 混合：客户端可传但必须校验。

**Decision**
采用 **3**：
- `AuthContext.site_ids` 为唯一事实源。
- 客户端 `site_id` 仅作“选择”，必须 ∈ Scope。
- 资源端点先反查资源 `site_id`，再校验成员关系。
- 数据访问层强制附加 `site_id` 条件。

**Consequences**
- 所有路由与仓储层改造。
- 多站点后端的前置条件。

**Status**：**PROPOSED**

---

## ADR-08.2.2-08 Resource Scope

**Context**
站点内可能存在敏感设备。

**Decision**
默认关闭；可选 `device_grants` 细粒度授权。
- 显式授权优先于站点角色。
- 资源授权不得突破站点成员边界。
- 设备删除时授权失效。

**Consequences**
- 表结构可选扩展；不启用时零额外复杂度。

**Status**：**PROPOSED**

---

## ADR-08.2.2-09 用户平面与机器平面分离

**Context**
现状存在 Agent Owner/Observer（Plane M），08.2.1 提出用户角色（Plane U）。

**Decision**
严格分离：
- 用户角色不改变 `owner_agent_id`。
- Agent Owner 不授予平台管理权。
- 授权判定分别执行，命令链需两者同时满足。

**Consequences**
- 避免“设备 Owner 即管理员”的错误映射。
- 命令授权更复杂但正确。

**Status**：**PROPOSED**

---

## ADR-08.2.2-10 命令双平面二次授权链

**Context**
当前无远端命令分发，`_handle_inbound_command` 只做授权不执行。

**Decision**
任何未来命令必须经过：
1. 用户认证 + 站点角色 + 资源授权；
2. 机器平面 Owner 在线 + 设备状态 + 置信度；
3. 服务端 → Owner Agent；
4. Agent 本地 Token + 白名单 + 状态复核；
5. 高风险命令 step-up（重认证/TOTP）；
6. 全链审计。

**Consequences**
- 不允许离线排队、不允许前端隐藏替代授权。
- 多站点与 UI 之前必须先完成授权链。

**Status**：**PROPOSED**

---

## ADR-08.2.2-11 前端 WS 短时票据

**Context**
未来前端事件订阅需要认证；URL 长期凭证会被日志/历史泄漏。

**Options**
1. Cookie-only。
2. URL 长期 Token。
3. 短时一次性票据。

**Decision**
采用 **3**：
- 已认证 REST 申请 30 s 单次票据。
- `wss://.../ws/v1/site?ticket=...`。
- 服务端绑定 `user_id + site_scope + permission_version`。
- 订阅逐条校验 Site 与设备归属。
- 快照/补齐强制 Scope 过滤。
- Origin 校验防 CSWSH。

**Consequences**
- 需要新增票据表/缓存与端点（未来）。
- **未完成认证授权前不得实施**。

**Status**：**PROPOSED**

---

## ADR-08.2.2-12 网络隔离不替代应用安全；审计身份化与限流

**Context**
常见误区：内网、Tailscale、无 CORS、只读 UI 即安全。

**Decision**
- 网络隔离只降低暴露面，**不替代**认证、授权、审计。
- 即使 Tailscale 部署也必须保留应用层认证授权。
- 默认不公网暴露；公网必须 TLS + 反向代理 + 限流 + 关闭 docs。
- 审计日志必须包含真实主体（用户/Agent）、Site、动作、结果。
- REST 与 WS 均需速率限制与请求体上限。

**Consequences**
- 需要审计中间件、限流中间件。
- 部署文档必须明确禁止公网裸奔。

**Status**：**PROPOSED**

---

## 1. 决策依赖关系

```text
ADR-01 认证载体
   └─▶ ADR-02 会话/CSRF/XSS
   └─▶ ADR-03 不用 JWT
   └─▶ ADR-06 RBAC
   └─▶ ADR-07 Site Scope
          └─▶ ADR-08 Resource Scope（可选）
   └─▶ ADR-12 审计与限流

ADR-04 Agent 凭证
   └─▶ ADR-05 mTLS
   └─▶ ADR-09 平面分离
          └─▶ ADR-10 命令授权链

ADR-01 + ADR-06 + ADR-07 + ADR-12
   └─▶ ADR-11 前端 WS 票据
```

**全部为设计依赖，不表示已实施。**

---

## 2. 变更声明

- 未修改任何产品代码、数据库、API、协议、固件或测试。
- 所有 ADR 状态：**PROPOSED**。
- 任何 APPROVED 变更必须由人工在本文中显式记录，不得由执行代理自行修改。
