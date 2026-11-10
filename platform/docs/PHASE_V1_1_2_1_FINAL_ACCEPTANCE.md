# V1.1.2.1 最终验收报告（Final Acceptance Audit）

- 审计日期：2026-11-04
- 审计对象：`site-server`、`gateway-agent`、Alembic 迁移、相关测试
- 审计方式：只读代码审查 + 本地实际执行测试（未连接、未修改任何生产数据库）
- 本轮不做功能开发，不提交 git，不执行 `alembic upgrade head`（仅对临时库做迁移往返验证）

---

## 0. 一句话结论

**V1.1.2.1 的代码实现基本到位，但存在 3 个必须在上线前解决的问题：**

1. **Observer Command 授权完全缺失**（Blocking）
2. **Gateway Agent 本地命令 REST 无任何认证**（Blocking，默认仅监听 127.0.0.1 可缓解）
3. **Measurement 生产链路 `agent_received_at` 恒为 NULL、`event_id` 退化为 uuid4**（Blocking for traceability/idempotency claim）

因此整体判定：**生产部署 = BLOCKED**。

---

## 1. 审计范围与依据

| 编号 | 审计项 | 主要依据文件 |
|---|---|---|
| A | Device Boot Identity | `gateway-agent/src/widscan_agent/device.py`、`manager.py`、`site_connector.py` |
| B | Observer Command Authorization | `site-server/src/widscan_site/agents/connection.py`、`main.py`、`gateway-agent/src/widscan_agent/site_connector.py`、`api/__init__.py` |
| C | Measurement Traceability | `site-server/src/widscan_site/database/models.py`、`ingestion/__init__.py`、`alembic/versions/002_device_ownership_and_traceability.py` |
| D | WebSocket Authentication | `site-server/src/widscan_site/config.py`、`agents/connection.py`、`main.py`、`gateway-agent/src/widscan_agent/site_connector.py` |
| E | 测试证据 | 实际执行 pytest / vitest |
| F | 生产部署前置条件 | 见 `PHASE_V1_1_2_1_PRODUCTION_DEPLOYMENT_CHECKLIST.md` |

---

## 2. 审计任务 A：Device Boot Identity

### 2.1 事实核对（逐条）

| # | 审计问题 | 结论 | 代码依据 |
|---|---|---|---|
| 1 | `device_boot_id` 是否由 Agent 本地生成 | **是** | `device.py:106` `self._device_boot_id = f"boot-{uuid.uuid4().hex[:8]}"` |
| 2 | 是否根据 `uptime_ms` 回退检测设备重启 | **是（有限）** | `device.py:121-142` `check_device_boot()`：`uptime_ms + 1000 < last` 才判定重启 |
| 3 | USB 断开但设备未重启时是否误生成新 boot_id | **否（Agent 进程存活期间）** | `manager.py:91-105` 以 `device_id` 为 key 复用 `Device` 实例；`Device` 对象未销毁 → boot_id 不变 |
| 4 | `Device` 实例重新创建时是否生成新 boot_id | **是** | boot_id 在 `Device.__init__` 生成；`manager.py:116-118` `remove_device()` 会 `pop` 掉实例，重新 `add_device` 即生成新随机 boot_id |
| 5 | Agent 重启后能否恢复原设备启动身份 | **否** | `_device_boot_id` 与 `_last_seen_uptime_ms` 均为内存字段，无持久化 |
| 6 | 两个 Agent 同时观察同一块 C5 是否生成不同 boot_id | **是** | 各 Agent 各自 `uuid4()`，无任何共享/协商机制 |
| 7 | `event_id` 是否真正使用 Device 实例的 `device_boot_id` | **设计上是的，生产链路上不是** | `site_connector.py:551-559` envelope 携带 `device_boot_id`；`ingestion/__init__.py:74-81` 计算 event_id。但见 2.2 |
| 8 | 是否存在重新生成/覆盖 event_id 的路径 | **存在（退化路径）** | `ingestion/__init__.py:79-81`：无 `sequence` 且无 `device_timestamp_ms` 时 `event_id = uuid4()`，每次唯一 |

### 2.2 关键缺陷：生产 WS 链路下 event_id 退化为 uuid4

`agents/connection.py:343-357` 调用 `record_device_frame()` 时**只传** `device_boot_id`，
没有传 `sequence`，也没有传 `device_timestamp_ms`。

而 `gateway-agent/src/widscan_agent/site_connector.py:582-597` 的 `device.frame` envelope
只转发 `type_code` + `decoded`，**不携带 sequence / 设备时间戳**。

因此生产链路命中的是 `ingestion/__init__.py:79-81` 的兜底分支：

```python
else:
    # 都没有 — fallback 到 uuid（不可避免的唯一兜底）
    event_id = str(uuid.uuid4())
```

**后果**：`UniqueConstraint("site_id", "event_id")`（`models.py:274`）在生产链路下永不触发，
重复上报无法幂等去重。当前「同 Agent 内事件幂等」只对**直接调用 `persist_measurement(... sequence=...)` 的单元测试路径**成立。

### 2.3 事件幂等能力边界（明确记录）

| 场景 | 幂等是否成立 |
|---|---|
| 同一 Agent + 同一 Device 实例 + 携带 sequence（单元测试路径） | **成立** |
| 同一 Agent + 生产 WS 链路（无 sequence） | **不成立**（uuid4 兜底） |
| 同一 Agent 重启后 | **不成立**（boot_id 重新随机生成） |
| 跨 Agent 观察同一物理设备 | **不成立**（boot_id 各自随机） |
| 老固件无 `uptime_ms` | 只能维持当前 boot_id，重启检测失效 |

### 2.4 跨 Agent 统一启动身份

**未实现，不得宣称。** 当前机制（Agent 本地 `uuid4` + `uptime_ms` 单调性）无法在
"两个 Agent 同时观察同一块 C5" 的场景下产生相同 `device_boot_id`。
若要实现，需固件侧提供 **boot nonce**（每次上电生成的持久 nonce，随 INFO 上报），
使不同 Agent 能读到同一值。此项属于未来固件协议变更，本轮不修改固件。

### 2.5 判定

**CONDITIONAL PASS** —— 实现存在且有测试，但「重启识别 + 跨 Agent 统一 + 生产幂等」三项能力边界必须如实公示。

---

## 3. 审计任务 B：Observer Agent Command 权限（重点安全项）

### 3.1 命令全链路逐层核对

| 层 | 位置 | 是否校验 |
|---|---|---|
| Gateway Agent → Site Connector 上行 | `gateway-agent/src/widscan_agent/site_connector.py` | 仅 WS credential 认证 |
| Site Server WS handler | `site-server/src/widscan_site/agents/connection.py:269-302` | 仅 credential 认证（`connection.py:190-206`） |
| Site Server Command validation | — | **不存在** |
| Site Server Command dispatcher | — | **不存在** |
| Gateway Agent Command 处理 | `site_connector.py:985-1120` | 仅 `is_command_allowed()` 命令名白名单，**无发送者身份/Owner 校验** |
| Device operation | `site_connector.py:1061` `device.send_command` | 无权限校验 |
| Gateway Agent 本地 REST 命令 | `gateway-agent/src/widscan_agent/api/__init__.py:143-153` | **完全无认证** |

### 3.2 明确结论（A / B / C 三选一）

**结论 C：代码存在拦截，但覆盖范围不完整。**

证据：

1. **Site Server 侧根本没有命令分发路径。**
   `main.py` 全部路由只有 17 条（`grep "@app\.(get|post|...)"`），**没有任何 `/commands` 端点**。
   `connection.py:291-294` 只被动接收 `command.response`，且注释写明「暂不存储」。
   → 因此当前「Observer 通过 Site Server 提交 Command」这条路**根本不存在**，
     也就不存在「Observer Command 被转发到 Owner Agent」的实现。

2. **Gateway Agent 侧的命令处理没有任何身份/归属校验。**
   `site_connector.py:985` `_handle_command_request()` 只做：
   `request_id` 校验 → 幂等 registry → `manager.get(device_id)` →
   `is_command_allowed(command)`（`site_connector.py:1041`）。
   `is_command_allowed` 是**安全白名单（防止危险指令）**，不是权限模型。

3. **Gateway Agent 本地 REST 命令端点完全未认证。**
   `api/__init__.py:143-153`：`POST /api/v1/devices/{device_id}/commands` 无 `Depends`、无 token、无来源校验。
   默认监听 `127.0.0.1:8765`（`gateway-agent/src/widscan_agent/config.py:23-24`），
   本机进程可直接下发命令；若部署时改为 `0.0.0.0`，局域网内任意主机可下发命令。

4. **没有任何"默认允许、未授权即放行"的正确性校验可依赖。**
   数据库中的 `devices.owner_agent_id` 只被用于展示/迁移/审计，**从未进入命令决策路径**。

### 3.3 风险分级

| 风险 | 等级 | 说明 |
|---|---|---|
| Observer 通过 Site Server 下发命令 | **当前不可达**（无分发路径） | 一旦后续实现 dispatcher 而不加校验，将立即成为漏洞 |
| 本机任意进程通过 Agent REST 下发命令 | **高** | 无认证；默认仅回环可缓解 |
| 能访问 Agent REST 的局域网主机下发命令 | **高**（若绑定 0.0.0.0） | 需部署侧强约束 |
| 前端 Web Console 直接下发命令 | 需复核 | Site Server 无命令 API，故当前无此路径 |

### 3.4 最小修复建议（未实施，仅建议）

> 未经授权不扩大修改范围，以下仅为建议，**均未落地**。

**建议 1（最小、优先）：Agent REST 命令端点加本地令牌校验**

- 文件：`gateway-agent/src/widscan_agent/api/__init__.py`
- 函数：`send_command()`（约 143-153 行）
- 做法：增加 `Depends(require_local_token)`，token 由 Agent 启动时生成并写入
  `config`（默认仅本机可读），或强制校验 `request.client.host == "127.0.0.1"`。
- 对应测试：`gateway-agent/tests/test_api.py` 新增
  `test_command_requires_local_token`（无 token → 401/403；带 token → 200）。

**建议 2：Site Server 未来 dispatcher 必须做 Owner 校验（实现 dispatcher 时同步做）**

- 文件：`site-server/src/widscan_site/agents/connection.py`（新增 `dispatch_command()`）
- 校验：`dev.owner_agent_id == requester_agent_id`，否则 `403` 并写 `AuditLog`。
- 对应测试：`site-server/tests/test_device_ownership.py` 新增
  `test_observer_cannot_dispatch_command`、`test_owner_can_dispatch_command`。

**建议 3：Gateway Agent 收到 `command.request` 时校验 site 侧签名/来源**

- 文件：`gateway-agent/src/widscan_agent/site_connector.py`
- 函数：`_handle_command_request()`（约 985 行）
- 做法：仅接受来自已认证 Site Server 连接的请求（当前已隐式满足，因只有 Server 会发），
  并拒绝 `device_id` 不存在于本地 manager 的请求（当前已有）。

### 3.5 判定

**BLOCKED（生产部署阻断项）** —— 在 Observer/命令授权未确认前，不得启用远程命令能力，不得让 Observer 拥有生产命令执行权限。

---

## 4. 审计任务 C：Measurement Traceability

### 4.1 数据模型核对

`site-server/src/widscan_site/database/models.py:242-280`：

| 字段 | 存在 | 可空 | 写入情况（生产 WS 链路） |
|---|---|---|---|
| `site_id` | ✅ | ❌ | 写入 |
| `device_id` | ✅ | ❌ | 写入 |
| `agent_id` | ✅ (`models.py:258`) | ✅ | **写入**（`ingestion/__init__.py:138`） |
| `device_boot_id` | ✅ (`models.py:259`) | ✅ | **写入** |
| `device_timestamp_ms` | ✅ | ✅ | **不写入**（WS 链路不转发） |
| `agent_received_at` | ✅ (`models.py:263`) | ✅ | **恒为 NULL**（见 4.3） |
| `site_received_at` | 使用 `created_at`（`models.py:268`） | ❌ | 由 `_utcnow()` 默认值写入 |
| `event_id` | ✅ (`models.py:267`) | ✅ | 写入，但退化为 uuid4（见 2.2） |

### 4.2 逐条确认

| # | 问题 | 结论 |
|---|---|---|
| 1 | 新数据能记录来源 Agent | **是**（`agent_id` 写入真实上报者） |
| 2 | 历史记录没有被删除 | **是**（002 迁移只 `add_column`，无 `delete`/`update`） |
| 3 | 历史 `agent_id` 保持 NULL | **是**（`nullable=True`，迁移不 backfill） |
| 4 | 不根据当前 Owner 反推历史 Agent | **是**（无任何 backfill SQL；`test_legacy_measurement_agent_id_null` 覆盖） |
| 5 | Observer 数据保留真实来源 Agent | **是**（`agent_id` 来自 WS 认证的 `agent_id`，与 `owner_agent_id` 无关） |
| 6 | event_id 唯一性约束符合幂等边界 | **否（当前生产能力不匹配）**（uuid4 兜底，约束不生效） |
| 7 | 迁移具备兼容性 | **是**（`upgrade/downgrade` 往返通过，见第 7 节） |
| 8 | Site Server 仍是唯一写入权威 | **是**（仅 `site-server` 有 `Measurement` 写入代码；Agent 只上报） |

### 4.3 关键缺陷：`agent_received_at` 恒为 NULL

`agents/connection.py:348-357`：

```python
record_device_frame(
    sess,
    site_id=site_id,
    agent_id=agent_id,
    device_id=device_id,
    frame_type_code=int(type_code),
    decoded=decoded,
    retention_rawiq_enabled=False,
    device_boot_id=device_boot_id,
)
```

`record_device_frame` 的 `agent_received_at` 默认 `None`（`ingestion/__init__.py:130`），
调用方未传值 → **数据库中始终为 NULL**。

Task 6 明确要求「每条 Measurement 必须能追溯产生时间与接收时间」。
当前只保证了 `site_received_at`（= `created_at`），缺 `agent_received_at`。
**根因在 Agent 侧：envelope 未携带 `agent_received_at`。**

### 4.4 判定

**CONDITIONAL PASS** —— 列与迁移齐备、`agent_id`/`device_boot_id` 正确写入；
但 `agent_received_at` 缺失、`event_id` 幂等在生产线不生效，两项需修复后才满足 Task 6 全部要求。

---

## 5. 审计任务 D：WebSocket Authentication

### 5.1 逐条验证

| # | 要求 | 结论 | 证据 |
|---|---|---|---|
| 1 | 默认 `allow_url_credential=false` | **是** | `config.py:57` |
| 2 | 生产默认不接受 URL Query Credential | **是** | `connection.py:144-149` → `close(4401, url_credential_not_allowed)` |
| 3 | 本地兼容需显式开启 | **是** | `allow_url=True` 才走 URL credential 分支（`connection.py:153`） |
| 4 | 正确首条 auth 可完成认证 | **是**（业务链路测试） | `connection.py:155-188`；`test_site_connector.py` 10 例中多条覆盖 |
| 5 | 错误 credential 被拒绝 | **是** | `connection.py:190-198`；`test_site_connector.py::test_03_bad_credential` |
| 6 | 未认证连接不能提交业务数据 | **是** | 首条非 `auth` → `close(4401)`（`connection.py:169-175`） |
| 7 | 未认证连接不能提交 Command | **是（但无命令路径）** | Site Server 无命令端点；认证前的消息全部在 auth 阶段被拒 |
| 8 | Credential 不进普通日志 | **是** | `_redact_ws_url()`（`connection.py:50-55`）+ 日志无 credential 输出 |
| 9 | TLS 证书验证未弱化 | **默认是，可显式关闭** | `site_connector.py:157-158` `tls_verify=True` 默认；`site_connector.py:762-763` 显式 `False` 时用 `_create_unverified_context()` |

### 5.2 补充事实

- Site Server 应用本身**未配置 TLS**（`site-server` 全仓无 `ssl_keyfile`/`ssl_certfile`/`https` 命中）。
  生产必须由反向代理或 uvicorn `--ssl-*` 提供 wss。
- `main.py:135-138` 启动日志会打印 `allow_url_credential` 与 `auth_timeout`，并在开启时给出警告。
- 认证超时默认 10s（`config.py:58`），超时即 `close(4401, auth_timeout)`。

### 5.3 验证层级（严格区分）

| 验证层级 | 是否覆盖 |
|---|---|
| 单元测试 | ✅ `tests/test_auth_hardening.py`（9 例：URL 脱敏 + 配置默认值 + 迁移往返） |
| 业务逻辑/集成测试 | ✅ `gateway-agent/tests/test_site_connector.py`（真实 Site Server + 真实 WS 连接） |
| WebSocket 端到端（TestClient） | ❌ 未使用 `TestClient.websocket_connect` 跑完整流程；此前尝试因 pytest-asyncio 生命周期问题放弃 |

**不得把单元测试描述成端到端测试。** 本项端到端覆盖缺口明确存在。

### 5.4 判定

**CONDITIONAL PASS** —— 核心安全默认值与拒绝逻辑正确且有两级测试；缺 WS 端到端自动化，且 TLS 由部署侧承担。

---

## 6. 审计任务 E：测试证据（本轮实际执行）

执行时间：2026-11-04（本机 macOS / Python 3.14.5 / node vitest 5.0.3）

| 项目 | Passed | Failed | Skipped | 状态 |
|---|---:|---:|---:|---|
| Gateway Agent | 57 | 0 | 0 | ✅ 通过 |
| Site Server | 22 | 0 | 0 | ✅ 通过 |
| Web (vitest) | 56 | 0 | 0 | ✅ 通过（10 文件） |
| RFTrack (backend) | 649 | 1 | 1 | ⚠️ 1 例 pre-existing 失败（651 collected） |
| Android | — | — | — | **未验证**（本轮未构建） |
| Firmware | — | — | — | **未验证**（本轮未构建，需 ESP-IDF） |
| **可运行合计** | **784** | **1** | **1** | **786 collected** |

### 6.1 实际命令

```bash
# Site Server
cd site-server && PYTHONPATH=src python3 -m pytest tests -q
# → 22 passed, 902 warnings in 0.73s

# Gateway Agent
cd gateway-agent && PYTHONPATH=src python3 -m pytest tests -q
# → 57 passed, 2589 warnings in 32.78s

# Web
cd repo/web && npm test -- --run
# → Test Files 10 passed (10) / Tests 56 passed (56)

# RFTrack
cd repo/rftrack/backend && python3 -m pytest tests -q
# → 651 collected；1 failed / 1 skipped / 649 passed（EXIT=1）
```

### 6.2 测试用例分布（用于可追溯）

- Site Server：`test_auth_hardening.py` 9 + `test_device_ownership.py` 8 + `test_measurement_traceability.py` 5 = 22
- Gateway Agent：`test_api.py` 9 + `test_config.py` 5 + `test_device.py` 9 + `test_events.py` 5 + `test_manager.py` 5 + `test_simulator.py` 7 + `test_site_connector.py` 10 + `test_transports.py` 7 = 57

### 6.3 RFTrack 唯一失败

`tests/test_info_line.py::test_the_current_info_line_yields_all_five_identity_fields`

实际差异（非本轮引入）：

```
assert info == {...5 fields...}
Left contains 2 more items:
  {'mac': None, 'uptime_ms': None}
```

即被解析的 INFO 行当前包含 `mac` 与 `uptime_ms` 两个字段，而测试断言只期望 5 个身份字段。
属既有测试期望滞后，与本轮 V1.1.2.1 改动无关（本轮未触碰 `rftrack`）。

### 6.4 关于历史「1245 vs 1242」

**无法从当前分支复现这两个数字。** 本轮以「实际 `--collect-only` + 实际 pass/fail 计数」为唯一口径：

- 实测 collected 总数 = 22 + 57 + 56 + 651 = **786**
- 实测 passed = 22 + 57 + 56 + 649 = **784**

历史报告中的 1245/1242 与本轮 786/784 差异来源只能推测为：
测试文件集合不同（历史分支含更多测试模块）或统计口径包含 Android/Firmware 构建用例。
**本报告不引用历史数字，也不声称已查明其来源**；以本轮实测为准。

### 6.5 覆盖缺口

| 缺口 | 影响 |
|---|---|
| Site Server WS 端到端（TestClient） | 首条 auth 完整流程无自动化 E2E |
| Observer Command 授权 | **零测试覆盖**（因为功能不存在） |
| 生产链路 `agent_received_at`/`event_id` | 无端到端断言覆盖 |
| Android / Firmware | 完全未验证 |
| TLS / wss 链路 | 无测试 |
| 多 Agent 并发认领竞态 | 依赖唯一约束，但无并发压测 |

---

## 7. 审计任务 C 补充：数据库迁移

`site-server/alembic/versions/002_device_ownership_and_traceability.py`

| 检查项 | 结论 |
|---|---|
| `devices` 新增 4 列 | ✅ `owner_agent_id` / `ownership_status` / `owner_assigned_at` / `owner_updated_at` |
| `measurements` 新增 2 列 | ✅ `agent_id`（nullable，历史 NULL）/ `device_boot_id` |
| 索引 | ✅ `ix_devices_owner` / `ix_measurements_agent` / `ix_measurements_device_boot` |
| FK 命名 | ✅ `fk_devices_owner_agent_id` / `fk_measurements_agent_id` |
| 历史数据不丢失 | ✅ 仅 `add_column`，无 `delete`/`update` |
| downgrade 可回滚 | ✅ 显式 `drop_constraint(..., type_="foreignkey")` + `drop_index` |
| 往返验证 | ✅ `test_auth_hardening.py::test_alembic_002_roundtrip`（22 passed 内含） |
| `event_id` 唯一约束 | ⚠️ 在 `001_initial.py:147` 已创建（`uq_measurement_event`），**不在 002 中** |
| 文档与实现一致性 | ⚠️ 002 文件头部注释声称创建「device_id + agent_id partial unique 索引」，实际**未创建** —— 注释与实现不符 |

**判定：PASS（实现层面）**，附 1 条文档瑕疵。

---

## 8. 最终验收等级（逐项）

| # | 项目 | 等级 | 核心理由 |
|---|---|---|---|
| 1 | Device Boot Identity | **CONDITIONAL PASS** | 实现存在；跨 Agent 统一启动身份未实现，Agent 重启即失效，禁止宣称 |
| 2 | Device Ownership | **PASS** | 首次认领/观察者不抢占/离线保留/重连恢复/管理员迁移/低置信度保护，8 测试全绿 |
| 3 | Observer Command Authorization | **BLOCKED** | 全链路无 Owner 校验；Agent 本地 REST 命令无认证 |
| 4 | Measurement Traceability | **CONDITIONAL PASS** | `agent_id`/`device_boot_id` 正确；`agent_received_at` 恒 NULL；生产 event_id 幂等不生效 |
| 5 | WebSocket Authentication | **CONDITIONAL PASS** | 默认与拒绝逻辑正确；无 WS E2E 自动化；TLS 由部署侧承担 |
| 6 | Database Migration | **PASS** | 002 迁移往返通过、无数据丢失、可回滚 |
| 7 | Regression Tests | **CONDITIONAL PASS** | 784 passed / 1 pre-existing failed / 1 skipped；Android、Firmware 未验证 |
| 8 | Production Readiness | **BLOCKED** | 第 3 项与 Agent REST 认证缺失；第 4 项追溯字段缺失 |

**不为了整体 PASS 而掩盖单项 BLOCKED。**

---

## 9. 生产部署是否允许（明确结论）

### **不允许直接上线。**

在完成以下最小修复前，生产部署保持冻结：

1. Gateway Agent 本地命令 REST 加认证（或强制回环绑定 + 明确部署约束）
2. Site Server 若实现命令分发，必须同步实现 Owner 校验
3. 补齐 `agent_received_at` 与可用的 `event_id`（sequence 或设备时间戳）在生产 envelope 中透传
4. 完成 WS 端到端自动化测试（首条 auth 全流程）

在条件 1 未完成前，**不得**将 Agent REST 绑定到 `0.0.0.0`。

---

## 10. 本轮未做的事（如实声明）

- 未修改任何业务代码
- 未执行 `alembic upgrade head`（仅临时库往返测试）
- 未连接/未修改生产数据库
- 未执行 `git commit` / `git push` / `git reset` / `git clean`
- 未构建 Android / Firmware
- 未实现跨 Agent boot nonce（需固件协议变更）
- 未实现 Observer Command 授权（仅给出建议）

---

## 11. 停止条件

按任务书要求，本报告完成后**立即停止**：

- 不进入 Multi-Node Spectrum V1
- 不开发频谱融合 / 瀑布图 / 多节点定位 / OTA
- 不扩展 Web Workbench
- 等待用户下一步指令
