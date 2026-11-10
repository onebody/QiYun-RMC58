# Phase 07.3 — Measurement 时间戳完整性

> 状态：PASS
> 日期：2026-11-04

## 1. 原始问题（B-3）

`Measurement.agent_received_at` 在生产链路上恒为 `NULL`，无法追溯 Gateway Agent 接收事件的时间。

## 2. 根因

- `gateway-agent` 的 `device.frame` 事件未携带接收时间。
- `site_connector.py::_to_site_envelope()` 只转发 `type_code` + `decoded`。
- `site-server` 的 `agents/connection.py` 调用 `record_device_frame()` 时没有传 `agent_received_at`。
- `ingestion/__init__.py::persist_measurement()` 虽有参数，但上游永远传 `None`。
- `site_received_at` 实际是 `Measurement.created_at`（Site Server 入库时间），与 `agent_received_at` 语义未区分。

## 3. 修改文件

| 文件 | 作用 |
|---|---|
| `gateway-agent/src/widscan_agent/device.py` | 收到帧时生成 `agent_received_at`（UTC ISO 8601）并放入 `device.frame` 事件 |
| `gateway-agent/src/widscan_agent/identity.py` | 新增 `utc_now_iso()`、`coerce_sequence()`、`coerce_device_timestamp_ms()` |
| `gateway-agent/src/widscan_agent/site_connector.py` | Envelope 原样携带 `agent_received_at` / `device_timestamp_ms` / `sequence`，缺失时补齐一次 |
| `site-server/src/widscan_site/agents/connection.py` | 将 envelope 字段解析后传入 `record_device_frame()` |
| `site-server/src/widscan_site/ingestion/__init__.py` | `parse_agent_timestamp()`、参数透传、非法时间保持 NULL、`record_device_frame()` 签名扩展 |
| `site-server/tests/test_phase07_ingestion.py` | 时间语义 / 时区 / 缺失 / 历史记录测试 |
| `site-server/tests/test_phase07_integration.py` | 真实 WebSocket 端到端时间戳测试 |
| `gateway-agent/tests/test_event_identity.py` | Agent 侧时间戳生成与转发保持测试 |

## 4. 时间语义

| 字段 | 定义 | 生成位置 | 语义 |
|---|---|---|---|
| `device_timestamp_ms` | 设备产生事件的时间（设备本地计时，ms） | C5 帧 → `decoded["tMs"]` | 设备时间轴，可能为 NULL |
| `agent_received_at` | Gateway Agent 接收到事件的时间 | `Device._read_loop`（UTC ISO 8601） | 只在缺失时由 Connector 补齐一次；Site Server **不覆盖** |
| `site_received_at` | Site Server 接收到事件的时间 | `Measurement.created_at`（`_utcnow()`） | 入库权威时间，独立于 Agent 时间 |

三者独立保存，不互相冒充；`device_timestamp_ms` 使用设备 uptime 语义，不参与排序判定。

## 5. 实现细节

### 5.1 Agent 侧

`device.py` 解码后：

```python
"agent_received_at": utc_now_iso(),   # 例如 2026-11-04T02:00:00+00:00
"sequence": sequence,
"device_timestamp_ms": device_timestamp_ms,
```

时间统一 UTC（带 `+00:00`），避免无时区字符串。

### 5.2 Connector 转发

`site_connector.py::_to_site_envelope()`：

```python
agent_received_at = bus_event.data.get("agent_received_at") or utc_now_iso()
```

已有值**原样转发**；仅当第三方发布者缺失时补一次。不会覆盖 Site Server 需要的字段。

### 5.3 Site Server 入库

`connection.py` 解析 envelope：

```python
record_device_frame(..., agent_received_at=parse_agent_timestamp(event_data.get("agent_received_at")), ...)
```

`ingestion/__init__.py::parse_agent_timestamp()` 支持：

- `datetime`（naive → UTC）
- ISO 8601 字符串（`Z` 归一化为 `+00:00`；带偏移转换为 UTC）
- epoch 秒（int/float）
- 非法输入 → `None`，并计数 `invalid_agent_timestamp`，**绝不用 `created_at` 冒充**。

## 6. 数据模型 / 迁移

- 复用既有字段：`Measurement.agent_received_at`、`device_timestamp_ms`、`sequence`、`created_at`。
- **无新增 Alembic 迁移**（字段已在 `002_device_ownership_and_traceability.py` 提供）。
- 历史记录不回填：`agent_received_at` 保持 NULL。

## 7. 测试

```
cd gateway-agent && PYTHONPATH=src python3 -m pytest tests/test_event_identity.py -q
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_ingestion.py -q
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_integration.py -q
```

覆盖：

- Agent 收到事件时生成 `agent_received_at`（带 UTC 时区）。
- Connector 转发保持时间戳不变。
- Site Server 入库三字段同时存在且互不相等（`agent_received_at` / `created_at` / `device_timestamp_ms`）。
- `device_timestamp_ms` 缺失保持 NULL。
- `agent_received_at` 缺失保持 NULL（历史 / 旧 Agent）。
- 非法 `agent_received_at` 不被伪造为 site 时间。
- 时区转换：`+08:00` → UTC 02:00；naive 按 UTC 解释。
- 历史 Measurement 保持 NULL（不回填）。
- 重复事件处理不改变时间戳（幂等路径不更新原记录）。
- Agent / Site Server 时钟偏差：时间字段独立保存，不用于事件顺序判定。

## 8. 已知限制

- Agent 与 Site Server 系统时钟可能偏差，`agent_received_at` 与 `created_at` 不能用于严格事件排序；事件顺序应以 `sequence` / `device_timestamp_ms` 语义为准。
- 旧 Agent（未升级）不上报 `agent_received_at` 时，Connector 会补一次转发时间；这次补齐的时间精度等于"转发时刻"，不是严格的前置接收时刻。
- 未做时钟校准（NTP / PTP），属于部署运维范围。

## 9. 未解决问题

- 设备端 boot nonce / 统一时钟源（跨 Agent 场景）不在本轮范围。
