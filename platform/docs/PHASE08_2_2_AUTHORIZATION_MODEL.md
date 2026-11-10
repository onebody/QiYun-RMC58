# Phase 08.2.2 — Authorization Model（授权模型）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**授权模型设计。未修改任何产品代码、数据库、API、协议或固件。**
> 日期：2026-11-05
> 状态：**PROPOSED（待人工审批）**

---

## 0. 核心原则

1. **两个平面，绝不合并**：
   - **用户平面（Plane U）**：人类用户的角色、Site 成员、资源授权。
   - **机器平面（Plane M）**：Agent 与 Device 的 Owner / Observer 关系。
2. **默认拒绝**：未显式授权的动作一律拒绝。
3. **服务端强制**：所有授权在后端执行；前端隐藏按钮不是授权。
4. **上下文驱动**：只从 `AuthContext` 取身份与 Scope，不信任客户端参数。
5. **失败关闭**：身份/Scope 解析失败 → 401/403，不降级放行。
6. **全量审计**：授权通过与拒绝都记录。

---

## 1. 主体与关系

### 1.1 Plane U（用户平面）

| 实体 | 说明 | 现状 |
|---|---|---|
| `users` | 平台用户 | **不存在**（PROPOSED） |
| `site_memberships` | 用户 ↔ Site 多对多 + 站点角色 | **不存在**（PROPOSED） |
| `device_grants`（可选） | 用户 ↔ Device 细粒度授权 | **不存在**（PROPOSED） |
| `sessions` | 会话与 `permission_version` | **不存在**（PROPOSED） |
| 平台角色 | `system_admin`（全局，跨站） | **不存在**（PROPOSED） |

### 1.2 Plane M（机器平面，已存在）

| 实体 | 说明 | 现状 |
|---|---|---|
| `Agent` | 机器身份，绑定一个 `site_id` | 存在 |
| `Device.owner_agent_id` | 设备管理权归属的 Agent | 存在（`models.py`） |
| `ownership_status` | owned / observer | 存在 |
| Agent 角色 | owner / observer / unauthorized | 存在（`devices/authorization.py` L89-117） |

### 1.3 关系图

```text
User ──< site_memberships >── Site ──< Agent (site_id) ──< Device (owner_agent_id)
  │                              │
  └──< device_grants >── Device ─┘

Plane U                                    Plane M
（谁能看/改平台）                    （哪个 Agent 有权管理设备）
```

**禁止的映射**：

- 用户 Site Admin **不等于** 任何 Agent 的 Owner。
- Agent Owner **不等于** Site Admin。
- 授予用户 Operator **不会**改变 `owner_agent_id`。

---

## 2. 角色定义

| 角色 | 作用域 | 定义 |
|---|---|---|
| **Observer**（Read Only） | 站点成员 | 只读该 Site 的资源与测量，可打开 Console |
| **Operator** | 站点成员 | Observer + 设备认领/重命名 + 发起设备命令（受机器平面约束） |
| **Site Admin** | 站点成员 | Operator + 设备删除/归属迁移 + Site 配置 + Agent 配对/吊销 |
| **System Admin** | 全局 | 跨 Site + 用户与系统配置 + 审计查看 |

---

## 3. 权限矩阵

动作定义与现有 `devices/authorization.py` 的 Action 语义保持一致，便于后续复用。

| 动作 | Observer | Operator | Site Admin | System Admin |
|---|---|---|---|---|
| 查看 Site 概览/统计 | 允许 | 允许 | 允许 | 允许 |
| 查看 Agent 列表/详情 | 允许 | 允许 | 允许 | 允许 |
| 查看 Device 列表/详情 | 允许 | 允许 | 允许 | 允许 |
| 查看 Measurement / 统计 | 允许 | 允许 | 允许 | 允许 |
| 查看 Diagnostics | **拒绝**（或脱敏只读） | 允许 | 允许 | 允许 |
| 打开 RF Console `/app/` | 允许 | 允许 | 允许 | 允许 |
| 认领 / 重命名 Device | 拒绝 | 允许 | 允许 | 允许 |
| 删除 Device | 拒绝 | 拒绝 | 允许 | 允许 |
| 迁移 Device Owner | 拒绝 | 拒绝 | 允许 | 允许 |
| 修改 Site 配置 | 拒绝 | 拒绝 | 允许 | 允许 |
| 生成配对码 / 配对 Agent | 拒绝 | 拒绝 | 允许 | 允许 |
| 吊销 Agent 凭证 | 拒绝 | 拒绝 | 允许 | 允许 |
| 发起设备命令 | 拒绝 | **有条件允许** | **有条件允许** | **有条件允许** |
| 用户管理 / 全局配置 | 拒绝 | 拒绝 | 拒绝 | 允许 |
| 查看审计日志 | 拒绝 | 拒绝 | 本 Site | 全部 |

> `diagnostics` 建议对 Observer 返回脱敏版本（不返回 `db_path`、不返回完整 Agent 列表），或直接拒绝。

---

## 4. Site Scope

### 4.1 规则

1. 用户对某 Site 的任何动作，必须存在 `site_memberships(user_id, site_id)`。
2. 用户角色 = 站点角色；`system_admin` 例外（全局）。
3. 请求中的 `site_id` **不可信**：
   - 若请求未带 `site_id` → 使用会话当前 Site；
   - 若带了 → 必须属于 `AuthContext.site_ids`，否则 403。
4. 资源类请求（`/devices/{id}`、`/measurements`）必须通过资源反查 `site_id`，再校验成员关系。
5. Agent 连接写入的 `site_id` 恒为服务端绑定值，客户端不可覆盖。

### 4.2 必答问题

| 问题 | 结论 | 依据 |
|---|---|---|
| 一个用户是否可以属于多个 Site？ | **可以**，`site_memberships` 多对多；每个 Site 可有不同角色 | PROPOSED |
| Site Admin 是否只能管理所属 Site？ | **是**，禁止跨站；跨站需 `system_admin` | PROPOSED |
| System Admin 是否可以跨 Site？ | **可以**，但必须审计；建议默认最小化授予 | PROPOSED |
| Observer 是否可以访问 Measurement？ | **可以**（只读），属于站点只读范围 | PROPOSED |
| Operator 是否可以下发命令？ | **有条件允许**，须同时满足机器平面与资源授权 | PROPOSED |
| Agent Owner 是否等于 Site Admin？ | **否**，两个平面 | VERIFIED（现状） |
| 命令操作需要哪些二次授权？ | 站点角色 + 资源授权 + 机器平面 Owner 在线 + Agent 本地白名单 + 可选 step-up 确认 | PROPOSED |
| 角色变更后已有会话如何生效？ | 递增 `permission_version`；中间件逐请求比对；不匹配立即 401；WS 订阅同步终止 | PROPOSED |

---

## 5. Resource Scope（可选细粒度）

### 5.1 何时需要

- 一个 Site 内存在敏感设备（如关键监测点），希望某些 Observer/Operator 只能看部分设备。
- 默认**不启用**：站点角色覆盖该 Site 全部设备。

### 5.2 设计

```text
device_grants(
  user_id, device_id, permission,   -- permission ∈ {read, operate}
  granted_by, granted_at, expires_at
)
```

判定顺序：

1. 无站点成员 → 拒绝。
2. 存在 `device_grants` 且未过期 → 以授权为准。
3. 不存在显式授权 → 回落到站点角色。
4. **显式拒绝优先**（若实现 deny 记录）。

### 5.3 必答问题

| 问题 | 结论 |
|---|---|
| Resource Scope 是否默认开启？ | **否**，避免运维复杂度 |
| 是否允许跨 Site 的资源授权？ | **否**，资源授权不得突破站点成员边界 |
| 设备被删除后授权如何处理？ | 级联删除 / 失效 |

---

## 6. Agent Owner / Observer（Plane M）

### 6.1 现状（VERIFIED）

- `resolve_agent_role`（`devices/authorization.py` L89-117）：
  - Device 不存在或不属于 Site → unauthorized；
  - Agent 不存在 / disabled → unauthorized；
  - Device 无 Owner → observer（只读）；
  - `owner_agent_id == agent_id` → owner；
  - 其他 Agent → observer。
- `_ALLOWED_ROLES`（L38-44）：Observer 永远不能 command / configure / transfer / delete。
- `_handle_inbound_command`（`connection.py` L310-345，授权判定 L334）先授权、后（未实现的）执行。
- 低置信度设备（`identity_confidence != high`）禁止非读动作（L157-158）。

### 6.2 约束

1. Plane M 只回答“**哪个 Agent 有权管理这台设备**”，不回答“哪个用户能登录”。
2. 用户 Operator 发起命令时，平台仍需选择**该设备当前 Owner Agent**作为执行者。
3. 若 Owner Agent 离线 → 命令不可执行（不排队、不伪造）。
4. Owner 迁移必须由 Site Admin 在 Plane U 发起，并在 Plane M 落库审计。

---

## 7. 命令授权链

### 7.1 目标链路（PROPOSED，当前未实现远端分发）

```text
[用户 Operator+]
   │ 1. 平台认证（会话 / Bearer）
   ▼
[Site Server 授权层]
   │ 2. 站点成员校验（site_id ∈ AuthContext.site_ids）
   │ 3. 角色校验（operator / site_admin / system_admin）
   │ 4. 资源授权校验（device_grants，可选）
   │ 5. 机器平面校验（device.owner_agent_id 对应 Agent 必须在线）
   │ 6. 设备状态校验（非 pending/offline/error/disabled/identity_conflict）
   │ 7. 低置信度校验（identity_confidence=high）
   ▼
[服务端 → Owner Agent（已认证 WS）]
   │ 8. 消息携带 request_id + device_id + command
   ▼
[Gateway Agent 本地授权]
   │ 9. 本地 API Token（内部服务身份）
   │ 10. Owner/Observer 复核（authorize_device_command）
   │ 11. 命令白名单 is_command_allowed
   │ 12. 设备可命令状态复核
   ▼
[设备执行]
```

### 7.2 二次授权（Step-up）

| 命令类型 | 二次确认 | 说明 |
|---|---|---|
| 只读查询类 | 不需要 | 不属于命令执行 |
| 常规配置 | 前端确认 + 审计 | 需要用户明确点击 |
| 破坏性/重启/刷写 | **重新认证或 TOTP** + 审计 | 高风险，PROPOSED |

> 二次授权是**补充**，不能替代服务端角色校验。

### 7.3 明确禁止

- 前端隐藏按钮替代后端授权。
- 客户端传入 `owner_agent_id` / `role` / `site_id` 作为授权依据。
- 绕过 Agent 本地白名单直接下发原始命令。
- 命令排队等待离线 Agent（会破坏时效与审计）。

---

## 8. 授权执行点（后端强制）

| 层 | 职责 | 不得下放 |
|---|---|---|
| 认证中间件 | 生成 `AuthContext` | 不判断业务权限 |
| 路由依赖 | 校验 `site_id` ∈ Scope | 不做数据过滤 |
| 服务层 | 资源反查 `site_id` + 角色判定 | 不信任入参 |
| 数据访问层 | 查询强制附加 `site_id` 条件 | 不允许无 Scope 查询 |
| 审计层 | 记录允许/拒绝、主体、目标、结果 | 不允许静默 |

---

## 9. 角色变更与会话生效

| 事件 | 处理 |
|---|---|
| 角色提升/降低 | 更新 `site_memberships` + `users.permission_version += 1` |
| 会话 | 中间件比对版本；不一致 → 401，要求重新登录 |
| 前端 WS（未来） | 服务端主动关闭或发送 `permission.changed`；重新握手 |
| Agent 凭证吊销 | 关闭活动 WS；Agent 置 disabled |
| 用户禁用 | 撤销全部会话；拒绝新登录 |

---

## 10. 与 08.2.1 决策的关系

- 08.2.1 的权限模型（ADR-08.2.1-03）仍为 **PROPOSED**。
- 本文在其基础上细化，但**不视为对其批准**。
- 若 08.2.1 后续被修改或拒绝，本文需同步修订。

---

## 11. 变更声明

- 未修改任何产品代码、数据库、API、协议、固件或测试。
- 文档状态：**PROPOSED**。
