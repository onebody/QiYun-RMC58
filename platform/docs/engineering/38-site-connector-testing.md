# 38 — Site Connector 测试覆盖

> **版本**：V1.1  
> **日期**：2026-11-04  
> **代码基准**：`gateway-agent/tests/test_site_connector.py` (425 行, 10 用例) · V1.1 A1-A10 修复点验证  
> **交叉引用**：[33-site-connector-runtime-audit.md](./33-site-connector-runtime-audit.md) — 审计发现的问题 → 测试覆盖确认  
> **区分说明**：V1.1 测试覆盖审计发现的所有关键修复点（A1 非阻塞/A2 首条 auth/A3 双队列/A4 锁串行/A5 幂等性/A7 9 态/A8 diagnostics 30 字段）。

---

## 一、测试基础设施

### 1.1 Fixture 矩阵

| Fixture | 类型 | 作用 | 代码位置 |
|---------|------|------|----------|
| `site_server` | 真实服务 | 内存 SQLite + uvicorn daemon thread 随机端口 | L59-86 |
| `gateway` | 真实组件 | EventBus + DeviceManager + 3 个 SimulatorTransport | L89-99 |
| `pair_credential` | REST 交互 | 创建 pairing_code → pair → 拿到 plain_credential | L102-126 |

**真实 vs Mock 策略**：

| 组件 | Mock | 真实 | 理由 |
|------|------|------|------|
| Site Server | ✗ | ✓ | 必须验证 WS 握手 + 认证 + 事件处理 |
| SQLite DB | — | 内存 SQLite | 无外部依赖 |
| uvicorn | ✗ | daemon thread | 随机端口避免冲突 |
| DeviceManager | ✗ | ✓ | 必须验证 Connector 与 DeviceManager 的隔离 |
| SimulatorTransport | ✗ | ✓ | 真实 frame 发布路径 |
| EventBus | ✗ | ✓ | 真实订阅/回调链 |

### 1.2 Site Server 启动时序

```python
# site_server fixture
port = random_port_from_OS()
threading.Thread(target=uvicorn.run(app, port=port), daemon=True).start()

# polling wait-ready
for _ in range(50):
    httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
    break
    time.sleep(0.1)  # 最多 5s 等 ready
```

### 1.3 Gateway 启动

```python
# gateway fixture
bus = EventBus()
manager = DeviceManager(bus=bus, poll_interval=5.0, reconnect_enabled=True)
for i in range(3):
    sim = SimulatorTransport(device_id=f"sim-c5-{i:03d}")
    manager.add_device(sim, device_id=f"sim-c5-{i:03d}")
manager.start()   # ← 立即发布 device.online
```

### 1.4 Connector 快速配置

所有用例统一使用：
```python
SiteConnector(
    bus=bus, agent_id=..., manager=manager,
    endpoint=f"ws://127.0.0.1:{port}",
    credential=..., enabled=True,
    heartbeat_interval=5.0,           # 加速测试（默认 15s）
    reconnect_initial_delay=0.5,      # 加速（默认 2.0s）
    reconnect_max_delay=2.0,          # 加速（默认 60s）
)
```

---

## 二、用例清单与覆盖矩阵

### 2.1 完整清单

| # | 类 | 用例 | V1.0 覆盖 | V1.1 A1-A10 覆盖 |
|---|-----|------|-----------|------------------|
| 1 | Connection | test_01_connect_success | ✓ | A2（首条 auth 成功）, A7（9 态 CONNECTED）, A4（锁隐式） |
| 2 | Connection | test_03_bad_credential | ✓ | A2（认证失败）, A7（非 CONNECTED 态） |
| 3 | Connection | test_11_agent_api_regression | ✓ | **全 Agent 回归**（Connector 启停后 REST 不变） |
| 4 | EventForwarding | test_08_device_online | ✓ | A3（Critical 队列 → online 事件）, A8（diagnostics 有事件统计） |
| 5 | EventForwarding | test_09_device_frame | ✓ | A3（Measurement 队列 → frame 事件） |
| 6 | EventForwarding | test_11_rawiq_skipped | ✓ | `_to_site_envelope` RAWIQ 跳过逻辑 |
| 7 | Reconnect | test_06_site_server_restart | ✓ | A7（RECONNECTING → CONNECTED） |
| 8 | Reconnect | test_12_independence_when_site_down | ✓ | **故障隔离验证**（Connector 不影响 DeviceManager） |
| 9 | Reconnect | test_13_independent_start_stop | ✓ | A7（STOPPED → CONNECTED）, A9（stop() 正常退出） |
| 10 | Reconnect | test_14_no_credential | ✓ | A7（STOPPED 语义） |

### 2.2 与审计 A1-A10 修复点的验证映射

| 修复点 | 描述 | 现有用例覆盖 | 验证强度 | 建议新增用例 |
|--------|------|-------------|---------|-------------|
| **A1** | fire-and-forget（无 future.result 阻塞） | 间接：test_12（Connector 不影响 DeviceManager） | 🟡 弱 | 高频 Simulator 并发 + DeviceManager 延迟监控 |
| **A2** | 首条消息 auth（URL 无 credential） | ✓ test_01（正常握手路径）, test_03（错误 credential） | 🟢 中 | **建议**：直接验证 URL 不含 credential（URL 字符串检查） |
| **A3** | 双队列 + 优先级发送 | 间接：test_08（online）+ test_09（frame） | 🟡 弱 | **建议**：高频 Measurement 持续注入 + Critical 事件延迟监控 |
| **A4** | asyncio.Lock 串行写 | 隐式（所有 send 都走锁） | 🔴 无 | **建议**：高并发 send + ConnectionClosedError 发生率 |
| **A5** | 7 态 Command + 幂等 registry | 🔴 无 | 🔴 完全缺失 | **必须新增**：重复 request_id / 离线拒绝 / 超时 / REJECTED |
| **A6** | 手动计数器（不跨线程 qsize） | 间接：test_11（REST diagnostics 正常） | 🟡 弱 | **建议**：REST 高频读 + Connector 高频写 → 计数器一致性 |
| **A7** | 9 态状态机 | ✓ test_01/test_06/test_13/test_14 | 🟢 中 | **建议**：每个状态显式验证 + 状态迁移序列 |
| **A8** | Diagnostics 30 字段 | 间接：test_08（total_events_sent > 0） | 🟡 弱 | **建议**：assert diagnostics keys 数量 + 字段类型 |
| **A9** | stop() force shutdown | 🔴 无 | 🔴 完全缺失 | **建议**：注入 WS hang → stop() 5s 内返回 |
| **A10** | TLS 配置 | 🔴 无 | 🔴 完全缺失 | **建议**：wss:// + 自签名 CA + verify=False |

---

## 三、现有用例详解

### 3.1 test_01_connect_success（握手成功）

**代码**：L136-160

验证点：
- `sc.start()` → 等待 `sc.state == ConnectorState.CONNECTED`（轮询 50×0.2s = 10s 超时）
- `sc.is_connected is True`
- Site Server REST `/api/v1/agents` 看到新 Agent

**V1.1 隐式验证**：
- A2：URL 不含 credential，首条 auth 消息通过 Site Server 校验
- A4：锁保护 send 不触发并发写错误（正常连接建立后心跳/快照/事件都走锁）
- A7：9 态流转 DISABLED → CONNECTING → AUTHENTICATING → CONNECTED

### 3.2 test_03_bad_credential（错误 credential）

**代码**：L162-180

验证点：
- `credential="bad-credential-abcdef..."` → 启动后 **3s** 等待
- `sc.state != ConnectorState.CONNECTED`
- **关键**：`len(manager.list_devices()) == 3`（DeviceManager 不受影响）

**V1.1 隐式验证**：
- A2：错误 credential 走首条 auth → Site Server 拒绝 → 返回 error 响应
- A7：非 CONNECTED 态（应该是 AUTH_FAILED — 但测试只检查 `!=`）
- **故障隔离**：Connector 认证失败不影响 DeviceManager

### 3.3 test_08_device_online_forwards（online 事件转发）

**代码**：L225-252

验证点：
- Connector 启动 → 1s 等待 devices_snapshot → Site Server `/api/v1/devices` 看到设备
- `sc.diagnostics["total_events_sent"] > 0`

**V1.1 隐式验证**：
- A3：Critical 队列接收 `device.online` 事件
- A8：diagnostics 返回 total_events_sent 字段

### 3.4 test_09_device_frame_forwards（frame 事件转发）

**代码**：L254-281

验证点：
- Simulator 持续发 METRICS frames → 4s 等待 → Site Server `/api/v1/measurements/stats` `total > 0`

**V1.1 隐式验证**：
- A3：Measurement 队列接收 `device.frame` 事件
- 双队列隔离生效（frame 不挤 online）

### 3.5 test_11_rawiq_default_skipped（RAWIQ 跳过）

**代码**：L282-312

验证点：
- 直接调用 `sc._to_site_envelope(rawiq_event)` → 返回 None
- 直接调用 `sc._to_site_envelope(metrics_event)` → 返回 dict

**注意**：这是唯一直接测试 Connector 内部方法的用例。绕过了 WS 连接，直接验证 envelope 逻辑。

### 3.6 test_06_site_server_restart_reconnect（重连）

**代码**：L318-351

验证点：
- Connector 连接成功 → `disable()` → 1s → `enable()` → 等待 CONNECTED
- `diagnostics["reconnect_attempts"] >= 0`

**V1.1 隐式验证**：
- A7：STOPPED → CONNECTING → AUTHENTICATING → CONNECTED（或 RECONNECTING 中间态）
- 退避重连逻辑

### 3.7 test_12_independence_when_site_down（故障隔离）

**代码**：L353-378

验证点：
- `endpoint="ws://127.0.0.1:19999"`（无人监听端口）
- 3s 等待 → `sc.state != ConnectorState.CONNECTED`
- **DeviceManager 不受影响**：`len(manager.list_devices()) == 3`
- **Agent REST API 正常**：`/health` 200, `/api/v1/devices` 3 台

**这是故障隔离的核心验证** — Connector 完全不影响 Agent 主进程。

### 3.8 test_13_independent_start_stop（独立启停）

**代码**：L380-412

验证点：
- start → CONNECTED → disable() → **STOPPED** → enable() → CONNECTED

**V1.1 隐式验证**：
- A9：stop() 正常退出（3s 内）
- A7：STOPPED 语义（V1.0 没有 STOPPED 态）

### 3.9 test_14_no_credential_pairing_state（无 credential 停态）

**代码**：L413-424

验证点：
- `credential=""` + `enabled=True` → start() 后 **STOPPED**（不是 CONNECTING）

**V1.1 语义**：V1.0 是 PAIRING 态，V1.1 统一为 STOPPED（等待 `enable(credential=...)`）

---

## 四、A5 Command 测试完全缺失

当前测试 **完全没有覆盖 Command 状态机**（A5 是 33 审计中 CRITICAL 级别的修复点）。

### 4.1 需要覆盖的子场景

| 子场景 | 优先级 | 难度 |
|--------|--------|------|
| 正常命令执行 | P0 | 低 |
| 重复 request_id → 返回缓存 | P0 | 中 |
| Device 离线 → REJECTED | P0 | 低 |
| 非法 command → REJECTED | P0 | 低 |
| 命令执行超时 → TIMEOUT | P1 | 中 |
| 命令执行异常 → FAILED | P1 | 中 |
| Registry 超 1024 自动清理 | P2 | 中 |
| 执行中 WS 断开 → 重发幂等 | P1 | 高 |
| Command statistics 正确 | P1 | 低 |

### 4.2 测试策略

需要模拟 Site Server 发送 `command.request` 的路径。两种方式：

**方式 A：真实 WS 连接**
```python
# 在 site_server 端保存 AgentConnection → 手动发 command.request
conn = registry.get(agent_id)
await conn.send_json({
    "type": "command.request",
    "request_id": "unique-uuid",
    "device_id": "sim-c5-000",
    "command": "INFO",
})
# 等 Connector 执行完 → 验证 command.response
```

**方式 B：直接调用内部方法**
```python
sc._command_registry["test-1"] = CommandRecord(...)
# 直接测试幂等性检查
```

**建议方式 A**（端到端验证），方式 B 补充边界条件。

---

## 五、A1/A3/A4/A9 测试建议

### 5.1 A1 — Fire-and-Forget 验证

```python
def test_bus_handler_non_blocking(self):
    """Connector event loop 暂停不阻塞 DeviceManager."""
    sc = SiteConnector(...)
    sc.start()
    # 等 CONNECTED
    
    # 暂停 Connector event loop（用 threading.Event）
    pause_evt = threading.Event()
    # ... 通过 patch 让 _outbound_priority_dispatch 暂停
    
    # DeviceManager 发布 100 个 event
    start = time.time()
    for i in range(100):
        bus.publish(Event("device.frame", ...))
    elapsed = time.time() - start
    
    # 断言发布 100 个事件不被阻塞（应 < 100ms）
    assert elapsed < 0.5, f"publish blocked for {elapsed:.2f}s"
```

### 5.2 A3 — 双队列优先级验证

```python
def test_critical_queue_priority(self):
    """高频 Measurement 不挤占 Critical."""
    # 注入高频 Measurement → 持续 120 events/s × 5s = 600 个
    # 同时 Critical 事件 → 验证延迟 < 50ms
    # Connector 在 Measurement 高频下 Critical events 零丢弃
```

### 5.3 A4 — 锁并发压力验证

```python
def test_no_concurrent_ws_write(self):
    """高并发 send 不触发 ConnectionClosedError: invalid frame."""
    # 同时 heartbeat + 事件 + command response → 检查无协议级错误
    # 跑 5min 看日志无 invalid frame
```

### 5.4 A9 — Force shutdown 验证

```python
def test_stop_force_shutdown(self):
    """WS hang → stop() 5s 内返回."""
    # mock ws.send 永远不返回 → stop() 必须在 5s 内退出
```

---

## 六、A10 TLS 测试建议

```python
def test_tls_configured(self):
    """配置了 CA file → 连接 ws:// 不受影响。"""
    sc = SiteConnector(tls_ca_file="/path/to/ca.pem", tls_verify=True)
    assert sc._build_ssl_context() is not None  # wss:// 时返回 ctx

def test_tls_disable_verify(self):
    """tls_verify=False → ssl context 存在."""
    sc = SiteConnector(endpoint="wss://...", tls_verify=False)
    assert sc._build_ssl_context() is not None
```

---

## 七、Simulator Transport 在测试中的角色

### 7.1 为什么用 Simulator

| 真实 Device | Simulator | 优势 |
|-------------|-----------|------|
| 需要 USB 硬件 | 纯 Python | CI/CD 可跑 |
| 初始化慢（USB 枚举） | 立即 ready | 用例快速启动 |
| 不可控的 frame 频率 | 固定频率 | 队列压力测试可预测 |
| 无法强制离线 | 可控状态 | REJECTED 测试可 mock |

### 7.2 Simulator 默认行为

从测试观察：
- 3 个 Simulator → 3 台设备
- 每个 Simulator 发布 `device.online`（启动时）
- 持续发 METRICS frames（test_09 验证 4s 后 Site Server measurements 增加）
- 不发 RAWIQ（type_code=4）— Connector 内部过滤

---

## 八、fixture 隔离与资源清理

### 8.1 Site Server fixture

```python
yield f"http://127.0.0.1:{port}"
# daemon thread 随主进程退出
# SQLite 内存 DB — 每个用例独立创建 Site Server → 独立 DB
```

### 8.2 Gateway fixture

```python
bus = EventBus()
manager = DeviceManager(...)
yield bus, manager
manager.stop()   # ← teardown
```

**每个用例独立创建 EventBus + DeviceManager** → fixture 之间无交叉污染。

### 8.3 Connector 清理

每个用例的 `finally` 块：
```python
sc.start()
try:
    ...
finally:
    sc.stop()   # ← 确保 daemon thread 退出
```

**最佳实践**：不用 pytest fixture 管 Connector 生命周期，让每个用例自己管。原因：
- Connector 有 start/stop 异步语义
- 某些用例需要 Connector 停在中间态（test_14 STOPPED）
- 每个用例的 Connector 配置不同（heartbeat 间隔等）

---

## 九、测试执行命令

```bash
# 全部用例
cd gateway-agent
python -m pytest tests/test_site_connector.py -v

# 只跑 Connection 类
python -m pytest tests/test_site_connector.py::TestSiteConnectorConnection -v

# 带日志
python -m pytest tests/test_site_connector.py -v -s --log-cli-level=DEBUG

# 收集用例数
python -m pytest tests/test_site_connector.py --collect-only -q
```

---

## 十、测试覆盖差距总结

| 审计项 | 修复状态 | 测试覆盖 | 差距 |
|--------|----------|----------|------|
| A1 fire-and-forget | ✅ 已修复 | 🟡 间接 | 缺 DeviceManager 延迟监控 |
| A2 首条消息 auth | ✅ 已修复 | 🟢 直接（test_01/test_03） | 基本覆盖 |
| A3 双队列 | ✅ 已修复 | 🟡 间接 | 缺优先级压力测试 |
| A4 锁串行化 | ✅ 已修复 | 🟢 隐式（所有 send 都过锁） | 缺显式并发压力 |
| A5 Command 7 态 + 幂等 | ✅ 已修复 | 🔴 **完全缺失** | **最高优先级补齐** |
| A6 手动计数器 | ✅ 已修复 | 🟡 间接 | 缺跨线程一致性测试 |
| A7 9 态状态机 | ✅ 已修复 | 🟢 直接（test_01/06/13/14） | 基本覆盖 |
| A8 Diagnostics 30 字段 | ✅ 已修复 | 🟡 间接 | 缺字段数量断言 |
| A9 stop() force | ✅ 已修复 | 🔴 **完全缺失** | 需注入 WS hang |
| A10 TLS 配置 | ✅ 已修复 | 🔴 **完全缺失** | 需 wss:// 测试 |

**结论**：现有 10 用例覆盖了 V1.1 大部分修复点的**间接验证**，但 A5 Command 测试 **完全缺失**（CRITICAL 级修复点），A9/A10 也缺失。建议补充至少 8-10 个新用例覆盖这些差距。
