# Phase 07.4 — Event ID 生产幂等机制

> 状态：PASS（同 Agent 幂等完整；跨 Agent 设备启动周期统一为明确保留限制）
> 日期：2026-11-04

## 1. 原始问题（B-4）

生产 ingestion 在缺少身份材料时无条件 `uuid4()`，且 site connector / ingestion 各自可能重算，
导致同一上游事件重复上报产生不同 `event_id`，数据库唯一约束无法实现真正幂等。

## 2. 根因

`site-server/src/widscan_site/ingestion/__init__.py` 旧逻辑：

```python
if sequence is not None: ...
elif device_timestamp_ms is not None: ...
else: event_id = str(uuid.uuid4())   # 无条件兜底
```

同时 `except IntegrityError: session.rollback(); return None` 把所有完整性错误当作"重复"吞掉。

## 3. 修改文件

| 文件 | 作用 |
|---|---|
| `gateway-agent/src/widscan_agent/identity.py` | 新增 `make_event_id()` / `is_valid_event_id()`，在接收时**只生成一次** |
| `gateway-agent/src/widscan_agent/device.py` | `device.frame` 携带 `event_id` |
| `gateway-agent/src/widscan_agent/site_connector.py` | 转发已有 `event_id`；仅在缺失时补一次，不覆盖 |
| `site-server/src/widscan_site/agents/connection.py` | 将上游 `event_id` 传入 ingestion |
| `site-server/src/widscan_site/ingestion/__init__.py` | `_resolve_event_id()`、格式校验、重复/非重复 IntegrityError 区分、统计计数 |
| `site-server/src/widscan_site/main.py` | `/api/v1/diagnostics` 暴露 `ingestion` 统计 |
| `site-server/tests/test_phase07_ingestion.py` | 16 个幂等/碰撞/兼容/IntegrityError 测试 |
| `site-server/tests/test_phase07_integration.py` | 真实 WebSocket 端到端 event_id 保持 + 重复去重 |
| `gateway-agent/tests/test_event_identity.py` | 11 个 event_id 生成/校验/转发测试 |

## 4. Event ID 语义

```
{device_id}:{device_boot_id}:{frame_type}:{sequence}        # 有 sequence
{device_id}:{device_boot_id}:{frame_type}:{device_timestamp_ms}  # 无 sequence
uuid4                                                       # 无任何身份材料（显式兜底）
```

格式校验：`^[A-Za-z0-9:_.\-]{1,128}$`（`ingestion/__init__.py::is_valid_event_id`）。

身份字段保持独立语义：

- `device_id`：物理设备身份
- `device_boot_id`：设备启动周期（Agent 本地基于 `uptime_ms` 生成）
- `agent_boot_id`：Agent 进程启动周期
- `connection_session_id`：连接会话
- `frame_type` / `sequence`：帧语义与帧序号

## 5. 实现细节

### 5.1 生成一次

- Agent：`Device._read_loop` 生成 `event_id`，确定性来源为 `device_id + device_boot_id + frame_type + seq/tMs`。
- Connector：`bus_event.data["event_id"]` 存在则原样转发；仅缺失时用相同算法补一次。
- Site Server：`ingestion._resolve_event_id()` 优先使用上游值；缺失才按兼容策略派生；非法值直接拒绝。

### 5.2 合法性与拒绝

上游 `event_id` 非法（含空格/超长/空串）→ 不写库，`invalid_event_id += 1`，返回 `None`。

### 5.3 重复 vs 真正的完整性错误

```python
except IntegrityError as exc:
    session.rollback()
    if is_duplicate_event_error(exc):   # uq_measurement_event / UNIQUE ... event_id
        duplicate_events += 1
        log.info("duplicate event ignored ...")
        return None
    integrity_errors += 1
    log.error("measurement integrity error (non-duplicate) ...")
    raise
```

- SQLite `UNIQUE constraint failed: measurements.site_id, measurements.event_id`
- PostgreSQL `duplicate key value violates unique constraint "uq_measurement_event"`

其他完整性错误（NOT NULL / FK / CHECK）不再被吞掉，会抛给上层并记录 ERROR。
WS 连接层 `connection.py` 的异常捕获也改为 `log.error(...)`，不再静默 `pass`，但**不会**导致连接崩溃。

### 5.4 计数与诊断

`ingestion.get_ingestion_stats()` 统计：

`persisted` / `duplicate_events` / `invalid_event_id` / `legacy_uuid_fallback` / `derived_event_id` / `agent_provided_event_id` / `invalid_agent_timestamp` / `integrity_errors`

通过 `GET /api/v1/diagnostics` 的 `ingestion` 字段对外只读暴露。

## 6. 数据库与兼容

- 唯一约束已存在：`Measurement.__table_args__` 中 `UniqueConstraint("site_id","event_id", name="uq_measurement_event")`，`001_initial.py` 已建。
- **无新增迁移**；本轮不需要也不执行生产迁移。
- 历史 event_id 保持原值，不重算、不批量替换、不删除。
- 旧 Agent（未升级）无 `event_id` → 按 `sequence` / `device_timestamp_ms` 派生，保留旧派生格式，避免跨版本身份变化。
- 重复事件不覆盖原始 Measurement（payload 保持首次写入值）。

## 7. 跨 Agent 限制（明确保留）

`device_boot_id` 由 Agent 本地基于 `uptime_ms` 生成。两个 Agent 观察同一块 C5 时可能得到不同 `device_boot_id`，
因此同一物理事件会产生不同 `event_id`，当前**不能跨 Agent 去重**。

本轮验收边界：

- 同一 Agent 内同一事件重发 → `event_id` 一致 → 幂等。
- Site Server 收到重复 `event_id` → 识别为 Duplicate Event，不崩溃、不覆盖。
- 数据库唯一约束真实触发（真实 SQLite 测试）。
- 不同事件不因错误计算碰撞（含 sequence 回绕边界测试）。
- 不同 Agent 转发的同一物理事件仍可能 2 条记录 —— 已由测试显式证明并记录为限制。

后续解决方向：固件 boot nonce（设备启动周期由设备自身生成并上报），不在本轮范围。

## 8. 测试

```
cd gateway-agent && PYTHONPATH=src python3 -m pytest tests/test_event_identity.py -q
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_ingestion.py -q
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_integration.py -q
```

覆盖：

- 同一事件重发 → 唯一键冲突 → 1 条记录 + `duplicate_events` 计数。
- 不同事件 → 不同 `event_id` → 2 条记录。
- Connector 转发不改变 `event_id`（envelope 断言）。
- Site Server 入库不改变 `event_id`（DB 断言）。
- 真正的 IntegrityError（NOT NULL）不被吞掉（`pytest.raises(IntegrityError)`）。
- 不同设备 / 不同 frame_type 不碰撞。
- sequence 回绕（`4294967295` → `0`）ID 合法且不同。
- 旧 Agent 无 `event_id` 的派生兼容路径。
- 历史 UUID 数据保持不变。
- 跨 Agent 同物理事件不同 boot_id → 2 条记录（限制测试）。
- 重复事件不改变时间戳、不覆盖 payload。

## 9. 未解决问题

- 固件 boot nonce / 跨 Agent 统一设备启动周期。
- 重复事件的可选"迟到更新"策略（本轮明确丢弃重复，不更新）。
- UUID 兜底路径仍存在（无任何身份材料的旧数据源），已显式计数，不再是无条件默认行为。
