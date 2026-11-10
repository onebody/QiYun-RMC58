# 24 — Site Connector 测试设计

> **版本**：v1.0  
> **日期**：2026-11-04  
> **作者**：Engineering  
> **引用**：`gateway-agent/tests/test_site_connector.py` (422 行, 15 用例) · `site_connector.py` L52-806 · `connection.py` L88-293  
> **交叉引用**：[21-site-connector-contract-audit.md](./21-site-connector-contract-audit.md) · [22-site-connector-architecture.md](./22-site-connector-architecture.md) · [23-site-connector-protocol.md](./23-site-connector-protocol.md)

---

## 一、测试矩阵总览（15 用例）

> 用例编号来自 `test_site_connector.py` 文件头注释 L3-18。标注 ✅ 表示已实现。

| # | 用例名 | 测试类 | 实现 | 核心验证点 | 依赖 |
|---|--------|--------|------|-----------|------|
| 1 | 连接握手成功 | Connection | ✅ | state=CONNECTED, Site 看到 Agent | site_server + pair_credential |
| 2 | 首次配对 pair_via_rest | Connection | ❌ 预留 | pairing_code → pair → credential | — |
| 3 | 错误 credential | Connection | ✅ | 不阻断 Agent | site_server（无需 pair） |
| 4 | Credential 撤销 | Connection | ❌ 预留 | Site revoke → Agent ERROR | — |
| 5 | Agent 重启后恢复 | Connection | ❌ 预留 | 持久化 credential 恢复 | — |
| 6 | Site Server 重启后自动重连 | Reconnect | ✅ | disable→enable 重连 | site_server + pair_credential |
| 7 | 网络中断恢复（指数退避） | Reconnect | ❌ 预留 | 退避延迟曲线 | — |
| 8 | EventBus device.online → Site 设备创建 | EventForwarding | ✅ | devices_snapshot → Site Device | site_server + pair_credential |
| 9 | EventBus device.frame → Site measurement | EventForwarding | ✅ | METRICS → Measurement 增加 | site_server + pair_credential |
| 10 | RAWIQ frame 默认跳过 | EventForwarding | ✅ | `_to_site_envelope` type_code=4 → None | 纯单元 |
| 11 | Agent REST API 回归 | Connection | ✅ | Connector 启停后 API 100% 正常 | site_server + pair_credential |
| 12 | Site 不可用时 Agent 独立运行 | Reconnect | ✅ | 设备管理不受影响 | gateway（无需 site） |
| 13 | Connector 独立启停 | Reconnect | ✅ | disable→DISABLED→enable→CONNECTED | site_server + pair_credential |
| 14 | 无 credential 进入 PAIRING | Reconnect | ✅ | 不尝试连接 | gateway（无需 pair） |
| 15 | devices_snapshot 全量发送 | EventForwarding | ⚠️ 合并到 #8 | WS 连接后 Site 收到全量 | site_server + pair_credential |

---

## 二、测试环境设计

### 2.1 Mock Site Server（真实 Site Server 内存 SQLite）

**Fixture**：`site_server`（L59-86）

```python
@pytest.fixture
def site_server():
    cfg = ServerConfig()
    cfg.database.url = "sqlite:///:memory:?cache=shared"
    app = create_app(cfg)
    port = 0  # OS 动态分配
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    t = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning"),
        daemon=True,
    )
    t.start()
    # 轮询 /health 等 ready（最多 5s）
    yield f"http://127.0.0.1:{port}"
    # 无显式 teardown — daemon 线程随 pytest 退出
```

| 设计点 | 选择 | 原因 |
|--------|------|------|
| SQLite `:memory:` | 单进程内 DB | pytest 进程内共享，每个 fixture 实例独立（cache=shared 只在同一进程内共享） |
| 真实 uvicorn 线程启动 | 非 TestClient | 需要真正的 WebSocket endpoint（TestClient 不支持 asyncio WS） |
| OS 动态端口 | port=0 | 避免端口冲突 |
| daemon 线程 | 自动随 pytest 退出 | 不用显式 teardown |
| 5s ready 等待 | 轮询 `/health` | uvicorn 启动有延迟 |

### 2.2 Simulator Transport（Gateway Agent 模拟设备）

**Fixture**：`gateway`（L89-99）

```python
@pytest.fixture
def gateway():
    bus = EventBus()
    manager = DeviceManager(bus=bus, poll_interval=5.0, reconnect_enabled=True)
    for i in range(3):
        sim = SimulatorTransport(device_id=f"sim-c5-{i:03d}")
        manager.add_device(sim, device_id=f"sim-c5-{i:03d}")
    manager.start()
    yield bus, manager
    manager.stop()
```

| 设计点 | 说明 |
|--------|------|
| 3 台 simulator | 模拟真实设备群（sim-c5-000 / sim-c5-001 / sim-c5-002） |
| DeviceManager.start() | 启动设备线程 + 开始发送 EventBus 事件 |
| reconnect_enabled=True | 模拟断线重连逻辑（V1 connector 不影响，由 DeviceManager 管） |
| teardown manager.stop() | 清理设备线程 |

### 2.3 Pairing Credential Fixture

**Fixture**：`pair_credential`（L102-126）

```python
@pytest.fixture
def pair_credential(site_server):
    # 1. POST /api/v1/agents/pairing-codes (ttl_seconds=1800)
    #    → {pairing_code, plain_credential, expires_at}
    # 2. POST /api/v1/agents/pair
    #    → {agent_id, credential_id, expires_at}
    # 3. 返回 {agent_id, credential_id, plain_credential, pairing_code}
```

| 步骤 | REST endpoint | 作用 |
|------|--------------|------|
| Step 1 | `POST /api/v1/agents/pairing-codes` | Site Server 生成临时配对码 |
| Step 2 | `POST /api/v1/agents/pair` | 用 pairing_code 换 agent_id + credential_id |
| Step 3 | — | 组装 dict 给 SiteConnector 构造用 |

**注意**：`plain_credential` 从 Step 1 的响应里拿（pairing-codes 同时返回 pairing_code 和 plain_credential），不是 Step 2 的返回。这是 `pair_via_rest()`（L248-282）不完整的原因 — pair 端点不返回 plain_credential。

---

## 三、Fixture 隔离策略

| Fixture | Scope | 隔离方式 | 共享性 |
|---------|-------|----------|--------|
| `site_server` | `function`（默认） | 每个测试启动新 uvicorn 线程 + 新 SQLite :memory: | **不共享** — 每个测试独立 |
| `gateway` | `function`（默认） | 每个测试新 EventBus + 新 DeviceManager + 3 新 SimulatorTransport | **不共享** |
| `pair_credential` | `function`（默认, 依赖 site_server） | 每个测试新 pair → 新 agent_id + 新 credential | **不共享** |
| SiteConnector 实例 | 测试方法内 `sc = SiteConnector(...)` | 每个方法内创建/销毁 | **不共享** — 用 try/finally `sc.stop()` |

**为什么 function 级？**
- Connector 有后台 daemon 线程 + 状态机，跨用例共享会产生残留状态
- SQLite :memory: 不共享（cache=shared 只在同一进程内的同一连接池共享，uvicorn 每次启动是新进程内的新 app）
- Agent 重连/断线状态无法安全 reset 到干净状态

**fixture 依赖链**：
```
site_server (function)  ← pair_credential (function, 依赖 site_server)
                         ← 大部分连接/事件测试用例
gateway (function)      ← 大部分测试都用（EventBus + DeviceManager）
                         ← SiteConnector 在每个测试内手动创建
```

---

## 四、15 用例逐个详解

### 用例 #1 — Site Server 正常连接握手

```
位置：TestSiteConnectorConnection::test_01_connect_success (L136-160)
依赖：site_server + gateway + pair_credential
配置：heartbeat_interval=5.0, reconnect_initial_delay=0.5, reconnect_max_delay=2.0
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 启动 Site Connector（enabled=True, 有 agent_id + credential + endpoint） |
| **Action** | `sc.start()` → 轮询 `sc.state == ConnectorState.CONNECTED`（最多 10s） |
| **Assert** | ① `sc.state == CONNECTED`；② `sc.is_connected is True`；③ Site Server `/api/v1/agents` 返回至少 1 条；④ finally `sc.stop()` |
| **Expected** | Connector 完成 WS 连接 + 收到 `connected` 握手 + 进入 CONNECTED；Site Server agent_connect_ws 注册成功 |
| **验证的代码路径** | `start()` L191-217 → `_lifecycle()` L441-500 → `_try_connect()` L502-550 → Site `agent_connect_ws` L88-127 |

---

### 用例 #3 — 错误 credential

```
位置：TestSiteConnectorConnection::test_03_bad_credential (L162-180)
依赖：site_server + gateway（无需 pair）
配置：heartbeat_interval=5.0, reconnect_initial_delay=0.5, reconnect_max_delay=2.0
```

| Phase | 步骤 |
|-------|------|
| **Setup** | Site Connector（agent_id="fake-agent", credential="bad-credential-abcdef1234567890"） |
| **Action** | `sc.start()` → sleep 3s |
| **Assert** | ① `sc.state != CONNECTED`（ERROR 或 DISCONNECTED 重试中）；② **关键** `len(manager.list_devices()) == 3` — Agent 设备管理不受影响 |
| **Expected** | Gateway Agent `InvalidStatusCode` (HTTP 4401) → `_set_state(ERROR, AUTH_FAILED)` + 清 credential + 停止重连；但独立故障隔离有效 |
| **验证的代码路径** | `_try_connect()` L531-538（InvalidStatusCode → ERROR + 清 credential）+ DeviceManager 独立运行 |

---

### 用例 #6 — Site Server 重启后自动重连

```
位置：TestSiteConnectorReconnect::test_06_site_server_restart_reconnect (L318-351)
依赖：site_server + gateway + pair_credential
配置：heartbeat_interval=3.0, reconnect_initial_delay=1.0, reconnect_max_delay=3.0
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action 1** | `sc.disable()` → sleep 1s → `sc.state != CONNECTED` |
| **Action 2** | `sc.enable(endpoint, credential)` → 轮询 `sc.state == CONNECTED` |
| **Assert** | ① disable 后断开；② enable 后恢复 CONNECTED；③ `sc.diagnostics["reconnect_attempts"] >= 0` |
| **Expected** | disable→stop→ENABLED=False；enable→补充 endpoint/credential→start→进入 CONNECTING→WS 握手→CONNECTED |
| **验证的代码路径** | `disable()` L239-242 → `stop()` → `enable()` L229-237 → `start()` → 重连循环 |

**说明**：测试用 disable→enable 模拟 Site Server 重启（fixture 里无法真正重启 Site Server，简化为 Connector 自己断→重连）。

---

### 用例 #8 — EventBus device.online → Site Server 设备创建

```
位置：TestSiteConnectorEventForwarding::test_08_device_online_forwards (L225-252)
依赖：site_server + gateway + pair_credential
配置：heartbeat_interval=5.0, reconnect_initial_delay=0.5, reconnect_max_delay=2.0
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action** | sleep 1s 等 devices_snapshot 处理 → GET Site `/api/v1/devices` |
| **Assert** | ① Site devices ≥ 1；② Connector `total_events_sent > 0` |
| **Expected** | WS 连接后 Connector 发 `agent.devices_snapshot`（L467, L726-751）→ Site `_handle_agent_message` L206-210 遍历 → `upsert_device_from_agent` L23-134 |
| **验证的代码路径** | `_send_devices_snapshot()` L726-751 → Site `_process_agent_event` device.online 分支 L224-236 → `upsert_device_from_agent` |

---

### 用例 #9 — EventBus device.frame METRICS → Site measurement

```
位置：TestSiteConnectorEventForwarding::test_09_device_frame_forwards (L254-280)
依赖：site_server + gateway + pair_credential
配置：heartbeat_interval=5.0, reconnect_initial_delay=0.5, reconnect_max_delay=2.0
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action** | sleep 4s 等 SimulatorTransport 持续发 METRICS frames |
| **Assert** | ① Site `/api/v1/measurements/stats` → `stats["total"] > 0`；② Connector `total_events_sent > 0` |
| **Expected** | SimulatorTransport 发 device.frame (type_code=1 METRICS) → EventBus → `_bus_handler` → `_outbound_queue` → `_outbound_dispatch_loop` → Site `record_device_frame` |
| **验证的代码路径** | EventBus bridge L288-323 + `_to_site_envelope` frame 分支 L357-371 + Site `record_device_frame` ingestion/__init__.py L104-127 |

---

### 用例 #10 — RAWIQ frame 默认跳过

```
位置：TestSiteConnectorEventForwarding::test_11_rawiq_default_skipped (L282-312)
依赖：site_server + gateway + pair_credential
核心：单元级验证 `_to_site_envelope()` 逻辑（不依赖完整 WS 流程）
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action** | 构造两个 Event 对象：RAWIQ (type_code=4) + METRICS (type_code=1) |
| **Assert** | ① `_to_site_envelope(rawiq_evt) is None`；② `_to_site_envelope(metrics_evt) is not None` |
| **Expected** | RAWIQ 跳过（L359-360）；METRICS 正常转发 |
| **验证的代码路径** | `_to_site_envelope()` L357-371（device.frame 分支 + RAWIQ 判断） |

---

### 用例 #11 — Agent REST API 回归

```
位置：TestSiteConnectorConnection::test_11_agent_api_regression_after_connector (L182-219)
依赖：site_server + gateway + pair_credential
核心：Connector 启停不影响 Agent 原有能力
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action** | `build_app(manager=manager, bus=bus, agent_id="test", connector=sc)` → `TestClient(app)` |
| **Assert** | ① `/health` → 200；② `/api/v1/agent` → 200；③ `/api/v1/devices` → 200, len=3；④ `/api/v1/devices/{id}` → 200；⑤ `/api/v1/devices/nope` → 404；⑥ `/api/v1/devices/{id}/commands INFO` → 200 或 503；⑦ `/api/v1/diagnostics` → 200；⑧ `/api/v1/site-connector` → 200, `json()["state"] == "connected"` |
| **Expected** | 所有原有端点正常；新增的 site-connector 端点也正常暴露 Connector 状态 |
| **验证的设计原则** | 独立故障隔离 — Site Connector 是 Agent 的附加能力，不改变任何已有行为 |

---

### 用例 #12 — Site 不可用时 Agent 独立运行

```
位置：TestSiteConnectorReconnect::test_12_agent_independence_when_site_down (L353-378)
依赖：gateway（**不需要 site_server**）
配置：endpoint="ws://127.0.0.1:19999"（没人监听的端口）, reconnect_initial_delay=0.5, reconnect_max_delay=2.0
核心：验证 Connector 不阻断 Agent 本地功能
```

| Phase | 步骤 |
|-------|------|
| **Setup** | Site Connector 指向不可达 endpoint |
| **Action** | `sc.start()` → sleep 3s |
| **Assert** | ① `sc.state != CONNECTED`（DISCONNECTED 重连中）；② **关键** `len(manager.list_devices()) == 3`；③ `manager.get("sim-c5-000") is not None`；④ Agent REST `/health` → 200, `/devices` → len=3 |
| **Expected** | Connector daemon 线程在后台循环重连，**不阻塞** DeviceManager；独立故障隔离 ✅ |
| **验证的设计原则** | 独立线程模型 — daemon 线程不阻塞主线程/uvicorn/DeviceManager |

---

### 用例 #13 — Connector 独立启停

```
位置：TestSiteConnectorReconnect::test_13_connector_independent_start_stop (L380-409)
依赖：site_server + gateway + pair_credential
```

| Phase | 步骤 |
|-------|------|
| **Setup** | 连接成功（state=CONNECTED） |
| **Action 1** | `sc.disable()` → sleep 1s |
| **Assert 1** | `sc.state == ConnectorState.DISABLED` |
| **Action 2** | `sc.enable(endpoint, credential)` → 轮询 CONNECTED |
| **Assert 2** | `sc.state == ConnectorState.CONNECTED` |
| **Expected** | disable → stop → state=DISABLED；enable → 补充 enabled=True → start → 重新连接 |
| **验证的代码路径** | `disable()` L239-242 → `enable()` L229-237 → `start()` |

---

### 用例 #14 — 无 credential 进入 PAIRING

```
位置：TestSiteConnectorReconnect::test_14_no_credential_pairing_state (L411-422)
依赖：gateway（不需要 site_server）
配置：endpoint="ws://something:1234", credential="", enabled=True
```

| Phase | 步骤 |
|-------|------|
| **Setup** | Site Connector enabled=True 但无 credential |
| **Action** | `sc.start()` → sleep 0.5s |
| **Assert** | `sc.state == ConnectorState.PAIRING` |
| **Expected** | 构造函数 `_state = PAIRING`（L124），start() 里 `if not credential → PAIRING`（L204-206），**不启动 daemon 线程** |
| **验证的代码路径** | `__init__` L124 + `start()` L204-206 |

---

## 五、Simulator Transport 行为（测试依赖理解）

SimulatorTransport 是 Gateway Agent 内置的测试 Transport（`transports/simulator.py`），在本测试中扮演「真实硬件 C5 设备」角色：

| 行为 | 说明 |
|------|------|
| 发 device.registered → DeviceManager | 新设备注册 |
| 发 INFO 行 → 握手 → 发 device.online | 建立在线状态 |
| 定时发 device.frame (type_code=1 METRICS) | `manager.start()` 后 simulator 持续发 |
| 发 device.state_change（connecting → handshaking → online） | 状态迁移 |

**为什么用 Simulator 而不是真实 USB 设备？**
- 跨平台可跑（CI/本地 macOS 都能跑）
- 速度快（不需要接真实硬件）
- 确定性行为（不会出现真实硬件的偶发 CRC 错误）

---

## 六、已知测试覆盖缺口

| 缺口 | 说明 | 补充难度 |
|------|------|----------|
| #2 pair_via_rest 完整流程 | pair_via_rest() L248-282 不完整，没自动 enable+credential | 中 — 需先修 Connector 侧 pair 逻辑 |
| #4 Credential 撤销 | 需要 Site Server 提供 revoke credential REST + Agent 感知 | 中 |
| #5 Agent 重启恢复 | 需要 credential YAML 持久化 + 启动时恢复 | 低 |
| #7 指数退避延迟曲线 | 需要 mock time.monotonic 精确测量 backoff 等待 | 中 |
| Site→Agent command.request | Site Server 发送端未实现（audit G7） | 中 |
| 网络分区模拟 | 需要断真实 WS 或注入超时，fixture 里难实现 | 高 |
| EventBus→Queue bridge 线程安全 | 多线程并发事件投递 | 中 |
| devices_snapshot 去重 | 重连时 snapshot 会不会和 device.online 重复上报 | 中 |

---

## 七、测试运行命令

```bash
# 在 gateway-agent/ 目录下
cd gateway-agent

# 跑 connector 全部测试
python -m pytest tests/test_site_connector.py -v

# 只跑连接类
python -m pytest tests/test_site_connector.py::TestSiteConnectorConnection -v

# 只跑事件转发类
python -m pytest tests/test_site_connector.py::TestSiteConnectorEventForwarding -v

# 只跑重连类
python -m pytest tests/test_site_connector.py::TestSiteConnectorReconnect -v

# 带覆盖率
python -m pytest tests/test_site_connector.py -v --cov=widscan_agent/site_connector.py --cov-report=term-missing
```

---

## 八、测试结果判定标准

| 类别 | 通过标准 |
|------|----------|
| 核心用例（#1, #3, #6, #8, #9, #10, #11, #12, #13, #14） | 100% pass |
| 预留用例（#2, #4, #5, #7） | 标注 skip，不阻断 main CI |
| 关键断言 — 独立故障隔离 | Agent 设备管理不受 Connector 状态影响 |
| 关键断言 — 协议一致性 | Site Server DB 数据（Device/ConnectionSession/Measurement）与 Agent 侧 EventBus 事件一一对应 |
| 关键断言 — RAWIQ 默认跳过 | Connector 不发 type_code=4，Site Server ingestion 也跳过 type_code=4 |

---

*文档结束。下一篇：[25-site-connector-acceptance.md](./25-site-connector-acceptance.md)*
