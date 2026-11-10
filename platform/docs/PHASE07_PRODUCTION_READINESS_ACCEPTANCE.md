# Phase 07 — Production Readiness Hardening 验收报告

> 日期：2026-11-04
> 范围：Phase 07.1 – 07.4（仅四个已确认的生产阻断项）
> 结论：四个阻断项的实现均已完成并有测试证据；整体生产就绪判定为 **CONDITIONAL PASS**
> （Android / Firmware 本轮未构建；RFTrack 存在 1 个与本轮无关的既有失败；远程命令下发能力尚未实现，属于明确保留的能力缺口）。

---

## 一、执行摘要

| 项目 | 修复前 | 修复后 | 状态 |
|---|---:|---:|---|
| Gateway Agent 测试 | 57 passed | **95 passed** | PASS |
| Site Server 测试 | 22 passed | **69 passed**（含 8 个真实 WS 集成） | PASS |
| Web 测试 | 56 passed | **56 passed** | PASS（未回归） |
| RFTrack 测试 | 651 collected / 649 passed / 1 failed / 1 skipped | 同左 | 既有失败，未修复 |
| Android | 未构建 | 未构建 | NOT VERIFIED |
| Firmware | 未构建 | 未构建 | NOT VERIFIED |

本轮未执行任何 `git reset` / `git clean` / `git commit` / `git push`，
未连接或修改生产数据库，未执行 Alembic 升级。

---

## 二、四项阻断修复映射

### 2.1 B-1 Agent Command REST Security — PASS

- 问题：本地命令 REST 无认证，只依赖 127.0.0.1。
- 根因：`build_app()` 未接认证配置；路由无 `Depends`。
- 修改：`gateway-agent/src/widscan_agent/api/__init__.py`（`require_command_auth`）、
  `config.py`（`require_auth` / `api_token` / 环境变量 / `resolve_bind_host`）、
  `main.py`、`config/agent.example.yaml`。
- 实现：Bearer / `X-API-Token`，`hmac.compare_digest`；未配置 Token → 503；
  错误/缺失 → 401；合法但命令不在白名单 → 403；Token 不落日志。
- 测试：`gateway-agent/tests/test_command_api_security.py`（16）、`test_config.py`（新增 5）、
  `test_api.py`（原有 9 用例携带 Token 后通过）。

### 2.2 B-2 Owner / Observer Command Authorization — PASS

- 问题：`site_connector.py:985` 只校验命令白名单，不校验请求者与 Device Owner 关系。
- 根因：身份与设备归属未参与命令链路；无统一权限矩阵。
- 修改：新增 `site-server/src/widscan_site/devices/authorization.py`；
  `agents/connection.py` 增加 `_handle_inbound_command()`；
  `gateway-agent/src/widscan_agent/site_connector.py` 增加 Agent 归属断言校验与 `commands_unauthorized`。
- 实现：Owner=管理；Observer=只读；Site Admin=服务端可信调用方；
  未授权/禁用 Agent=拒绝；Device 不可用状态→拒绝命令（读取仍允许）；
  服务端只使用已认证 `agent_id` + 服务端 Device 记录，忽略消息自报的 owner。
- 测试：`test_phase07_authorization.py`（17）、`test_phase07_command_gateway.py`（6）、
  `test_phase07_integration.py`（真实 WS Owner/Observer）、
  `gateway-agent/tests/test_command_authorization.py`（6）。
- 限制：Site Server 无远程命令分发端点；授权通过后返回 `remote command dispatch not implemented`。
  本轮按要求未新增完整远程命令系统（详见 07.2 文档）。

### 2.3 B-3 Measurement Timestamp Integrity — PASS

- 问题：`agent_received_at` 恒为 NULL。
- 根因：Agent 未生成、Connector 未转发、Ingestion 未接收。
- 修改：`device.py`、`identity.py`、`site_connector.py`、
  `site-server/agents/connection.py`、`ingestion/__init__.py`。
- 实现：Agent 接收时生成 UTC ISO 8601；Connector 原样转发；Site Server 解析后原样入库；
  `site_received_at = created_at`；`device_timestamp_ms` 允许 NULL；禁止用 site 时间冒充 agent 时间。
- 测试：`test_phase07_ingestion.py`（时序/时区/缺失/历史）与 `test_phase07_integration.py`
  （真实 WS 端到端断言三字段）。
- 数据库：复用既有字段，无新增迁移。

### 2.4 B-4 Event ID Production Idempotency — PASS

- 问题：`ingestion/__init__.py:74` 无条件 `uuid4`，全链路可能重算。
- 根因：生成位置过晚且兜底无条件；IntegrityError 全量吞掉。
- 修改：`identity.py`、`device.py`、`site_connector.py`、
  `site-server/agents/connection.py`、`ingestion/__init__.py`、`main.py`（诊断统计）。
- 实现：Agent 接收时生成一次 → 全链路原样传递；格式校验；缺失时按显式兼容策略派生；
  唯一键冲突识别为 Duplicate Event；非重复 IntegrityError 抛出并记录 ERROR；统计计数对外可查。
- 测试：`test_phase07_ingestion.py`（16）、`test_phase07_integration.py`（真实 WS 重复去重）、
  `gateway-agent/tests/test_event_identity.py`（11）。
- 限制：跨 Agent 同一物理事件仍可能产生不同 `device_boot_id` → 不同 `event_id`，
  本轮明确不宣称已实现跨 Agent 去重（有专门测试证明 2 条记录）。

---

## 三、八项最终判定

| # | 项目 | 判定 | 依据 |
|---|---|---|---|
| 1 | B-1 Agent Command REST Security | **PASS** | 17 个安全/配置测试；未认证无法执行命令；Token 脱敏 |
| 2 | B-2 Owner Authorization | **PASS** | 29 个授权/命令入口测试 + 真实 WS；Observer 无法执行 |
| 3 | B-3 Measurement Timestamp Integrity | **PASS** | 单元 + 真实 WS 端到端三时间戳断言 |
| 4 | B-4 Event ID Idempotency | **PASS** | 同 Agent 重复 1 条记录；唯一约束真实触发；跨 Agent 限制有测试记录 |
| 5 | Database Compatibility | **PASS** | 无 schema 变更；复用 002 字段；`uq_measurement_event` 已存在；历史数据未动 |
| 6 | Gateway Agent Regression | **PASS** | 95 passed（57 → 95）；原有 9 个 API 用例通过 |
| 7 | Site Server Integration | **PASS** | 69 passed（22 → 69），含 8 个真实 uvicorn + WebSocket 集成 |
| 8 | Production Readiness | **CONDITIONAL PASS** | 四项阻断已解除；但远程命令分发未实现、Android/Firmware 未验证、RFTrack 1 个既有失败 |

解除对应阻断的条件核对：

- [x] 未认证请求不能执行命令（401/503）。
- [x] Observer 不能执行 Owner 专属命令（`denied: observer_read_only`）。
- [x] Agent 不能操作不属于其授权范围的 Device（`device_not_found` / Agent 断言拒绝）。
- [x] `agent_received_at` 正确传递并持久化（DB 断言）。
- [x] `event_id` 全链路保持一致（Agent → Connector → Site Server → DB）。
- [x] 同 Agent 重复事件幂等（唯一约束 + duplicate 计数）。
- [x] 历史数据保持完整（不回填、不重算、不删除）。
- [x] 数据库唯一约束真实有效（真实 SQLite 冲突测试）。

---

## 四、测试执行记录（本轮真实执行）

### 4.1 Gateway Agent

命令：

```bash
cd gateway-agent
PYTHONPATH=src python3 -m pytest tests -q
```

原始结果：`95 passed, 4245 warnings in 43.05s`

分文件：

| 文件 | 测试数 | 说明 |
|---|---:|---|
| test_api.py | 9 | 原有，携带 Token |
| test_config.py | 10 | 5 原有 + 5 新增 |
| test_command_api_security.py | 16 | 新增（07.1） |
| test_command_authorization.py | 6 | 新增（07.2） |
| test_event_identity.py | 11 | 新增（07.3/07.4） |
| test_site_connector.py | 10 | 原有 |
| test_device.py / test_manager.py / test_events.py / test_simulator.py / test_transports.py | 9 / 5 / 5 / 7 / 7 | 原有 |

Collected 95 / Passed 95 / Failed 0 / Skipped 0。

### 4.2 Site Server

命令：

```bash
cd site-server
PYTHONPATH=src python3 -m pytest tests -q
```

原始结果：`69 passed, 3030 warnings in 6.36s`

分文件：

| 文件 | 测试数 | 说明 |
|---|---:|---|
| test_device_ownership.py | 8 | 原有 |
| test_measurement_traceability.py | 5 | 原有 |
| test_auth_hardening.py | 9 | 原有 |
| test_phase07_ingestion.py | 16 | 新增（07.3/07.4） |
| test_phase07_authorization.py | 17 | 新增（07.2） |
| test_phase07_command_gateway.py | 6 | 新增（07.2） |
| test_phase07_integration.py | 8 | 新增（真实 uvicorn + WebSocket） |

Collected 69 / Passed 69 / Failed 0 / Skipped 0。

### 4.3 Web

```bash
cd repo/web && npm test -- --run
```

原始结果：`Test Files 10 passed (10)`，`Tests 56 passed (56)`。
（未修改 Web 代码；单机模式相关用例无回归。）

### 4.4 RFTrack

```bash
cd repo/rftrack/backend && python3 -m pytest tests -q
```

结果：`651 collected`，`649 passed`，`1 failed`，`1 skipped`。

失败用例（既有，与本轮修改无关）：

`tests/test_info_line.py::test_the_current_info_line_yields_all_five_identity_fields`
断言期望 5 个字段，实际解析结果额外包含 `mac: None`、`uptime_ms: None`。

本轮未修改 RFTrack 代码，因此保留该既有失败，不将其计入通过。

### 4.5 Android / Firmware

NOT VERIFIED。本轮未触发 Gradle / ESP-IDF 构建；无可用运行证据，不计入通过。

---

## 五、跨模块集成验证清单

`site-server/tests/test_phase07_integration.py`（真实 Site Server + 真实数据库 + 真实 WebSocket）：

| 集成项 | 覆盖测试 |
|---|---|
| Agent 本地命令 REST 认证 | `gateway-agent/tests/test_command_api_security.py`（TestClient 真实路由） |
| Owner Agent 命令授权 | `test_owner_command_authorized_observer_denied` |
| Observer 命令拒绝 | 同上（`denied: observer_read_only`） |
| 非法 Agent 命令拒绝 | `test_phase07_command_gateway.py::test_unauthenticated_agent_identity_denied` |
| Measurement 三类时间戳 | `test_measurement_timestamps_and_event_id_end_to_end` |
| Measurement 来源 Agent | 同测试 `m.agent_id == "agent-A"`；跨 Agent 测试断言两条来源 |
| event_id 全链路一致 | 同测试 DB 断言 + `test_event_identity.py` envelope 断言 |
| 重复事件去重 | `test_duplicate_event_deduplicated_end_to_end` |
| Agent 断线 / 重连 | `test_agent_disconnect_keeps_owner_and_reconnect_restores` |
| Site Server 重启 | `test_site_server_restart_preserves_measurements_and_ownership` |
| Device 离线状态恢复 | 同上（断线 → offline，重连 → online，Owner 保持） |
| 历史 Measurement 查询 | 重启后 REST 查询 `event_id` 保持；历史 NULL 单元测试 |
| 原始 widscan58 单机功能 | `repo/web` 56 个测试（未改动 Web 代码） |

未使用 Mock 替代全部集成验证：命令授权、时间戳、event_id、断线重连、重启均走真实 WS 与真实 SQLite。

---

## 六、数据库与迁移

- 本轮**没有**数据库结构变更。
- 复用 `002_device_ownership_and_traceability.py` 提供的：
  `Measurement.agent_id` / `device_boot_id` / `device_timestamp_ms` / `agent_received_at` / `sequence`。
- `uq_measurement_event`（`site_id + event_id`）在 `001_initial.py` 已存在，真实触发验证通过。
- 历史记录：不回填、不删除、不重算 event_id（有专门测试）。
- 未执行 `alembic upgrade`，未连接生产数据库。
- 生产部署前仍需：备份数据库、确认 `alembic current`、在预发环境验证（本轮未执行）。

---

## 七、已知限制（必须保留）

1. **跨 Agent 设备启动周期统一未解决**：`device_boot_id` 由 Agent 本地基于 `uptime_ms` 生成；
   两个 Agent 观察同一 C5 可能产生不同 boot_id → 不同 event_id → 不去重。
   解决方向是固件 boot nonce，属于后续阶段。
2. **没有远程命令下发能力**：Site Server 授权通过后不会转发到 Agent，只返回未实现信息。
3. **Site Admin 依赖服务端调用方自证**：当前管理 REST 没有管理员认证体系。
4. **本地命令 API 无 TLS**：Token 用于本机进程边界；生产不建议跨主机暴露。
5. **无 Token 轮换 / 多调用方分权**。
6. **时间戳不用于严格排序**：Agent / Site Server 时钟可能偏差；顺序应依赖 sequence / device_timestamp。
7. **旧 Agent 兼容路径**：无 upstream `event_id` 时按旧派生格式生成；无身份材料时仍可能 uuid4（已计数）。
8. **RFTrack 既有失败**未在本轮修复（不在 Phase 07 范围）。
9. Android / Firmware 未构建验证。

---

## 八、生产部署建议

### Gateway Agent

1. `export GATEWAY_API_TOKEN="<强随机值>"`；保持 `api.require_auth: true`。
2. 校验启动日志：`command API security: require_auth=True token_configured=True`。
3. 仅在本机调用命令 API，携带 `X-API-Token` / Bearer。
4. 若配置外部监听，确认认证与 Token 均生效，否则服务会回退 127.0.0.1。

### Site Server

1. `websocket.allow_url_credential=false`（默认已满足）。
2. 认证必须使用首条 `auth` message。
3. 生产升级前：备份 SQLite/PostgreSQL；确认 Alembic 版本；本轮无新迁移。
4. 启动后检查 `GET /api/v1/diagnostics` 的 `ingestion` 计数与 `active_agent_connections`。
5. Observer Agent 在生产可连接、可上报 Measurement，但命令一律拒绝。

### 观测

- 关注 `duplicate_events` 增长（正常幂等 vs 上游重发异常）。
- 关注 `integrity_errors`（应为 0；非 0 表示真实数据完整性问题）。
- 关注 `invalid_event_id` / `invalid_agent_timestamp`（应为 0）。

---

## 九、尚未解决的问题 / 下一阶段候选

- 固件 boot nonce（跨 Agent 设备启动周期统一）。
- 远程命令下发 + 审计 + 速率限制。
- Site Admin 认证与 RBAC。
- Android / Firmware 构建与回归验证。
- RFTrack `test_info_line` 既有失败修复（归属 RFTrack 模块）。

---

## 十、停止声明

本轮只完成 Phase 07.1 – 07.4 的实现、测试与验收。
未启动 Site Workbench UI、原始 Web Console 平台化、Electron、Android 新功能、
Multi-Node Spectrum V1、频谱融合、多节点定位、OTA。
开发到此停止，等待下一步指令。
