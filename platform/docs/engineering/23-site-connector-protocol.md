# 23 — Site Connector WebSocket 协议规范

> **版本**：v1.0  
> **日期**：2026-11-04  
> **作者**：Engineering  
> **引用**：Gateway Agent `site_connector.py` L504-760 · Site Server `connection.py` L88-293  
> **交叉引用**：[21-site-connector-contract-audit.md](./21-site-connector-contract-audit.md)（Gap 表 G1-G10）· [22-site-connector-architecture.md](./22-site-connector-architecture.md)（状态机/线程模型）

---

## 一、连接 URL

```
ws://<site-server-host>:<port>/ws/v1/agents/connect?agent_id=<agent_id>&credential=<plain_text_credential>
```

| 组件 | 类型 | 必填 | 说明 | 代码位置 |
|------|------|------|------|----------|
| `agent_id` | string (UUID) | ✅ | Agent 启动时生成 / 从 config 读 | connector L504, connection L93 |
| `credential` | string (plaintext) | ✅ | Site Server pair 颁发的明文 credential | connector L504, connection L94 |

**额外 Header**（Gateway Agent 发送）：
```
User-Agent: WIDSCAN58-Agent/0.6.0    connector L508
```

**WebSocket 超时**：
- Gateway Agent 连接超时：10s（`asyncio.wait_for websockets.connect`, L507）
- Gateway Agent 等 `connected` 握手超时：5s（L513）
- Site Agent 等 Agent 消息超时：60s（connection.py L152）

---

## 二、认证流程

### 2.1 REST 配对（Pairing Code → Credential）

```
┌─────────┐                          ┌──────────────┐                          ┌─────────┐
│  Site   │───① POST /api/v1/agents/pairing-codes (ttl_seconds)──▶│  Site Server │──▶ DB     │
│ Console │◀── {pairing_code, plain_credential, expires_at}────────│              │          │
└────┬────┘                          └──────┬───────┘                          └─────────┘
     │                                       │
     │ 把 pairing_code 贴给 Agent 用户
     ▼
┌─────────┐                          ┌──────────────┐                          ┌─────────┐
│ Gateway │───② POST /api/v1/agents/pair ─────────────────────────────────────▶│  Site Server │──▶ DB (verify pairing_code)
│  Agent  │   {pairing_code, agent_version, platform, name, agent_id}         │              │          │
└────┬────┘◀── {agent_id, credential_id, expires_at}───────────────────────────└──────┬───────┘◀─┘
     │                                                                                 │
     │ ③ 拿 plain_credential 作为 SiteConnector.credential 启动 WS
     ▼                                                                                 ▼
  ┌──────────────────────────────────────────────────────────────────────────────────────┐
  │ WS 连接 /ws/v1/agents/connect?agent_id=&credential=                                 │
  │ Site Server verify_credential(): sha256(credential_plain) → AgentCredential 表里查  │
  └──────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 credential 校验（Site Server `auth/__init__.py` → connection.py L102-108）

```python
cred = verify_credential(sess, agent_id, credential)
# 内部：
# 1. credential_hash = sha256_hex(credential_plain)
# 2. SELECT * FROM AgentCredential WHERE agent_id=? AND credential_hash=?
# 3. 检查 revoked_at IS NULL AND expires_at > now
```

### 2.3 认证失败

| 场景 | Site Server 响应 | 代码位置 |
|------|-----------------|----------|
| 缺少 agent_id 或 credential | `{"error": "missing agent_id or credential"}` + `ws.close(code=4401)` | connection.py L97-99 |
| credential 无效/撤销 | `{"error": "credential invalid or revoked"}` + `ws.close(code=4401)` | connection.py L106-108 |
| DB 异常 | `{"error": str(exc)}` + `ws.close(code=4500)` | connection.py L113-115 |
| HTTP 层 4401/401 | Gateway Agent `InvalidStatusCode` → 进入 ERROR 状态、清 credential、停止重连 | connector L533-538 |

---

## 三、握手时序（成功路径）

```
Gateway Agent                                    Site Server
     │                                               │
     │──── WS open ─────────────────────────────────▶│
     │                                               │ 接受 / 校验 credential
     │◀─── {"type":"connected", agent_id, site_id, server_time} ─────
     │                                               │
     │──── {"type":"agent.devices_snapshot", devices:[...]} ──────▶│
     │                                               │ upsert devices
     │──── {"type":"heartbeat", agent_id, uptime, devices:[{device_id}]} ─▶│
     │                                               │ record_heartbeat
     │◀─── {"type":"heartbeat.ack"} ─────────────────│
     │                                               │
     │──── {"type":"agent.event", event:{type, device_id, data}} ─▶│
     │                                               │ _process_agent_event
     │                                               │
     │   ... 持续事件流 + 心跳 ...                      │
     │                                               │
     │◀─── {"type":"command.request", request_id, device_id, command} │ (可选 V1 Site Server 未实现发送端)
     │                                               │
     │──── {"type":"command.response", request_id, ok, reply} ───▶│
```

---

## 四、Agent → Site Server 消息类型

### 4.1 `heartbeat`

**发送频率**：每 15s（可配置 `heartbeat_interval`）  
**代码位置**：connector L570-599

```json
{
  "type": "heartbeat",
  "agent_id": "gateway-001",
  "uptime": 1234,
  "devices": [
    {"device_id": "c5-v1.0.0-abcd"},
    {"device_id": "c5-v1.0.0-ef12"}
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"heartbeat"` |
| `agent_id` | string | ✅ | Gateway Agent ID |
| `uptime` | integer | ✅ | 自本次 WS 连接建立起的秒数（`time.monotonic() - connected_at`） |
| `devices` | array of object | ✅ | 当前 manager.list_devices() 的 device_id 列表 |
| `devices[].device_id` | string | ✅ | Device ID |

**Site Server 处理**：connection.py L186-191 → `record_heartbeat(sess, agent_id)`

---

### 4.2 `agent.event`（事件转发）

**触发条件**：EventBus 事件到达 + Connector 处于 CONNECTED 状态 + `_to_site_envelope()` 非 None  
**代码位置**：connector L329-423（合成） + L601-620（发送）

```json
{
  "type": "agent.event",
  "event": {
    "type": "<event_type>",
    "device_id": "c5-v1.0.0-abcd",
    "data": { ... }
  }
}
```

**支持的 event_type 列表**（5 种）：

| event_type | 源 EventBus type | data 结构 | 合成位置 |
|------------|------------------|-----------|----------|
| `device.online` | `device.online` | `{info, transport}` | L336-354 |
| `device.discovered` | `device.registered`（G1 补偿） | `{info, transport}` | L393-405 |
| `device.frame` | `device.frame` | `{type_code, decoded}`（**type_code=4 RAWIQ 跳过**） | L357-371 |
| `device.state_change` | `device.state_change` | `{from, to, error?}`（**to="discovered" 跳过**） | L374-390 |
| `device.removed` | `device.removed` | `{}` | L408-416 |

#### `device.online` / `device.discovered` 的 data

```json
{
  "info": {
    "firmware_version": "v1.0.0",
    "hardware_model": "esp32c5",
    "phy_version": "esp32c5-phy-1.2",
    "protocol_version": 1,
    "capabilities": {"rawiq": true, "spectrum": true, "metrics": true}
  },
  "transport": {
    "kind": "usb_serial",
    "path": "/dev/ttyACM0"
  }
}
```

| transport 字段 | 取值优先级 | 代码位置 |
|----------------|-----------|----------|
| `kind` | `TransportInfo.kind` → fallback `bus_event.data["transport"]["kind"]` | L349 |
| `path` | `TransportInfo.path` → fallback `TransportInfo.label` → fallback `bus_event.data["transport"]["label"]` | L350 |

#### `device.frame` 的 data

```json
{
  "type_code": 1,
  "decoded": {
    "rssi": -45,
    "frequencies": [2412, 2437, 2462],
    "gateway_count": 3
  }
}
```

**Frame Type Code 映射**（ingestion/__init__.py L14-21 + 固件定义）：

| type_code | 名称 | 默认行为 |
|-----------|------|----------|
| 1 | METRICS | ✅ 转发 + 持久化 |
| 2 | SPECTRUM | ✅ 转发（bins >512 截断到 256） |
| 3 | TELEMETRY | ✅ 转发 + 持久化 |
| 4 | RAWIQ | ❌ **Connector 跳过不转发**（L359-360） |
| 5 | LOG | ✅ 转发 |
| 6 | LEVELS | ✅ 转发 |
| 其他 | UNKNOWN_N | ✅ 转发（Site Server map_frame_type 走 fallback） |

#### `device.state_change` 的 data

```json
{
  "from": "online",
  "to": "error",
  "error": "crc_fail"
}
```

**DeviceState → Site Device.status 映射**（connection.py L282-293）：

| Agent DeviceState | → Site Device.status |
|-------------------|----------------------|
| `online` | `online` |
| `connected` | `online` |
| `disconnected` | `offline` |
| `error` | `error` |
| `reconnecting` | `offline` |
| `unsupported` | `unsupported` |
| `identity_conflict` | `identity_conflict` |
| `discovered` | **跳过不发**（G1 补偿已由 `device.registered → device.discovered` 处理） |
| `connecting` / `handshaking` | 不映射 |

---

### 4.3 `agent.devices_snapshot`

**触发条件**：WS 连接建立 + 收到 `connected` 握手后立即发送一次（L726-751）  
**补偿目标**：Site Server 可能在 Agent 重连期间丢失的事件  
**代码位置**：connector L467（调用）+ L726-751（实现）

```json
{
  "type": "agent.devices_snapshot",
  "devices": [
    {
      "type": "device.online",
      "device_id": "c5-v1.0.0-abcd",
      "data": {
        "info": {
          "firmware_version": "v1.0.0",
          "hardware_model": "esp32c5",
          "phy_version": "esp32c5-phy-1.2",
          "protocol_version": 1,
          "capabilities": {"rawiq": true, "spectrum": true, "metrics": true}
        },
        "transport": {
          "kind": "usb_serial",
          "path": "/dev/ttyACM0"
        }
      }
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"agent.devices_snapshot"` |
| `devices` | array | ✅ | 当前 `manager.list_devices()` 全部设备 |
| `devices[].type` | string | ✅ | 固定 `"device.online"` |
| `devices[].device_id` | string | ✅ | Device ID |
| `devices[].data` | object | ✅ | 同 `device.online` 的 data |

**Site Server 处理**：connection.py L206-210 → 遍历每个 device 调用 `_process_agent_event()` → `upsert_device_from_agent()`

**重要副作用**：upsert 会创建新 ConnectionSession，必须先 UPDATE 关闭同 device+agent 下所有旧的 `status="connected"` session（Bug 修复，devices/__init__.py L102-110）。

---

### 4.4 `command.response`

**触发条件**：收到 Site Server `command.request` + Agent 执行完命令  
**代码位置**：connector L709-721

```json
{
  "type": "command.response",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "ok": true,
  "reply": "INFO: widscan58|v1.0.0|..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"command.response"` |
| `request_id` | string | ✅ | 来自 Site Server request 的 request_id |
| `ok` | boolean | ✅ | 命令执行结果（Device.send_command 返回） |
| `reply` | string | ✅ | 命令回复文本，**最大 500 字符截断**（L717） |

**失败 ok=false 场景**：

| 场景 | reply 内容 | 代码位置 |
|------|-----------|----------|
| connector 无 DeviceManager | `"connector has no device manager"` | L683 |
| device_id 找不到 | `"device {id} not found"` | L688 |
| device OFFLINE/ERROR | `"device {id} state={state.value}"` | L692 |
| 命令不在白名单 | `"command not allowed: {command}"` | L697 |
| 执行抛异常 | `"execution error: {exc}"` | L707 |

---

## 五、Site Server → Agent 消息类型

### 5.1 `connected`（握手成功）

**触发条件**：Agent WS 连接 + credential 校验成功 + register AgentConnection 后立即发送  
**代码位置**：connection.py L122-127（Site Server）+ connector L513-519（Gateway 接收）

```json
{
  "type": "connected",
  "agent_id": "gateway-001",
  "site_id": "site-001",
  "server_time": "2026-11-04T10:30:00Z"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"connected"` |
| `agent_id` | string | ✅ | 本次连接的 agent_id（回显） |
| `site_id` | string | ✅ | 该 Agent 归属的 site_id |
| `server_time` | string (ISO 8601 UTC) | ✅ | Site Server 当前 UTC 时间 |

**Gateway Agent 校验**：connector L515 — `hello.get("type") != "connected"` 则视为异常、关 WS。

---

### 5.2 `heartbeat.ack`

**触发条件**：Site Server `heartbeat_ack_task()` 每 30s 发一次  
**代码位置**：connection.py L134-146（发送）+ connector L652-654（接收处理）

```json
{
  "type": "heartbeat.ack"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"heartbeat.ack"` |

**Gateway Agent 处理**：connector L652-654 — 更新 `_last_heartbeat_ack = time.monotonic()`

**Gateway 超时逻辑**（connector L579-582）：`time.monotonic() - _last_heartbeat_ack > 60s` → raise RuntimeError → 触发 HEARTBEAT_TIMEOUT 断线。

---

### 5.3 `command.request`

**触发条件**：Site Server 下发命令给 Agent → Device  
**代码位置**：connector L656-658（接收入口）+ connector L662-707（完整处理链）  
**⚠️ 注意**：Site Server V1 代码里**没有发送端实现**（connection.py 中没有 Site→Agent command.request 的发送逻辑）。Connector 侧已完整实现接收和执行链，Site Server 后续加发送端点即可互通。

```json
{
  "type": "command.request",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "device_id": "c5-v1.0.0-abcd",
  "command": "GAIN MANUAL 45"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | ✅ | `"command.request"` |
| `request_id` | string (UUID) | ✅ | 请求唯一标识，用于 response 回包配对 |
| `device_id` | string | ✅ | 目标设备 ID |
| `command` | string | ✅ | 原始命令字符串（会过 `is_command_allowed()` 白名单校验） |

---

## 六、心跳时序

```
时间轴（秒）:  0     15     30     45     60     75     90     105   ...
              │      │      │      │      │      │      │      │
Agent:        ──hb──▶│      │──hb──▶│      │──hb──▶│      │──hb──▶│
              │      │◀──ack│      │◀──ack│      │◀──ack│      │◀──ack│
              │      │      │      │      │      │      │      │
Site Server:  ──recv hb──▶│      │──recv hb──▶│      │──recv hb──▶│
              │      │──send ack─▶│      │──send ack─▶│      │──send ack─▶│
              │      │      │      │      │      │      │      │
```

| 角色 | 间隔 | 超时 | 说明 |
|------|------|------|------|
| Agent 发 heartbeat | 15s（`heartbeat_interval`） | — | connector L574 |
| Agent 等 heartbeat.ack | — | 60s（`heartbeat_ack_timeout`） | connector L579-582 |
| Site Server 发 heartbeat.ack | 30s | — | connection.py L138 |
| Site Server 等 Agent 消息 | — | 60s（`ws.receive_json timeout`） | connection.py L152-155 |

**心跳丢包场景**：

| 场景 | 触发断线 | 原因 |
|------|----------|------|
| Site Server 超过 60s 没收到 Agent 消息 | Site Server 主动 break 主循环 | connection.py L152-155 |
| Agent 60s 没收到 ack | Agent `_heartbeat_loop` raise RuntimeError | connector L579-582 |
| Site Server 超过 30s 没收到 Agent 心跳 | heartbeat_ack_task `heartbeat_received=False → break` | connection.py L139-141 |

---

## 七、EventBus 事件 → 协议映射表

| Agent EventBus type | → Site event.type | Connector 合成逻辑 | Site Server 处理 |
|---------------------|-------------------|-------------------|------------------|
| `device.online` (P0) | `device.online` | 直接转发 + 取 Device.info/transport_info | `upsert_device_from_agent` → Device + DeviceEndpoint + ConnectionSession + SiteEvent |
| `device.registered` (P1) | `device.discovered` | G1 补偿 — Agent 没有 discovered 事件 | 同 device.online → upsert |
| `device.frame` (P0) | `device.frame` | 直接转发 **但 type_code=4 RAWIQ 跳过** | `record_device_frame` → Measurement（RAWIQ 也在 ingestion 层跳过） |
| `device.state_change` (P0) | `device.state_change` | 转发 **但 to="discovered" 跳过** | 更新 Device.status |
| `device.removed` (P1) | `device.removed` | 直接转发 | connection.py L275 — V1 不做 DB 操作（预留） |
| `agent.started` (P1) | **跳过** | 靠 heartbeat 携带 devices count | — |
| `agent.error` (P1) | **跳过** | connector V1 没有 Site 端审计通道 | — |
| `device.stream_stats` (P2) | **跳过** | Site Server 不处理 | — |
| `device.line` (P2) | **跳过** | 原始 UART 行，Site Server 不需要 | — |
| `device.reconnect_scheduled` (P2) | **跳过** | Agent 内部事件 | — |
| `device.online`（来自 `devices_snapshot`） | `device.online` | WS 连接后一次性发送全量 | upsert（带 ConnectionSession replace） |

**代码引用**：映射逻辑 `_to_site_envelope()` L329-423，EventBus 订阅 L293-299。

---

## 八、Command request/response 完整流程

```
Site Server                              Gateway Agent SiteConnector
     │                                            │ DeviceManager Device
     │── command.request ─────────────────────────▶│
     │   {request_id, device_id, command}          │
     │                                            │── manager.get(device_id)
     │                                            │── device.send_command(command)
     │                                            │   ├── is_command_allowed() ✅
     │                                            │   ├── state != OFFLINE/ERROR ✅
     │                                            │   ├── transport.write(line)
     │                                            │   ├── read reply (timeout 1s)
     │                                            │   └── return (ok: bool, reply: str)
     │                                            │
     │◀── command.response ───────────────────────│
     │   {request_id, ok, reply[:500]}            │
```

**命令白名单**（`protocol/adapter.py` — `is_command_allowed(line)`）：

| 类型 | 示例 |
|------|------|
| 允许的精确命令 | `INFO`, `STATUS?`, `CONFIG?`, `METRICS?`, `GAIN?`, `REBOOT`, `SCAN START`, `SCAN STOP` |
| 允许的前缀命令 | `GAIN MANUAL 45`, `FILTER xxx`, `FFT xxx`, `SETTLE xxx`, `CHANNELS xxx` |
| 拒绝（不会发） | 任何不在白名单中的命令 |

**V1 限制**：Site Server 没有实现 command.request 的发送端（connection.py 里没有对应逻辑）；但 Connector 接收 + DeviceManager 执行 + command.response 回包全部就绪，可随时对接。

---

## 九、错误码 / 状态码

### 9.1 WebSocket Close Code

| Code | 含义 | 触发端 | 代码位置 |
|------|------|--------|----------|
| `4401` | Agent 认证失败 | Site Server | connection.py L98, L107 |
| `4500` | Site Server DB/内部错误 | Site Server | connection.py L114 |
| `1000` | 正常关闭 | 双方 | — |

### 9.2 HTTP 认证层错误

| 状态码 | 含义 | Gateway Agent 行为 |
|--------|------|-------------------|
| `401` / `4401`（InvalidStatusCode） | credential 无效 | `_set_state(ERROR, AUTH_FAILED)` + 清 credential + 停止重连 |
| 其他非 101 | 连接拒绝 | `_disconnect_reason = CONNECT_FAILED` 或 `SITE_UNAVAILABLE` |

### 9.3 command.response ok=false reply 前缀码

| reply 内容前缀 | 含义 |
|---------------|------|
| `connector has no device manager` | Connector 构造时没传 manager |
| `device {id} not found` | DeviceManager 查不到该 device_id |
| `device {id} state={state}` | 设备 OFFLINE/ERROR 状态不能下发命令 |
| `command not allowed: {cmd}` | `is_command_allowed()` 返回 False |
| `execution error: {exc}` | 命令执行抛异常 |

### 9.4 diagnostics 中的错误统计

| 字段 | 含义 |
|------|------|
| `state=ERROR` | Auth 失败停止重连 |
| `reconnect_attempts > 0` | 重连中 |
| `dropped_events > 0` | 断线期间丢弃的 EventBus 事件数 |
| `last_heartbeat_ack` 远小于 `time.monotonic()` | ack 超时风险 |

---

## 十、完整消息序列表（按类型）

```
双向 JSON frame text：
  {
    "type": "<string>",
    ...其他字段...
  }

Agent → Site（4 种）：
  1. heartbeat             (每15s)
  2. agent.event           (EventBus 事件转发)
  3. agent.devices_snapshot (WS 连接后一次性)
  4. command.response      (命令回包)

Site → Agent（3 种）：
  1. connected             (握手成功，一次性)
  2. heartbeat.ack         (每30s)
  3. command.request       (命令下发，V1 Site 侧未实现发送)

Site → Agent Close Code：
  4401 = 认证失败
  4500 = 内部错误
  1000 = 正常关闭
```

---

## 十一、协议版本兼容性

| 项目 | 值 | 说明 |
|------|-----|------|
| Gateway Agent User-Agent | `WIDSCAN58-Agent/0.6.0` | connector L508 |
| Site Server 路由前缀 | `/ws/v1/agents/connect` | connection.py L4 |
| 协议版本标识 | **未实现** | V1 没有 version 字段，靠路由前缀 `/v1/` 区分 |
| 前向兼容策略 | Site Server 忽略未知 message type；Agent 忽略未知 `connected` 外的 Site type | connector L660 `ignoring Site Server message type=%s` |

---

*文档结束。下一篇：[24-site-connector-testing.md](./24-site-connector-testing.md)*
