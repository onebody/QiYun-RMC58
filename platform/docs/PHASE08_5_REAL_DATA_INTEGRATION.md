# Phase 08.5 — Real Data Integration（真实数据集成验证）

> 项目：QiYun / widscan58
> 阶段：Phase 08 Platform UI Integration / 08.5
> 验证日期：2026-11-05（本地时间，Asia/Shanghai）
> 结论：**CONDITIONAL PASS**（真实数据链路全部跑通；4 项接口缺口与 1 项实时通道缺口已记录，未修改冻结协议）

---

## 1. 验证环境与数据性质声明

本阶段**没有真实 WIDSCAN58 硬件**。为验证「前端 → Site Server → Gateway Agent → Device → Measurement」的真实链路，采用：

| 组件 | 实例 | 说明 |
|---|---|---|
| Site Server | 真实进程 `python -m widscan_site.main` | 真实 FastAPI + 真实 SQLite 文件 `site.db` |
| Gateway Agent | 真实进程 `python -m widscan_agent.cli --simulators 2` | 真实 Agent 主程序，真实 WebSocket 反向连接，真实首条消息认证 |
| Device | Agent 内置 `SimulatorTransport` | **测试数据**：内存中生成符合固件 `frames.h` 尺寸与 CRC 的真实二进制帧，经真实帧解析器解码 |
| 前端 | `site-server/web`（Platform Shell） | 真实浏览器访问 `http://127.0.0.1:8790/platform/` |

> **明确标注**：Device 为 `sim-c5-000` / `sim-c5-001`，传输类型为 `simulator`，**不是真实硬件在线状态**。
> 本文所有数字与截图若用于交付，必须保留「本机验证环境 / 模拟设备」标注，不得表述为生产硬件运行结果。

运行目录（临时，不进入仓库）：`/tmp/widscan58-phase08/`
- `site.yaml`（Site Server 配置，`allow_url_credential=false`）
- `agent.yaml`（Agent 配置，`site.enabled=true`、首条消息认证、`require_auth=true`）
- `site.db`（真实 SQLite）

启动命令：

```bash
# 1) Site Server（端口 8790，SQLite 文件库）
cd /tmp/widscan58-phase08
PYTHONPATH=<repo>/site-server/src python3 -m widscan_site.main --config site.yaml --host 127.0.0.1 --port 8790

# 2) 配对（REST）
curl -X POST http://127.0.0.1:8790/api/v1/agents/pairing-codes -d '{"ttl_seconds":300,"max_active":5}'
curl -X POST http://127.0.0.1:8790/api/v1/agents/pair \
  -d '{"pairing_code":"...","agent_id":"gateway-001","agent_version":"1.1.0","platform":"darwin"}'

# 3) Gateway Agent（2 台模拟设备，命令 API 需 Token）
GATEWAY_API_TOKEN=phase08-local-token PYTHONPATH=<repo>/gateway-agent/src \
  python3 -m widscan_agent.cli --config agent.yaml --simulators 2 --host 127.0.0.1 --port 8791
```

前端地址：`http://127.0.0.1:8790/platform/#/overview`；原始 Console：`http://127.0.0.1:8790/app/`。

---

## 2. 逐项验证结果

### 2.1 Site API

| 项 | 结果 | 实测证据 |
|---|---|---|
| `GET /health` | PASS | `{"ok":true,"site_id":"site-001"}` |
| `GET /api/v1/site` | PASS | 返回真实 `name`（来自 yaml）、`timezone`、`status`、`counts` |
| `counts.agents_total / agents_online / devices_total / devices_online / measurements_total` | PASS | 随真实数据变化（0/0 → 1/1、2/2、35196） |
| `GET /api/v1/sites`（多站点列表） | **未实现（404）** | `curl` 返回 404；平台 Sites 页标注「多站点未实现」 |
| `site.description` / `location` | 部分缺陷 | yaml 中已填 `description`/`location`，`bootstrap_default_site` 只写入 `name`/`timezone`，故 API 返回空串 |

### 2.2 Gateway Agent API

| 项 | 结果 | 实测证据 |
|---|---|---|
| `GET /api/v1/agents` | PASS | `gateway-001`，`name`/`agent_version=1.1.0`/`platform=darwin`/`os_info`/`credential_id`/`device_count=2` |
| `status`（在线/离线） | PASS | 连接后 `online`；Agent 进程退出后立即 `offline`；重启后自动恢复 `online` |
| `last_seen_at` | PASS | 每次心跳更新（`17:38:47` → 后续心跳时间） |
| `GET /api/v1/agents/{id}`（详情） | **未实现（404）** | 平台详情页用列表数据渲染 + 标注「无详情接口」 |
| 凭证状态端点 | **未实现** | 平台显示「凭证状态：未提供（无 REST 端点）」 |
| 配对 / 吊销 | PASS（接口可用） | `pairing-codes` → `pair` 成功；平台未提供操作入口（只读） |

### 2.3 Device API

| 项 | 结果 | 实测证据 |
|---|---|---|
| `GET /api/v1/devices` | PASS | 2 台：`sim-c5-000`、`sim-c5-001`，`status=online`，`owner_agent_id=gateway-001`，`ownership_status=owned` |
| `endpoints[]` | PASS | `transport_type=simulator`（**真实字段，08.4 结论已修正**）、`status=connected`、`last_connected_at` |
| `GET /api/v1/devices/{id}` | PASS | 身份、所有权、端点、会话、最近 20 条 Measurement |
| 会话历史 `sessions` | PASS | 3 次断开（`disconnect_reason=agent_offline`）+ 当前 connected，与 Agent 重启次数一致 |
| `?transport_type=` 查询参数 | **无效（未过滤）** | 传 `usb_serial` 仍返回 2 台 simulator 设备；平台显式标注「后端入参未生效」 |
| `devices_pending` 语义 | 说明 | 计数 = `adopted_at IS NULL`（与「待认领」同义，不是通信状态） |
| `last_seen_at` 语义 | 说明 | 仅在 `device.online` / `device.discovered` 更新，**不随 Measurement 更新**；平台列名「最后上报」会长期停在上线时刻 |
| identity_confidence=low | 未触发 | 本环境所有设备 `high`；只读逻辑未实际触发（保留为未验证项） |

### 2.4 Measurement API

| 项 | 结果 | 实测证据 |
|---|---|---|
| `GET /api/v1/devices/{device_id}/measurements` | PASS | 返回真实帧：METRICS 34341 / SPECTRUM 199 / TELEMETRY 663 |
| `frame_type` 过滤 | PASS | `?frame_type=TELEMETRY` 仅返回 TELEMETRY |
| `limit` / `offset` 分页 | PASS | `limit=100` 生效；页码按钮按 offset 翻页 |
| `id` / `sequence` / `event_id` / `payload` | PASS | 逐条可见，payload 可展开 |
| `created_at`（Site Received） | PASS | 真实入库时间 |
| `device_timestamp_ms` | PASS（部分为空） | METRICS 有值；SPECTRUM 帧本身无该字段 → `null`（协议特性，非遗漏） |
| `agent_received_at` | **已落库但接口未返回** | 数据库 100% 有值；REST 响应无该字段 → 平台显示「接口未返回」 |
| `site_received_at` 独立字段 | 未提供 | 仅能用 `created_at` 表示 |
| `GET /api/v1/measurements`（站点级） | **未实现（404）** | 只能按 Device 查询 |
| 时间范围过滤 | **未实现** | 平台标注「后端未提供时间过滤」 |
| `total`（分页总数） | 未提供（`/stats` 可用） | 列表无总数；`GET /api/v1/measurements/stats` 返回 `total` + `last_created_at` |
| `GET /api/v1/measurements/stats` | PASS | `total=23201 → 23298`（5 秒内真实增长） |

### 2.5 时间戳与 Event ID 完整性（真实数据）

以运行结束时的 `site.db` 校验：

| 校验 | 结果 |
|---|---|
| `event_id` 非空 | 100%（0 条为空） |
| `agent_received_at` 非空 | 100% |
| `agent_received_at > created_at`（因果倒置） | **0 条** |
| `measurements.distinct event_id` | 与行数一致（无冲突；`uq_measurement_event` 生效） |
| `diagnostics.ingestion` | `persisted=N`、`agent_provided_event_id=N`、`duplicate_events=0`、`invalid_event_id=0`、`integrity_errors=0` |
| 设备重启隔离 | 6 个不同 `device_boot_id`（3 次 Agent 重启 × 2 设备），event_id 命名空间不冲突 |

结论：Phase 07.3 时间戳完整性与 Phase 07.4 Event ID 幂等在真实 Agent → Site Server 链路中复现通过。

### 2.6 WebSocket 实时事件

| 项 | 结果 |
|---|---|
| `/ws/v1/site`（前端事件流） | **未实现**（`main.py` 文档字符串声称存在，实际无该路由；平台状态栏显示「未实现（轮询）」） |
| 替代方案 | 轮询：Gateways/Devices 5s、Overview/Measurements 10s、Sites/System 15s（08.2 已确认） |
| 轮询有效性 | PASS（可见标签页）：Measurement `30737 → 31317`，状态栏时间 `02:05:29 → 02:06:01` |
| 隐藏标签页行为 | 轮询暂停（`document.hidden` 时只重排定时器不取数），切回前台立即刷新；**这是设计行为，不是故障** |

> 说明：浏览器自动化首轮「无实时更新」的观测，原因是标签页处于后台（`visibilityState=hidden`），并非轮询失效。激活标签页后实测恢复。

### 2.7 在线状态 / 断线 / 恢复

| 场景 | 操作 | 结果 |
|---|---|---|
| Agent 掉线 | 终止 Agent 进程 | Site：`agents_online=0`；设备 `status=offline`、端点 `disconnected`；`active_agent_connections=[]`；平台 Overview `0 / 1`、`0 / 2`，Gateways 徽标「离线」 ✔ |
| Agent 重连 | 重启 Agent 进程 | 自动认证成功 + `devices_snapshot`；设备恢复 `online`；平台 `1 / 1`、`2 / 2` ✔ |
| Site Server 宕机 | 终止 Site Server | 平台徽标「Site Server 不可达」，各页错误条 `NET /api/v1/... — 网络不可达` + 「显示的是上一次成功数据」+ 重试；**不崩溃、不清空** ✔ |
| Site Server 恢复 | 重启 Site Server（同一 SQLite） | 数据完整保留；Agent 指数退避重连（第 5 次尝试后成功）；平台自动恢复「已连接」、错误条消失，**无需手动刷新** ✔ |
| 页面刷新 | 浏览器重载 | 路由与数据恢复，计数继续增长 ✔ |

### 2.8 原始 RF Console 独立入口

| 项 | 结果 | 证据 |
|---|---|---|
| `/app/` 静态托管 | PASS | HTTP 200，`text/html` |
| Console 页面可运行 | PASS | `document.title=Widscan58`，`h1=Widscan58`，9 个 `<canvas>`，模式按钮 Monitor/Lap timer/Power meter 与 Connect 均在 |
| 设备连接链路未被平台改写 | PASS（静态确认 + 页面加载） | Console 仍使用自身 Web Serial / WebUSB / WS bridge / Replay；平台仅提供链接，不注入数据、不内嵌 |
| Web Serial 真实硬件连接 | **本环境未验证** | 无硬件、自动化浏览器不支持串口授权；属于「未验证」而非「通过」 |

---

## 3. 数据分类清单

| 类别 | 内容 |
|---|---|
| 真实后端数据 | Site 记录、Agent 记录与心跳、Device 记录与端点/会话、Measurement 全部行、Diagnostics 与 Ingestion 计数、`measurements/stats` |
| 本地模拟数据（明确标注） | 2 台 `SimulatorTransport` 设备（`sim-c5-*`，传输类型 `simulator`），帧由模拟器生成但走真实协议/解析/入库链路 |
| Mock 数据 | **无**。平台前端无硬编码设备、无硬编码计数（`app.js` 中不含 `sim-c5` / 具体计数字面量）；生产模式不会加载 Mock |
| 暂未实现的数据 | 多站点列表、Agent 详情与凭证状态、告警、站点级 Measurement 列表、时间范围过滤、Measurement 总数、`agent_received_at` 展示、前端事件 WebSocket |

---

## 4. 接口缺口清单（只记录，未修改冻结协议）

| # | 缺口 | 影响页面 | 现有表现 | 建议（后续阶段） |
|---|---|---|---|---|
| 1 | `GET /api/v1/sites` 不存在；实例本身单站点 | Sites | 标注「单站点部署」 | 需产品决策是否引入多站点实例 |
| 2 | Measurement 响应不含 `agent_received_at` | Measurements / Device 详情 | 列显示「接口未返回」 | 在响应模型增加只读字段（需协议评审） |
| 3 | 无站点级 `/api/v1/measurements`、无时间范围、无总数 | Measurements | 仅按 Device + limit/offset | 增加查询参数与 total，需评审 |
| 4 | 无 `/api/v1/agents/{id}`、无凭证状态、无告警端点 | Gateways 详情 / Overview 告警 | 用列表数据 + 「未实现」标注 | 后续阶段补齐 |
| 5 | `/ws/v1/site` 未实现 | 全平台实时性 | 轮询（5–15s）；状态栏标注「未实现（轮询）」 | 实现前端事件流后再替换轮询 |
| 6 | `devices?transport_type=` 参数被接受但未过滤 | Devices | 提示「后端入参未生效」 | 后端补过滤逻辑 |
| 7 | `site.description` / `location` 未生效 | Sites 详情 | 空串 | 修正 bootstrap 写入或 PATCH |
| 8 | Device `last_seen_at` 不随帧更新 | Devices「最后上报」 | 长期停留在上线时刻 | 语义定义或改为 frame 时间 |
| 9 | REST 无鉴权、无 CORS | 全局 | 同源部署下可用 | 生产暴露前必须补齐 |
| 10 | `measurements_stats` 使用 ORM Query 传 `execute()` | 系统 | 仅 DeprecationWarning | 改为 Core `select()` |

---

## 5. 对 08.4 结论的修正

| 08.4 原表述 | 实测修正 |
|---|---|
| 「Device 传输类型未提供」 | 不准确。`endpoints[].transport_type` **有真实值**（本环境 `simulator`），详情页「传输」列已正确显示；未生效的只是 `?transport_type=` **过滤参数**。 |
| 「平台轮询实时性未验证」 | 已验证：可见标签页每 5/10/15s 真实取数；隐藏标签页暂停轮询属设计行为。 |
| 「REST 无鉴权为安全债」 | 保持：确认为真实缺口（本阶段不改）。 |

前端无需因此修改代码（无功能性缺陷）；唯一发现的「重复文字」现象来自浏览器翻译扩展注入，不属于应用代码问题。

---

## 6. 本轮实际测试记录

| 测试 | 命令 | 时间 | collected | passed | failed | skipped |
|---|---|---|---|---|---|---|
| Site Server 全量 | `cd site-server && PYTHONPATH=src python3 -m pytest -q` | 2026-11-05 01:35 | 74 | **74** | 0 | 0 |
| Gateway Agent 全量 | `cd gateway-agent && python3 -m pytest -q` | 2026-11-05 02:09 | 95 | **95** | 0 | 0 |
| Platform UI 专项 | `cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase08_platform_ui.py -q` | 2026-11-05 01:35 | 5 | **5** | 0 | 0 |
| Console 单元测试 | `cd repo/web && npx vitest run` | 2026-11-05 01:35 | 56 | **56** | 0 | 0 |
| Console 构建 | `cd repo/web && npm run build` | 2026-11-05 01:35 | — | 成功 | — | — |

运行期间真实数据规模：Measurement > 35,000 条；Agent 重启 3 次、Site Server 重启 1 次；全部恢复无数据丢失。

---

## 7. 结论

**08.5：CONDITIONAL PASS**

- 已通过：Site / Agent / Device / Measurement 四类真实数据接入；在线/离线与断线恢复；三段时间戳落库完整性；Event ID 幂等；刷新恢复；Console 独立入口加载。
- 条件：前端实时事件流（`/ws/v1/site`）未实现，以轮询替代并显式标注；`agent_received_at` 落库但未暴露；多站点、Agent 详情、告警、站点级 Measurement 查询为接口缺口。
- 未验证：Web Serial / WebUSB 真实硬件链路（无硬件，需在有设备环境补测）。

**本子阶段完成，等待人工确认后进入 08.6（功能验收与真实运行截图）。**
