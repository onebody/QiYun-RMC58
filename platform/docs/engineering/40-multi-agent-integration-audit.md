# Multi-Node Integration V1 — 多 Agent / 多 Device 集成架构审计

**审计日期**: 2026-11-04  
**审计范围**: Gateway Agent + Site Server + SQLite 真实 schema + 2 Agent × 同一 device_id 真实联调  
**审计方法**: Schema introspection + 真实 WS 连接（2 Agent 顺序连接同一 Site Server）+ 代码阅读

---

## 1. 架构全景

```
                    ┌─────────────────────┐
                    │      Site Server     │
                    │  (FastAPI + WS)      │
                    └──────┬──────┬───────┘
                           │      │
              ┌────────────┘      └────────────┐
              ↓                                  ↓
     ┌─────────────────┐              ┌─────────────────┐
     │  Gateway Agent0 │              │  Gateway Agent1 │
     │  (threading)    │              │  (threading)    │
     │  ┌──┐ ┌──┐ ┌──┐ │              │  ┌──┐ ┌──┐ ┌──┐ │
     │  │C5│ │C5│ │C5│ │              │  │C5│ │C5│ │C5│ │
     │  └──┘ └──┘ └──┘ │              │  └──┘ └──┘ └──┘ │
     └─────────────────┘              └─────────────────┘
```

### Agent 身份

- **agent_id**: Config 配置 / 首次配对生成，UUID 格式（如 `agent0-70f70091`）
- **Credential**: 配对后获得，SHA256 hash 存 Site Server（V1.1 首条消息认证，不带 URL）
- **Agent→Site**: 一个 Agent 一条反向 WS 连接

### Device 身份

- **device_id**: Agent 本地合成 `c5-{fw_version}-{abs(hash(path)) % 10000:04x}`
- **关键约束**: device_id 在同一 Site 内唯一（Site Server Device 表主键）
- **风险**: 不同 Agent 连接同一 Site 时，**物理上不同但 hash 碰撞的 Device 会命中同一 Device 记录**
  - 当前 V1 假设：每个 Site 由一个 Agent 管辖 → 碰撞概率极低
  - 多 Agent 部署后：需要确保 **device_id 在 Agent 间协调不碰撞**

---

## 2. SQLite Schema 审计（真实 DB，2 Agent 联调）

### 2.1 devices（15 cols）

```
cols:
  id               VARCHAR(64)  PK       ← device_id 全局唯一
  site_id          VARCHAR(64)  NOT NULL  ← FK → sites
  display_name     VARCHAR(255)
  hardware_model   VARCHAR(128)
  firmware_version VARCHAR(64)
  protocol_version INTEGER
  phy_version      VARCHAR(128)
  capabilities     JSON
  status           VARCHAR(32)           ← pending | online | offline | error
  identity_confidence VARCHAR(16)
  first_seen_at    DATETIME
  last_seen_at     DATETIME
  adopted_at       DATETIME              ← 认领标记，非空即已认领
  created_at       DATETIME     NOT NULL
  updated_at       DATETIME     NOT NULL

indexes:
  ix_devices_site_status  [site_id, status]
  ix_devices_site_id      [site_id]       ← FK auto-index

unique:
  (id) — 全局唯一 device_id，不是 (site_id, device_id) 联合唯一
```

**🔴 GAP-1: Device 表无 owner_agent_id**
- Device 不知道自己"主要归属"哪个 Agent
- 归属完全靠 ConnectionSession 桥接
- 多 Agent 同时上报同一 device_id 时，谁最后上报谁接管 session

**🟡 GAP-2: Device.id 全局唯一**
- 不能 (site_id, device_id) 联合唯一
- 同一 device_id 不能出现在两个不同 Site
- 当前设计假设 device_id 全局稳定（符合 widscan58 固件身份模型）

---

### 2.2 connection_sessions（9 cols）

```
cols:
  id               VARCHAR(64)  PK
  device_id        VARCHAR(64)  NOT NULL  ← FK → devices
  agent_id         VARCHAR(64)  NOT NULL  ← FK → agents
  boot_id          VARCHAR(64)            ← 固件未提供 = NULL
  connected_at     DATETIME     NOT NULL
  disconnected_at  DATETIME
  disconnect_reason VARCHAR(255)
  reconnect_count  INTEGER
  status           VARCHAR(32)            ← connected | disconnected | error

indexes:
  ix_sessions_device_connected  [device_id, status]
  ix_connection_sessions_agent_id  [agent_id]
  ix_connection_sessions_device_id  [device_id]

unique:
  (id) — 无额外 unique 约束
```

**🟡 GAP-3: 无 (device_id, agent_id, status='connected') unique 约束**
- 代码层 upsert 已修（先 close 旧 connected session 再新建）
- DB 层仍可并发插入两条 connected session → MultipleResultsFound
- 修复: 加 partial unique index `UNIQUE(device_id, agent_id) WHERE status='connected'`

---

### 2.3 measurements（13 cols）

```
cols:
  id               INTEGER      PK AUTOINCREMENT
  site_id          VARCHAR(64)  NOT NULL  ← FK → sites
  device_id        VARCHAR(64)  NOT NULL  ← FK → devices
  session_id       VARCHAR(64)            ← FK → connection_sessions（可选）
  frame_type       VARCHAR(32)  NOT NULL  ← METRICS | SPECTRUM | TELEMETRY | LEVELS
  frame_type_code  INTEGER
  device_timestamp_ms INTEGER             ← 固件原始 t_ms
  agent_received_at DATETIME              ← Agent 接到设备数据时间
  sequence         INTEGER
  source           VARCHAR(32)
  payload_json     JSON
  event_id         VARCHAR(128)           ← 幂等键
  created_at       DATETIME     NOT NULL

indexes:
  ix_measurements_device_time  [device_id, created_at]
  ix_measurements_site_time    [site_id, created_at]
  ix_measurements_site_frame   [site_id, frame_type]

unique:
  uq_measurement_event  (site_id, event_id)  ← 幂等去重
```

**🟡 GAP-4: Measurement 无 agent_id 列**
- 归属通过 session_id 间接查询 ConnectionSession.agent_id
- 查询多 Agent measurement 归属需要 JOIN
- 修复（可选）: 加 agent_id 列

**🔴 GAP-5: event_id 永远是 uuid.uuid4()**
- `ingestion/__init__.py:L70`: `event_id = str(uuid.uuid4())`
- 幂等约束 `(site_id, event_id)` 实际上永远不触发
- 同一帧重复上报 → 不会被去重
- 修复: event_id = f"{device_id}-{sequence}-{device_timestamp_ms}-{frame_type_code}"

---

### 2.4 device_endpoints（8 cols）

```
unique: (device_id, agent_id, transport_type, endpoint_identifier)
```
✅ 这个设计正确 — 同一 Agent 对同一 Device 同一传输同一端点只一条记录。

---

### 2.5 agents（12 cols）

```
indexes: ix_agents_site_status [site_id, status]
unique:  (id) — 无 (site_id, id) 联合（但 id 本身就是 site-scope）
```

✅ 同一 Site 可以注册多个 Agent。同一 Site 不会有两个相同 agent_id（因为是 UUID）。

---

## 3. 代码审计

### 3.1 Agent 上报 Device.online → Device 状态永远 pending 🔴

**位置**: `site-server/src/widscan_site/devices/__init__.py`

**新建 Device（L44-59）**:
```python
dev = Device(..., status="pending", ...)  # L54 硬编码 pending
```

**Device 已存在时的状态更新（L73-74）**:
```python
if dev.adopted_at is not None and dev.status in ("offline", "error", "reconnecting"):
    dev.status = "online"
# 注意: pending 永远不会被置 online！因为 if 条件里没有 pending
# 注意: 没有 adopted_at 的 Device，adopted_at=None，整个 if 都不进！
```

**结论**:
1. Agent 首次上报 device.online → Device 新建 → status=pending
2. Agent 重连再上报 → Device 存在 → adopted_at=None → if 条件不满足 → 状态不变 → 仍是 pending
3. 只有 Admin 通过 adopt_device() 认领后 → Device.status = "online" + adopted_at = now
4. 之后 Agent 再断线再上线 → adopted_at 非空 → if 条件满足 → online → offline → online 正确切换

**修复方案**: upsert_device_from_agent 中：
- Device 新建 → status="online"（Agent 上报 online 就是 online）
- Device 已存在且不是 adopted → status="online"（重新上线）
- Device 已存在且 adopted → 保持 adopted 状态不变

### 3.2 Agent 断开 → Device 离线同步 ✅

**位置**: `site-server/src/widscan_site/agents/connection.py:L202-211`

```python
finally:
    registry.unregister(agent_id)
    with get_session() as sess:
        mark_devices_offline_by_agent(sess, agent_id)  # ✅ 调用了
        update_agent(sess, agent_id, status="offline")
```

**mark_devices_offline_by_agent（devices/__init__.py:L202-244）**:
1. 把该 Agent 下所有 DeviceEndpoint 置 disconnected
2. 把该 Agent 下所有 connected ConnectionSession 置 disconnected
3. 对该 Agent 管辖的所有 Device，检查是否还有其他 Agent 连着：
   - 有其他 Agent 连着 → 不改 offline（多 Agent 容错 ✅）
   - 没有其他 Agent 连着 → 置 offline（仅 online/reconnecting 覆盖，pending 不动）

**多 Agent 容错设计**: ✅ 正确。Agent0 带 Device X online，Agent1 也上报 Device X（endpoint 不同）→ 两个 Agent 都有 DeviceEndpoint。Agent0 断开 → Device X 还有 Agent1 的 endpoint connected → 不置 offline。

**但有个问题**: 如果 Device.status 是 pending（因为 BUG-1），mark_devices_offline 只覆盖 online/reconnecting → pending 永远 pending。

### 3.3 ConnectionSession upsert 已修复 ✅

**位置**: `devices/__init__.py:L102-120`

先 UPDATE 关闭旧 connected session，再 INSERT 新的。已修 MultipleResultsFound。

### 3.4 V1.1 首条消息 auth ✅

**位置**: `agents/connection.py:L88-128`（已改）

- URL 无 credential → 等待首条 auth 消息（5s timeout）
- 首条不是 auth → 拒绝
- 首条是 auth → 校验 credential → 允许业务消息
- URL 有 credential（V1.0 兼容）→ 直接走 V1.0 路径

### 3.5 Gateway Agent 每 Agent 多 Device ✅

**DeviceManager**: `add_device(device, device_id)` 支持 N 设备
**EventBus**: 每事件携带 device_id → Connector 正确隔离
**SiteConnector**: 单一 Connector 绑定单一 agent_id，转发所有 device 的事件

---

## 4. 多 Agent × 同一 device_id 真实联调结果

**测试脚本**: 2 Agent 顺序连接同一 Site Server，都上报同一 device_id

```
Device 表:
  id=c5-1.0.0-aaaa site=site-001 status=pending  ← 🔴 预期 online，实际 pending

ConnectionSession 表:
  sess=1da006be-82f device=c5-1.0.0-aaaa agent=agent0-99b63 status=disconnected
  sess=8ec71d66-ce8 device=c5-1.0.0-aaaa agent=agent1-2abc7 status=disconnected
  ← 两个 Agent 各建了一条 session，顺序断开（正常）

DeviceEndpoint 表:
  ep=15008150-5e8 device=c5-1.0.0-aaaa agent=agent0-99b63 path=/dev/p001
  ep=82646d35-15b device=c5-1.0.0-aaaa agent=agent1-2abc7 path=/dev/p002
  ← 正确！同一 device 被两个 Agent 认领，不同 endpoint
```

---

## 5. 问题汇总 + 修复优先级

| # | 严重度 | 类型 | 位置 | 描述 | 修复 |
|---|---|---|---|---|---|
| **BUG-1** | 🔴 CRITICAL | 逻辑 BUG | devices/__init__.py:L54, L73-74 | Agent 上报 online → Device 永远 pending | upsert_device: dev 新建或非 adopted → status="online" |
| **GAP-1** | 🟡 MEDIUM | Schema | models.py Device 表 | 无 owner_agent_id | 加 owner_agent_id 列 + upsert 时填入 |
| **GAP-2** | 🟡 MEDIUM | Schema | models.py Device.id | 全局唯一 device_id | 接受当前设计（符合固件身份模型） |
| **GAP-3** | 🟡 MEDIUM | Schema | ConnectionSession 表 | 无 partial unique | 代码层已修；加 DB partial unique index |
| **GAP-4** | 🟡 MEDIUM | Schema | Measurement 表 | 无 agent_id 列 | 可选：加列 + 每次写入填 agent_id |
| **GAP-5** | 🟡 MEDIUM | 逻辑 | ingestion/__init__.py:L70 | event_id 是 uuid → 幂等失效 | 用 device_id+sequence+timestamp 构造 event_id |

---

## 6. 修复计划

1. **立即修复 BUG-1**: upsert_device_from_agent 中 pending → online 路径
2. **修复 GAP-5**: Measurement.event_id 构造改为基于业务字段
3. **修复 GAP-1**: Device 表加 owner_agent_id（先代码层逻辑，DB 迁移后续）
4. **修复 GAP-3**: ConnectionSession 加 partial unique index（可选，代码已防）
