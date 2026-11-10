# 21 — Site Connector 契约审计（真实代码逐文件核对）

> **严禁假设**：所有接口均来自 Gateway Agent V1 和 Site Server V1 的实际代码行号。发现不一致时优先兼容已实现的一侧。
> 审计时间：2026-11-04，Gateway Agent 47/47 pytest 通过，Site Server 14 REST + Agent Reverse WS 全部 smoke 通过。

---

## 一、Gateway Agent 实际能力（已实现）

### 1.1 事件总线 `events/bus.py`

```python
@dataclass
class Event:
    type: str                        # "device.online", "agent.started", ...
    data: Dict[str, Any] = {}
    device_id: Optional[str] = None
    ts: float = 0.0                  # monotonic time

# EventBus 是同步 pub/sub（故意不用 asyncio.Queue）
# Connector 需要用 threading 桥接到自己的 asyncio loop
```

### 1.2 EventBus 事件类型清单（**实际发布的全部**）

| # | 事件 type | 发布位置 | device_id | data keys |
|---|---|---|---|---|
| 1 | `agent.started` | manager:51 | ❌ | `devices: int` |
| 2 | `agent.stopped` | manager:77 | ❌ | — |
| 3 | `agent.error` | manager:139,150 | ❌ | `phase: str`, `error: str` |
| 4 | `device.registered` | manager:99 | ✅ | `transport: {kind, label, path, ...}` |
| 5 | `device.online` | device:323 | ✅ | `info: dict`, `transport: {kind, label, path}` |
| 6 | `device.state_change` | device:137 | ✅ | `from: str`, `to: str`, `error: str?`, `reconnect_attempt: int` |
| 7 | `device.frame` | device:385 | ✅ | `type_code: int`, `decoded: dict` |
| 8 | `device.stream_stats` | device:368 | ✅ | `bytes_in`, `crc_errors`, `resync_bytes`, `frames_in` |
| 9 | `device.line` | device:397 | ✅ | `line: str` |
| 10 | `device.reconnect_scheduled` | device:414 | ✅ | `attempt: int`, `delay: float` |
| 11 | `device.removed` | manager:114 | ✅ | — |

### 1.3 DeviceState 枚举（真实值）

```python
class DeviceState(str, enum.Enum):
    DISCOVERED = "discovered"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    ONLINE = "online"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    IDENTITY_CONFLICT = "identity_conflict"
```

### 1.4 Device.info 结构（来自 parse_info_line）

```python
# device.py:321 — self._info = parsed
# 典型字段（固件 INFO widscan58|v1.0.0|...）
{
    "firmware_version": "v1.0.0",
    "hardware_model": "esp32c5",
    "protocol_version": 1,
    "phy_version": "esp32c5-phy-1.2",
    "capabilities": {"rawiq": True, "spectrum": True, "metrics": True},
    # ... 取决于固件实现
}
```

### 1.5 TransportInfo 结构

```python
@dataclass
class TransportInfo:
    kind: str                    # "usb_serial", "tcp", "simulator"
    label: str                   # "/dev/ttyACM0" 或 "sim:demo"
    path: str = ""               # 物理定位键
    device_id: Optional[str] = None
    extra: Dict[str, Any] = {}
```

### 1.6 命令白名单（`protocol/adapter.py`）

```python
ALLOWED_SINGLE = {
    "INFO", "PROTOCOL?", "PROTOCOL MONITOR", "PROTOCOL RH",
    "STATUS?", "CONFIG?", "CONFIG SAVE", "CONFIG RESET",
    "METRICS?", "GAIN?", "GAIN HARDWARE", "MODE GROUP", "MODE SINGLE",
    "SCAN START", "SCAN STOP", "REBOOT", "BOOTLOADER",
    "ENABLE?", "PROFILE?", "TIMING?", "SPURS?", "SPURS CLEAR",
    "FFTTYPE?", "LEVELFILTER?", "PHYIDLE?", "GPIO?", "GPIO SAVE",
    "CHANNELS DEFAULT",
}

ALLOWED_PREFIXES = (
    "GAIN MANUAL", "FILTER", "FFT", "SEGMENTS", "CAPN", "RATE",
    "SETTLE", "IQCORR", "SPEC", "RAWIQ", "WIN", "EMA", "AVG",
    "THROTTLE", "PHYIDLE", "TUNESKIP", "REGWATCH", "SPURS ADD",
    "SPURS GUARD", "FFTEVAL SYNTH", "FFTEVAL LIVE", "FFTTYPE",
    "LEVELFILTER", "GPIO", "CHANNELS", "ENABLE", "MODE", "METRICS",
)

def is_command_allowed(line: str) -> bool: ...

# Device.send_command(line) -> tuple[bool, str]  # (ok, reply)
```

### 1.7 Agent 配置结构（`config.py`）

```python
class AgentConfig(BaseModel):
    id: str = "gateway-001"        # ← Connector 用这个作为初始 agent_id
    log_level: str = "INFO"

class GatewayConfig(BaseModel):
    agent: AgentConfig
    api: ApiConfig                    # host, port
    discovery: DiscoveryConfig         # usb_enabled, poll_interval
    transport: SerialConfig            # baudrate, read_timeout
    reconnect: ReconnectConfig         # initial_delay, max_delay, multiplier
    stream: StreamConfig               # queue_size, rawiq_enabled
    # ⚠️ 没有 site 相关配置 — 需要新增 SiteConfig
```

### 1.8 Agent 启动流程（`main.py:run()`）

```python
def run(cfg, host, port, simulator_count):
    cfg = cfg or GatewayConfig()
    bus = EventBus()
    manager = make_manager(bus, cfg)
    # inject simulators...
    manager.start()

    app = build_app(manager=manager, bus=bus, agent_id=cfg.agent.id, ...)

    # ← Site Connector 应该在这里初始化，传入 bus + cfg + manager
    # ← Connector.start() 在 manager.start() 之后、uvicorn.run() 之前

    uvicorn.run(app, host=host, port=port, ...)
    # finally: manager.stop() + connector.stop()
```

### 1.9 Device 命令通道

```python
# Device.send_command(line) — 完整的命令执行通道
# 1. is_command_allowed(line) 白名单校验
# 2. transport.is_connected() 检查
# 3. transport.write(line + "\n")
# 4. 读回复（等 ASCII 行，timeout=1s）
# 5. return (ok: bool, reply: str)

# Site Connector 必须通过 DeviceManager.get(device_id).send_command() 下发命令
# 不得直接写 transport
```

---

## 二、Site Server 实际 WS 协议（已实现）

### 2.1 Agent 连接 URL

```
GET ws://site-server:PORT/ws/v1/agents/connect
  ?agent_id=<uuid>
  &credential=<plain_text_credential>    # ← Agent 侧存的明文 credential
```

### 2.2 认证流程（`auth/__init__.py`）

```python
# Agent pairing
# POST /api/v1/agents/pairing-codes  {ttl_seconds: 600} → {pairing_code: "ABCDEFGH", plain_credential: "...", expires_at}
# POST /api/v1/agents/pair           {pairing_code: "ABCDEFGH", agent_version, platform, ...} → {agent_id, credential_id, expires_at}

# Agent WS connect 时 credential 校验
# verify_credential(session, agent_id, credential_plain):
#   credential_hash = sha256_hex(credential_plain)
#   → 查 AgentCredential.credential_hash
#   → 检查 agent_id 匹配、revoked_at is None、expires_at > now
```

### 2.3 Site Server ↔ Agent 消息协议（`connection.py`）

#### Agent → Site Server

| type | 必填字段 | 说明 |
|---|---|---|
| `heartbeat` | agent_id, uptime, devices? | Agent 每 15s 发 |
| `agent.event` | event: {type, device_id, data} | EventBus 事件转发 |
| `agent.devices_snapshot` | devices: [{type, device_id, data}] | Agent 启动/重连后发全量 |
| `command.response` | request_id, ok, reply | Agent 回复 Site 下发命令 |

#### Site Server → Agent

| type | 必填字段 | 说明 |
|---|---|---|
| `connected` | agent_id, site_id, server_time | 握手成功 |
| `heartbeat.ack` | — | Site 每 30s 发一次确认心跳 |
| `command.request` | request_id, device_id, command | Site 下发命令 — **Site Server 代码里没有发送实现，但 Connector 必须能接收** |

### 2.4 事件协议细节（`connection.py:_process_agent_event`）

```python
# Site Server 期望 agent.event.event 结构：
{
    "type": "device.online" | "device.discovered" | "device.frame" | "device.state_change" | "device.removed",
    "device_id": "c5-v1.0.0-abcd",
    "data": {...}
}

# 各事件 data 结构：

# device.online / device.discovered
{
    "info": {
        "firmware_version": "v1.0.0",
        "hardware_model": "esp32c5",
        "phy_version": "esp32c5-phy-1.2",
        "protocol_version": 1,
        "capabilities": {"rawiq": True, ...},
        "identity_confidence": "high",  # 可选
    },
    "transport": {"kind": "usb_serial", "path": "/dev/ttyACM0"}
}

# device.frame
{
    "type_code": 1,        # 必须是整数
    "decoded": {...}       # Agent 解码后的 payload dict
}

# device.state_change
{
    "from": "online",
    "to": "error",
    "reason": "crc_fail"   # Site Server 不关心 reason，只关心 to 的值
}
```

### 2.5 Frame Type Code 映射（`ingestion/__init__.py`）

```python
FRAME_TYPE_MAP = {
    1: "METRICS",
    2: "SPECTRUM",
    3: "TELEMETRY",
    4: "RAWIQ",      # 默认丢弃
    5: "LOG",
    6: "LEVELS",
}

# ✅ 和固件定义一致（frames.h + simulator.py）
```

### 2.6 DeviceState → Site Device.status 映射（`connection.py`）

```python
def _agent_state_to_site_status(agent_state: str) -> str:
    mapping = {
        "online": "online", "connected": "online",
        "disconnected": "offline",
        "error": "error", "reconnecting": "offline",
        "unsupported": "unsupported",
        "identity_conflict": "identity_conflict",
    }
    return mapping.get(agent_state, "")
```

---

## 三、接口对齐分析（Gap 表）

| # | Site Server 期望 | Gateway Agent 提供 | Gap 类型 | Connector 处理 |
|---|---|---|---|---|
| G1 | `device.discovered` 事件 | Agent manager 只发 `device.registered`，Device 发 `device.online` | ⚠️ 缺事件 | `device.registered` + DeviceState.DISCOVERED→`device.discovered` 合成 |
| G2 | `transport.path` 或 `transport.label` | TransportInfo 有 kind/path/label | ✅ 兼容 | 用 `transport.path`，空则 fallback `transport.label` |
| G3 | `info.firmware_version` / `hardware_model` / `phy_version` / `capabilities` | Device.info 有这些字段 | ✅ 对齐 | 直接取 device.info |
| G4 | `device.frame.type_code: int` | Device event.data 有 type_code(int) | ✅ 对齐 | 直接取 |
| G5 | `device.frame.decoded: dict` | Device event.data 有 decoded(dict) | ✅ 对齐 | 直接取 |
| G6 | Agent→Site `agent.devices_snapshot` | Agent 没有 snapshot 概念 | ⚠️ 缺功能 | Connector 在 WS connect 后发一次全量 device.online |
| G7 | Site→Agent `command.request`（文档） | Site Server 代码里没发送端 | ⚠️ 半实现 | Connector 必须能接收；Site Server 后续加发送端点 |
| G8 | Credential SHA256 校验 | Agent 必须存明文 credential 并在 WS URL 传 | ✅ 协议一致 | Agent 存明文，URL query 传递 |
| G9 | Site Server heartbeat ack | Agent 现有心跳机制独立 | ✅ 独立 | Connector 自己管心跳 |
| G10 | RAWIQ 默认不持久化/转发 | Agent stream 默认 rawiq_enabled=False | ✅ 对齐 | Connector 跳过 RAWIQ frame |

---

## 四、Device 身份与 Transport 映射关系

```
Agent device_id 格式：  c5-{fw_version}-{abs(hash(transport_path)) % 10000:04x}
示例：                  c5-v1.0.0-abcd

Agent TransportInfo → Site transport 映射：
  TransportInfo.kind       → Site transport.kind
  TransportInfo.path       → Site transport.path
  TransportInfo.label      → Site transport.path (如果 path 为空)

Agent Device.info → Site info 映射：
  info["firmware_version"] → Site info.firmware_version     ✅ 字段名对齐
  info["hardware_model"]   → Site info.hardware_model       ✅ 字段名对齐
  info["phy_version"]      → Site info.phy_version           ✅ 字段名对齐
  info["protocol_version"] → Site info.protocol_version     ✅ 字段名对齐
  info["capabilities"]    → Site info.capabilities          ✅ 字段名对齐
```

---

## 五、Connector 需要订阅的 EventBus 事件优先级

| 优先级 | Agent EventBus 事件 | → Site 消息类型 | 重要性 |
|---|---|---|---|
| P0 | `device.online` | `device.online` | 🔴 核心 — 设备注册 |
| P0 | `device.frame` | `device.frame` | 🔴 核心 — 数据摄入 |
| P0 | `device.state_change` | `device.state_change` | 🔴 核心 — 状态同步 |
| P1 | `device.registered` | `device.discovered` | 🟡 GAP 补偿 |
| P1 | `agent.started` | heartbeat（携带 devices count） | 🟡 |
| P1 | `agent.error` | SiteEvent 审计 | 🟡 |
| P2 | `device.stream_stats` | 丢弃（Site Server 不处理） | 🟢 |
| P2 | `device.line` | 丢弃（不转发） | 🟢 |
| P2 | `device.reconnect_scheduled` | 丢弃 | 🟢 |
| P2 | `device.removed` | `device.removed` | 🟡 边界 |

---

## 六、Connector → Site Server 出站事件合成表

```python
# Agent EventBus Event → Site Server agent.event envelope
def to_site_envelope(bus_event: Event, agent: Device) -> Optional[dict]:
    """返回 None = 跳过不转发的事件（如 RAWIQ frame, device.line 等）"""
    
    if bus_event.type == "device.online":
        return {
            "type": "agent.event",
            "event": {
                "type": "device.online",
                "device_id": bus_event.device_id,
                "data": {
                    "info": agent.info,   # Device.info dict
                    "transport": {
                        "kind": agent.transport_info.kind,
                        "path": agent.transport_info.path or agent.transport_info.label,
                    },
                },
            },
        }
    
    if bus_event.type == "device.frame":
        type_code = bus_event.data.get("type_code", 0)
        # 跳过 RAWIQ
        if type_code == 4:
            return None
        return {
            "type": "agent.event",
            "event": {
                "type": "device.frame",
                "device_id": bus_event.device_id,
                "data": {
                    "type_code": type_code,
                    "decoded": bus_event.data.get("decoded") or {},
                },
            },
        }
    
    if bus_event.type == "device.state_change":
        to_state = bus_event.data.get("to", "")
        if to_state == "discovered":
            return None  # 不要重复发 discovered
        return {
            "type": "agent.event",
            "event": {
                "type": "device.state_change",
                "device_id": bus_event.device_id,
                "data": {
                    "from": bus_event.data.get("from"),
                    "to": to_state,
                    "error": bus_event.data.get("error"),
                },
            },
        }
    
    if bus_event.type == "device.registered":
        # G1 补偿：manager 发 registered + Device 状态 = DISCOVERED → discovered
        return {
            "type": "agent.event",
            "event": {
                "type": "device.discovered",
                "device_id": bus_event.device_id,
                "data": {
                    "info": {},  # 还没握手，info 可能空
                    "transport": bus_event.data.get("transport") or {},
                },
            },
        }
    
    if bus_event.type == "agent.started":
        return None  # 通过 heartbeat 携带
    
    # ... 其他跳过
```

---

## 七、禁止事项重申

1. ❌ 不改 C5 PHY/DSP/二进制协议
2. ❌ 不重写 Gateway Agent（47/47 pytest 已通过）
3. ❌ 不重写 Site Server（14 REST + WS 已通过）
4. ❌ 不改 device_id 合成规则
5. ❌ Agent 不直接写 Site Server 数据库
6. ❌ RAWIQ 默认不转发/持久化
7. ❌ Connector 不可用时 Agent 本地功能不能退化
8. ❌ 不 git reset/clean
9. ❌ 不自动 commit/push

---

## 八、已确认的兼容性结论

1. **Agent ↔ Site Server WS 协议** ✅ 完全兼容 — URL query credential, heartbeat, agent.event envelope 结构全部对齐
2. **帧类型码** ✅ 固件 → Agent → Site Server 三级一致：METRICS=1, SPECTRUM=2, TELEMETRY=3, RAWIQ=4, LOG=5, LEVELS=6
3. **Device info 字段名** ✅ Agent Device.info dict 和 Site Server upsert_device_from_agent 期望的 key 完全匹配
4. **Transport 映射** ✅ Agent TransportInfo.kind/path → Site transport.kind/path 对齐
5. **命令执行通道** ✅ Device.send_command() 完整，Connector 复用 — 不绕开 Agent 核心

**唯一需要 Connector 层做的合成/转换**：
- `device.registered` → `device.discovered`（G1 补偿）
- WS connect 后发送 `agent.devices_snapshot`（全量 device.online 批量）
- RAWIQ frame 过滤（G10 显式执行）
