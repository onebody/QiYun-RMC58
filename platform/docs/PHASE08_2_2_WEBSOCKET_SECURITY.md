# Phase 08.2.2 — WebSocket Security（WebSocket 安全架构）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**WebSocket 安全设计。未修改任何 WebSocket 实现或协议。**
> 日期：2026-11-05
> 状态：**PROPOSED（待人工审批）**

---

## 0. 两个必须分开审计的平面

| 平面 | 端点 | 方向 | 主体 | 现状 |
|---|---|---|---|---|
| **WS-A（Agent 平面）** | `/ws/v1/agents/connect` | Agent → Server | 机器身份 | **已实现**（首消息认证） |
| **WS-U（用户平面）** | `/ws/v1/site` | Server → 浏览器 | 人类用户 | **未实现**（`main.py` L11 仅有文档字符串） |

**强约束**：

- 不得用 WS-A 的认证机制直接推导 WS-U 的安全结论。
- **认证未完成前，不得批准前端 WebSocket 订阅进入实施。**
- 本阶段不实现任何 WS 改动；协议保持冻结。

---

## 1. WS-A：Agent 反向连接（现状审计）

### 1.1 现有流程（VERIFIED）

```text
Agent                         Site Server
  │ ---- WS 连接 (URL: agent_id) ----> │  accept()            L134
  │                                    │  等待首条消息 ≤10s    L159-164
  │ ---- {type:"auth", agent_id, credential} --> │
  │                                    │  verify_credential  L191-199
  │                                    │  site_id = agent.site_id  L200-201
  │ <---- {type:"connected", site_id} -- │  L215-220
  │ ---- 心跳 / 事件 ------------------> │  心跳超时 60s         L241-247
  │ <---- 命令 ACK（无远端分发） ------- │  L332-371
```

### 1.2 已有控制

| 控制 | 证据 |
|---|---|
| 首条消息必须 auth | `connection.py` L170-176 |
| 认证超时 10 s | L159-164 |
| 生产禁止 URL credential | L145-150 |
| 凭证 SHA256 + agent_id 绑定 + 吊销/过期 | `auth/__init__.py` L166-183 |
| Site 由服务端绑定 | `connection.py` L200-201 |
| 未认证不处理业务 | L170-184 |
| 心跳超时断开 | L241-247 |
| URL 脱敏 | L46-53 |

### 1.3 缺口（对应审计 G-10–G-13）

| 编号 | 缺口 | 影响 |
|---|---|---|
| WSA-01 | `accept()` 先于认证 | 未认证连接可占用资源 |
| WSA-02 | 无连接/认证速率限制 | 暴力尝试、连接耗尽 |
| WSA-03 | 无 Origin / Host 校验 | 非浏览器客户端无所谓，但缺纵深 |
| WSA-04 | 撤销凭证不关闭活动连接 | 撤销不即时生效（HIGH） |
| WSA-05 | 连接建立后不复核凭证有效期 | 长连接可越过过期 |
| WSA-06 | Site 缺失回落 `"site-001"` | 潜在错误归属 |
| WSA-07 | 无重放防护（无 nonce） | 凭证可被重放（明文 `ws://` 时 HIGH） |
| WSA-08 | 无消息大小/频率限制 | 资源耗尽 |
| WSA-09 | 同 `agent_id` 新连接覆盖旧连接 | 身份混淆、旧连接悬挂 |
| WSA-10 | 默认示例 `ws://` | 中间人 |

### 1.4 增强建议（PROPOSED）

| 编号 | 建议 | 前置 |
|---|---|---|
| WSA-E1 | 认证前连接数上限 + 按 IP/agent_id 失败限流 | SP-09 |
| WSA-E2 | 调用 `revoke_credential` 时通过 registry 主动关闭连接 | SP-10 |
| WSA-E3 | 连接每 N 分钟（如 10 min）复核凭证状态/过期；失效即 4401 | SP-10 |
| WSA-E4 | Site 缺失直接 4401，删除 `"site-001"` 兜底 | SP-03 |
| WSA-E5 | 同 `agent_id` 已在线的处理策略：踢旧 + 审计，或拒绝新 | SP-10 |
| WSA-E6 | 跨主机强制 `wss://`；回环可 `ws://` | SP-16 |
| WSA-E7 | 可选 nonce 挑战（需协议变更 → 单独批准，不得默认实施） | 需协议审批 |
| WSA-E8 | 单帧最大字节 + 每秒消息上限 | SP-09 |
| WSA-E9 | Origin 校验（浏览器来源一律拒绝 Agent WS） | SP-06 |
| WSA-E10 | 认证成功/失败/撤销/替换连接全部审计 | SP-07 |

---

## 2. WS-U：前端事件订阅（设计，未实现）

### 2.1 前置条件（硬门禁）

WS-U **不得**在以下条件全部满足前进入实施：

1. 用户认证与会话已上线（SP-01/SP-02）。
2. Site Scope 强制已上线（SP-03）。
3. RBAC 已上线（SP-04）。
4. CSRF / Origin / Host 校验已上线（SP-06）。
5. 审计已身份化（SP-07）。
6. 限流已上线（SP-09）。
7. 安全测试计划中 WS 用例通过（见 `PHASE08_2_2_SECURITY_TEST_PLAN.md`）。

### 2.2 认证：短时票据，URL 不携带长期凭证

**禁止**：

- 禁止把会话 Token、Agent 凭证、长期 API Key 放在 WS URL。
- 禁止仅依赖 Cookie 而不校验 Origin（存在 CSWSH 风险）。

**方案（PROPOSED）**：

```text
1) 已认证 REST 调用：
   POST /api/v1/ws/tickets
   ← { "ticket": "<32B random>", "expires_in": 30 }

2) 建立连接：
   wss://<host>/ws/v1/site?ticket=<ticket>
   - ticket 单次使用、30 秒有效、绑定 user_id + site_scope
   - 服务端握手阶段校验并立即销毁 ticket

3) 连接建立后：
   - 服务端持有 principal 与 site_scope
   - 客户端不得再提交身份
```

要点：

| 项 | 设计 |
|---|---|
| 票据生成 | CSPRNG 32 字节；服务端存 SHA256 + `used`/`expires_at` |
| 票据绑定 | `user_id`、`permission_version`、`site_scope`、来源 IP（可选） |
| URL 暴露 | 仅短时票据；日志脱敏 `ticket=` |
| Cookie 方案 | 若使用 Cookie，必须校验 `Origin` 白名单，且 `SameSite=Strict` |
| 失败响应 | 4401（未认证）/ 4403（无权限），不带内部细节 |

### 2.3 订阅授权（连接后）

```json
{ "type": "subscribe", "site_id": "site-001", "topics": ["device.status", "measurement"], "device_ids": ["..."] }
```

服务端规则：

1. `site_id` 必须 ∈ 连接绑定的 `site_scope`，否则 **4403 并终止**。
2. `device_ids` 必须属于该 Site，否则整条订阅拒绝。
3. 订阅主题白名单：`device.status`、`agent.status`、`measurement`、`site.stats`。
4. 单连接订阅上限（如 100 个设备），超限拒绝。
5. 服务端在**事件分发前**再次校验事件 `site_id` 与连接 Scope，防止越权推送。
6. 不信任客户端声称的 Site。

### 2.4 事件信封

```json
{
  "type": "event",
  "site_id": "site-001",
  "topic": "measurement",
  "event_id": "...",
  "sequence": 123,
  "device_id": "...",
  "device_boot_id": "...",
  "agent_id": "...",
  "device_timestamp_ms": 1234567890,
  "site_received_at": "2026-11-05T...",
  "payload": { }
}
```

> 当前 Measurement REST **不返回** `agent_received_at`；WS 也不得虚构该字段。

### 2.5 快照与补齐

| 场景 | 设计 |
|---|---|
| 订阅后初始快照 | 服务端按 Scope 查询 DB，发送 `snapshot`（分页/分批） |
| 断线重连补齐 | 客户端携带 `last_event_id`；服务端校验该事件属于本 Scope 后再补发 |
| 快照权限 | 快照查询强制附加 `site_id` 条件，与其他 REST 查询同一授权层 |
| 无法补齐 | 明确返回 `snapshot_too_old`，客户端重新拉全量 REST |
| 禁止 | 不得用客户端提供的 `site_id` 直接查库 |

### 2.6 生命周期与权限变化

| 事件 | 服务端行为 | 客户端行为 |
|---|---|---|
| 会话过期 | 关闭 4401 | 重新登录 → 重新取 ticket |
| 会话被撤销（退出） | 关闭 4401 | 停止重连，回登录页 |
| 角色变更 | 递增 version → 发送 `permission.changed` → 关闭 4403 | 重新登录 |
| Site 成员被移除 | 终止该 Site 订阅；若无其他 Site 则关闭 | 刷新 |
| 设备被删除/迁移 | 发送 `device.removed`；停止该设备推送 | 更新 UI |
| 服务端重启 | 连接断开 | 指数退避重连（每次重新取 ticket） |
| 慢客户端 | 有界队列；超出丢弃并按策略标记 `dropped` | 可重新拉快照 |

### 2.7 断线重连（PROPOSED）

- 客户端指数退避 + 抖动：初始 1 s，倍率 2，上限 60 s。
- 每次重连必须重新申请 ticket（不得复用）。
- 连续认证失败（4401/4403）停止自动重连，提示用户。
- 网络恢复后先做一次 REST 增量校验，再订阅。

### 2.8 跨站隔离

| 检查 | 规则 |
|---|---|
| 连接握手 | ticket 绑定单一 `site_scope` 集合 |
| 订阅 | 逐条校验 `site_id` |
| 事件分发 | 分发前校验事件 `site_id` |
| 快照/补齐 | 查询强制 `site_id` 过滤 |
| 审计 | 跨站订阅尝试必须记录为拒绝事件 |
| 多 Site 用户 | 允许多个订阅，但每条独立校验；不得一条订阅覆盖多 Site |

### 2.9 限流与容量

| 项 | 建议 |
|---|---|
| 单用户连接数 | ≤ 5 |
| 单连接订阅数 | ≤ 100 设备 |
| 每连接事件速率 | 上限 + 超限丢弃策略 |
| 认证失败 | 5 次/分钟 → 拒绝并审计 |
| 消息大小 | ≤ 64 KB（事件）；超限断开 |

---

## 3. 与现有架构的关系

| 项 | 结论 |
|---|---|
| 是否修改现有 Agent WS | **否**（本阶段只提建议） |
| 是否新增 `/ws/v1/site` | **否**（未批准，不实施） |
| 是否影响轮询 | 未来 WS 上线后可降级轮询；**当前轮询保持不变** |
| 是否影响 08.2.1 事件流 ADR | 不改变其 PROPOSED 状态；本文为安全前置 |
| 协议冻结 | 保持；WSA-E7 需单独协议审批 |

---

## 4. 审计日志字段（WS）

| 字段 | 说明 |
|---|---|
| `ts` | 时间 |
| `plane` | `agent` / `user` |
| `event` | connect / auth_ok / auth_fail / subscribe / subscribe_denied / close / revoked |
| `principal` | `agent_id` / `user_id` |
| `site_id` | 绑定 Site |
| `remote_ip` | 来源 |
| `reason` | 失败原因（不含凭证） |
| `detail` | 不记录 credential/ticket 明文 |

---

## 5. 明确不做

- 不实现前端 WS。
- 不修改 Agent WS 协议字段。
- 不把长期凭证放 URL。
- 不以 Cookie-only 且无 Origin 校验的方式建立 WS。
- 不在本阶段写代码或迁移。

---

## 6. 变更声明

- 未修改任何 WebSocket 实现、协议、代码或测试。
- 文档状态：**PROPOSED**。
