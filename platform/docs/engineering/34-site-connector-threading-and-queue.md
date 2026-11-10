# 34 — Site Connector 线程桥接与队列架构

> **版本**：V1.1 重写  
> **日期**：2026-11-04  
> **代码基准**：`gateway-agent/src/widscan_agent/site_connector.py` (1219 行) · V1.1 A1/A3/A4/A6 修复  
> **交叉引用**：[33-site-connector-runtime-audit.md](./33-site-connector-runtime-audit.md) — 修复 A1/A3/A4/A6 的工程落地  
> **区分说明**：本文档覆盖 V1.1 线程/队列加固，与 [22-site-connector-architecture.md](./22-site-connector-architecture.md) (V1.0) 形成代际升级。V1.0 审计中的 A1/A3/A4/A6 问题在 V1.1 已根治。

---

## 一、问题根源（V1.0 审计 A1/A3/A4/A6）

### 1.1 A1 — EventBus 回调阻塞 DeviceManager 线程

**V1.0 罪魁祸首**：`future.result(timeout=0.5)` 阻塞发布线程（[33 §2 A1](./33-site-connector-runtime-audit.md)）。

V1.1 修复前 `_bus_handler` 在 Device 线程中 `future.result()` 最多阻塞 0.5s，导致：
- 高频 Simulator Device 发布 frame 时 DeviceManager 被卡住
- Connector event loop 暂停（GC/CPU 抢占）→ Device 线程被迫等待
- 队列满载时所有事件一起超时丢弃

### 1.2 A3 — 单一队列高低频不分

V1.0 单个 256 容量队列（L139），`device.frame`（高频 1-15 events/s）挤占 `device.online` / `device.state_change` 等关键事件。

### 1.3 A4 — WebSocket 并发写

V1.0 无锁：`_outbound_dispatch_loop` / `_heartbeat_loop` / `_handle_command_request` 三路并发 `await ws.send()`。Site Server 侧已正确实现 `asyncio.Lock`（`connection.py:L46-53`），Agent 侧缺失。

### 1.4 A6 — `asyncio.Queue.qsize()` 跨线程

REST API 线程（uvicorn）调用 `diagnostics` property 直接访问 `_outbound_queue.qsize()` — 非线程安全。

---

## 二、线程桥接模型（V1.1 A1 根治）

### 2.1 核心：Fire-and-Forget + add_done_callback

**代码**：`_bus_handler` L465-501

```
EventBus.publish(event)          ← DeviceManager 线程（同步）
    │
    ▼
_bus_handler(event)              ← 在 Device 线程中，同步回调
    │
    ├─ 快速门控：state ∉ {CONNECTED, CONNECTING, AUTHENTICATING, RECONNECTING} → return
    │   (L476-478)
    │
    ├─ 取 loop + queue（L480-483）— loop 已关则 return
    │
    ├─ _is_critical_event() → 决定进哪个队列（L487）
    │
    └─ asyncio.run_coroutine_threadsafe(
            _queue_put_nowait(queue, event, is_critical),
            loop,
        )
        .add_done_callback(_on_bus_future_done)     ← 不等待！
        
→ _bus_handler 立即返回（微秒级）→ Device 线程继续发布下一个事件
→ _queue_put_nowait 在 Connector event loop 中执行（非阻塞 put_nowait）
→ _on_bus_future_done 仅做统计清理
```

**与 V1.0 的本质区别**：

| 维度 | V1.0 | V1.1 |
|------|------|------|
| 等待策略 | `future.result(timeout=0.5)` | `future.add_done_callback(...)` |
| Device 线程阻塞 | 最多 500ms | 0ms（立即返回） |
| 事件丢弃策略 | `put()` 阻塞超时后 `_dropped_events += 1` | `put_nowait` + `QueueFull` → 原子计数 |
| 失败通知 | 通过异常传播 | 通过计数器（`_dropped_by_type`） |

### 2.2 快速门控（L474-478）

```python
state = self.state
if state not in (ConnectorState.CONNECTED, ConnectorState.CONNECTING,
                 ConnectorState.AUTHENTICATING, ConnectorState.RECONNECTING):
    return
```

在 `run_coroutine_threadsafe` 之前先检查 Connector 状态 — 避免在 DISABLED/STOPPED/ERROR 态下无谓提交。`self.state` property 通过 `threading.Lock` 保证原子性（L236-239）。

### 2.3 `_queue_put_nowait`（L511-532）

```python
async def _queue_put_nowait(self, queue, event, is_critical):
    try:
        queue.put_nowait(event)          # ← 非阻塞！
        # 更新手动计数器（event loop 内原子）
        self._critical_queue_len = queue.qsize() if is_critical else ...
        return True
    except asyncio.QueueFull:
        if is_critical:
            self._dropped_critical += 1
            self._critical_queue_full_count += 1
            log.warning("CRITICAL QUEUE FULL — dropping event type=%s", event.type)
        else:
            self._dropped_measurement += 1
            self._measurement_queue_full_count += 1
        self._dropped_by_type[event.type] += 1
        return False
```

设计要点：
- `put_nowait` 不阻塞 event loop — Connector 线程永不被 Device 线程卡住
- Critical 队列满时打 WARNING（告警）— Measurement 静默丢弃（INFO 级）
- 计数器在 event loop 内维护 → REST 线程只读原子 int（A6 根治）

### 2.4 `_on_bus_future_done`（L534-543）

回调只处理两种情况：
1. `future.result() == False` — 队列满丢弃（计数已在 `_queue_put_nowait` 里加过）
2. `CancelledError / Exception` — loop 已关闭（shutdown 路径）→ 静默忽略

**永不阻塞、永不抛异常** — 这是 EventBus 同步回调的铁律。

### 2.5 线程安全计数器（A6 根治）

V1.1 不再让 REST 线程直接读 `asyncio.Queue.qsize()`：

```python
# L208-210 — 手动维护的计数器
self._critical_queue_len = 0        # int（CPython 原子读）
self._measurement_queue_len = 0
```

所有写操作都在 Connector event loop 中：
- `_queue_put_nowait` 成功后赋值 `queue.qsize()`
- `_outbound_priority_dispatch` 每次发送后刷新（L933-934）

`diagnostics` property（L254-301）直接返回计数器值 — REST 线程只读，完全安全。

---

## 三、双队列隔离（V1.1 A3 根治）

### 3.1 队列定义与容量

**代码**：L117-118

```python
CRITICAL_QUEUE_MAX = 128
MEASUREMENT_QUEUE_MAX = 128
MAX_COMMAND_REGISTRY = 1024   # Command 去重 registry（见 36 文档）
```

两个 `asyncio.Queue` 在 `_lifecycle` 中初始化（L659-660）：

```python
self._critical_queue = asyncio.Queue(maxsize=128)
self._measurement_queue = asyncio.Queue(maxsize=128)
```

### 3.2 Critical vs Measurement 分类（L502-509）

```python
def _is_critical_event(self, event):
    etype = event.type
    if etype in ("device.online", "device.state_change", "device.registered",
                  "device.removed", "agent.started", "agent.error"):
        return True
    return False   # device.frame（METRICS/SPECTRUM/TELEMETRY/LEVELS）→ Measurement
```

| 事件类型 | 队列 | 典型频率 | 丢弃影响 |
|----------|------|----------|----------|
| `device.online` | Critical | 低（设备上线时 1 次） | Site Server 看不到新设备 |
| `device.state_change` | Critical | 中（状态跃迁时） | Site 状态不同步 |
| `device.registered` | Critical | 低 | 发现事件丢失 |
| `device.removed` | Critical | 低 | 设备下线丢失 |
| `agent.started` / `agent.error` | Critical | 极低频 | Agent 状态丢失 |
| `device.frame` (非 RAWIQ) | Measurement | 1-15 events/s | 频谱数据丢失（可接受） |
| `device.frame` (RAWIQ) | 不入队 | — | Connector 内过滤（`_to_site_envelope` L576-577 返回 None） |

### 3.3 优先级发送（L890-934）

`_outbound_priority_dispatch` 实现优先级调度：

```python
# 伪代码（L911-934）
while not stop_event.is_set():
    got = wait_for(critical_queue.get(), timeout=0.05)   # 优先 50ms
    if got is None:
        got = wait_for(measurement_queue.get(), timeout=0.2)  # 间隙 200ms
    if got is None:
        continue
    envelope = _to_site_envelope(got)
    if envelope is None: continue    # RAWIQ skip
    await _send_json(envelope)       # ← asyncio.Lock 串行化
    total_events_sent += 1
    # 刷新计数器
    critical_queue_len = critical_queue.qsize()
    measurement_queue_len = measurement_queue.qsize()
```

**时间参数含义**：
- Critical 取队列 timeout = 0.05s → 只要 critical 队列有数据，50ms 内必被消费
- Critical 为空时 Measurement timeout = 0.2s → 间隙里拉测量数据
- 单消费者 → 天然保证 FIFO，不会并发写 WS

**效果**：120 Measurement events/s 持续发送时，Critical 事件延迟 ≤ 50ms，永不被挤占。

### 3.4 丢弃计数体系

| 计数器 | 类型 | 写位置 | diagnostics key |
|--------|------|--------|-----------------|
| `_dropped_critical` | int | `_queue_put_nowait` L524 | `dropped_critical` |
| `_dropped_measurement` | int | `_queue_put_nowait` L529 | `dropped_measurement` |
| `_critical_queue_full_count` | int | `_queue_put_nowait` L525 | `critical_queue_full_count` |
| `_measurement_queue_full_count` | int | `_queue_put_nowait` L530 | `measurement_queue_full_count` |
| `_dropped_by_type: Dict[str,int]` | defaultdict(int) | `_queue_put_nowait` L531, `_bus_handler` L500 | `dropped_by_type` |

丢弃路径有两条：
1. `_queue_put_nowait` 内部 QueueFull → 计数 + WARNING 日志
2. `_bus_handler` 中 `run_coroutine_threadsafe` 异常 → `_dropped_by_type[event.type] += 1`（L500）

---

## 四、asyncio.Lock 串行写（V1.1 A4 根治）

### 4.1 为什么需要锁

`websockets.asyncio.WebSocketClientProtocol.send()` 文档明确：**Not thread-safe**。多协程同时 `await ws.send()` 会导致 opcode 交织、frame 不完整、协议级解析失败。

V1.0 有 4 路并发写：
1. `_outbound_dispatch_loop` → `_send_json`
2. `_heartbeat_loop` → `_send_json`
3. `_handle_command_request` → `_send_command_response` → `_send_json`
4. `_send_devices_snapshot` → `_send_json`

V1.1 修复后全部通过同一个锁串行化。

### 4.2 锁的实现

**创建**：`_lifecycle` L656
```python
self._send_lock = asyncio.Lock()
```

**使用**：`_send_json` + `_send_json_raw`（L1150-1160）
```python
async def _send_json(self, obj: dict) -> None:
    if self._ws is None or self._stop_event.is_set() or self._send_lock is None:
        raise RuntimeError("connector not connected")
    await self._send_json_raw(self._ws, obj)

async def _send_json_raw(self, ws, obj: dict) -> None:
    assert self._send_lock is not None
    async with self._send_lock:
        await ws.send(json.dumps(obj))
```

**设计要点**：
- `async with` → 上下文管理，确保异常也释放锁
- `_send_json_raw` 是唯一真正调 `ws.send()` 的地方 — 所有路径必经
- `_try_connect` 和 `_do_auth` 调用 `_send_json_raw(ws, auth_msg)` 时 ws 还没赋给 `self._ws`（L809），但锁已创建
- `_send_command_response`（L1104-1115）和 `_send_devices_snapshot`（L1141）都走 `_send_json` → 自动获取锁

### 4.3 锁竞争分析

单消费者 `_outbound_priority_dispatch` 串行发送，`_heartbeat_loop` 每 15s 一次，`_handle_command_request` 按需触发。锁竞争极低 — 平均等待时间 << 1ms。

### 4.4 Site Server 侧对称实现

Site Server `connection.py:L46-53` 已实现同等机制：

```python
class AgentConnection:
    def __init__(self, ...):
        self._lock = asyncio.Lock()

    async def send_json(self, obj: dict) -> None:
        async with self._lock:
            await self.websocket.send_json(obj)
```

双向都有锁 → WS frame 永不交织。

---

## 五、三个并发 Task 的协作模型

### 5.1 Task 创建与终止

**代码**：`_lifecycle` L698-716

```python
heartbeat_task = asyncio.create_task(self._heartbeat_loop())     # Task 1
send_task = asyncio.create_task(self._outbound_priority_dispatch())  # Task 2
recv_task = asyncio.create_task(self._inbound_dispatch_loop())      # Task 3

# 等任一 task 完成 → 判定断线
done, pending = await asyncio.wait(
    [heartbeat_task, send_task, recv_task],
    return_when=asyncio.FIRST_COMPLETED,
)

# 取消剩余
for t in pending:
    t.cancel()
for t in pending:
    try:
        await asyncio.wait_for(t, timeout=2.0)
    except (asyncio.CancelledError, Exception):
        pass
```

### 5.2 各 Task 职责

| Task | 方法 | 循环条件 | 超时 | 完成条件 |
|------|------|----------|------|----------|
| Heartbeat | `_heartbeat_loop` L859-888 | `sleep(interval)` 15s | ack 超时 60s → `RuntimeError` | WS 异常 / ack 超时 |
| Send | `_outbound_priority_dispatch` L890-934 | critical 50ms / measurement 200ms | 无（纯消费） | 发送异常 → raise |
| Recv | `_inbound_dispatch_loop` L936-957 | `ws.recv()` timeout=90s | 90s 空转 continue | `ConnectionClosed` / WS 异常 |

### 5.3 Task 间通信

三个 Task **无直接共享状态**：
- Heartbeat 和 Send 都通过 `_send_json`（走锁）发送 → 无竞争
- Recv 通过 `_handle_site_message` → `_handle_command_request` → 可能调 `_send_json` → 仍走锁
- 心跳 ack 更新 `_last_heartbeat_ack`（L966）— 单写者 Recv，Heartbeat 读它

**关键设计**：共享的 WS 对象只有一个写路径（锁保护）和一个读路径（Recv task 独占 `ws.recv`）。

### 5.4 断线原因推断（L1207-1219）

任一 Task 抛出异常时，通过 `t.exception()` 推断具体断线原因：

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
    return WS_ERROR   # fallback
```

---

## 六、Event Loop 生命周期

### 6.1 启动流程

```
SiteConnector.start()               ← 调用线程（Agent 主线程或 REST）
    │
    ├─ 检查 enabled / thread alive / credential
    ├─ 注册 EventBus handlers
    └─ threading.Thread(target=_run_event_loop, daemon=True)
         │
         ▼
_run_event_loop()                   ← daemon 线程
    │
    ├─ asyncio.new_event_loop()
    ├─ asyncio.set_event_loop(self._loop)
    ├─ loop.run_until_complete(self._lifecycle())
    │   └─ 主循环：connect → auth → connected → 三个 task → 断线 → 推断 → 重连
    │
    └─ finally:
         ├─ loop.run_until_complete(_safe_close_ws)
         ├─ loop.close()
         └─ self._loop = None
```

### 6.2 停止语义

**优雅关闭路径**：`stop()` L346-370

```python
def stop(self):
    state = STOPPING
    self._disconnect_all(USER_INITIATED)    # → self._stop_event.set()
    
    # 等 daemon 线程退出
    self._thread.join(timeout=3.0)
    if self._thread.is_alive():
        # 超时 → 强杀
        self._force_stale_shutdown()
        self._thread.join(timeout=2.0)
    
    # 清理
    _unregister_event_handlers()
    state = STOPPED
```

**强杀路径**：`_force_stale_shutdown`（L386-412）

```python
def _force_stale_shutdown(self):
    # 调度强杀协程到 Connector event loop
    future = asyncio.run_coroutine_threadsafe(
        self._force_shutdown_async(), self._loop
    )
    future.result(timeout=2.0)   # ← 这次等（安全，因为是 force 场景）

async def _force_shutdown_async(self):
    # 取消所有 task
    for task in asyncio.all_tasks(self._loop):
        if task is not current and not task.done():
            task.cancel()
    # 关 WS
    if self._ws is not None:
        await self._ws.close()
```

**时序保证**：
1. `_disconnect_all` → `_stop_event.set()` → 三个 Task 的循环条件立刻感知
2. `join(3.0)` 等 loop 自然退出
3. 超时 → `force_shutdown_async` 强制 cancel + close
4. 最坏情况 3+2=5s 内 stop() 返回

### 6.3 防止残留线程（start 时检查）

`start()` L315-318：
```python
if self._thread is not None and self._thread.is_alive():
    log.warning("Site Connector thread already alive — forcing stale shutdown")
    self._force_stale_shutdown()
```

防止 stop() 超时残留的 daemon thread 阻塞下一次 start()。

---

## 七、线程安全总结

| 共享对象 | 保护机制 | 访问方向 | 代码位置 |
|----------|----------|----------|----------|
| `self._state` | `threading.Lock` (`self._lock`) | 双向读写 | L236-239, L1195-1205 |
| `self._stop_event` | `threading.Event` | 双向读写（原子） | L190, L1189 |
| `self._loop` | `Optional[asyncio.AbstractEventLoop]` | daemon 线程写，Device/REST 线程读 | L191 |
| `self._ws` | 独占写（Connector loop）+ `self._lock` 保护属性访问 | Connector 线程独占 | L192 |
| `self._send_lock` | `asyncio.Lock` | 所有 `_send_json` 路径 | L193, L1158-1160 |
| `self._critical_queue_len` / `_measurement_queue_len` | CPython int 原子读 | event loop 写，REST 线程读 | L209-210 |
| `self._critical_queue` / `_measurement_queue` | `asyncio.Queue`（线程安全） | Device 线程 `run_coroutine_threadsafe` put → event loop get | L205-206 |
| `self._command_registry` | event loop 独占（所有 command 处理在 `_inbound_dispatch_loop` 中） | 单线程读写 | L222 |
| `self._credential` | `threading.Lock`（通过 start/stop 的 lock 块） | 双向读写 | L165, L331, L732 |
| `self._dropped_by_type` | event loop 独占写 + Device 线程写（L500）— 非严格安全但语义可接受 | 近似安全 | L217 |

---

## 八、验收标准

来自 [33 §4](./33-site-connector-runtime-audit.md) 修复后验收：

- [x] EventBus 回调 profile 不含 `Connector._bus_handler` 阻塞（A1 根治）
- [x] 120 Measurement events/s 持续 5min → Device state events 0 丢弃（A3 双队列）
- [x] 高并发 send 不触发 `websockets.exceptions.ConnectionClosedError: invalid frame`（A4 锁）
- [x] REST `/api/v1/site-connector` 队列字段与 event loop 内部一致（A6 手动计数器）
- [x] stop() 后 5s 内 Connector daemon thread 退出（A9 force shutdown）
