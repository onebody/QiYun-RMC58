# Phase 08 — Platform UI Integration 验收报告

> 项目：QiYun / widscan58
> 阶段：Phase 08（08.1 – 08.6）
> 验收日期：2026-11-05
> 最终结论：**CONDITIONAL PASS**

---

## 1. 总结

| 维度 | 结果 |
|---|---|
| 统一平台入口 | 已交付：Site Server 同源托管 `/platform/`，Hash 路由 7 个页面，原始 Console 以 `/app/` 独立模块接入 |
| 原始 Console 能力 | 未改动任何代码；构建通过；可从平台外链进入，也可脱离 Site Server 独立运行 |
| 平台管理数据 | 全部来自真实 Site Server REST API；无 Mock、无硬编码、无假图表 |
| 未实现能力 | 11 处缺口，全部在 UI 中显式标注「未实现 / 未提供」，未包装为可用 |
| 冻结协议 | 设备协议、事件协议、数据模型、Phase 07 修复均未修改 |
| 最终判定 | **CONDITIONAL PASS**（2 项硬件相关验收未验证，1 项为模拟数据下验证） |

---

## 2. 验收环境与数据性质

- Site Server：真实进程，真实 SQLite（`/tmp/widscan58-phase08/site.db`），`allow_url_credential=false`
- Gateway Agent：真实进程，真实 WebSocket 首条消息认证，`--simulators 2`
- Device：`sim-c5-000` / `sim-c5-001`，传输类型 `simulator` —— **明确为测试数据，不是真实硬件在线**
- RF Console：`/app/`，截图包含「Disconnected（未连接）」与「Simulated device（模拟设备）」两种真实状态
- 浏览器视口：1512×724 CSS px / 3024×1448 物理像素（Retina 2x，笔记本屏幕）
- 运行期间真实数据量：Measurement > 35,000 条；Agent 重启 4 次、Site Server 重启 1 次

---

## 3. 12 项功能验收

| # | 验收项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 平台首页真实加载 Site 数据 | **PASS** | `#/overview` 显示真实 `site-001`、名称来自 yaml、`counts` 实时变化；`screenshots/phase08/phase08-overview.jpg` |
| 2 | Gateway Agent 列表展示真实 Agent | **PASS** | `gateway-001`、`agent_version=1.1.0`、`darwin`、`device_count=2`、最后心跳；`phase08-gateways.jpg` |
| 3 | Device 列表展示真实 Device | **PASS** | `sim-c5-000/001`、`online`、`owned`、`gateway-001`、端点 `connected`；`phase08-devices.jpg` |
| 4 | Measurement 页面展示真实测量数据 | **PASS** | METRICS/SPECTRUM/TELEMETRY 真实行；Frame 过滤与 limit/offset 生效；`phase08-measurements.jpg` |
| 5 | 实时状态更新正常 | **PASS**（轮询） | 可见标签页 30s 内 Measurement `30737 → 31317`；`/ws/v1/site` 未实现，状态栏显式标注「WebSocket：未实现（轮询）」 |
| 6 | 断开连接后正确显示离线状态 | **PASS** | 终止 Agent → Overview `Gateway Agent 0 / 1`、`Device 0 / 2`，Gateways 徽标「离线」；`phase08-offline-state.jpg` |
| 7 | 刷新页面后状态能够恢复 | **PASS** | 重载后路由与数据恢复；Server 重启后同源页面自动回到「已连接」，错误条消失，无需手动刷新 |
| 8 | 原始 RF Console 可独立运行 | **PASS** | `/app/` HTTP 200，标题 `Widscan58`，Channels/Band Panorama/Waterfall/Protocol Console 完整；`phase08-rf-console.jpg` |
| 9 | 原始设备连接功能正常 | **NOT VERIFIED** | 无真实硬件；自动化浏览器无法授权 Web Serial/USB。Console 未连接状态截图如实呈现，未伪装 |
| 10 | 原始频谱显示功能正常 | **CONDITIONAL PASS** | 模拟设备下 8 通道频谱与 Band Panorama 正常实时绘制并标注 synthetic；`phase08-rf-console-simulator.jpg`。真实硬件频谱未验证 |
| 11 | Electron 入口没有被破坏 | **PASS** | `repo/web` 与 `repo/web-desktop` 零改动；Console 构建成功；`web-desktop/dist` 与 `web/dist` 产物存在（未启动 GUI 交互验证） |
| 12 | 不存在 UI 集成引入的关键回归 | **PASS** | Site Server 74 / Gateway Agent 95 / Console 56 / Platform 5 全通过；构建成功；现有 `/api/v1/*` 与 `/health` 未被平台挂载影响 |

---

## 4. 测试记录（本轮实际执行）

| 测试 | 命令 | 时间 | collected | passed | failed | skipped |
|---|---|---|---|---|---|---|
| Site Server 全量 | `cd site-server && PYTHONPATH=src python3 -m pytest -q` | 2026-11-05 | 74 | **74** | 0 | 0 |
| Gateway Agent 全量 | `cd gateway-agent && python3 -m pytest -q` | 2026-11-05 | 95 | **95** | 0 | 0 |
| Platform UI 专项 | `pytest tests/test_phase08_platform_ui.py -q` | 2026-11-05 | 5 | **5** | 0 | 0 |
| Console 单元测试 | `cd repo/web && npx vitest run` | 2026-11-05 | 56 | **56** | 0 | 0 |
| Console 构建 | `cd repo/web && npm run build` | 2026-11-05 | — | 成功（`✓ built`） | — | — |
| 静态语法检查 | `node --check site-server/web/app.js` | 2026-11-05 | — | 通过 | — | — |

API 连通性（真实实例，`curl` 实测）：`/health` 200、`/api/v1/site` 200、`/api/v1/agents` 200、`/api/v1/devices` 200、`/api/v1/devices/{id}` 200、`/api/v1/devices/{id}/measurements` 200、`/api/v1/measurements/stats` 200、`/api/v1/diagnostics` 200、`/api/v1/sites` **404（未实现）**、`/api/v1/agents/{id}` **404（未实现）**。

---

## 5. 截图清单

目录：`docs/screenshots/phase08/`（全部来自实际运行实例，均为 3024×1448 物理像素）

| 文件 | 内容 | 数据性质 |
|---|---|---|
| `phase08-overview.jpg` | 平台 Overview | 真实 API |
| `phase08-sites.jpg` | Site 管理 | 真实 API（单站点） |
| `phase08-gateways.jpg` | Gateway Agent 列表 | 真实 API |
| `phase08-devices.jpg` | Device 列表 | 真实 API（simulator 设备） |
| `phase08-measurements.jpg` | Measurement 数据 | 真实 API（simulator 帧） |
| `phase08-rf-console.jpg` | 原始 Console（Disconnected） | 真实未连接状态 |
| `phase08-rf-console-simulator.jpg` | 原始 Console 模拟频谱 | **模拟数据（页面已标注 synthetic）** |
| `phase08-live-t1.jpg` | Overview 实时更新 T1（Measurement 313047） | 真实 API |
| `phase08-live-t2.jpg` | Overview 实时更新 T2（Measurement 318514，+5467） | 真实 API |
| `phase08-offline-state.jpg` | 断线离线状态（0/1、0/2、离线徽标） | 真实 API |

> 说明：模拟设备相关截图**不是**生产硬件运行截图，交付与宣传时必须保留「本机验证 / 模拟设备」标注。

---

## 6. 本阶段文件清单

### 新增

| 文件 | 说明 |
|---|---|
| `docs/PHASE08_1_FRONTEND_ASSET_AUDIT.md` | 前端资产审计（10 项要求全覆盖） |
| `docs/PHASE08_2_PLATFORM_INFORMATION_ARCHITECTURE.md` | 统一信息架构（7 页面 + 路由 + 缺口矩阵） |
| `docs/PHASE08_3_UI_DESIGN_SYSTEM.md` | 统一 UI 设计规范 |
| `docs/PHASE08_4_PLATFORM_UI_INTEGRATION.md` | 入口与导航集成实现说明 |
| `docs/PHASE08_5_REAL_DATA_INTEGRATION.md` | 真实数据集成验证 |
| `docs/PHASE08_ACCEPTANCE_REPORT.md` | 本报告 |
| `docs/screenshots/phase08/*.jpg` | 10 张真实运行截图 |
| `site-server/web/index.html` | Platform Shell 页面骨架 |
| `site-server/web/styles.css` | 平台样式（08.3 Token 落地） |
| `site-server/web/app.js` | 路由 / API / 轮询 / 7 页面渲染 |
| `site-server/tests/test_phase08_platform_ui.py` | 平台挂载与回归测试（5 条） |

### 修改

| 文件 | 变更 |
|---|---|
| `site-server/src/widscan_site/main.py` | 新增 `/platform` 静态挂载与 `/`→`/platform/` 重定向；可选挂载 `/app`（`SITE_CONSOLE_DIR`，不存在则跳过） |
| `site-server/web/app.js` | 08.6 收尾：Event ID 改为单行省略显示（`truncCell`），Boot ID 取消强换行，Payload 列前移，表格行高由 ~71px 降至 36px |
| `site-server/web/styles.css` | 新增 `.pf-trunc` 规则（单行省略 + title 显示完整值） |

### 未改动（关键）

`repo/web/**`（原始 Console）、`repo/web-desktop/**`（Electron）、`gateway-agent/**`、数据库模型与 Alembic 迁移、设备/事件协议、Phase 07 修复代码。

---

## 7. 未实现 / 缺口清单（记录，不在本阶段处理）

1. `GET /api/v1/sites` 不存在（单站点实例），平台 Sites 页已标注
2. Measurement 响应不含 `agent_received_at`（库内 100% 有值），列显示「接口未返回」
3. 无站点级 Measurement 列表、无时间范围过滤、分页无 `total`
4. 无 `/api/v1/agents/{id}`、无凭证状态端点、无告警端点
5. `/ws/v1/site` 未实现 → 轮询替代（5/10/15s），状态栏显式标注
6. `devices?transport_type=` 参数被接受但未过滤
7. `site.description` / `location` 配置写入未生效
8. Device `last_seen_at` 不随帧更新，「最后上报」会停在上线时刻
9. REST 无鉴权、无 CORS（同源内网可用；对外暴露前必须补齐）
10. `measurements_stats` 存在 `SADeprecationWarning`（ORM Query 被丢弃）
11. 无真实告警数据源（Overview「最近告警」显示未实现）

---

## 8. 未验证项（必须明示）

| 项 | 原因 | 复验条件 |
|---|---|---|
| 真实硬件 Web Serial / WebUSB 连接（验收 #9） | 无 WIDSCAN58 硬件；自动化浏览器无串口授权 | 在有设备的环境手工连接一次 `/app/` |
| 真实硬件频谱 / Waterfall（验收 #10 的真实部分） | 同上 | 同上 |
| Electron GUI 实际启动交互（验收 #11 的运行时部分） | 未在自动化中启动桌面窗口 | 在桌面环境执行一次 `web-desktop` 启动 |

---

## 9. 结论

**Phase 08 最终判定：CONDITIONAL PASS**

理由：
- 统一平台入口、导航、设计规范、真实数据接入均已实现并通过实际运行验证（10/12 项 PASS）。
- 条件一：验收 #10 仅在模拟设备下证明频谱渲染正常，真实硬件频谱未验证；#9 完全未验证。
- 条件二：前端实时事件流（`/ws/v1/site`）未实现，采用轮询，已在 UI 与文档中显式标注，不构成「伪可用」。
- 条件三：11 项接口缺口只做记录与显式呈现，未修改任何冻结协议或数据模型。

**不存在 BLOCKED 项；不将任何未验证能力标记为 PASS。**

---

## 附录：复现步骤

```bash
# 1) 构建原始 Console（产物同时供 /app 挂载）
cd repo/web && npm install && npm run build

# 2) 启动 Site Server（会同时挂载 /platform 与 /app）
cd site-server && PYTHONPATH=src python3 -m widscan_site.main \
  --config <site.yaml> --host 127.0.0.1 --port 8790

# 3) 配对并启动 Gateway Agent（2 台模拟设备）
curl -X POST http://127.0.0.1:8790/api/v1/agents/pairing-codes -d '{"ttl_seconds":300}'
curl -X POST http://127.0.0.1:8790/api/v1/agents/pair \
  -d '{"pairing_code":"...","agent_id":"gateway-001","agent_version":"1.1.0"}'
GATEWAY_API_TOKEN=<token> PYTHONPATH=repo/gateway-agent/src \
  python3 -m widscan_agent.cli --config <agent.yaml> --simulators 2

# 4) 打开平台与 Console
open http://127.0.0.1:8790/platform/#/overview
open http://127.0.0.1:8790/app/
```
