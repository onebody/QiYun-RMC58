# 37 — Site Connector 弹性与故障隔离

> **版本**：V1.1  
> **日期**：2026-11-04  
> **代码基准**：`gateway-agent/src/widscan_agent/site_connector.py` L60-69, L842-854, L386-412  
> **交叉引用**：[33-site-connector-runtime-audit.md](./33-site-connector-runtime-audit.md) — A7（状态模型）+ A8（诊断）+ A9（force shutdown）  
> **区分说明**：V1.0 审计中"状态模型不完整、stop() 无 force、诊断指标不足"问题在 V1.1 已根治。

---

## 一、9 态 ConnectorState

### 1.1 状态定义

**代码**：L60-69

```python
class ConnectorState(str, enum.Enum):
    DISABLED = "disabled"          # 配置禁用
    STOPPED = "stopped"            # 显式停止 / 缺 credential
    CONNECTING = "connecting"      # WS 握手中
    AUTHENTICATING = "authenticating"  # WS 已连，发首条 auth
    CONNECTED = "connected"        # 认证通过 + 心跳正常 + 事件转发中
    RECONNECTING = "reconnecting"  # 断线，退避等待下一次连接
    AUTH_FAILED = "auth_failed"    # 认证失败（credential 撤销）— 停止重连
    ERROR = "error"                # 持续性错误 — 等待手动处理
    STOPPING = "stopping"          # stop() 正在执行
```

### 1.2 与 V1.0 对比

| V1.0 6 态 | V1.1 9 态 | 新增 |
|-----------|-----------|------|
| DISABLED | DISABLED | — |
| — | **STOPPED** | ✓ start() 后显式停止 / 缺 credential |
| CONNECTING | CONNECTING | — |
| — | **AUTHENTICATING** | ✓ WS 已连 → auth 未通过 |
| CONNECTED | CONNECTED | — |
| DISCONNECTED | **RECONNECTING** | ✓ 明确语义：退避等待重连 |
| — | **AUTH_FAILED** | ✓ 永久失败 → 停止重连 |
| ERROR | ERROR | — |
| — | **STOPPING** | ✓ stop() 执行中（3s 超时语义） |

### 1.3 完整状态流转图

```
          ┌────────────────────────────────────────────────────────────────┐
          │                        start()                                │
          │     enabled=False?  →  DISABLED                               │
          │     缺 credential?  →  STOPPED                                │
          └────────────────┬─────────────────────────────────────────────┘
                           │ (enabled + credential)
                           ▼
          ┌─────────────┐   connect() 成功    ┌─────────────────┐
          │  CONNECTING  │─────────────────────▶│ AUTHENTICATING   │
          └──────┬──────┘                     └────────┬────────┘
                 │                                    │
        connect 失败 / 4401/401                  auth 成功
                 │                                    │
                 ▼                                    ▼
          ┌─────────────┐                        ┌─────────────┐
          │AUTH_FAILED  │  清空 credential       │  CONNECTED  │ ◀─── 心跳正常 + 事件转发
          └─────────────┘  停止重连               └──────┬──────┘
                                                        │
                                               心跳超时 / WS 断开 /
                                               send 异常 / recv 异常
                                                        │
                                                        ▼
          ┌─────────────┐   USER_INITIATED    ┌─────────────────┐
          │   STOPPED   │◀────────────────────│  RECONNECTING    │
          └──────▲──────┘                     └────────┬────────┘
                 │                                    │
          stop()                                退避 delay 后重连
                 │                                    │
                 ▼                                    ▼
          ┌─────────────┐                     回到 CONNECTING
          │  STOPPING   │                      (循环直至 success)
          └─────────────┘
                 │
          3s 超时 → force shutdown → 线程退出
                 │
                 ▼
              STOPPED

          注：ERROR 状态在当前代码中通过 DisconnectReason 统一处理，
              实际状态机循环会推断并进入 RECONNECTING/AUTH_FAILED/STOPPED
```

### 1.4 状态切换代码路径

| 状态 | 设置位置 | 触发条件 |
|------|----------|----------|
| DISABLED | `start()` L312, `__init__` L179 | `enabled=False` |
| STOPPED | `__init__` L181, `start()` L322, `stop()` L369, `_lifecycle` L725 | 缺 credential / stop() 完成 |
| CONNECTING | `start()` L331, `_lifecycle` L666 | start() 触发 / 每次重连前 |
| AUTHENTICATING | `_lifecycle` L677 | WS 握手成功 → auth 消息发送中 |
| CONNECTED | `_lifecycle` L690 | auth 通过 + 心跳正常 |
| RECONNECTING | `_lifecycle` L737 | 断线后进入 |
| AUTH_FAILED | `_lifecycle` L729, `_try_connect` L773 | 4401/401 或 auth error 响应 |
| ERROR | 未显式设置（通过 DisconnectReason 传递） | 持续性错误 |
| STOPPING | `stop()` L353 | stop() 开始执行 |

### 1.5 `_set_state` 统一入口

**代码**：L1195-1205

```python
def _set_state(self, new_state: ConnectorState, reason=None):
    with self._lock:
        old = self._state
        self._state = new_state
        if reason is not None:
            self._disconnect_reason = reason if isinstance(reason, DisconnectReason) else None
    if old != new_state:
        reason_str = f" reason={reason.value}" if reason else ""
        log.info("state: %s → %s%s", old.value, new_state.value, reason_str)
```

- `threading.Lock` 保护跨线程读写
- 每次状态变化打 INFO 日志（含 disconnect_reason）
- reason 是 `DisconnectReason` enum 时才存

---

## 二、DisconnectReason 统一入口

### 2.1 定义

**代码**：L76-83

```python
class DisconnectReason(str, enum.Enum):
    USER_INITIATED = "user_initiated"
    CONNECT_FAILED = "connect_failed"
    AUTH_FAILED = "auth_failed"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    WS_CLOSED = "ws_closed"
    WS_ERROR = "ws_error"
    SITE_UNAVAILABLE = "site_unavailable"
```

### 2.2 统一断线入口

**代码**：`_disconnect_all` L1186-1189

```python
def _disconnect_all(self, reason: DisconnectReason):
    self._disconnect_reason = reason
    self._stop_event.set()
```

所有主动/被动断线都通过这一个函数：

| 调用方 | reason | 场景 |
|--------|--------|------|
| `stop()` L355 | USER_INITIATED | 用户/API 调用 stop() |
| `_heartbeat_loop` L870 | HEARTBEAT_TIMEOUT | 60s 没收到 ack |
| `_inbound_dispatch_loop` L945 | WS_CLOSED | `ConnectionClosed` 异常 |
| `_try_connect` L783 | SITE_UNAVAILABLE | OSError / ConnectionError |
| `_try_connect` L777 | CONNECT_FAILED | InvalidStatusCode（非 4401/401） |
| `_infer_disconnect_reason` L1207-1219 | 推断值 | 三个 task 任一先死 |

### 2.3 断线原因推断

**代码**：L1207-1219

```python
def _infer_disconnect_reason(self, done_tasks):
    for t in done_tasks:
        exc = t.exception()
        msg = str(exc).lower()
        if "heartbeat" in msg or "ack timeout" in msg:
            return HEARTBEAT_TIMEOUT
        if isinstance(exc, (ConnectionClosed, ConnectionClosedError)):
            return WS_CLOSED
        if isinstance(exc, (OSError, ConnectionError)):
            return SITE_UNAVAILABLE
    return self._disconnect_reason or WS_ERROR
```

FIRST_COMPLETED 语义：三个 task 任何一个抛出异常 → Connector 整体视为断线。推断逻辑：
1. Heartbeat task 抛 `RuntimeError("heartbeat ack timeout")` → 明确 HEARTBEAT_TIMEOUT
2. Recv task 抛 `ConnectionClosed` → WS_CLOSED
3. Send task 抛 `OSError`（发送失败）→ SITE_UNAVAILABLE
4. fallback → 用已设置的 `_disconnect_reason`，再 fallback 到 WS_ERROR

---

## 三、指数退避 + 抖动

### 3.1 算法实现

**代码**：`_backoff_reconnect` L842-854

```python
async def _backoff_reconnect(self):
    self._reconnect_attempts += 1
    import random
    delay = self._reconnect_initial_delay * (self._reconnect_multiplier ** (self._reconnect_attempts - 1))
    delay = min(delay, self._reconnect_max_delay)
    delay *= (0.5 + random.random() * 0.5)   # 抖动 ±50%
    log.info("Site Connector reconnect attempt %d in %.1fs",
             self._reconnect_attempts, delay)

    deadline = time.monotonic() + delay
    while time.monotonic() < deadline and not self._stop_event.is_set():
        await asyncio.sleep(0.2)
```

### 3.2 默认参数

**代码**：L152-154

```python
reconnect_initial_delay: float = 2.0    # 首次等待 2s
reconnect_max_delay: float = 60.0       # 最大等待 60s
reconnect_multiplier: float = 2.0       # 倍增因子
```

### 3.3 退避计算示例

| 尝试次数 | 基础延迟 (s) | 抖动范围 (s) | 最大延迟 |
|----------|-------------|-------------|---------|
| 1 | 2.0 | 1.0 – 2.0 | 2.0 |
| 2 | 4.0 | 2.0 – 4.0 | 4.0 |
| 3 | 8.0 | 4.0 – 8.0 | 8.0 |
| 4 | 16.0 | 8.0 – 16.0 | 16.0 |
| 5 | 32.0 | 16.0 – 32.0 | 32.0 |
| 6 | 60.0 | 30.0 – 60.0 | 60.0（封顶） |
| 7+ | 60.0 | 30.0 – 60.0 | 60.0 |

### 3.4 抖动的必要性

多个 Agent 同时因 Site Server 重启而断线 → 无抖动时同时重试 → Thundering Herd 打垮 Site Server。±50% 抖动打散重试时间。

### 3.5 可中断等待

L852-853：
```python
while time.monotonic() < deadline and not self._stop_event.is_set():
    await asyncio.sleep(0.2)
```

等待不是一次性 `await asyncio.sleep(delay)` — 而是每 0.2s 检查 `_stop_event`。这保证 stop() 时最多 0.2s 内感知并退出。

---

## 四、故障隔离矩阵

### 4.1 Site Server 关闭 / DNS 失败

| 维度 | 行为 |
|------|------|
| 入口 | `_try_connect` OSError / ConnectionError → `DisconnectReason.SITE_UNAVAILABLE` (L783) |
| 状态迁移 | CONNECTING → RECONNECTING |
| 重连 | 指数退避 + 抖动，永不停止 |
| Credential | **保留**（传输层错误不是认证失败） |
| Agent 影响 | DeviceManager 完全正常，Connector daemon thread 独自运行 |
| Diagnostics | `last_error=str(exc)`, `reconnect_attempts` 递增 |

### 4.2 TLS 错误

| 维度 | 行为 |
|------|------|
| 入口 | `websockets.connect` SSL handshake 失败 → `WebSocketException` → SITE_UNAVAILABLE |
| 特殊 | 自定义 CA file 配置错误 → 同样重连（用户可修改后重启 Agent） |
| 重连 | 指数退避 |

### 4.3 Credential 撤销

| 维度 | 行为 |
|------|------|
| 入口 | ① HTTP 4401/401 → `_try_connect` AUTH_FAILED (L772-776) |
|      | ② auth 消息被 Site Server 拒绝 → `_do_auth` 收到 error 响应 → AUTH_FAILED (L822-826) |
| 状态迁移 | CONNECTING/AUTHENTICATING → **AUTH_FAILED**（停止重连） |
| 关键动作 | `self._credential = ""` + `self._enabled = False` (L732-733) |
| 停止条件 | `_lifecycle` L734 `break` 跳出主循环 → daemon thread 退出 |
| Agent 影响 | DeviceManager 完全正常 |
| 恢复方式 | 重新配对 + `enable(credential=...)` |

### 4.4 WS 正常断开

| 维度 | 行为 |
|------|------|
| 入口 | `_inbound_dispatch_loop` `ConnectionClosed` → `DisconnectReason.WS_CLOSED` (L945) |
| 重连 | 指数退避 + 抖动 |
| Credential | 保留（主动关闭 ≠ 认证问题） |
| 命令影响 | EXECUTING 中的命令 → Site 重发 → 查 registry 决定 |

### 4.5 WS 内部异常

| 维度 | 行为 |
|------|------|
| 入口 | `_outbound_priority_dispatch` send 异常 → raise → FIRST_COMPLETED |
|      | `_infer_disconnect_reason` 根据异常类型推断 |
| 重连 | 取决于推断结果（WS_ERROR / SITE_UNAVAILABLE） |

### 4.6 内部异常（Connector 自身）

**代码**：`_run_event_loop` L640-641

```python
except Exception as exc:
    log.error("Connector event loop crashed: %s", exc, exc_info=True)
```

所有未捕获异常 → log.error → finally 清理 WS + close loop → daemon thread 退出。**永不传播到 Agent 主进程**。

### 4.7 队列满载

| 场景 | 队列 | 行为 | 日志级别 | 计数 |
|------|------|------|----------|------|
| Measurement 满载 | Measurement(128) | 静默丢弃 + 计数 | INFO（无 WARNING） | `dropped_measurement += 1` |
| Critical 满载 | Critical(128) | 丢弃 + 计数 | **WARNING** | `dropped_critical += 1`, `critical_queue_full_count += 1` |

Critical 队列满载是告警级事件 — 运维需要介入（Connector event loop 卡住？Site Server 无响应？）。

### 4.8 故障隔离总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Gateway Agent 进程                            │
│                                                                      │
│  ┌──────────────────────────┐   ┌────────────────────────────────┐  │
│  │   DeviceManager          │   │   SiteConnector                 │  │
│  │   + Transport (USB/Sim)   │   │   daemon thread (daemon=True)   │  │
│  │   独立 EventBus 线程      │◀──│   _bus_handler: fire-and-forget │  │
│  │   完全不感知 Connector    │   │   event loop: 私有              │  │
│  └──────────────────────────┘   │                                │  │
│                                  │  Connector 故障隔离:             │  │
│  ┌──────────────────────────┐   │  • daemon=True → 主进程不受影响   │  │
│  │   FastAPI REST            │   │  • 任何异常 catch → log → exit  │  │
│  │   /api/v1/site-connector │   │  • QueueFull → count + drop      │  │
│  │   只读 diagnostics        │   │  • AUTH_FAILED → 永久停止       │  │
│  └──────────────────────────┘   │  • stop() force shutdown       │  │
│                                  └────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
         │                              │
         │ 故障隔离                     │ WS 断线不影响
         ▼                              ▼
    DeviceManager                  Site Server
    正常运行                       独立进程
```

---

## 五、stop() 优雅关闭 + Force Shutdown

### 5.1 关闭时序

```
stop() 调用
    │
    ├─ state = STOPPING (L353)
    ├─ _disconnect_all(USER_INITIATED)
    │   ├─ _disconnect_reason = USER_INITIATED
    │   └─ _stop_event.set()           ← 三个 task 循环条件立即感知
    │
    ├─ thread.join(3.0)                ← 等优雅退出
    │   │
    │   ├── 3s 内退出 → 干净
    │   │
    │   └── 超时 → _force_stale_shutdown() (L386)
    │           ├─ run_coroutine_threadsafe(_force_shutdown_async, loop)
    │           ├─ future.result(timeout=2.0)  ← 强杀等待
    │           │
    │           └── _force_shutdown_async (L399)
    │               ├─ asyncio.all_tasks → cancel 所有（除自己）
    │               └─ ws.close()
    │
    ├─ _thread.join(2.0)  ← 再等强杀
    ├─ _thread = None
    ├─ _unregister_event_handlers()
    └─ state = STOPPED
```

### 5.2 三个 Task 的优雅退出路径

| Task | 感知 stop_event 的方式 | 退出 |
|------|----------------------|------|
| Heartbeat | `while not self._stop_event.is_set()` L862, L865 break | 自然退出 |
| Send | `while not self._stop_event.is_set()` L911 | 自然退出 |
| Recv | `while not self._stop_event.is_set()` L939 | 自然退出 |

`_lifecycle` 主循环 L664: `while not self._stop_event.is_set()` — 最外层也感知。

### 5.3 为什么可能需要 Force Shutdown

虽然三个 Task 和主循环都检查 `_stop_event`，但以下场景可能卡 3s+：

1. `ws.send()` 被 TCP 缓冲阻塞（Site Server 无响应）— send 不检查 stop_event
2. `ws.recv()` 在 90s timeout 等待中 — 虽然有 timeout 但循环里要等 90s
3. `run_in_executor` 中的 `device.send_command` 阻塞 — ThreadPoolExecutor 无法强杀线程
4. `asyncio.wait_for(ws.connect)` 10s 超时中

这些场景下 graceful 等 3s 不够 → force shutdown 介入。

### 5.4 Force Shutdown 的作用

`_force_shutdown_async`（L399-412）：
```python
async def _force_shutdown_async(self):
    # 取消所有 task（除了当前这个强杀协程自身）
    current = asyncio.current_task()
    for task in asyncio.all_tasks(self._loop):
        if task is not current and not task.done():
            task.cancel()
    # 关 WS
    if self._ws is not None:
        await self._ws.close()
        self._ws = None
```

`task.cancel()` 触发 `CancelledError` → 各 task 的 `try...except CancelledError: pass` 分支立即退出。

### 5.5 总关闭时间上界

- 优雅等：3.0s
- Force 等：2.0s
- **总计 ≤ 5.0s**

---

## 六、Diagnostics 丰富化（V1.1 A8 根治）

### 6.1 完整字段清单

**代码**：`diagnostics` property L254-301

| 分类 | 字段 | 类型 | 说明 |
|------|------|------|------|
| **配置** | `enabled` | bool | 是否启用 |
| | `state` | str | ConnectorState 值 |
| | `endpoint` | str | Site Server URL |
| | `has_credential` | bool | 是否有 credential（只暴露 bool） |
| | `tls_ca_file` | str/null | 自定义 CA file |
| | `tls_verify` | bool | 是否验证证书 |
| **连接** | `connected_at` | float/null | 上次连接成功时间 |
| | `last_disconnected_at` | float/null | 上次断开时间 |
| | `last_error` | str | 最近一次错误描述 |
| | `reconnect_attempts` | int | 当前重连次数 |
| | `total_disconnects` | int | 累计断线次数 |
| | `ws_open` | bool | WS 对象是否存在 |
| **心跳** | `last_heartbeat_sent` | float | 上次发送时间 |
| | `last_heartbeat_ack` | float | 上次收到 ack 时间 |
| **队列** | `critical_queue_len` | int | Critical 队列当前长度（手动计数器） |
| | `critical_queue_cap` | int | Critical 队列容量 |
| | `measurement_queue_len` | int | Measurement 队列当前长度 |
| | `measurement_queue_cap` | int | Measurement 队列容量 |
| **事件** | `total_events_sent` | int | 累计发送事件数 |
| | `total_events_failed` | int | 累计发送失败数 |
| | `dropped_critical` | int | Critical 队列丢弃总数 |
| | `dropped_measurement` | int | Measurement 队列丢弃总数 |
| | `dropped_by_type` | Dict[str,int] | 每种事件类型的丢弃数 |
| | `critical_queue_full_count` | int | Critical 队列满载次数 |
| | `measurement_queue_full_count` | int | Measurement 队列满载次数 |
| **命令** | `pending_commands` | int | Registry 总大小（含终态） |
| | `commands_received` | int | 累计收到命令数 |
| | `commands_succeeded` | int | 累计成功数 |
| | `commands_failed` | int | 累计失败数 |
| | `commands_timeout` | int | 累计超时数 |
| | `commands_rejected` | int | 累计拒绝数 |

**总计 30 字段**（不含 `dict(self._dropped_by_type)` 展开的内部条目）。V1.0 只有 12 字段。

### 6.2 线程安全保证

`diagnostics` property 通过 `self._lock` 保护 `self._state` 读取（L256）。所有 int counter 在 event loop 内维护 — CPython int 读原子。

---

## 七、验收标准

| # | 验收项 | 验证方法 | 预期 |
|---|--------|----------|------|
| 1 | 9 态覆盖 | 代码审查 enum L60-69 | DISABLED/STOPPED/CONNECTING/AUTHENTICATING/CONNECTED/RECONNECTING/AUTH_FAILED/ERROR/STOPPING |
| 2 | Site Server 关后重连 | 测试 `test_06` | Agent 自动 RECONNECTING → CONNECTING |
| 3 | Credential 撤销后停止 | 测试 `test_03` | AUTH_FAILED + credential="" |
| 4 | stop() 超时 force | 注入 WS hang → stop() | 5s 内返回 |
| 5 | diagnostics ≥ 25 字段 | REST API 调用 | 返回 dict keys 数量 |
| 6 | 队列满载告警 | 高频注入 + Connector 暂停 | WARNING 日志 |
| 7 | 内部异常不传播 | 人为 raise → Agent 继续运行 | DeviceManager 不受影响 |
| 8 | 退避 + 抖动 | 连续 10 次重连 delay | 时间呈指数 + 随机分布 |
