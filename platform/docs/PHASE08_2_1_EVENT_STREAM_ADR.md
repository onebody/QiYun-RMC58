# Phase 08.2.1 — ADR-08.2.1-02：事件流裁定

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.1 — 实际代码审计结果补充约束
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> 约束：不修改业务代码、不修改数据库、不修改 API、不修改协议
> 依据：`gateway-agent/src/widscan_agent/site_connector.py`、`events/bus.py`、`site-server` WS 接入、`site-server/web/app.js`、08.5 运行数据

## 0. 证据等级定义

| 等级 | 含义 |
|---|---|
| **L1** | 运行实测（08.5 / 08.6 真实进程、日志、统计） |
| **L2** | 静态代码事实（可指认文件/行号） |
| **L3** | 既有文档记录（本轮未复测） |
| **L4** | 推演/估算（无运行证据） |

## 1. 引用事实与证据等级

> 完整 16 项事实见 `docs/PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md` §2（F-01–F-16）；本 ADR 引用与事件流直接相关的条目。

| 事实 | 内容 | 等级 | 证据 |
|---|---|---|---|
| F-05 | 前端 5/10/15 秒固定间隔轮询 | **L2**（并 L1） | `app.js` 各页 `{ms}`；08.5 观察数值按秒级刷新 |
| F-06 | 无前端 `/ws/v1/site` 事件流 | **L1** | 08.5 端点探测 404 |
| F-07 | 无完整前端 Event Client | **L2** | `site-server/web` 无订阅/游标/去重代码 |
| F-12 | `agent_received_at` 已入库但 Measurement API 未暴露 | **L2**（并 L1） | `measurements_list` 白名单无该字段；08.5 接口响应确认 |
| F-09 | REST 无鉴权 | **L1** | 08.5 无凭证 `curl` 成功 |
| F-01 | 单站点、无 `/api/v1/sites` | **L1** | 08.5 404 |
| F-15 | Phase 07 四项 PASS，整体 CONDITIONAL | **L3** | Phase 07 验收文档 |

## 2. 当前实现（必须与未来设计分开）

### 2.1 当前：Agent → Site Server

| 环节 | 现状 | 等级 | 证据 |
|---|---|---|---|
| 运输 | 单条 WebSocket `/ws/v1/agents/connect` | L2 | `site-server` 路由 |
| 认证 | 首条 `auth` 消息（agent_id + credential），15 s 超时 | L2 | `agents/connection.py:151-199` |
| 上行事件 | `device.online` / `device.frame` / `device.state_change` / `device.registered` / `device.removed` / `agent.started` / `agent.error` | L2 | `_register_event_handlers` |
| 队列 | **双队列**：Critical 128 / Measurement 128 | L2 | `site_connector.py:180-181,696-697` |
| 优先级 | Critical 优先；`recv(critical, 50 ms)` 超时后 `recv(measurement, 200 ms)` | L2 | `_outbound_priority_dispatch` |
| RAWIQ | Connector 内直接跳过（不排队） | L2 | `site_connector.py:518-592` |
| 满队列 | **丢弃新到事件** + 计数（`_dropped_critical/_dropped_measurement`），非淘汰最旧 | L2 | `site_connector.py:521-542` |
| 每事件 ACK | **无**；仅心跳 `heartbeat ⇄ heartbeat.ack`（Agent 每 15 s；Server 45 s 超时） | L2 | `_heartbeat_loop` / `site-server` 回执 |
| 持久化缓冲 | **无**；队列在内存，进程重启丢失；断线态事件按状态门控丢弃 | L2 | `_bus_handler` 状态门控 + 生命周期建队 |
| 重连 | 指数退避重连；重连后发送 `agent.devices_snapshot` | L2 | `_backoff_reconnect` / `_send_devices_snapshot` |
| 幂等 | Site Server 以 `(site_id,event_id)` 唯一键去重 | L2 | `models.py` `uq_measurement_event` |
| 完整性 | `agent_received_at` 入库；缺失/非法保持 NULL | L2 | `ingestion` |

### 2.2 当前：Site Server → Frontend

| 环节 | 现状 | 等级 |
|---|---|---|
| 推送通道 | **不存在** `/ws/v1/site` | L1 |
| 前端 Event Client | **不存在** | L2 |
| 替代方案 | 页面轮询（Overview 10 s、Gateways/Devices 5 s、Sites/System 15 s）；Measurements 无自动刷新 | L2 |
| 隐藏标签页 | 暂停轮询，恢复立即刷新 | L2 |
| 失败处理 | 保留 stale + 错误条 + 固定间隔继续 + 手动重试；**无指数退避** | L2 |
| 增量更新 | 无；整页重建 | L2 |

> 结论：**当前没有前端实时事件流**。任何“实时推送”“订阅”“补数”描述都属于未来设计。

## 3. A. Gateway Agent → Site Server 裁定

### 3.1 队列饥饿风险（Q1）

**风险存在，但当前未观测到。**

```text
dispatch 循环：
  critical = await recv(critical_q, timeout=50ms)
  if critical: send; continue
  measurement = await recv(measurement_q, timeout=200ms)
  if measurement: send
```

- Critical 持续非空时，Measurement **可被无限期饿死**。
- 无配额、无权重、无“Critical 最多连续 N 条”限制。
- 当前 Critical 事件为低频状态类事件，08.5 运行中**未出现**饥饿现象（L1 观察，非压力测试）。
- **裁定（PROPOSED）**：在启用高帧率/多设备前必须引入配额；当前不改代码。

**风险等级**：当前运行 **低**；规模增长后 **中–高**。

### 3.2 负载数据与容量（Q2）

| 指标 | 值 | 等级 |
|---|---|---|
| Critical 队列容量 | 128 | L2 |
| Measurement 队列容量 | 128 | L2 |
| 实测上行吞吐 | ≈18–19 事件/s（08.5：5 s 内 97 条；32 s 内 580 条） | L1 |
| 单队列缓冲时长（按 19/s） | ≈6.8 s | L4 |
| 饱和/丢弃测试 | **未做**；无“队列满”warning 记录 | L1/L4 |
| 计数可观测性 | `_dropped_critical`、`_dropped_measurement`、`*_full_count` 已存在 | L2 |

**裁定（PROPOSED）**：现有数据只能证明“低负载下有余量”，**不足以**证明多设备高帧率下的容量；需要独立压测（见 Q10）。

### 3.3 是否需要配额调度（Q3）

**需要，但不在本阶段实施。**（L4，PROPOSED）

建议规则：

- 每发送 N 条 Critical，至少发送 1 条 Measurement（如 2:1）。
- 或时间片轮转：每 100 ms 窗口内 Critical 最多占 80%。
- 对 Critical 队列设“最大等待时间”，超时提升 Measurement。
- 配额参数必须可配置并有指标。

### 3.4 是否需要持久化缓冲（Q4）

**需要评估，属于独立后端阶段。**（L2 + L4，PROPOSED）

现状：队列在内存；进程崩溃、重启、长时间断线会**永久丢失**未发送事件。

| 方案 | 优点 | 风险 |
|---|---|---|
| A. Agent 本地 WAL/Spool（仅 Critical + 可配置 Measurement） | 崩溃恢复、断线补发 | 磁盘/IO、顺序、清理策略 |
| B. 仅 Measurement 批量落盘 | 保证数据完整性 | 实现复杂 |
| C. 不持久化，依赖 Site 端幂等 | 简单 | 丢数据不可恢复 |

建议：先 A（Critical 必持久化，Measurement 可选），并以 `event_id` 幂等保证重复安全。

### 3.5 是否需要 ACK（Q5）

**需要，但应为批量 ACK，不是逐条 ACK。**（L2 + L4，PROPOSED）

- 现状：仅心跳 ACK；Site Server 不确认事件落库。
- 风险：Agent 认为“已发送”，但服务器可能未持久化（尤其断线/崩溃窗口）。
- 建议：Site Server 定期返回 `ingest.ack`（批次 `event_id` 范围/计数），Agent 据此推进游标；配合已有唯一键实现 at-least-once。
- 不得修改现有协议字段；需新增消息类型并单独评审。

### 3.6 当前可靠性边界（结论）

| 场景 | 当前行为 | 数据影响 |
|---|---|---|
| 队列满 | 丢新事件 + 计数 | Measurement 可能缺失 |
| Agent 崩溃 | 内存队列丢失 | 未发送事件永久丢失 |
| 断线重连 | 部分状态丢弃；重连发设备快照 | 期间 Measurement 可能缺失 |
| 重复发送（未来） | Site 端按 `(site_id,event_id)` 去重 | 安全 |

## 4. B. Site Server → Frontend 裁定（未来设计，当前未实现）

### 4.1 WebSocket 与订阅授权（Q6 / Q10 / 安全）

**PROPOSED 设计要点：**

1. 新增 `/ws/v1/site`；**首条消息必须认证**，不得接受 URL 中的裸 token。
2. 订阅必须携带/解析 `site_id`，并与 principal 的 Site Scope 求交；越权 → 关闭（4403）。
3. 订阅主题白名单：`site.counts`、`agent.status`、`device.status`、`measurement.recent`；不允许客户端自定义查询。
4. 每个连接限制订阅数；慢客户端只保留“最新快照 + 游标”，不无限缓存。
5. 事件推送必须带 `event_id`，前端按 `event_id` 去重。
6. 禁止在 WebSocket 中传输 RAWIQ/频谱高频数据。

### 4.2 快照补齐（Q7）

**需要。**（L4，PROPOSED）

```text
connect → auth → subscribe(site)
       ← snapshot {counts, recent_agents, recent_devices, recent_measurements, cursor}
       ← increment {event_id, type, ...}
```

- 无快照会出现“订阅后列表缺历史”的空窗。
- 断线重连必须带 `cursor`（最后已处理 `event_id`/时间），服务端补发之后的事件；无法补齐时下发新快照并让前端整体替换。

### 4.3 重复消息与慢客户端

| 问题 | 方案 |
|---|---|
| 重复推送 | 前端按 `event_id` 去重；服务端至少一次 |
| 乱序 | 以 `site_received_at` + 单调序号排序；前端以快照为准 |
| 慢客户端 | 服务端丢弃中间增量，只发“最新快照”标记；记录 `slow_client_dropped` |
| 页面切换 | 订阅随路由生命周期建立/关闭；不得跨页面复用旧订阅 |
| 多页面 | 单标签页单连接；多标签页各自连接，服务端限流 |

### 4.4 轮询过渡方案（Q8）

**在 `/ws/v1/site` 可用前，轮询仍是唯一方案。**（L2，PROPOSED）

- 保留 5/10/15 s 轮询作为 WS 不可用时的降级路径。
- 前端必须能同时处理“推送 + 轮询兜底”，但不得双写同一状态（推送优先，轮询仅在断连时启用）。
- 页面切换必须清理订阅与定时器。

### 4.5 5xx 指数退避（Q9）

**需要，且应作为独立的前端可靠性修复。**（L2，PROPOSED）

现状：固定间隔，无退避；持续 5xx 会以固定频率持续请求。

建议：

- 5xx / NET：指数退避（1s → 2s → 4s … 上限 60 s）+ 抖动；成功后重置。
- 4xx（除 408/429）：不重试，直接展示错误（避免放大错误）。
- 429：尊重 `Retry-After`。
- 页面隐藏时暂停；恢复时立即刷新一次。
- **禁止**把退避实现混入 UI 视觉改造；单独提交、单独验收。

### 4.6 事件推送是否独立后端阶段（Q10）

**是。**（PROPOSED）

`/ws/v1/site` 涉及：认证、Site Scope、订阅管理、快照、游标、补数、慢客户端、限流、指标。它不属于 UI 集成，应作为独立后端能力阶段。

### 4.7 不应纳入 UI 集成范围的优化

| 优化 | 归属 |
|---|---|
| 队列配额/优先级重构 | Agent 后端 |
| WAL/持久化缓冲 | Agent 后端 |
| 批量 ACK / 协议消息 | Agent + Site Server 协议 |
| `/ws/v1/site`、订阅、快照、游标 | Site Server 后端 |
| Site Scope 注入 | 后端授权层 |
| 前端指数退避 | 前端可靠性修复（独立小阶段） |

## 5. 决策汇总

| 项 | 决策 | 状态 |
|---|---|---|
| 现在实现前端 WS | 否 | **PROPOSED** |
| 现在重构 Agent 事件协议 | **否（冻结）** | **PROPOSED（硬约束）** |
| Critical/Measurement 配额 | 需要，独立后端阶段 | **PROPOSED** |
| 持久化缓冲 | 需要评估，独立阶段 | **PROPOSED** |
| 逐事件 ACK | 不采用；建议批量 ACK | **PROPOSED** |
| 前端 WS 快照 | 未来必需 | **PROPOSED** |
| 轮询指数退避 | 需要，独立前端修复 | **PROPOSED** |
| 保留轮询作为降级 | 是 | **PROPOSED** |

## 6. 影响与风险

| 风险 | 等级 | 说明 |
|---|---|---|
| 饥饿导致 Measurement 延迟 | 中 | 高负载 + Critical 突发 |
| 队列满/崩溃丢数据 | 中–高 | 无持久化 |
| 无 ACK 导致“假成功” | 中 | 断线窗口 |
| 未来 WS 无 Scope 导致越权订阅 | 高 | 必须与安全阶段同步 |
| 前端持续 5xx 请求放大 | 低–中 | 无退避 |
| 协议提前重构破坏兼容 | 高 | 明确冻结，禁止 |

## 7. 本阶段结论

- 当前事件流 = **Agent→Server 有双队列，Server→Frontend 无事件流**。
- 所有可靠性增强（配额、持久化、ACK、WS、快照、退避）均**未实施**，列为独立阶段。
- 本 ADR 保持 **PROPOSED**，等待人工审批；本轮未修改任何业务代码/协议。
