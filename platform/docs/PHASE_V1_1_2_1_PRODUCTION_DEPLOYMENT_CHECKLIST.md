# V1.1.2.1 生产部署检查清单（Production Deployment Checklist）

- 生成日期：2026-11-04
- 性质：**只读审查结论 + 操作步骤**。本轮未执行 `alembic upgrade head`，未连接/未修改任何生产数据库。
- 当前结论：**生产部署 BLOCKED**（详见第 0 节与 `PHASE_V1_1_2_1_FINAL_ACCEPTANCE.md`）

---

## 0. 放行判定

| 阻断项 | 证据 | 放行条件 |
|---|---|---|
| B-1 Gateway Agent 本地命令 REST 无认证 | `gateway-agent/src/widscan_agent/api/__init__.py:143-153` | 增加本地 token 校验，或部署强制回环绑定并有访问控制 |
| B-2 Observer / 命令无 Owner 授权 | `site_connector.py:985-1120`；`site-server` 无命令分发路径 | 实现命令分发时同步实现 Owner 校验；否则明确禁用远程命令 |
| B-3 `agent_received_at` 恒 NULL | `connection.py:348-357`；`ingestion/__init__.py:130` | Agent envelope 携带 `agent_received_at` 并在 Site 侧落库 |
| B-4 生产 `event_id` 退化为 uuid4 | `ingestion/__init__.py:74-81`；`site_connector.py:582-597` | 透传 `sequence` 或 `device_timestamp_ms` |

**在 B-1 ~ B-4 全部关闭前，不得部署到生产。**

---

## 1. 数据库（DBA 人工执行，本轮不执行）

### 1.1 部署前

- [ ] 确认生产库位置与连接串（不在仓库内硬编码；通过环境变量或外部配置注入）
- [ ] 完成数据库文件级备份（SQLite：`cp site.db site.db.bak.$(date +%Y%m%d%H%M%S)`，并验证备份可打开）
- [ ] 记录当前 Alembic 版本：
      `alembic current`（应在 `001_initial`）
- [ ] 记录关键表行数（迁移前后必须一致）：
      `SELECT COUNT(*) FROM measurements;`
      `SELECT COUNT(*) FROM devices;`
      `SELECT COUNT(*) FROM agents;`

### 1.2 迁移适用性确认

- [ ] 确认目标 revision：`002_device_ownership`（`down_revision = "001_initial"`）
- [ ] 在**临时副本库**上预演：
      `alembic upgrade head`
      然后 `alembic downgrade -1` 再 `alembic upgrade head`
- [ ] 确认 `002` 只执行 `add_column` / `create_index`，不含 `delete` / `update`（源码已核对：是）
- [ ] 确认 `devices` 新增列：`owner_agent_id`、`ownership_status`、`owner_assigned_at`、`owner_updated_at`
- [ ] 确认 `measurements` 新增列：`agent_id`（nullable）、`device_boot_id`

### 1.3 迁移后校验

- [ ] `PRAGMA table_info(devices);` 包含 `owner_agent_id` / `ownership_status`
- [ ] `PRAGMA table_info(measurements);` 包含 `agent_id` / `device_boot_id`
- [ ] 行数与 1.1 记录一致（历史数据零丢失）
- [ ] 历史 `measurements.agent_id` 全为 NULL：
      `SELECT COUNT(*) FROM measurements WHERE agent_id IS NOT NULL;` → 应为 0
- [ ] Site Server 启动日志**无** schema 缺失告警（`main.py:107-121` 的自检逻辑）

### 1.4 回滚方案

- [ ] 首选：`alembic downgrade -1`（回到 `001_initial`）
- [ ] 兜底：用 1.1 的备份文件整体恢复
- [ ] 回滚后验证：应用可启动、`/health` 返回 200、`/api/v1/measurements/stats` 可访问

---

## 2. Site Server

### 2.1 配置

- [ ] `websocket.allow_url_credential = false`（默认值，勿在 YAML 中覆盖为 true）
- [ ] `websocket.auth_timeout_seconds` 保持默认 `10.0`（或按业务调整，不得关闭）
- [ ] `api.host`：生产不得为 `0.0.0.0` 以外的无保护暴露；建议置于反向代理之后
- [ ] `database.url` 指向生产库；`database.echo = false`

### 2.2 启动日志确认（`main.py:135-138`）

- [ ] 出现 `WebSocket security: allow_url_credential=False auth_timeout=10.0s`
- [ ] **不得**出现 `⚠️ allow_url_credential=True ...` 警告
- [ ] 出现 `DB schema check` 无缺失告警

### 2.3 凭据与日志

- [ ] 确认 WS URL 日志被脱敏：`connection.py:50-55` `_redact_ws_url()`，日志中出现 `credential=***`
- [ ] 抽查日志文件，确认无明文 credential
- [ ] 确认反向代理/网关不记录 URL query（因为已禁用 URL credential）

### 2.4 TLS

- [ ] Site Server 应用层无 TLS 配置（已确认全仓无 `ssl_keyfile`/`https`）
- [ ] **必须**由反向代理（Nginx/Caddy）或 `uvicorn --ssl-keyfile --ssl-certfile` 提供 `wss://`
- [ ] 确认证书链有效、未过期
- [ ] 确认禁用 TLS 1.0/1.1，仅允许 1.2+

### 2.5 接口鉴权（现状说明）

- [ ] **注意**：Site Server REST API 当前**无用户认证**（`main.py` 全部端点无 `Depends` 鉴权）
- [ ] 生产必须通过反向代理做网络隔离/来源限制，或加网关鉴权
- [ ] `POST /api/v1/devices/{device_id}/transfer-owner`（`main.py:308-332`）属高权限操作，**必须**限制为管理员来源

---

## 3. Gateway Agent

### 3.1 认证方式

- [ ] 生产 Agent 使用**首条 auth message**：
      `{"type":"auth","agent_id":"...","credential":"..."}`（`site_connector.py:770` 连接 URL 只带 `agent_id`）
- [ ] 确认连接 URL **不含** `credential` 参数（`site_connector.py:769-770` 已不携带）
- [ ] 确认 Agent 配置 `site.endpoint` 为 `wss://`（生产必须 TLS）

### 3.2 重连与状态恢复

- [ ] 验证 Agent 断线重连后：`devices_snapshot` 重新上报（`connection.py:297-301`）
- [ ] 验证 Owner 离线时 Device 的 `owner_agent_id` **不被清除**
      （`devices/__init__.py` 的 `mark_devices_offline_by_agent` 只改 endpoint/status，不清 owner）
- [ ] 验证 Agent 重启后 Device 仍归属原 Owner（依赖 DB 持久化，与内存无关）

### 3.3 本地 REST 暴露面（**关键**）

- [ ] 保持 `api.host = 127.0.0.1`（默认，`gateway-agent/src/widscan_agent/config.py:23`）
- [ ] **禁止**将 `api.host` 改为 `0.0.0.0`（命令端点无认证，见 B-1）
- [ ] 若必须远程访问，须先完成 B-1 修复，并在防火墙层限制来源

### 3.4 TLS（客户端侧）

- [ ] 保持 `tls_verify = True`（默认，`site_connector.py:158`）
- [ ] 自签证书场景需显式配置 `tls_ca_file` 指向 CA，**不要**通过 `tls_verify=False` 关闭校验
- [ ] 若曾使用 `tls_verify=False`，上线前必须移除

### 3.5 离线容错

- [ ] 验证 Site Server 不可达时，Agent 仍能本地采集与本地 REST 服务（网络故障不影响本地采集，`site_connector.py` 独立线程/任务）
- [ ] 验证 Agent 自身崩溃重启后，采集自动恢复

---

## 4. Observer Agent

- [ ] **在命令授权拦截未确认前，禁止为 Observer Agent 开通任何生产命令执行权限**
- [ ] 当前现实：Site Server 无命令分发端点，Observer 无法下发命令；但 **Gateway Agent 本地 REST 可绕过**
- [ ] 因此：Observer 所在主机不得暴露 Agent REST 端口
- [ ] 不得依据「数据库有 `owner_agent_id`」推断命令权限安全

---

## 5. 发布顺序（建议）

1. 备份生产库（含行数基线）
2. 临时副本库预演迁移（upgrade → downgrade → upgrade）
3. 停 Site Server（避免迁移期间写入）
4. 执行 `alembic upgrade head`
5. 校验 schema 与行数（第 1.3 节）
6. 启动 Site Server → 检查启动日志（第 2.2 节）
7. 升级 Gateway Agent → 确认使用首条 auth、`wss://`、`tls_verify=True`
8. 冒烟：`/health` → `/api/v1/devices` → `/api/v1/measurements/stats`
9. 观察 30 分钟：Agent 上线、measurement 增长、无 credential 日志泄漏
10. 若异常 → 按 1.4 回滚

---

## 6. 部署后验证清单

- [ ] `GET /health` → 200
- [ ] `GET /api/v1/agents` → 目标 Agent 为 `online`
- [ ] `GET /api/v1/devices` → 设备 `owner_agent_id` 正确、`ownership_status=owned`
- [ ] `GET /api/v1/devices/{id}/measurements` → 新数据含 `agent_id`、`device_boot_id`
- [ ] 抽查新 Measurement：`agent_id` 非 NULL、`event_id` 非空
- [ ] **预期已知缺陷**：`agent_received_at` 仍为 NULL（B-3 未修复前必然如此，需在验收记录中标注）
- [ ] 日志中无明文 credential
- [ ] 断网测试：拔掉 Site 侧网络，本地采集不中断

---

## 7. 明确禁止的动作（本轮及上线前）

- ❌ 擅自执行 `alembic upgrade head`（须 DBA/负责人确认窗口）
- ❌ 直接连接或修改生产数据库
- ❌ 开启 `allow_url_credential=true` 并用于生产
- ❌ 将 Gateway Agent `api.host` 改为 `0.0.0.0`
- ❌ 设置 `tls_verify=False`
- ❌ 在 B-1 ~ B-4 未关闭前上线
- ❌ 执行 `git reset` / `git clean` / `git commit` / `git push`

---

## 8. 附：相关配置项速查

| 配置项 | 文件 | 默认 | 生产要求 |
|---|---|---|---|
| `websocket.allow_url_credential` | `site-server/src/widscan_site/config.py:57` | `False` | `False` |
| `websocket.auth_timeout_seconds` | `config.py:58` | `10.0` | `10.0` |
| `api.host` (Site Server) | `config.py:26` | `127.0.0.1` | 反向代理后 |
| `api.host` (Agent) | `gateway-agent/src/widscan_agent/config.py:23` | `127.0.0.1` | **必须** `127.0.0.1` |
| `site.endpoint` | `gateway-agent/.../config.py:54` | `""` | `wss://...` |
| `tls_verify` | `site_connector.py:158` | `True` | `True` |
| `retention.rawiq_enabled` | `config.py:46` | `False` | `False`（除非有明确存储预算） |
