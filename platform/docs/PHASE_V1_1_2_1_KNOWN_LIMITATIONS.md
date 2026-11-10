# V1.1.2.1 已知限制清单（Known Limitations）

- 生成日期：2026-11-04
- 依据：本轮机读代码审查结果（未修改代码）
- 原则：**只记录代码中真实存在的行为限制**；不把「建议修复」写成「已完成」。

---

## 1. 设备启动身份（Device Boot Identity）

### L-1.1 `device_boot_id` 由 Agent 本地随机生成

- 位置：`gateway-agent/src/widscan_agent/device.py:106`
- 行为：`f"boot-{uuid.uuid4().hex[:8]}"`，在 `Device.__init__` 中生成
- 限制：纯本地随机值，不具备跨进程/跨 Agent 的可比性

### L-1.2 Agent 重启后无法恢复原设备启动身份

- 位置：`device.py:106-107`（`_device_boot_id`、`_last_seen_uptime_ms` 均为内存字段）
- 限制：无持久化。Agent 重启 → 新 `Device` 实例 → 新随机 boot_id，即使物理设备未重启
- 后果：重启前后同一物理设备的 `device_boot_id` 不同

### L-1.3 `Device` 实例销毁重建会产生新 boot_id

- 位置：`manager.py:116-118` `remove_device()` 执行 `self._devices.pop(...)`
- 限制：随后 `add_device()`（`manager.py:81-105`）创建新 `Device`，boot_id 重新随机
- 缓解：仅当 `device_id` 保持不变且实例未被移除时（`manager.py:91-92` 复用）boot_id 才稳定

### L-1.4 USB 断开但设备未重启 → 不误判（仅 Agent 存活期间成立）

- 位置：`manager.py:91-105` + `device.py:121-142`
- 限制：成立前提是 `Device` 实例未被销毁、Agent 进程未重启
- 反例：进程重启或实例移除后，即使设备未重启也会得到新 boot_id

### L-1.5 跨 Agent 统一启动身份 **未实现**

- 位置：`site_connector.py:551-559`（每个 Agent 各自读取自己实例的 boot_id）
- 限制：两个 Agent 同时观察同一块 C5，会得到**两个不同的 device_boot_id**
- **明确声明：当前系统没有实现跨 Agent 的设备启动周期统一识别，不得宣称具备该能力**

### L-1.6 老固件无 `uptime_ms` → 重启检测失效

- 位置：`device.py:126-128`：`if uptime_ms is None: return False`
- 限制：无法检测设备重启，只能沿用当前 boot_id
- 迁移建议（未实现）：固件在 INFO 中提供 **boot nonce**（每次上电生成、持久不变），
  才能让不同 Agent 读到相同启动标识

### L-1.7 生产 WS 链路 `event_id` 退化为 uuid4

- 位置：`site-server/src/widscan_site/agents/connection.py:343-357`（不传 `sequence`/`device_timestamp_ms`）
  → `ingestion/__init__.py:74-81` 落入 `event_id = uuid4()` 兜底分支
- 位置（上游）：`site_connector.py:582-597`，`device.frame` envelope 不含 sequence 与设备时间
- 限制：`UniqueConstraint("site_id","event_id")`（`models.py:274`）在生产链路下**永远不会触发**
- **结论：同 Agent 内的事件幂等能力在生产链路下不成立；仅直接调用 `persist_measurement(sequence=...)` 的路径成立**

---

## 2. 设备所有权（Device Ownership）

### L-2.1 只有归属语义，没有权限执行

- 位置：`site-server/src/widscan_site/devices/__init__.py`（`upsert_device_from_agent` / `transfer_device_owner`）
- 限制：`owner_agent_id` / `ownership_status` 仅用于状态展示、迁移决策与审计
- 限制：**从未被用于命令授权判定**（全仓库无 `owner_agent_id` 参与授权分支）

### L-2.2 Observer 无专属能力限制

- 位置：`connection.py:304-357` `_process_agent_event()`
- 限制：Observer 与 Owner 的事件处理路径完全相同（`device.online` / `device.frame` / `device.state_change` 全部接受）
- 说明：当前设计允许 Observer 上报数据（并正确记录其 `agent_id`），但**没有拒绝能力**（也没有可拒绝的操作，见 3.1）

### L-2.3 所有权并发安全依赖数据库约束，未做压测

- 位置：`devices/__init__.py` 事务提交 + `models.py` 索引/唯一约束
- 限制：无多 Agent 并发认领的并发测试；无压力测试证据

---

## 3. Observer Command 权限

### L-3.1 Site Server 不存在命令分发路径

- 位置：`site-server/src/widscan_site/main.py`（17 条路由，无 `/commands`）
- 位置：`connection.py:291-294` 仅被动消费 `command.response` 且不存储
- 限制：当前**Owner 与 Observer 都无法通过 Site Server 下发命令**（既非安全设计，也非可用功能）

### L-3.2 Gateway Agent 命令处理无身份/归属校验

- 位置：`site_connector.py:985-1120` `_handle_command_request()`
- 校验内容仅：`request_id` 非空 → 幂等 registry → `manager.get(device_id)` → `is_command_allowed(command)`
- 限制：`is_command_allowed`（`site_connector.py:1041`）是危险指令白名单，**不是权限模型**

### L-3.3 Gateway Agent 本地 REST 命令端点无认证

- 位置：`gateway-agent/src/widscan_agent/api/__init__.py:143-153`
- 性质：`POST /api/v1/devices/{device_id}/commands` 无 `Depends`、无 token、无来源校验
- 缓解现状：默认绑定 `127.0.0.1:8765`（`gateway-agent/src/widscan_agent/config.py:23-24`）
- 限制：一旦改为 `0.0.0.0`，局域网任意主机可下发命令
- **判定：生产部署阻断项**

### L-3.4 无任何「未授权即放行」以外的兜底

- 说明：不存在第二道防线（如命令签名、site 侧签发 token、Owner 白名单）

---

## 4. Measurement Traceability

### L-4.1 `agent_received_at` 生产链路恒为 NULL

- 位置：`connection.py:348-357` 调用 `record_device_frame()` 未传 `agent_received_at`
- 位置：`ingestion/__init__.py:130` 该参数默认 `None`
- 限制：Task 6 要求的「Agent 接收时间」在实际数据中缺失
- 根因：`site_connector.py` envelope 未携带该时间

### L-4.2 `device_timestamp_ms` / `sequence` 生产链路不写入

- 位置：`site_connector.py:582-597`（frame envelope 仅 `type_code` + `decoded`）
- 限制：设备侧时间与序号无法追溯；同时导致 4.3

### L-4.3 `event_id` 幂等生产不生效

- 见 L-1.7

### L-4.4 历史数据 `agent_id` 为 NULL，且不可反推

- 位置：`alembic/versions/002_device_ownership_and_traceability.py`（只加列，不 backfill）
- 限制：这是**有意设计**（不得根据当前 Owner 反推历史 Agent），不是缺陷
- 影响：历史数据无法回答「谁上报的」

### L-4.5 `site_received_at` 复用 `created_at`

- 位置：`models.py:268`
- 限制：语义上可接受，但与 `agent_received_at` 区分不显式（列名不叫 `site_received_at`）

---

## 5. WebSocket 认证

### L-5.1 无 WebSocket 端到端自动化测试

- 现状：`tests/test_auth_hardening.py` 为纯单元测试（URL 脱敏、配置默认值、迁移往返）
- 现状：完整 WS 连接由 `gateway-agent/tests/test_site_connector.py` 间接覆盖
- 缺口：未使用 `TestClient.websocket_connect` 跑首条 auth 的完整拒绝/超时流程
- **不得将单元测试描述为端到端测试**

### L-5.2 Site Server 应用本身不提供 TLS

- 事实：`site-server` 全仓无 `ssl_keyfile` / `ssl_certfile` / `https` 配置
- 限制：wss 必须由反向代理或 uvicorn 启动参数提供；应用层无 TLS 强制

### L-5.3 Agent 侧 TLS 验证可被显式关闭

- 位置：`gateway-agent/src/widscan_agent/site_connector.py:758-766`
- 默认：`tls_verify=True`（`site_connector.py:158`）→ `ssl.create_default_context(cafile=...)`
- 限制：显式传 `tls_verify=False` 时使用 `ssl._create_unverified_context()`，完全跳过证书校验
- 说明：默认未弱化；但缺少「关闭验证时必须打印告警」的保护

### L-5.4 URL credential 兼容模式仍可开启

- 位置：`site-server/src/widscan_site/config.py:57`，默认 `False`
- 限制：显式设 `True` 后，凭据会出现在 URL（可能进入代理/访问日志）
- 说明：`main.py:137-138` 会打印警告，但无法强制禁止

---

## 6. 测试与构建

| 项目 | 状态 | 说明 |
|---|---|---|
| Gateway Agent | 57 passed | 可用 |
| Site Server | 22 passed | 可用 |
| Web | 56 passed | 可用 |
| RFTrack | 649 passed / 1 failed / 1 skipped | 1 failed 为 pre-existing（`test_info_line.py` 期望值滞后） |
| Android | **未验证** | 本轮未构建 |
| Firmware | **未验证** | 本轮未构建（需 ESP-IDF 本地环境） |
| WS 端到端 | **未验证** | 无自动化用例 |
| 多 Agent 并发认领 | **未验证** | 无并发测试 |

---

## 7. 文档与实现不一致（待修正，未修改代码）

| 项 | 位置 | 说明 |
|---|---|---|
| 002 迁移注释 | `alembic/versions/002_device_ownership_and_traceability.py:11` | 注释称创建「device_id + agent_id partial unique 索引」，实际未创建 |
| `main.py` 旧 ORM API | `site-server/src/widscan_site/main.py:375-377` | `sess.execute(sess.query(...))` 触发 `SADeprecationWarning`（非阻塞） |
| `connection.py` 注释 | `connection.py:12` | 文档串称「Command 转发」，实际无分发实现 |

---

## 8. 明确不承诺的能力（禁止对外宣称）

1. 跨 Agent 的设备启动周期统一识别
2. 生产链路的事件幂等去重
3. `agent_received_at` 追溯
4. Observer 命令权限隔离（功能与拦截均不存在）
5. Android / Firmware 已验证
6. 已具备生产直接上线条件
