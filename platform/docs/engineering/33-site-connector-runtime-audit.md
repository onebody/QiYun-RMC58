# Site Connector V1.1 — 运行时架构审计报告

**审计日期**: 2026-11-04  
**审计目标**: `gateway-agent/src/widscan_agent/site_connector.py` (806 行, V1.0)  
**配套审计**: `site-server/src/widscan_site/agents/connection.py` (293 行)  
**审计维度**: Threading ↔ asyncio 桥接 / 队列安全 / WebSocket 并发写 / 认证安全 / Command 状态机 / 生命周期 / 诊断

---

## 1. 架构概览（V1.0）

```
DeviceManager.publish(event)     ← Device 线程（USB/Simulator）
         │
         ▼
EventBus.publish()               ← 同步 + threading.Lock，逐个调用 handler
         │
         ▼
_bus_handler(event)              ← 同步回调，在 Device 线程中
         │
         ▼
asyncio.run_coroutine_threadsafe(self._outbound_queue.put(event), self._loop)
         │
         ▼
asyncio.Queue (maxsize=256)      ← 在 Connector daemon 线程的 event loop 中
         │
         ├── _outbound_dispatch_loop → _to_site_envelope() → _send_json() → ws.send()
         ├── _heartbeat_loop → _send_json() → ws.send()          ← 并发写 ⚠️
         └── _handle_command_request → _send_command_response → ws.send() ← 并发写 ⚠️
```

**Connector daemon thread**: `SiteConnector.start()` 创建，`asyncio.new_event_loop()` 私有 loop，`_lifecycle()` 主循环。

**Site Server 认证**: Agent 在 WebSocket URL Query 中带 `credential=xxx`；Site Server `connection.py:L93-94` 直接从 `ws.query_params` 读取。

---

## 2. 发现的问题（按严重度排序）

### A1. EventBus 回调阻塞 DeviceManager 线程 🔴 CRITICAL

**位置**: `site_connector.py:L307-324` — `_bus_handler`

```python
def _bus_handler(self, event: Event) -> None:
    if self._loop is None or self._outbound_queue is None or self._state != ConnectorState.CONNECTED:
        return
    try:
        future = asyncio.run_coroutine_threadsafe(
            self._outbound_queue.put(event),
            self._loop,
        )
        future.result(timeout=0.5)   # ← BLOCKING!
    except Exception:
        self._dropped_events += 1
```

**问题**: `EventBus.publish()` 在 Device 线程中同步调用所有 handler。这里调用 `future.result(timeout=0.5)` 会阻塞发布者线程等待 Connector 线程的 event loop drain queue。

**触发条件**:
- Connector loop 暂停（GC 停顿 / CPU 抢占 / debug breakpoint）→ Device 线程最多被阻塞 0.5s
- `_outbound_queue` 满载（256 条）→ `put()` 等消费者取出 → future.result 超时（0.5s）→ 事件丢弃
- 断线期间 Device 正常运行但 Connector 不在 CONNECTED 状态 → L312 直接丢弃

**违反原则**: "EventBus 回调不得等待网络发送" / "EventBus 同步回调只负责非阻塞提交"（任务书 §2 P4）

**修复方向**:
- 使用 `loop.call_soon_threadsafe(future.add_done_callback, self._on_bus_future)`
- EventBus 回调只做 fire-and-forget，不等待
- add_done_callback 里只做 dropped / ok 统计

---

### A2. Credential 明文在 URL Query 中 🔴 CRITICAL

**位置**: `site_connector.py:L504` — `_try_connect`

```python
url = f"{self._endpoint.rstrip('/')}/ws/v1/agents/connect?agent_id={self._agent_id}&credential={self._credential}"
```

**问题**:
1. WebSocket URL 会出现在 Nginx/Proxy access log
2. 会出现在 `websockets` 客户端连接日志
3. 会出现在异常堆栈中（InvalidStatusCode exc 可能带 URL）
4. 明文 credential 一旦泄露，所有已配对 Agent 身份可被冒充

**Site Server 端**: `connection.py:L93-94`
```python
agent_id = ws.query_params.get("agent_id", "").strip()
credential = ws.query_params.get("credential", "").strip()
```

**违反原则**: "Credential 不得放入 WebSocket URL Query"（任务书 §2 P7）

**修复方向**:
- Agent: 连接 URL **不含 credential**，`await ws.connect(url)` 完成后立即发送 `{type: "auth", agent_id, credential, agent_version}` 首条认证消息
- Site Server: `await ws.accept()` 后，等待首条消息（timeout=3s），若是 auth 消息则校验，否则 fallback URL query（V1.0 兼容）；认证通过前禁止业务消息
- Agent→URL 只传 `agent_id`（非敏感）

---

### A3. 单一队列 + 高低频不分 🔴 CRITICAL

**位置**: `site_connector.py:L139` — `__init__`

```python
self._outbound_queue: Optional[asyncio.Queue] = None   # 单一 256 条
```

**问题**:
- Simulator Transport 每 ~1s 对每设备发 **5 种 frame type**（METRICS / SPECTRUM / TELEMETRY / LEVELS / LOG）
- 3 个 Simulator × 5 = 15 events/s → 256 条队列 ~17s 填满
- `device.frame spectrum` 高频数据挤占 `device.online` / `device.state_change` 等关键事件
- 队列满载时 `put()` 阻塞 0.5s 后放弃 → 重要事件一起丢

**违反原则**: "高频频谱数据不得挤占重要设备状态事件"（任务书 §2 P6）/ "不得无限制缓存"

**修复方向**:
- 双队列: `_critical_queue` (maxsize=128) + `_measurement_queue` (maxsize=128)
- Critical: device.online / device.offline / device.state_change / device.registered / agent.error / 配置执行结果
- Measurement: device.frame (type_code 1/2/3) / device.stream_stats
- 关键队列满载 → log critical + 计数 + 可选本地持久化
- 测量队列满载 → **丢最旧的**（队列尾部），计数 `dropped_measurement`
- `_outbound_dispatch_loop` **优先消费 critical**，间隙拉 measurement

---

### A4. WebSocket send 并发写 🔴 CRITICAL

**位置**: 多处

| 协程 | 调用位置 | 目的 |
|---|---|---|
| `_outbound_dispatch_loop` | L616 | 发送 agent.event |
| `_heartbeat_loop` | L595 | 发送 heartbeat |
| `_handle_command_request` → `_send_command_response` | L713 | 发送 command.response |
| `_send_devices_snapshot` | L748 | 重连时发送全量 |

**问题**: `websockets.asyncio.WebSocketClientProtocol` 的 `send()` 不是并发安全的 — 两个 task 同时 `await ws.send()` 会触发协议级错误（opcode 交织 / frame 不完整），导致连接断开。

**Site Server 端已正确实现**: `AgentConnection.send_json` 用 `asyncio.Lock` 串行化发送（connection.py:L48-53）。

**违反原则**: "WebSocket 发送必须做成单连接串行写"（经验 100035970）

**修复方向**:
- Agent 侧同样加 `asyncio.Lock`，所有 `_send_json` 都 `async with self._send_lock: await self._ws.send(...)`
- 或者更彻底：**一个单独的 sender task** 从统一的 send 队列消费并串行写

---

### A5. Command 无状态机 + 幂等性 🔴 CRITICAL

**位置**: `site_connector.py:L662-707` — `_handle_command_request`

```python
async def _handle_command_request(self, msg: dict) -> None:
    request_id = msg.get("request_id", "")
    # ... validate → execute device.send_command → send response
```

**缺失**:
- **无状态机**: 直接 RECEIVED → EXECUTING → SUCCEEDED/FAILED，跳过 ACCEPTED/EXECUTING 状态
- **无幂等性**: 同一 request_id 重到 → 重复执行 → device 被多次发送命令
- **无断线保护**: 执行到一半 WS 断开 → Site Server 重发 → 又执行一次
- **无命令级超时**: 用 device.send_command 内部 ~1s timeout，但 Connector 不知道这个状态
- **无 REJECTED 状态**: 非法参数 → 直接 FAILED，应先 REJECTED

**违反原则**: 任务书 §6 全部要求

**修复方向**:
- Command state enum: RECEIVED → ACCEPTED → EXECUTING → SUCCEEDED/FAILED/TIMEOUT/REJECTED
- 内存 command registry: `_command_registry: Dict[str, CommandRecord]` 已执行过的 request_id
- 重复 request_id → 查 registry → 返回**已存的**结果（不重新执行）
- 断线恢复: Agent 重启后 registry 丢失（当前 V1.1 用内存 registry 足够）；Site Server 重发同一 request_id → Connector 检查 registry → 若已 SUCCEEDED 则返回已有结果
- Command-level timeout: 30s 全局超时（device.send_command 一般 < 2s，留余量）

---

### A6. asyncio.Queue.qsize() 跨线程调用 🟡 MEDIUM

**位置**: `site_connector.py:L179` — `diagnostics` property

```python
"outbound_buffer": self._outbound_queue.qsize() if self._outbound_queue else 0,
```

**问题**: `diagnostics` 从 **REST API 线程**（uvicorn）访问 `asyncio.Queue.qsize()`。文档说 "Only safe when called from the event loop thread"。虽然 CPython 实现里不会崩溃，但非线程安全 — 如果 Connector 线程正在 qsize 调用中同时 REST 线程也在调，内部计数器可能错乱。

**修复方向**: 在 Connector event loop 中维护独立的计数器 `_critical_queue_len` / `_measurement_queue_len`，每次 put/get 手动 +-=1；REST 线程读这个计数器。

---

### A7. 状态模型不完整 🟡 MEDIUM

**当前**: DISABLED / PAIRING / CONNECTING / CONNECTED / DISCONNECTED / ERROR

**缺失**:
- AUTHENTICATING（WS 已建但首条 auth 未通过）
- RECONNECTING（BACKOFF 中）
- STOPPING（stop() 正在执行）

**修复方向**: 扩展 ConnectorState 枚举，在对应阶段切换。

---

### A8. Diagnostics 指标不足 🟡 MEDIUM

**当前 12 个字段**：state, enabled, endpoint, has_credential, connected_at, reconnect_attempts, outbound_buffer, pending_commands, total_events_sent, dropped_events, last_heartbeat_sent, last_heartbeat_ack

**缺失（任务书 §7 要求的 ~30 项）**:
- 最近断开时间 / 最近错误
- critical_queue_len / measurement_queue_len / critical_queue_cap / measurement_queue_cap
- critical_queue_full_count / measurement_queue_full_count
- dropped_by_type: Dict[str, int]（每种事件类型丢弃数）
- auth 相关: auth_state / credential_id 摘要
- 命令: commands_received / commands_succeeded / commands_failed / commands_timeout / commands_rejected
- 连接: last_connected_at / last_disconnected_at / total_disconnects
- 当前 WS 状态: ws_open / send_lock_contended

**修复方向**: 大幅扩展 diagnostics dict，所有指标由 Connector event loop 中的代码维护（避免跨线程读）。

---

### A9. stop() 无 force 超时 🟡 MEDIUM

**位置**: `site_connector.py:L219-227`

```python
def stop(self) -> None:
    self._disconnect_all(DisconnectReason.USER_INITIATED)
    if self._thread is not None:
        self._thread.join(timeout=3.0)   # 3s 超时后直接返回
        self._thread = None
```

**问题**: 3s 超时后 thread 仍在跑（比如卡在 WS 关闭超时 10s）→ daemon thread 留着 → 下次 start() 可能出错（L193 check `is_alive()` 返回 True）。

**修复方向**: 
- stop() 最多等 3s，超时后**标记 thread 为 stale**，下次 start() 检查
- 若 event loop 还在（`self._loop is not None and not self._loop.is_closed()`），用 `run_coroutine_threadsafe` 强行 close WS + cancel all tasks

---

### A10. 无 TLS 配置 🟡 MEDIUM

**位置**: `site_connector.py:L508`

```python
ws = await asyncio.wait_for(
    websockets.connect(url, additional_headers={"User-Agent": "..."}),
    timeout=10.0,
)
```

**问题**: 无 `ssl` 参数。连接 `wss://` 时：
- 默认用系统 CA 验证 → 自签名证书连不上
- 生产环境无选项关闭证书验证（反而是好事，但需要显式 ssl context）
- 无 CA file 配置

**修复方向**:
- Config 加 `tls_ca_file: str` / `tls_verify: bool = True`
- `websockets.connect(url, ssl=ssl_ctx or True)` — 默认系统 CA，可配 CA file，开发环境可显式关闭

---

## 3. 问题汇总与修复优先级

| # | 问题 | 严重度 | 影响范围 | 修复阶段 |
|---|---|---|---|---|
| A1 | future.result() 阻塞 EventBus | 🔴 CRITICAL | 所有 Device 发布者 | Phase 2 |
| A2 | Credential 在 URL Query | 🔴 CRITICAL | 认证安全 | Phase 3 |
| A3 | 单队列 / 高低频不分 | 🔴 CRITICAL | 事件可靠性 | Phase 2 |
| A4 | WS 并发写 | 🔴 CRITICAL | 连接稳定性 | Phase 2 |
| A5 | Command 无状态机/幂等 | 🔴 CRITICAL | 命令可靠性 | Phase 4 |
| A6 | Queue.qsize() 跨线程 | 🟡 MEDIUM | 诊断准确性 | Phase 2 |
| A7 | 状态模型不完整 | 🟡 MEDIUM | 可观测性 | Phase 5 |
| A8 | Diagnostics 指标不足 | 🟡 MEDIUM | 诊断 API | Phase 7 |
| A9 | stop() 无 force | 🟡 MEDIUM | 生命周期 | Phase 6 |
| A10 | 无 TLS 配置 | 🟡 MEDIUM | 安全配置 | Phase 3 |

---

## 4. 修复后验收检查点

- [ ] EventBus 回调 profile 不含 `Connector._bus_handler` 阻塞
- [ ] Credential 不出现在 Nginx access log / `websockets` debug log / 异常堆栈 / diagnostics API
- [ ] 120 Measurement events/s 持续 5min → Device state events 0 丢弃
- [ ] 高并发 send 不触发 `websockets.exceptions.ConnectionClosedError: invalid frame`
- [ ] 同一 request_id 到达 3 次 → 只执行 1 次，后 2 次返回缓存结果
- [ ] REST /api/v1/site-connector 返回 ≥ 25 个字段，不含 credential 明文
- [ ] stop() 后 5s 内 Connector daemon thread 退出
- [ ] wss:// + 自签名 CA 文件 → 连接成功；wss:// + verify=False → 不允许（生产默认）
