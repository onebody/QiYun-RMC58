# Phase 08.2.2 — Authentication Architecture（身份认证架构）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**认证架构设计。未修改任何产品代码、数据库、API、协议或固件。**
> 日期：2026-11-05
> 关联：`PHASE08_2_2_SECURITY_ASSET_AUDIT.md`、`PHASE08_2_2_THREAT_MODEL.md`
> 状态：**PROPOSED（待人工审批）**

---

## 0. 设计约束

1. **本地优先**：Site Server 主要单机/局域网部署，不能引入重型 IdP。
2. **断网自治**：Site Server 与 Gateway Agent 在无互联网时仍须工作。
3. **低资源**：ESP32-C5 不承担任何用户认证、RBAC 或 TLS 终端职责。
4. **多站点预留**：认证结果必须与 Site Scope 解耦，便于后续多站点。
5. **不实现**：本阶段只给方案，不改代码。
6. 08.2.1 全部决策仍为 PROPOSED，本方案不依赖其被批准。

---

## 1. 资产与主体

| 主体 | 说明 | 当前是否存在 |
|---|---|---|
| 人类用户（User） | 浏览器/移动端操作者 | **不存在** |
| 平台角色（Role） | Observer / Operator / Site Admin / System Admin | **不存在**（08.2.1 仅提出） |
| Site 成员关系 | 用户与 Site 的多对多授权 | **不存在** |
| Gateway Agent | 机器身份，拥有凭证 | 存在（AgentCredential） |
| Device | 被 Agent 管理的设备 | 存在 |
| Device Owner / Observer | **Agent 平面**的设备管理关系 | 存在（`owner_agent_id`） |
| 服务身份 | Site Server 进程自身 | **无显式身份** |

> **强约束**：人类用户角色与 Agent Owner/Observer 是**两个平面**，不得合并。

---

## 2. 用户身份认证方案比较

### 2.1 候选方案

| 维度 | A. Session + Cookie | B. JWT Access + Refresh | C. Opaque Token + 服务端 Session | D. 反向代理身份认证 | E. 混合架构 |
|---|---|---|---|---|---|
| 本地部署适应性 | **强**（无需额外组件） | 强 | **强** | 中（需 Nginx/Authelia/SSO） | **强** |
| 多站点扩展 | 强（会话与站点无关） | 中（需处理跨站 token） | **强** | 中（依赖代理配置） | **强** |
| 浏览器安全 | **强**（HttpOnly 不可被 JS 读取） | 弱（localStorage 易被 XSS 窃取） | **强** | 强 | **强** |
| Token 撤销 | **即时**（删会话） | 难（需黑名单） | **即时** | 取决于代理 | **即时** |
| 用户退出 | **即时** | 需等过期或黑名单 | **即时** | 代理登出 | **即时** |
| 会话过期 | 易配置 | 需双 token 逻辑 | 易配置 | 代理配置 | 易配置 |
| CSRF | **需专门防护** | 低（Bearer 头） | **需专门防护（Cookie）** | 需防护 | 按载体分别处理 |
| XSS 影响 | 低（Cookie 不可读） | **高**（可读 token） | 低（Cookie 不可读） | 低 | 低 |
| 运维复杂度 | 低 | 中（密钥、轮换、黑名单） | **低** | 中高（代理与 SSO） | 中 |
| 断网自治 | **强** | 强 | **强** | **弱**（依赖外部 IdP） | 强（本地为主） |
| 移动端/API 接入 | 中（Cookie 不便） | 强 | **强**（Bearer） | 弱 | **强** |
| 是否需要新增存储 | 会话表 | 无需（但需黑名单表） | **会话表** | 无 | 会话表 |

### 2.2 逐项判断

- **A（纯 Cookie Session）**：浏览器体验最好、撤销最简单；但无法直接服务移动端/脚本客户端。
- **B（JWT）**：无状态、跨服务友好；但**撤销与“退出即失效”代价高**，且浏览器侧极易落入 localStorage → 与 T-05 冲突。本地单机部署不需要无状态收益。
- **C（Opaque Token + 服务端 Session）**：兼顾简化与可控；适合作为统一底座。
- **D（反向代理认证）**：适合已有企业 SSO 的场景；但依赖外部组件，断网与个人部署不友好；且**代理认证不能替代应用层授权**。
- **E（混合）**：浏览器用 HttpOnly Cookie（A/C 载体），API/移动端用 Bearer Opaque Token（C 载体），统一落同一会话表；可选接入 D 作为企业模式。

### 2.3 推荐（PROPOSED）

> **推荐 E：混合认证架构**
> - 浏览器默认：**Opaque 会话令牌 + HttpOnly Cookie**（服务端会话表）。
> - API / 移动端：**同一套 Opaque Token 的 Bearer 形式**（无 Cookie）。
> - 企业部署：可选**反向代理身份认证模式**，但必须显式开启且仅信任来自回环/代理的受信头。
> - **明确不推荐 JWT 作为默认方案。**

适用边界：

| 场景 | 是否适用 E |
|---|---|
| 单机 Site Server + 浏览器 | 是 |
| 局域网多用户 | 是 |
| 断网环境 | 是（本地会话表） |
| 多站点（同一服务） | 是（会话与站点解耦） |
| 企业 SSO 已有 | 是（叠加 D 模式） |
| 纯公网无代理 | 是，但必须 TLS + 限流 + 关闭 docs |

### 2.4 会话令牌规格（PROPOSED）

| 项 | 规格 |
|---|---|
| 生成 | 32 字节 CSPRNG，base64url |
| 服务端存储 | 只存 SHA256(token)，不存明文 |
| Cookie 名 | `widscan_session` |
| Cookie 属性 | `HttpOnly; SameSite=Strict; Path=/; Secure`（TLS 时） |
| 空闲超时 | 30 分钟 |
| 绝对超时 | 12 小时 |
| 轮换 | 登录时新建；每 15 分钟活跃轮换一次（可选） |
| 退出 | 服务端 `revoked_at` + 清 Cookie |
| 角色变更 | 递增 `users.permission_version`，会话携带旧版本 → 立即 401 |
| 登录限流 | 每 IP + 每账户双维度；连续 5 次失败锁定 15 分钟 |
| 口令哈希 | Argon2id（首选）或 bcrypt cost ≥ 12 |
| 多设备 | 允许，会话独立可单独撤销 |

### 2.5 CSRF 与 XSS 约束（PROPOSED）

- Cookie 会话必须同时具备：
  1. `SameSite=Strict`；
  2. `Origin` / `Referer` 白名单校验（写操作）；
  3. **双提交 CSRF Token**：`widscan_csrf`（可读 Cookie）+ 请求头 `X-CSRF-Token`，服务端比对。
- 所有写操作仅接受 `Content-Type: application/json`，拒绝表单跨站提交。
- CSP 建议：`default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`。
- 禁止在浏览器 `localStorage` 存放任何会话/凭证。

### 2.6 反向代理模式（可选，PROPOSED）

- 仅当 `TRUST_PROXY_AUTH=true` 且 Site Server 绑定 `127.0.0.1` 时启用。
- 只信任代理注入的 `X-Auth-User` / `X-Auth-Roles`，并要求代理清除外部同名头。
- **禁止**在公网直连时信任任何客户端自带的身份头。

---

## 3. Agent 与 Site Server 身份认证

### 3.1 候选方案比较

| 维度 | 固定共享密钥 | **每 Agent 独立凭证（现状）** | 短期 Token | mTLS | 设备证书 |
|---|---|---|---|---|---|
| 首次注册 | 简单但不可区分 | 配对码 + 一次性 | 需签发服务 | 需 CA | 需产线烧录 |
| Agent 绑定 Site | 弱 | **强**（credential 记录） | 强 | 强 | 强 |
| 凭证生成 | 手工 | 服务端 CSPRNG | 服务端签发 | CA 签发 | 产线 |
| 凭证存储 | 配置 | 配置/环境变量 | 内存为主 | 证书文件 | 安全元件 |
| 轮换 | 难 | **可设计** | 天然 | 可 | 难 |
| 撤销 | 需改所有端 | **即时**（DB） | 需黑名单 | CRL/OCSP | CRL |
| Agent 重置 | 手工 | 重新配对 | 重新签发 | 重签证书 | 返厂 |
| Agent 更换主机 | 不受控 | 不受控（缺口） | 较弱 | **强**（证书绑定） | 强 |
| Site 迁移 | 手工 | 重新签发 | 重新签发 | 重签 | 重签 |
| 离线重连 | 可用 | **可用**（长有效期） | **差**（易过期） | 可用 | 可用 |
| 本地低资源 | 好 | **好** | 好 | 中（TLS 开销） | 中 |
| 运维复杂度 | 低 | **低** | 中 | **高** | 高 |
| ESP32-C5 适用 | 不适用 | **不适用（Agent 侧承担）** | 不适用 | 不适用 | 不适用 |

### 3.2 推荐（PROPOSED）

> **基线：保留“每 Agent 独立凭证”（现状 P-01–P-03），并补齐生命周期。**
> - **跨主机/互联网多站点**：可选叠加 **mTLS**，作为传输与身份的双重保证。
> - **明确不采用短期 Token 作为唯一凭证**（长时间离线运行会反复失效）。
> - **明确不让 ESP32-C5 承担用户认证/RBAC**；设备只与 Gateway Agent 建立本地受信链路。
> - **不采用固定共享密钥**（无法撤销、无法区分 Agent）。

### 3.3 凭证生命周期（PROPOSED）

| 阶段 | 设计 |
|---|---|
| 生成 | 服务端 CSPRNG 48 字节 base64url；DB 仅存 SHA256 |
| 分发 | Site Admin 在已认证会话中创建一次性配对码（默认 300 s，最多 5 个）；Agent 通过 **TLS** 调 `pair` |
| 存储（Agent） | 首选环境变量 `GATEWAY_SITE_CREDENTIAL`；次选配置文件且权限 `0600`；**禁止日志输出** |
| 使用 | WS 首消息 `{type:"auth", agent_id, credential}`；跨主机必须 `wss://` |
| 有效期 | 默认 30 天；到期前提示轮换 |
| 轮换 | 签发新凭证 → 新旧并行 24 h 宽限 → 吊销旧凭证；不中断数据 |
| 撤销 | DB `revoked_at` + Agent 置 `disabled` + **主动关闭该 Agent 的活动 WS 连接** |
| 过期 | 认证时拒绝（`AUTH_FAILED`）；Agent 停止重连并等待人工重新配对 |
| 重置 | 管理员吊销 → 生成新配对码 → Agent 重新配对（`agent_id` 可保留） |
| 更换主机 | 需管理员吊销旧凭证并重新配对；可选主机指纹告警 |
| Site 迁移 | 吊销旧 Site 凭证 → 在新 Site 生成配对 → 历史数据保留原 `site_id` |
| 离线重连 | 指数退避 + 抖动（现状）；`AUTH_FAILED` 不无限重试（现状 L767-769） |
| 认证尝试限流 | 按 `agent_id` + 来源 IP 限制失败次数（新增） |

### 3.4 重放与中间人（PROPOSED）

- `ws://` 仅允许回环（`127.0.0.1`）。
- 跨主机必须 `wss://` 且校验证书；自签 CA 时 Agent 配置 `tls_ca_file`（现有字段已支持，`site_connector.py` L163-164）。
- 可选增强：服务端下发一次性 `nonce`，Agent 返回 `HMAC(credential, nonce)`；避免凭证在每次重连时直接出现在消息体。**该增强不改变现有协议语义前不得实施**（协议冻结）。

---

## 4. 统一认证流程

### 4.1 浏览器登录（PROPOSED）

```text
用户 → GET /login（静态）
     → POST /api/v1/auth/login {username, password}
         ← Set-Cookie: widscan_session=<opaque>; HttpOnly; SameSite=Strict
         ← Set-Cookie: widscan_csrf=<random>; SameSite=Strict
     → 后续请求自动携带 Cookie；写请求附 X-CSRF-Token
     → 每次请求中间件：校验会话 → 加载用户 → 加载 Site 成员 → 生成 AuthContext
```

### 4.2 API / 移动端（PROPOSED）

```text
POST /api/v1/auth/token {username, password} → {access_token, expires_at}
Authorization: Bearer <opaque token>
```

- 与浏览器会话同一张会话表，`kind=api`；可单独撤销。
- 不使用 Refresh Token 轮换链（本地场景不需要）。

### 4.3 Agent 连接（现状 + 增强）

```text
Agent → wss://server/ws/v1/agents/connect (URL 仅 agent_id)
     → 首消息 {type:"auth", agent_id, credential}
     ← {type:"connected", site_id}
     → 事件 / 心跳
```

增强点：撤销即断、过期拒连、失败限流、可选 nonce。

---

## 5. AuthContext（供授权层使用）

中间件解析后注入统一上下文（PROPOSED）：

```text
AuthContext {
  principal_type: "user" | "agent" | "service"
  user_id?: str
  agent_id?: str
  site_ids: set[str]          # 用户来自成员关系；Agent 来自绑定
  platform_role?: str         # 用户全局角色（如 system_admin）
  site_roles: dict[site_id, role]
  permission_version: int
  session_id?: str
}
```

**约束**：

- 授权层**只读 AuthContext**，永不读取客户端传入的 `site_id` / `role` / `owner_agent_id`。
- Agent 请求的 `site_ids` 恒为单元素（其绑定 Site）。

---

## 6. 与多站点、断网、移动端的关系

| 主题 | 结论 |
|---|---|
| 多站点 | 认证与会话与 Site 无关；授权按 `site_ids` 过滤（见 Authorization Model） |
| 断网 | 会话存本地 DB，无需外部 IdP；Agent 凭证长有效期支持离线 |
| 移动端 | 复用 Bearer Opaque Token；不需要额外协议 |
| 单点登录 | 可选反向代理模式；非默认 |
| 密钥管理 | 会话密钥无需签名密钥（Opaque）；仅需 CSPRNG 与 DB |

---

## 7. 明确不做

- 不实施 JWT。
- 不在前端存储任何 Token。
- 不把 Agent 凭证放到 URL。
- 不让 ESP32-C5 参与用户认证。
- 不修改现有 WS 协议字段（除非单独批准协议变更）。
- 不在本阶段写任何代码或迁移。

---

## 8. 变更声明

- 未修改任何产品代码、数据库、API、协议、固件或测试。
- 文档状态：**PROPOSED**。
