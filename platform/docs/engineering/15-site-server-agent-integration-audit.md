# 15. Site Server ↔ Gateway Agent 集成审计

> 日期：2026-11-04
> 审计对象：`gateway-agent/src/widscan_agent/`（Git 分支 `fw-wifi-relay` 未触碰，所有审计阅读的是已交付代码）
> 审计目标：明确 Site Server V1 与 Gateway Agent V1 的边界、可复用接口、缺口。

---

## 1. Agent 实际支持的功能清单

| 模块 | 已实现 | 备注 |
|---|---|---|
| **Transport 层** | `Transport` ABC + `UsbSerialTransport` + `TcpTransport` + `SimulatorTransport` | Transport 纯同步，上层负责包 asyncio；pyserial 薄封装；Simulator 生成真实 CRC 帧 |
| **USB 发现** | `list_usb_serial_candidates()` + `handshake_once()` | 阻塞 handshake：PROTOCOL MONITOR → INFO；结果 `HandshakeResult{ok, path, device_id, transport_info, duration_ms}` |
| **协议解析** | import `rftrack.backend.app.services.frame_parser.FrameStream` | 三种路径自动定位（sys.path → 相对 repo → env var）；**不在 Agent 内部重写** |
| **设备状态机** | 9 状态（DISCOVERED/CONNECTING/HANDSHAKING/ONLINE/DISCONNECTED/RECONNECTING/ERROR/UNSUPPORTED/IDENTITY_CONFLICT） | 每个设备一个 daemon 线程；exponential backoff `min(initial × multiplierⁿ, max)` |
| **EventBus** | 线程安全 pub/sub + history(500) + wildcard + handler 异常隔离 | Event 结构 `{type, data, device_id, ts}` |
| **REST API** | 9 端点：`/health` + `/api/v1/{agent,devices,devices/{id},devices/{id}/...(connect,disconnect,reconnect,commands),diagnostics}` | `commands` 走白名单校验（ALLOWED_SINGLE + ALLOWED_PREFIXES） |
| **WebSocket** | `/ws/v1/events` | 单向事件流；订阅 `EventBus("*")`；默认丢弃 RAWIQ(type_code=4)；每客户端 maxlen=2048 |
| **DeviceManager** | 设备 registry + USB 轮询 + 手动注入 | `add_device(transport, device_id)` / `remove_device(device_id)` / `list_devices()` / `get(device_id)` |
| **配置** | YAML + Pydantic v2，6 段（agent/api/discovery/transport/reconnect/stream） | 默认值完整；`api.host` 硬限 127.0.0.1 |
| **Simulator** | `SimulatorTransport` | 60Hz METRICS / 2Hz TELEMETRY / 0.5Hz SPECTRUM；INFO/GAIN/STATUS 回复硬编码 |
| **CLI** | argparse `widscan-gateway --config --host --port --simulators N` | 可独立启动 |
| **测试** | 47 pytest 全通过 | 覆盖 simulator/transports/events/device/manager/api/config |

---

## 2. Site Server 可直接复用的 Agent 接口

### 2.1 本机 Agent 场景（同机部署）

Site Server 可以直接 HTTP 调用 Agent：

| Agent 端点 | Site Server 用途 |
|---|---|
| `GET /health` | Agent 健康检查 |
| `GET /api/v1/agent` | Agent 版本/配置上报 |
| `GET /api/v1/devices` | 设备发现轮询 |
| `GET /api/v1/devices/{id}` | 单设备详情 |
| `POST /api/v1/devices/{id}/commands` | 下发允许的设备命令 |
| `GET /api/v1/diagnostics` | Agent 运行诊断（bytes_in, crc_errors, event counts） |
| `WS /ws/v1/events` | 订阅所有事件（device.frame / device.online / device.state_change / ...） |

### 2.2 Agent 事件模型（Site Server 需要消费的）

| Event type | 触发时机 | 核心字段 |
|---|---|---|
| `agent.started` | Agent 启动 | `devices` count |
| `agent.stopped` | Agent 停止 | — |
| `device.registered` | 设备被 DeviceManager 注册 | `transport.kind/path/extra` |
| `device.removed` | 设备被移除 | — |
| `device.online` | 设备进入 ONLINE | `info.{target,fw_version,commit_time,phy_version}` + `transport` |
| `device.state_change` | 每次状态转换 | `from/to/error/reconnect_attempt` |
| `device.frame` | 解码出一帧 | `type_code` + `decoded dict`（frame_parser.decode 输出） |
| `device.line` | 一行 ASCII 回复 | `line` 字符串 |
| `device.stream_stats` | 每次 read_loop 喂流 | `bytes_in, crc_errors, resync_bytes, frames_in` |
| `agent.error` | poll/discover 阶段异常 | `phase` + `error` |

### 2.3 Agent Simulator 作为 Site Server 数据源

Site Server 的集成测试可以直接调用：
```python
from widscan_agent.transports.simulator import SimulatorTransport
```
把 simulator 注入 Agent DeviceManager，Agent 就会像真机一样产生事件。

---

## 3. 尚未具备的接口（Site Server 必须补足的）

| 缺口 | 影响 | 修复策略 |
|---|---|---|
| Agent **没有反向连接能力**（只有 localhost HTTP server） | 不同主机的 Agent 不能被 Site Server 发现 | Agent 增加一个**最小 Site Connector**（独立模块，不改现有 API） |
| Agent 没有**持久化 device_id**（每次启动用 hash 生成） | Agent 重启后 device_id 可能变化 → Site Server 认不出老设备 | Agent 配置文件中持久化生成的 device_id（首次启动时写入 YAML） |
| Agent 没有**配对/凭证体系** | Site Server 无法鉴别 Agent 身份 | Site Connector 用 pairing_code 换 credential，Agent 持久化 credential 并在后续连接时带上来 |
| Agent WS 没有**认证**（纯 EventBus 广播） | 任何 localhost 进程都能订阅 Site Server 需要的事件 | Site Connector 专用通道，Agent 与 Site Server 之间独立协议 |
| Agent 没有**心跳机制** | Site Server 只能靠 DeviceManager 的 `last_seen_ts` 粗粒度判断 | Site Connector 加 15s 心跳 |
| Agent 没有**配置下发命令队列** | Site Server profile 下发需要 Site Connector 主动调用 Agent commands API | Site Connector 实现 command 透传 + 结果回推 |
| Agent **不记录 boot_id**（固件没提供） | ConnectionSession 无法关联固件启动 | Site Server 侧生成 `session_id`，不冒充固件 boot_id |

---

## 4. Agent 需要修改的最小内容（Site Connector）

Agent 现有 API/Manager/Device/Transport 全不动。只加一个新模块：

```
gateway-agent/src/widscan_agent/connector.py
```

**Site Connector 设计原则：**
1. Agent 启动后，读 config 里的 `site.url` 和 `site.credential`
2. 如果有，主动建立 WebSocket 长连接到 Site Server 的 `/ws/v1/agents/connect`
3. 如果没有，跳过（Agent 继续独立运行，现有 API 不受影响）
4. WS 协议用 JSON envelope（见下方 §5）
5. 连接断线自动重连（backoff 复用现有逻辑）
6. 把 `DeviceManager` 的 EventBus 事件**转发**给 Site Server（不影响本地 API/WS）
7. 接收 Site Server 命令 → 转调 `Device.send_command()` → 回推结果

**为什么改 Agent 最小：**
- 所有底层能力（DeviceManager、Device、Transport、EventBus、协议解析）Agent 已经具备
- Site Connector 只是消费 EventBus + 调用 Device API 的薄壳
- 不修改任何现有模块，测试不退化

---

## 5. Site Server ↔ Agent 通信协议（Agent Connector 专用）

### 5.1 连接协议

```text
Agent ──── WebSocket wss://site-server:PORT/ws/v1/agents/connect ──── Site Server
             首次连接带 ?agent_id=xxx&credential=yyy
```

### 5.2 事件 Envelope

```json
{
  "event_id": "agent-20261104T100000Z-001",
  "event_version": 1,
  "event_type": "device.frame",
  "agent_id": "agent-001",
  "device_id": "c5-sim-001-abc1",
  "agent_received_at": "2026-11-04T10:00:00.123Z",
  "device_timestamp": null,
  "source": "hardware",
  "payload": {
    "type_code": 1,
    "decoded": { "...": "..." }
  }
}
```

### 5.3 Site Server → Agent 命令

```json
{
  "type": "command.request",
  "request_id": "req-uuid-xxx",
  "device_id": "c5-sim-001-abc1",
  "command": "GAIN MANUAL 45"
}
```

Agent 回复：
```json
{
  "type": "command.response",
  "request_id": "req-uuid-xxx",
  "ok": true,
  "reply": "GAIN MANUAL OK",
  "device_id": "c5-sim-001-abc1"
}
```

### 5.4 心跳

```json
// Agent → Site（每 15s）
{ "type": "heartbeat", "agent_id": "agent-001", "uptime": 3600, "devices": 4 }

// Site → Agent（每 30s）
{ "type": "heartbeat.ack" }
```

### 5.5 连接鉴权

- 首次连接用 `pairing_code` 一次性换 `agent_credential`
- 后续连接持久化 credential 并在连接 URL 或 header 中带上
- Site Server 侧校验 credential → 查出 Agent → 绑定 site_id

---

## 6. 不做什么（防退化）

1. **不修改 Agent 现有 `/api/v1/*` REST 端点** — 保持本地独立运行
2. **不修改 Agent 现有 `/ws/v1/events`** — 保持 Web Console 兼容
3. **不修改 firmware 协议层** — Site Server 只消费 Agent 输出
4. **不把 Agent 内部 Python 类直接序列化** — 走上面的 JSON envelope
5. **不把 RFTrack 的 site→relay→gateway→node 模型复制进 Site Server** — V1 核心是 Site + Agent + Device

---

## 7. 决策速查表

| 问题 | 结论 |
|---|---|
| Agent 和 Site Server 能否在同一台主机？ | 可以，本机 HTTP + WS |
| 不同主机的 Agent 怎么接入？ | Agent Connector 反向 WebSocket 出站连接 |
| Agent 需要改多少？ | **只加一个 `connector.py` 模块**，不动现有代码 |
| device_id 用什么生成？ | Agent 首次启动生成 `c5-{fw_version}-{hash(path)}`，写入 config 持久化 |
| Agent 身份怎么认证？ | pairing_code 一次性换 agent_credential，持久化后带在连接里 |
| RAWIQ 怎么处理？ | Site Connector 默认丢弃，显式启用才允许转发 |
| 测试数据源 | 直接用 `SimulatorTransport` 注入 Agent DeviceManager |
