# 22 — Site Connector 架构设计

> **版本**：v1.0  
> **日期**：2026-11-04  
> **作者**：Engineering  
> **引用**：Gateway Agent `site_connector.py` (806 行) · Site Server `connection.py` / `devices/__init__.py` / `ingestion/__init__.py`  
> **交叉引用**：[21-site-connector-contract-audit.md](./21-site-connector-contract-audit.md) — 本文档是审计结论的工程落地

---

## 一、模块边界图

```
┌───────────────────────────── Gateway Agent 进程 ─────────────────────────────┐
│                                                                              │
│  ┌─────────────┐  EventBus.subscribe()  ┌─────────────────────────────────┐  │
│  │ DeviceManager│──────────────────────▶│         SiteConnector            │  │
│  │  + Device   │  (同步回调, threading)  │  ┌───────────────────────────┐  │  │
│  └──────┬──────┘                         │  │ EventBus→Queue Bridge      │  │  │
│         │                                │  │ _bus_handler() L307       │  │  │
│         ▼                                │  └────────────┬──────────────┘  │  │
│  ┌─────────────┐  REST API               │               │ run_coroutine_   │  │
│  │ FastAPI App │────────────────────────▶│               ▼ threadsafe     │  │  │
│  └─────────────┘  (外部读写 connector)    │  ┌───────────────────────────┐  │  │
│         │                                │  │ asyncio.Queue(max=256)    │  │  │
│         │                                │  └────────────┬──────────────┘  │  │
│         │                                │               │                 │  │
│         │                                │  ┌────────────▼──────────────┐  │  │
│         │                                │  │  独立 Daemon Thread         │  │  │
│         │                                │  │  ┌──────────────────────┐ │  │  │
│         │                                │  │  │ asyncio EventLoop     │ │  │  │
│         │                                │  │  │ ┌──task1──┐ ┌──task2┐┌│  │  │
│         │                                │  │  │ │ heartbeat│ │outbound│││  │  │
│         │                                │  │  │ └────────┘ └────────┘││  │  │
│         │                                │  │  │ ┌──task3──┐          ││  │  │
│         │                                │  │  │ │ inbound │          ││  │  │
│         │                                │  │  │ └─────────┘          ││  │  │
│         │                                │  │  └──────────────────────┘ │  │  │
│         │                                │  └───────────────────────────┘  │  │
│         │                                └──────────────┬──────────────────┘  │
│         │                                               │                     │
└─────────┼───────────────────────────────────────────────┼─────────────────────┘
          │                                               │ ws:// (反向)
          │ 独立故障隔离                                    │                 │
          ▼                                               ▼                 │
┌───────────────────────────── Site Server 进程 ──────────────────────────────┐
│                                                                              │
│  FastAPI / WebSocketEndpoint                                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ agent_connect_ws() L88                                                │    │
│  │   ├─ 认证 verify_credential()                                         │    │
│  │   ├─ 注册 AgentConnection → registry (进程内 dict)                    │    │
│  │   ├─ 发 connected 握手                                                │    │
│  │   ├─ heartbeat_ack_task (每30s 发 ack, 检测 Agent 心跳)               │    │
│  │   ├─ _handle_agent_message (heartbeat / agent.event / snapshot / cmd) │    │
│  │   ├─ _process_agent_event → devices/__init__.py upsert / ingestion/   │    │
│  │   └─ finally: mark_devices_offline_by_agent                          │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 边界说明

| 边界 | 方向 | 协议 | 说明 |
|------|------|------|------|
| DeviceManager → Connector | threading 同步回调 | EventBus pub/sub | `SiteConnector._register_event_handlers()` L288-300 订阅 7 种事件 |
| Connector → Site Server | asyncio WebSocket | JSON text frame | `/ws/v1/agents/connect?agent_id=X&credential=Y` L504 |
| Connector ← Site Server | asyncio WebSocket | JSON text frame | `connected` / `heartbeat.ack` / `command.request` |
| Site Server → DB | 同步 SQLAlchemy Session | upsert / record_frame | `upsert_device_from_agent()` / `record_device_frame()` |
| Agent REST → Connector 状态 | threading HTTP | REST `/api/v1/site-connector` | Agent 侧 diagnostics 属性 + FastAPI 端点暴露 |

**关键**：Site Connector 故障（断线/重连循环）**绝不影响** DeviceManager 设备接入 / 数据接收 / Agent REST API。Connector 是 Agent 的可选附加能力。

---

## 二、状态机图

```
                     enabled=False
                      ┌─────────┐
                      │ DISABLED│◀──── stop() / disable()
                      └────┬────┘
                           │ enabled=True + 无 credential
                           ▼
                      ┌─────────┐
                      │ PAIRING │──── enable(endpoint, credential)
                      └────┬────┘                  │
                           │ 有 credential+endpoint │
                           ▼                       │
                    ┌──────────┐                    │
                    │CONNECTING │ ◀── 重连循环 ──────┘
                    └────┬─────┘
                         │ WS 握手 + connected 消息 OK
                         ▼
                    ┌──────────┐
                    │ CONNECTED │  ◀── 心跳 ack OK + 事件循环正常
                    └────┬─────┘
                         │ 断线（任一 task 完成）
                         ▼
                    ┌─────────────┐
               ┌───▶│ DISCONNECTED │
               │    └────┬────────┘
               │         │ 指数退避 sleep 结束后
               │         │ USER_INITIATED → 退出
               │         │ AUTH_FAILED → ┌──────┐
               │         │               │ ERROR│(停止重连)
               │         └──────────────▶└──────┘
               │
               └──── 重连成功后回到 CONNECTED
```

### 状态定义（`site_connector.py` L52-58）

| 状态 | 枚举值 | 进入条件 | 退出条件 |
|------|--------|----------|----------|
| DISABLED | `"disabled"` | `enabled=False` / `stop()` / `disable()` | `enable()` 且有 credential |
| PAIRING | `"pairing"` | `enabled=True` 但 `credential==""` 或 `endpoint==""` | `enable(endpoint, credential)` 补全 |
| CONNECTING | `"connecting"` | `_lifecycle()` 开始 / 重连循环入口 | `_try_connect()` 返回 WS 成功 → CONNECTED / 返回 None → DISCONNECTED |
| CONNECTED | `"connected"` | WS 握手 OK + 收到 `connected` 消息 | 3 个并发 task 任一完成 → DISCONNECTED / AUTH_FAILED → ERROR |
| DISCONNECTED | `"disconnected"` | 断线推断 | 指数退避结束后 → CONNECTING（重连循环）/ stop_event → 退出 |
| ERROR | `"error"` | `InvalidStatusCode 4401/401` | 手动重新 pair 后 enable() |

### DisconnectReason 7 种（L61-68）

| 枚举值 | 触发位置 | 说明 |
|--------|----------|------|
| `USER_INITIATED` | `stop()` 手动调用 | 正常关闭 |
| `CONNECT_FAILED` | `_try_connect()` L539 | HTTP 非 4401/401 的拒绝 |
| `AUTH_FAILED` | `_try_connect()` L535 | credential 校验被拒（清 credential + 停止重连） |
| `HEARTBEAT_TIMEOUT` | `_heartbeat_loop()` L579-582 | 60s 没收到 ack |
| `WS_CLOSED` | `_infer_disconnect_reason()` L802 | Site Server 主动关 WS |
| `WS_ERROR` | `_try_connect()` L549 | 未分类 WS 异常 |
| `SITE_UNAVAILABLE` | `_try_connect()` L544 / `_infer_disconnect_reason()` L804 | DNS/OSError/ConnectionError |

---

## 三、线程 / asyncio 模型

```
┌─────────────────────── Gateway Agent 主进程 ───────────────────────┐
│                                                                     │
│  Thread: Main / uvicorn                                             │
│    ├── DeviceManager.start() → Device 线程 × N                       │
│    ├── EventBus (threading.Event, 同步 pub/sub)                     │
│    ├── FastAPI App ── /api/v1/site-connector (读 connector.diagnostics)│
│    └── SiteConnector.start() → 启动下面的 daemon 线程                 │
│                                                                     │
│  ═══════════ Thread: SiteConnector (daemon=True, name="SiteConnector") ═══════════│
│                                                                     │
│    asyncio.EventLoop (run_until_complete _lifecycle)                │
│    ┌────────────────────────────────────────────────────────────┐   │
│    │ asyncio.Queue(max=256)  ← EventBus handler 投递            │   │
│    │                                                            │   │
│    │ 并发 task (FIRST_COMPLETED 任一触发整体断线清理)：          │   │
│    │ ┌─────────────────┐ ┌────────────────┐ ┌────────────────┐ │   │
│    │ │  heartbeat_task │ │ outbound_task  │ │ inbound_task   │ │   │
│    │ │ 15s 间隔        │ │ Queue→WS send  │ │ WS recv→dispatch│ │   │
│    │ │ ack 60s 超时   │ │ (0.5s get timeout)│ (90s recv timeout)│ │   │
│    │ └─────────────────┘ └────────────────┘ └────────────────┘ │   │
│    │                                                            │   │
│    │ lifecycle() 控制 while loop ── 断线推断 → 指数退避 → 重连 │   │
│    └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Thread 间同步：                                                     │
│    threading.Lock  → _state / _disconnect_reason                    │
│    threading.Event → _stop_event（可打断 asyncio.sleep）             │
│    asyncio.run_coroutine_threadsafe → EventBus 回调投递 Queue        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3 个并发 task（L470-478）

| Task | 代码位置 | 周期 | 失败条件 | 影响 |
|------|----------|------|----------|------|
| `heartbeat_loop` | L570-599 | 15s 发 heartbeat | 60s 没收到 ack → raise RuntimeError | HEARTBEAT_TIMEOUT 断线 |
| `outbound_dispatch_loop` | L601-620 | 0.5s timeout 取 Queue | WS send 抛异常 → raise | WS_ERROR / WS_CLOSED |
| `inbound_dispatch_loop` | L622-643 | 90s recv timeout (静默 OK) | ConnectionClosed / Exception | WS_CLOSED |

**设计选择**：`return_when=asyncio.FIRST_COMPLETED` — 任一失败即整体清理（避免半连接状态）。

---

## 四、EventBus → Queue Bridge（核心设计）

### 问题

- EventBus 是 **同步** pub/sub（`events/bus.py`），handler 在 Device 线程里被调用
- Connector 运行在独立的 **asyncio event loop** 线程里
- 不能在 Device 线程直接调 `asyncio.Queue.put()`（非线程安全）
- 不能阻塞 Device 线程等 asyncio 操作完成

### 解决方案（`site_connector.py` L288-323）

```python
# 注册 handler（同步）
self._bus.subscribe("device.online", self._bus_handler)   # L293
self._bus.subscribe("device.frame", self._bus_handler)    # L294
self._bus.subscribe("device.state_change", self._bus_handler)  # L295
self._bus.subscribe("device.registered", self._bus_handler)    # L296
self._bus.subscribe("device.removed", self._bus_handler)       # L297
self._bus.subscribe("agent.started", self._bus_handler)       # L298
self._bus.subscribe("agent.error", self._bus_handler)         # L299

# handler 体
def _bus_handler(self, event: Event) -> None:    # L307
    if self._loop is None or self._outbound_queue is None or self._state != ConnectorState.CONNECTED:
        return    # 未启动/已断开时直接丢弃（靠重连后 devices_snapshot 补偿）
    try:
        future = asyncio.run_coroutine_threadsafe(    # L317
            self._outbound_queue.put(event),
            self._loop,
        )
        future.result(timeout=0.5)   # L321 — 0.5s 超时避免 Device 线程阻塞
    except Exception:
        self._dropped_events += 1    # L323 — 统计丢弃
```

### 关键设计点

| 点 | 实现 | 位置 |
|----|------|------|
| 线程安全投递 | `asyncio.run_coroutine_threadsafe()` | L317-320 |
| Device 线程不阻塞 | 0.5s timeout | L321 |
| 未连接时丢弃 | `_state != CONNECTED` 短路返回 | L312-314 |
| 丢弃统计 | `_dropped_events` 计数器 | L146, L323 |
| 事件转换 | `_to_site_envelope()` 过滤 + 合成 | L329-423 |
| devices_snapshot 补偿 | WS 连接后发全量设备 | L726-751 |

### `_to_site_envelope` 事件合成/过滤（L329-423）

| Agent EventBus type | → Site event.type | 特殊处理 | 代码 |
|---------------------|-------------------|----------|------|
| `device.online` | `device.online` | 取 manager.get(device_id).info / transport_info | L336-354 |
| `device.frame` | `device.frame` | **type_code=4 (RAWIQ) → None 跳过** | L357-371 |
| `device.state_change` | `device.state_change` | **to="discovered" → None 跳过**（G1 补偿） | L374-390 |
| `device.registered` | **`device.discovered`** | G1 补偿：Agent 没有 discovered 事件 | L393-405 |
| `device.removed` | `device.removed` | — | L408-416 |
| `agent.started` | **跳过** | 靠 heartbeat 携带 devices count | L419-421 |

---

## 五、重连策略（指数退避）

### 参数（构造函数 L105-107）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `reconnect_initial_delay` | 2.0s | 第一次重连延迟 |
| `reconnect_max_delay` | 60.0s | 延迟上限 |
| `reconnect_multiplier` | 2.0 | 指数倍率 |

### 实现（`_backoff_reconnect` L552-564）

```python
def _backoff_reconnect(self) -> None:
    self._reconnect_attempts += 1
    delay = min(
        self._reconnect_initial_delay * (self._reconnect_multiplier ** (self._reconnect_attempts - 1)),
        self._reconnect_max_delay,
    )
    # 可打断等待：每 0.2s 检查 stop_event
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline and not self._stop_event.is_set():
        await asyncio.sleep(0.2)
```

### 重连次数 vs 延迟

| attempt | delay (默认参数) |
|---------|-----------------|
| 1 | 2s |
| 2 | 4s |
| 3 | 8s |
| 4 | 16s |
| 5 | 32s |
| 6+ | 60s（封顶） |

### 停止重连条件

| 条件 | 原因 | 代码位置 |
|------|------|----------|
| `stop()` 调用 | USER_INITIATED | L221, L494 |
| Auth 4401/401 | AUTH_FAILED → ERROR → 清 credential + 停止重连 | L533-538 |
| `_stop_event.is_set()` | 上层要求退出 | L454, L494 |

---

## 六、独立运行故障隔离

### 隔离原则

```
Connector 异常
    │
    ├── WS send 抛异常 → outbound_task raise → lifecycle 清理 → 重连
    ├── 心跳超时 → heartbeat_task raise → lifecycle 清理 → 重连
    ├── Site Server 重启 → inbound_task ConnectionClosed → 重连
    ├── Auth 被撤 → 进入 ERROR → 停止重连
    └── 所有这些都在 daemon 线程 + asyncio loop 内
         不影响：
           ✅ DeviceManager 设备管理
           ✅ EventBus 事件分发
           ✅ Agent REST API（/api/v1/devices, /api/v1/health, ...）
           ✅ Device 数据摄入
```

### 验证（test_site_connector.py）

- `test_12_agent_independence_when_site_down` L353-378：Site Server 不可用 → Agent 正常，3 台 simulators 正常管理
- `test_11_agent_api_regression_after_connector` L182-219：Connector 启停后 Agent REST API 100% 正常

---

## 七、Config 结构

### SiteConnector 构造函数参数（L94-121）

```python
SiteConnector(
    *,
    bus: EventBus,                              # 必需 — EventBus 实例
    agent_id: str,                              # 必需 — 从 GatewayConfig.agent.id
    manager: Optional[Any] = None,              # DeviceManager（用于 command dispatch + devices_snapshot）
    endpoint: str = "",                         # ws://host:port 或 wss://host:port
    credential: str = "",                       # Site Server pair 返回的明文 credential
    enabled: bool = False,                      # 是否启用
    heartbeat_interval: float = 15.0,           # 心跳发送间隔（秒）
    heartbeat_ack_timeout: float = 60.0,        # 心跳 ack 超时（秒）
    reconnect_initial_delay: float = 2.0,       # 指数退避初始值
    reconnect_max_delay: float = 60.0,          # 指数退避最大值
    reconnect_multiplier: float = 2.0,          # 指数倍率
    max_outbound_buffer: int = MAX_OUTBOUND_BUFFER,  # 出站 Queue 最大容量（默认 256）
)
```

### Agent YAML config 需要新增的 SiteConfig（审计文档 1.7 节）

```yaml
agent:
  id: "gateway-001"
site:                           # ← 新增
  enabled: false
  endpoint: "ws://site-server:8766"
  credential: ""                # 配对后持久化
  heartbeat_interval: 15.0
  heartbeat_ack_timeout: 60.0
  reconnect_initial_delay: 2.0
  reconnect_max_delay: 60.0
  reconnect_multiplier: 2.0
  max_outbound_buffer: 256
```

### diagnostics 属性（L171-185）

```python
@property
def diagnostics(self) -> dict:
    return {
        "state": self.state.value,           # ConnectorState
        "enabled": self._enabled,
        "endpoint": self._endpoint,
        "has_credential": bool(self._credential),
        "connected_at": self._connected_at,   # monotonic 时间戳
        "reconnect_attempts": self._reconnect_attempts,
        "outbound_buffer": self._outbound_queue.qsize() if self._outbound_queue else 0,
        "pending_commands": len(self._pending_commands),  # 预留 — V1 未实现 request_id→Future
        "total_events_sent": self._total_events_sent,
        "dropped_events": self._dropped_events,
        "last_heartbeat_sent": self._last_heartbeat_sent,
        "last_heartbeat_ack": self._last_heartbeat_ack,
    }
```

### Agent REST 端点暴露

Agent FastAPI 端点 `/api/v1/site-connector` 返回 `connector.diagnostics` dict（测试 `test_11` 中 L215-217 验证）。

---

## 八、已知限制（V1）

| # | 限制 | 说明 | 代码位置 |
|---|------|------|----------|
| L1 | `_pending_commands` dict 始终空 | Connector V1 发了 command.response 但没实现 request_id→Future 映射（Site Server V1 也没发 command.request） | L140, L709-721 |
| L2 | `pair_via_rest()` 不完整 | L248-282 — 拿到 pair 成功但没自动把 credential 写进 connector，需要调用方自己补 credential | L278-282 |
| L3 | Site Server 只有 1 个固定 site_id | 多 Site 隔离 V1 没做 | connection.py L110 fallback `"site-001"` |
| L4 | 断线期间不缓存事件 | `_bus_handler` L312-314 直接丢弃，靠重连后 `devices_snapshot` 补偿 | L312-314 |
| L5 | Site Server command.request 没发送端 | connection.py 没有 Site→Agent command.request 的发送实现；Connector V1 只有接收端准备 | audit G7 |

---

## 九、Site Server 侧架构（连接处理）

### AgentConnection / AgentConnectionRegistry（connection.py L38-83）

```
registry (进程内 dict[str, AgentConnection], threading.Lock 保护)
    │
    ├── register(conn)        → Agent 新连接覆盖旧连接（同 agent_id）
    ├── unregister(agent_id)  → Agent 断开清理
    ├── get(agent_id)         → 查在线 Agent
    └── list_ids()            → 所有在线 agent_id

AgentConnection
    ├── agent_id / websocket / site_id
    ├── connected_at (monotonic) + connected_at_dt (UTC)
    ├── running: bool            → 主循环退出标志
    ├── _lock: asyncio.Lock      → send_json 串行化
    └── send_json(obj)           → 带锁发送
```

### 主事件循环流程（connection.py L130-174）

```python
try:
    ack_task = asyncio.create_task(heartbeat_ack_task())
    while conn.running:
        try:
            raw = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
        except asyncio.TimeoutError: break   # Agent 60s 没说话
        except WebSocketDisconnect: break
        await _handle_agent_message(raw, agent_id=agent_id, site_id=site_id, conn=conn)
        heartbeat_received = True
    ack_task.cancel()
finally:
    registry.unregister(agent_id)
    # DB: mark_devices_offline_by_agent + update_agent(status="offline")
```

### 断线后 DB 清理顺序（connection.py L166-174 → devices/__init__.py L202-244）

1. `DeviceEndpoint.status → disconnected`
2. `ConnectionSession.status → disconnected, disconnect_reason="agent_offline"`
3. Device.status → offline（**仅**当没有其他 Agent 还连着这个 device）

---

## 十、Bug 修复记录

### MultipleResultsFound — ConnectionSession

**位置**：`devices/__init__.py` L102-110  
**触发条件**：Agent 重连后先发 `agent.devices_snapshot`（内有多条 device.online），紧接着 Device 发 `device.online` 事件；两次 upsert 会创建两条 `status="connected"` 的 ConnectionSession，`get_active_session_for_device()` 用 `.scalar_one_or_none()` 可能返回多条。

**修复**：在创建新 ConnectionSession **之前**，先 UPDATE 关闭同 device+agent 下所有 `status="connected"` 的旧 session：

```python
# devices/__init__.py L102-110
from sqlalchemy import update as sa_update
close_old = sa_update(ConnectionSession).where(
    ConnectionSession.device_id == device_id,
    ConnectionSession.agent_id == agent_id,
    ConnectionSession.status == "connected",
).values(status="disconnected", disconnected_at=now, disconnect_reason="replaced")
session.execute(close_old)

# 然后再建新的
sess = ConnectionSession(..., status="connected")
session.add(sess)
```

---

*文档结束。下一篇：[23-site-connector-protocol.md](./23-site-connector-protocol.md)*
