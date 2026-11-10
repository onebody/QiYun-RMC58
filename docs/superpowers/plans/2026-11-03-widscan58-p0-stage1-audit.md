# widscan58 双工作区平台 · P0 阶段 1（代码盘点与功能差异确认）

> **本文件只做审计与清单，不含任何实现。** 用户指令的闸口是"确认没有与现有设计文档冲突后，再开始实际编码"，所以本文的最后一段（§6 冲突申报）是必须逐条拍板的前置件。文中所有"P0 处置"都是**计划**，不是完成状态。

**冻结基线（用户 2026-11-03 五条决策）**：① 本期不新增 C5 ASCII 固件命令，必要缺口单独提交协议扩展方案；② Gateway 行本身作设备准入载体，节点保留独立 `node_id` 与 `boot_id`；③ 场地平面图走用户上传图片 + 本地服务器存储；④ P0 只做 PER_NODE 与 MAX 两种频谱展示及聚合语义；⑤ `sample_summary` 增 `floor_dbm`/`snr_db`/`peak_dbm` 三个可空字段并兼容旧数据。

---

## 0. 本轮实测基线（阶段 7 的"数字只增不减"起点）

在 `fw-wifi-relay` 分支、当前工作区状态（并行会话的 6 个固件/计划文件未提交、本设计不碰）下实测：

| 门槛 | 命令（工作目录如括注） | 本轮实测 | 既有记录 |
|---|---|---|---|
| 后端 pytest | `python3 -m pytest tests`（`rftrack/backend/`） | **617 passed / 1 skipped**（93.0 s，合计 618） | 618/1 — 一致 |
| 工作台单测 | `node --test test/*.test.mjs`（`rftrack/frontend/workbench/`） | **214 pass / 0 fail** | 214 — 一致 |
| 真浏览器冒烟 | `node rftrack/frontend/e2e/workbench.mjs` | **通过 65 / 65**（52.3 s，未捕获异常 0） | 65 — 一致 |
| 原 `web/` 单测 | `npx vitest run`（`web/`） | **10 文件 / 56 passed** | 56 — 一致 |
| 固件 native | `pio test -e native`（`firmware/`） | **本轮未复跑** | 34 — 沿用记录 |

固件那条如实标注未复跑：P0 承诺 `firmware/` 一行不碰（§7 可验证为 `git diff --stat HEAD -- firmware web serve.mjs` 除白名单文件外为空），所以不复跑不构成验收缺口；但阶段 2 一旦需要动 `web/`，本行必须补测。

**阶段 2 出口（2026-11-03，作为阶段 3 的"只增不减"起点）**：后端 **640 passed / 1 skipped（合计 641）**（85.5 s，+23 条 = `test_console_mount.py` 6 + `test_info_line.py` 17）；工作台 **214 / 0 fail**；真浏览器冒烟 **65 / 65**（51.1 s）；`web/` vitest **56 passed**。`web/` 与 `serve.mjs` 在本阶段零改动（`git status --short -- web serve.mjs` 为空），所以固件 native 那行仍未复跑、口径不变。

> 订正一处：上一版这里写的是"641 passed / 1 skipped"，把 junit 的 `tests` 属性（合计数）当成了 passed。真实数字是合计 641、passed 640、skipped 1——在 `git archive HEAD` 的干净副本上重跑核过（该副本另有一条 `test_replay.py` 失败，原因是它依赖被 gitignore 的本地夹具目录，与代码无关）。

**阶段 3.1 出口（2026-11-03 实测，作为阶段 3.2–3.4 的起点）**：后端 **649 passed / 1 skipped（合计 650）**，比阶段 2 出口 +9 条 = `test_node_identity.py` 5 + `test_info_line.py` 的 `identity_source` 四态 4；`test_migrations.py` 15 条原样绿（两条建库路径对拍是本迁移的回归线）。其余三门槛本轮未复跑（3.1 只动后端与迁移，未碰前端）。

注：`python` 在本机不存在，只有 `python3`；`pytest -q` 在本机不打印汇总行时改用 `--junit-xml` 读属性。

---

## 1. 原有控制台功能差异表（按用户 §二 的 18 项）

判定图例：`✅` 已实现且真跑；`🟡` 部分（缺列/缺口径/缺一侧）；`❌` 缺；`⛔` 单机专属，平台侧不移植（依赖 Web Serial 或物理串口独占）。

| # | 原 Console 功能 | 原实现证据（仓库相对路径） | 单机侧 `/app` | 平台侧 Site Monitoring 现状 | P0 处置 | 动固件 |
|---|---|---|---|---|---|---|
| 1 | USB Serial 连接 | [web/serial/device.ts](../../../web/src/serial/device.ts)、[web/src/main.ts:793](../../../web/src/main.ts#L793) `ws://<host>/serial?ep=`、一板一页锁 [web/serve.mjs:172](../../../web/serve.mjs#L172) | ✅ Web Serial 直连 + 桥透传两路 | 🟡 平台只有 `/ws/bridge/{ep}` 透传给 serve.mjs（[app/routers/ws.py:20](../../../rftrack/backend/app/routers/ws.py#L20)），`/app` 页面尚未由后端发出（[app/main.py:78](../../../rftrack/backend/app/main.py#L78) 只挂 `/workbench`） | 阶段 2 ✅：后端加 `/app` 静态挂载（可选件，缺失不算故障），Web Serial 直连路径**原样保留** | 否 |
| 2 | 设备身份及固件信息 | INFO 行口径在 [web/src/model/store.ts:85-93](../../../web/src/model/store.ts#L85) `parseInfo`（`widscan58\|target\|proto\|fw\|commit\|phy`） | ✅ | ❌（阶段 1 快照，行号现已移动）后端把文本行**只计数不解析**（[capture.py:626-627](../../../rftrack/backend/app/services/capture.py#L626) `piece.kind == "text"` → `self.text_lines += 1`），`Node.fw_version`/`protocol_version`（[models.py:231](../../../rftrack/backend/app/models.py#L231)）只有手工 `PUT /api/nodes/{id}` 会填 | 阶段 2 ✅：在 capture 的 text 分支解析 INFO（纯 ASCII 行，不改帧、不加命令），落 `Node.fw_version`/`protocol_version` 两列；target/commit/phy 三样等 `007_node_identity` 给列；**阶段 3.1 ✅**：`007_node_identity` 落了身份四列 + 启动会话三列，INFO 全套入库并标 `identity_source='info_line'`，`PUT /api/nodes/{id}` 声明版本时标 `manual` | **否**（复用现有协议） |
| 3 | 频道配置 | [views/channelPlanDialog.ts:29](../../../web/src/views/channelPlanDialog.ts#L29)、[model/channelPlan.ts:11-24](../../../web/src/model/channelPlan.ts#L11)（`BANDS`、`MAX_CHANNELS=8`） | ✅ 8 槽 + 混合频段 | ❌ **表有、API 无、UI 无**：`ChannelPlan`/`ChannelPlanItem` 存在于 [models.py:223/244](../../../rftrack/backend/app/models.py#L223)，但 `app/routers/` 与 `app/schemas.py` 全文无引用（本轮 grep 证实） | 阶段 4：`/api/channel-plans` **只读**（C-8 已裁定）；下发到板子留到方案 C 的会话写权落地后单独一轮 | 否 |
| 4 | 扫描控制（START/STOP、MODE、GAIN） | [views/controls.ts:11](../../../web/src/views/controls.ts#L11) | ✅ | 🟡 平台只有桥级 `POST /api/board/mode`（scan\|rh，[app/routers/bridge.py:60](../../../rftrack/backend/app/routers/bridge.py#L60)），无节点级扫描控制 | 阶段 5：**只读态**（C-8 已裁定 P0 不做写侧）；下发 `START/STOP/MODE/GAIN` 留到方案 C 的 sticky 单写者落地后单独一轮，汇聚实例维持显式 405 | 否 |
| 5 | GROUP 模式 | [views/controls.ts:72](../../../web/src/views/controls.ts#L72)、[model/channelPlan.ts:45/52/69](../../../web/src/model/channelPlan.ts#L45)、格子口径 [views/channelGrid.ts:137](../../../web/src/views/channelGrid.ts#L137) | ✅ | 🟡 上报侧已有：`_point()` 把帧的 `mode` 原样放进环（[capture.py:1199-1220](../../../rftrack/backend/app/services/capture.py#L1199)），但摘要表无 mode 列、工作台不显示 LO 分组 | 阶段 4：工作台读现成 `mode` 事实并显示；不新增采集字段 | 否 |
| 6 | 实时频谱 | SPECTRUM 帧 [web/src/proto/frames.ts:191-203](../../../web/src/proto/frames.ts#L191)，全景 [views/panorama.ts:86](../../../web/src/views/panorama.ts#L86) | ✅ | ❌ 帧被丢弃：[capture.py:649-655](../../../rftrack/backend/app/services/capture.py#L649) 的丢弃名单含 spectrum；工作台 `spectrum-view`/`waterfall-view` 至今是 PLANNED（[registry.js:134-150](../../../rftrack/frontend/workbench/registry.js#L134)，其 reason 里 `capture.py:573` 行号已过期，实际 649） | 阶段 4/5：`010_spectrum_grid` + 丢弃分支拆分 + `spectrum-band.js`；决策 4 的 PER_NODE/MAX 落在这里 | 否 |
| 7 | 瀑布图 | 256 项色表 [web/src/util/canvas.ts:174-178](../../../web/src/util/canvas.ts#L174) | ✅ | ❌ 同上；且 `workbench/chart.js` **无** 256 项色表（只有 4 色系列 `palette`，[chart.js:143-148](../../../rftrack/frontend/workbench/chart.js#L143)） | 阶段 3：色表以 JSON 基线**对拍复用**（两套构建体系不能互相 import），阶段 4 出图 | 否 |
| 8 | RSSI / Noise Floor / SNR | 度量键 `rssi/rssiEma/floor/snr/clearFloor/peak/…`（[frames.ts:25](../../../web/src/proto/frames.ts#L25) `ChannelMetrics`） | ✅ | 🟡 入库只有 `rssi_mean/min/max/last` + `ema_*`（[models.py:431-443](../../../rftrack/backend/app/models.py#L431) `SampleSummary`，无 floor/snr/peak 列）；**数据在环里已可用**（`_point()` 不做字段挑选） | 阶段 4：决策 5 三列 `floor_dbm`/`snr_db`/`peak_dbm`，可空、旧行 NULL | 否 |
| 9 | 中心频率与 99% 占用带宽 | 格子 [views/channelGrid.ts:191-192](../../../web/src/views/channelGrid.ts#L191)，局部 [views/zoom.ts:157-160](../../../web/src/views/zoom.ts#L157) | ✅ | ❌ `centroidOffHz`/`obwLoHz`/`obwHiHz` 未入库、无面板 | 阶段 4：随 #8 一并出列（**C-7 已裁定按 A**），同一条 `_point()` 通路 | 否 |
| 10 | 信号峰值分析 | [views/zoom.ts:130-136](../../../web/src/views/zoom.ts#L130)（`peakOffHz`/`centroidOffHz`/`CHF.NO_CENTROID`） | ✅ | ❌ 同上 | 同 #9，随 `009_summary_metrics` 出列 | 否 |
| 11 | RAWIQ | [views/rawiq.ts:36](../../../web/src/views/rawiq.ts#L36) | ✅ | ⛔ 平台侧按约束 3 维持 `rawiq_persist=False`（[config.py:135](../../../rftrack/backend/app/config.py#L135)），RAWIQ 不进常规数据面 | P0 不做，验收写"平台侧仍不落地" | 否 |
| 12 | LEVELS | 前端 [model/store.ts:194](../../../web/src/model/store.ts#L194) `case FRAME_LEVELS`；后端 [frame_parser.py:205-249](../../../rftrack/backend/app/services/frame_parser.py#L205) 返回 `channels`，且 [capture.py:57-63](../../../rftrack/backend/app/services/capture.py#L57) `FRAME_NAMES` 丢弃名单**不含** levels | ✅ | ✅ 已入库（本轮源码翻案，早前一版差异表判 ❌ 已撤回） | 阶段 7 只补一条断言：LEVELS 帧计数在 30 min 真机窗内非零 | 否 |
| 13 | 录制与回放 | [serial/recorder.ts:5/39](../../../web/src/serial/recorder.ts#L5) `Recorder`/`ReplayTransport` | ✅ 文件级 | ✅ 平台有录制目录 → `feed(source="mock")`，来源不可洗白（[tests/test_replay.py](../../../rftrack/backend/tests/test_replay.py) 文档字符串四条口径） | 阶段 7 复跑，不改 | 否 |
| 14 | 局部设置 | [model/channelPlan.ts](../../../web/src/model/channelPlan.ts) `loadDeviceSettings`/`saveDeviceSettings`（localStorage） | ✅ | ❌ 无"节点本地设置"表/端点；`/api/settings/local-import` 是另一件事（导入审计，[models.py:350](../../../rftrack/backend/app/models.py#L350)） | 阶段 5：`node_local_setting`（P0 视 §6 C-7 决定做/不做） | 否 |
| 15 | 全局设置 | 平台侧 [app/routers/settings.py:54-197](../../../rftrack/backend/app/routers/settings.py#L54) | 🟡 前端只有 localStorage 口径 | ✅ `global` / `site/{id}` / `schema` / `local-import` 全套 | 阶段 3 只在入口上打通，不改语义 | 否 |
| 16 | 浏览器内固件烧录 | [views/flashDialog.ts:18](../../../web/src/views/flashDialog.ts#L18)、[serial/flasher.ts:93](../../../web/src/serial/flasher.ts#L93) `SUPPORTED_CHIPS` | ✅ | ⛔ 必须 Web Serial，不能经后端代理（会抢串口、且违反约束 4） | 保留在 `/app`，平台侧不做 | 否 |
| 17 | VTX 功率计 | [views/power.ts:24/93](../../../web/src/views/power.ts#L24)、[model/power.ts:7-9](../../../web/src/model/power.ts#L7)（`POWER_GAIN`/`POWER_WIN_HZ`/衰减与平滑） | ✅ 单机第三页 | ⛔ 站点侧不做功率计面板 | 保留在 `/app` | 否 |
| 18 | RotorHazard / 浏览器计时 | [views/timer.ts:43/194/310](../../../web/src/views/timer.ts#L43)、[views/rhServerDialog.ts:8](../../../web/src/views/rhServerDialog.ts#L8)、[proto/rh.ts:6-28](../../../web/src/proto/rh.ts#L6)、[serial/rhLink.ts:19](../../../web/src/serial/rhLink.ts#L19)、serve.mjs 的 `serial:`/`uart:` 与 `--rh-*-cmd` | ✅ 单机第二页 + RH 服务端读 | 🟡 平台能"切到 rh 态"（`POST /api/board/mode`）但无计圈面板；约束 9 要求 USB/UART/RH 路径不删 | 保留在 `/app` + serve.mjs；平台侧阶段 7 只做"切换态可见" | 否 |

**两个口径必须分开读**（这是前一版差异表犯过的错，本轮已修）：

- **`/app` 单机侧**：18 项**全部存在**且可用真板手测（`web/` 自 S0 起一行未碰，`npx vitest run` 56 全绿）。P0 对这一侧的承诺是"**不删除、不依赖网络、不改造**"，不是"重写"。
- **平台侧（Site Monitoring）**：❌/🟡 集中在 #2/#3/#4/#5/#6/#7/#8/#9/#10/#14，即身份解析、频道/控制面、频谱三视图、摘要缺列（决策 5 的三列 + C-7 裁定的四列）、节点本地设置。⛔ 三项（#11/#16/#17）+ #18 的计圈面板**按设计不移植**（C-7 口径 A）。

---

## 2. P0 文件修改清单（按用户 §六 的 7 阶段）

### 阶段 1（本文件）
只新增 [docs/superpowers/plans/2026-11-03-widscan58-p0-stage1-audit.md](2026-11-03-widscan58-p0-stage1-audit.md) 一个文件。零代码改动。

### 阶段 2 — 原有控制台功能恢复（**改动最小的一阶段**）
`/app` 侧不重写，只把"平台能发出这一页"打通，并让身份进库：

| 文件 | 动作 | 内容 |
|---|---|---|
| [rftrack/backend/app/config.py](../../../rftrack/backend/app/config.py) | 改 | 加 `web_dir: Path \| None`（env `RFTRACK_WEB_DIR`，空值 = 不挂，合法部署形态） |
| [rftrack/backend/app/main.py](../../../rftrack/backend/app/main.py) | 改 | 新增 `mount_console(app, settings)`，形状照现有 `mount_workbench`（[main.py:64-83](../../../rftrack/backend/app/main.py#L64)）：挂 `/app`，把"挂没挂、为什么"写进 `app.state`，**不进 `problems`** |
| [rftrack/backend/app/services/health 检查](../../../rftrack/backend/app/routers/health.py) | 改 | 健康页多一节 `console`（与现有 `workbench` 同字段：mounted/reason/dir/index） |
| [rftrack/backend/app/services/frame_parser.py](../../../rftrack/backend/app/services/frame_parser.py) | 改 | 加 `parse_info_line(text) -> dict \| None`（**纯文本、不碰六帧结构**） |
| [rftrack/backend/app/services/capture.py](../../../rftrack/backend/app/services/capture.py) | 改 | `:626-627` 的 text 分支：计数之后调 `parse_info_line`，命中则上报身份；不命中零行为变化 |
| [rftrack/backend/app/services/ingest.py](../../../rftrack/backend/app/services/ingest.py) | 改（只改模块注释） | 身份落库的**执行者其实是 `capture.py`**（节点行的归属键 `(gateway_id, transport_addr)` 只有在流上才成立，`reconcile` 看不见字节），所以这里改的是"写入路径只有这里 / 三列必须留 NULL"那句已经不成立的话 |
| tests | 新增 | `tests/test_console_mount.py`、`tests/test_info_line.py`（含畸形行、缺字段、旧固件只回 4 段） |

### 阶段 3 — 双工作区路由与组件复用
| 文件 | 动作 | 内容 |
|---|---|---|
| [rftrack/frontend/workbench/index.html](../../../rftrack/frontend/workbench/index.html) | 改 | 工作区入口：`Single Node Console`（→ `/app/`）与 `Site Monitoring`（→ 本页）；**不改** `web/` 内部 |
| [rftrack/frontend/workbench/app.js](../../../rftrack/frontend/workbench/app.js) | 改 | 顶栏加工作区切换与"当前是哪个工作区"的显式状态（多对象界面规则：不能只藏在 URL 里） |
| [rftrack/frontend/workbench/registry.js](../../../rftrack/frontend/workbench/registry.js) | 改 | PLANNED 条目按 #6/#7 转 live；每条带 `workspace` 标注（single-node / site），并把过期行号 `capture.py:573` 改 649 |
| 新增 `rftrack/frontend/workbench/vendor/waterfall-lut.json` + `web/src/util/canvas.lut.test.ts` + `workbench/test/waterfall-lut.test.mjs` | 新增 | 256 项色表的**双向对拍**基线（TS 与无构建 ESM 不能互相 import，这是唯一不重复开发的路） |
| 新增 `rftrack/frontend/workbench/components/spectrum-band.js` | 新增 | 频谱带（阶段 4 出数据后接入） |

### 阶段 4 — Site / Gateway / Node 数据模型（含决策 5、决策 4 的栅格）
| 文件 | 动作 | 内容 |
|---|---|---|
| [rftrack/backend/app/models.py](../../../rftrack/backend/app/models.py) | 改 | `Gateway`(:168) 加 `wifi_key`、`kind` 取值加 `wifi-relay`（C-6，零 DDL）；`Node`(:195) 加 `boot_id`/`boot_changed_at`/`time_sync_quality`；`SampleSummary`(:431) 加七列；新表 `candidate_node`、`spectrum_capture`、`site_plan`、`node_position` |
| [rftrack/backend/app/schemas.py](../../../rftrack/backend/app/schemas.py) | 改 | `GatewayOut`/`NodeOut` 新字段；`CandidateOut`/`AdoptIn`/`SitePlanOut`/`NodePositionIn` |
| [rftrack/backend/app/services/capture.py](../../../rftrack/backend/app/services/capture.py) | 改 | `:649-655` 丢弃分支拆三路：带 `channels` → 摘要（不变）；SPECTRUM → 栅格；RAWIQ/TELEMETRY/LOG → 仍只计数 |
| 新增 `rftrack/backend/app/services/spectrum_grid.py` | 新增 | 350-bin 分箱、同 detector 逐 bin 取最大（= MAX）、缺帧记账、detector 变更隔离 |
| [rftrack/backend/app/services/ingest.py](../../../rftrack/backend/app/services/ingest.py) | 改 | `:114-134` 自动建 Node 的口子改为写 `candidate_node`（R-3：发现 ≠ 认领） |
| 迁移 | 新增 | `alembic/versions/007…011`，见 §3 |

### 阶段 5 — 设备准入与发现基础
| 文件 | 动作 | 内容 |
|---|---|---|
| [rftrack/backend/app/routers/gateways.py](../../../rftrack/backend/app/routers/gateways.py) | 改 | 现有 5 端点(:37-:103) 加 `wifi_key` 登记/改派；准入 = 该行 `kind='wifi-relay'` 且 `status='active'`（`dialer.py:308` 本来就不拨非 active，零新逻辑） |
| 新增 `rftrack/backend/app/routers/discovery.py` | 新增 | `GET /api/candidates`、`POST /api/candidates/{id}/adopt`、`DELETE`（拒绝留痕）、`denied` 列表与"一键建草稿" |
| [rftrack/backend/app/routers/nodes.py](../../../rftrack/backend/app/routers/nodes.py) | 改 | 现有 3 端点(:52/:67/:75) 加归属改派与 `boot_id` 可见性；身份只读，不再靠手填 |
| tests | 新增 | `test_relay_admission.py`（未登记 key 拒入且留痕 / 已登记重拨不重复建行 / 登记幂等）、`test_discovery_adopt.py` |

### 阶段 6 — 场地平面图与节点位置
| 文件 | 动作 | 内容 |
|---|---|---|
| 新增 `rftrack/backend/app/routers/site_plans.py` | 新增 | 上传（multipart，限类型与字节）、多版、激活、静态读回；存本地目录、库里只存路径与 sha256 |
| 新增 `rftrack/backend/app/services/site_plan.py` | 新增 | 落盘、命名、激活版本切换、旧版不删 |
| 新增 `rftrack/backend/app/routers/node_positions.py` | 新增 | 节点 ↔ 平面图点位（x/y 归一 + 参考标定两点的比例尺） |
| 新增 `rftrack/frontend/workbench/components/site-plan-view.js` | 新增 | 图上标点、点位标签、盲区显示 |
| 迁移 | | `011_site_plan` |

### 阶段 7 — 自动化测试与回归
门槛复跑（§0 五条）+ 帧对拍不许放宽（`tests/test_frame_parity.py`、`tests/golden/frames-v1.json`）+ 一条 4 节点 ≥30 min 真机窗（判据见 §5）。

---

## 3. 数据库迁移清单

现状：`007_node_identity` **已于阶段 3.1 落地**，`alembic/versions/` 为 `001_baseline … 007_node_identity`，`008` 起空闲。两份设计都认领过 007，**C-5 已裁定合并为一次迁移**（方案 C 的 `007_inline_relay` 号位作废，`relay_enrollment` 表不建）；**号位 = 执行顺序**，故身份迁移占 007、relay ingress 顺延 008（用户 2026-11-03 裁定）。

| 迁移 | 内容 | 兼容性 | 对应决策 |
|---|---|---|---|
| `007_node_identity` | **已落地（阶段 3.1）**。`node` 追加七列，顺序即 `models.Node` 声明顺序：身份四列 `identity_source String(16) NULL`/`identity_target String(48) NULL`/`commit_time String(32) NULL`/`phy_version String(64) NULL` + 启动会话三列 `boot_id String(36) NULL`/`boot_changed_at NULL`/`time_sync_quality String(16) NOT NULL default 'unsynced'`；`(gateway_id, transport_addr)` 唯一键不动（[models.py:218](../../../rftrack/backend/app/models.py#L218)） | 七列全追加在末尾、旧行除 `unsynced` 外一律 `NULL`（不补造身份）；`up()` 按 `PRAGMA table_info` 跳过已有列所以 create_all 库也走得通；`down()` 只撤这七列 | 决策 1 + 2（身份来源可指认；无 `CLOCK?` ⇒ 会话派生，C-4） |
| `008_relay_ingress` | `gateway.wifi_key String(64) NULL` + 部分唯一索引；`gateway.kind` 加值 `wifi-relay`（裸 `String(16)` 无枚举约束，**零 DDL**，C-6）；`relay_denial` 留痕表；seed 一行内置接入机（幂等，判据三条）；**不建 `relay_enrollment`**（C-5） | 加列可空 + 只增行，旧行零影响 | 决策 2 |
| `009_summary_metrics` | `sample_summary` 加**七列** `REAL NULL`：决策 5 的 `floor_dbm`/`snr_db`/`peak_dbm` + C-7 裁定的 `centroid_off_hz`/`obw_lo_hz`/`obw_hi_hz`/`peak_off_hz`；`up()` 是七条 ADD COLUMN，`down()` 需要真身 | 旧行留 NULL，读侧必须容忍 NULL（不补 0，0 是合法 dBm） | 决策 5 + C-7 |
| `010_spectrum_grid` | 新表 `spectrum_capture`：`(node_id, grid_start_khz, bin_khz, detector, t_node_ms, t_rx_ms, bins blob, bin_count, missing_prev, sync_quality)`；写入量口径：栅格后 ≈0.35 KB/s·节点（8 节点 ≈ 0.24 GB/天） | 新表，不动现有任何表 | 决策 4 + C-2 |
| `011_site_plan` | 新表 `site_plan`（多版 + 激活标记 + 本地路径 + sha256）与 `node_position`（plan_id + node_id + x/y + 标定） | 新表 | 决策 3 |
| `012_aggregate` | **仅登记号位**，属内连接入设计 S11，不在 P0 | — | — |

撤表声明：`relay_enrollment` **不建**（决策 2 把准入载体定为 Gateway 行本身）；**C-5 已裁定**，方案 C 设计文档 §1.1/§1.2/§4.2-10 需按此回改（下一轮与本设计同批）。

---

## 4. API 变更清单

只增不改语义：现有 62 条端点（本轮从 `app/routers/` 逐条枚举）行为与状态码全部保持，下表除最后两条"改"以外全是新增。

| # | 端点 | 动作 | 说明 |
|---|---|---|---|
| 1 | `GET /app/*`（静态） | 新增（可选件） | 原 Console 由后端发出；`RFTRACK_WEB_DIR=` 时不挂，不算故障 |
| 2 | `GET /api/health` | 改（只加字段） | 多一节 `console`，与现有 `workbench` 同形状 |
| 3 | `POST /node?id=<key>` + `WS /node?id=<key>` | 新增 | 后端自己收 Wi-Fi 拨入；key 不匹配 → close + `relay_denial` 留痕 |
| 4 | `GET /api/gateways/{id}` 出参 | 改（只加字段） | `wifi_key`、`kind`、`admission_state` |
| 5 | `PUT /api/gateways/{id}/wifi-key` | 新增 | 登记/换 key；重复登记 409 `wifi_key_exists` |
| 6 | `GET /api/candidates` | 新增 | 发现列表（含 `first_seen`、`observations`、`claimed`）；**只读，不改状态** |
| 7 | `POST /api/candidates/{ref}/adopt` | 新增 | 认领 → 建/绑 `Node`；未登记网关上无候选可认领 |
| 8 | `DELETE /api/candidates/{ref}` | 新增 | 显式拒绝，落 `denied` |
| 9 | `GET /api/denied` | 新增 | 拒入留痕 + 一键建草稿的入口数据 |
| 10 | `GET /api/nodes/{id}` 出参 | 改（只加字段） | `boot_id`、`time_sync_quality`、`identity_source`、`fw_version` 改为 INFO 真值 |
| 11 | `GET /api/samples/summary` | 改（只加字段） | 新增七字段（决策 5 三列 + C-7 四列），旧桶回 `null` |
| 12 | `GET /api/spectrum?node=&mode=per_node\|max` | 新增 | 栅格读侧；`mode ∈ {median, avg_power}` 显式 **422** |
| 13 | `WS /ws/spectrum` | 新增 | 有界队列，沿用 `/ws/live` 的 `live_queue_limit=64` 丢最旧风格 |
| 14 | `POST /api/site-plans`（multipart） | 新增 | 上传图片，本地存储 |
| 15 | `GET /api/site-plans`、`POST /api/site-plans/{id}/activate` | 新增 | 多版 + 激活 |
| 16 | `GET /api/site-plans/{id}/image` | 新增 | 从本地文件发图 |
| 17 | `PUT /api/nodes/{id}/position`、`GET /api/sites/{id}/positions` | 新增 | 点位登记与批量读 |
| 18 | `GET /api/channel-plans`（+ items） | 新增（**只读**） | 库里已有表、API 空白（#3）；C-8 裁定 P0 不做写侧 |
| 19 | 节点级 `START/STOP/MODE/GAIN` 下发 | **P0 不做**（C-8 已裁定） | 写面要先有方案 C 的 sticky 单写者；`POST /api/board/mode` 语义不变 |

---

## 5. 测试及验收标准

**通用规则**：每阶段收尾必报五栏（修改文件 / 数据库变化 / 测试结果 / 未完成项 / 无回归证据）；门槛数字**只增不减**，基线取 §0 本轮实测值；UI 未经浏览器实测的项必须在汇报里显式声明"未实测"；不 commit（除非用户当轮明确要求）。

| 阶段 | 判据（可执行） | 回归证据 |
|---|---|---|
| 1 | 本文件 5 份清单齐、§6 每条冲突有用户答复 | `git status --porcelain` 除本文档与既有未跟踪文件外为空 |
| 2 | `/app` 未配目录 → `mounted=false, reason=disabled` 且 `status` 仍 `ok`；配了 → `GET /app/` 200、两条相对资源 200、`health.console.index=true`。INFO 命中 → `Node.fw_version`/`protocol_version` 两列真值；同一份身份报三遍只写一次；畸形行不产生任何写入。**本阶段零迁移**，所以 `identity_source`、`target`、`commit_time`、`phy_version` 四样留给 `007_node_identity`（阶段 3.1） | 后端 ≥ 618（新增用例只增）；`test_frame_parity.py` 原样绿 |
| 3 | **3.1 ✅（2026-11-03）**：`007_node_identity` 七列形状与两条建库路径对拍绿；旧行除 `unsynced` 外全 `NULL`；INFO 命中 → 身份四列真值 + `identity_source='info_line'`；人工 PUT 版本 → `manual`；设备报过之后人工再 PUT 不冒充设备说的；`GET /api/nodes/{id}` 出参含 `identity_source`/`identity_target`/`phy_version`/`time_sync_quality`。后端 649 passed / 1 skipped（合计 650，+9）。3.2–3.4 见下行 | 工作台 214 / `web/` 56 已复跑；真浏览器冒烟见 3.2 收尾 |
| 3.2–3.4 | 工作台显示两个工作区入口且当前工作区名可见于状态栏；`waterfall-lut.json` 双向对拍 256/256 逐字节相等 | 工作台 ≥ 214、`web/` vitest ≥ 56、冒烟 ≥ 65 |
| 4 | 七个新字段在旧行回 `null`（不补 0）；`/api/samples/summary` 出参含七字段；SPECTRUM 帧不再进 `skipped_seen`（栅格行数 > 0 且 skipped 增量只含 rawiq/telemetry/log）；350-bin blob 解回逐 bin 等于帧内同 detector 最大值；`mode=per_node` 与 `mode=max` 各出一条真数据曲线，`median`/`avg_power` 回 422 | 五门槛 + `test_migrations.py` 覆盖 007→011 前后向 |
| 5 | 未登记 key 拨入：连接被 close、`relay_denial` 有行、**`gateway` 表零新增**；已登记重拨：不重复建行；登记幂等：第二次 409 `wifi_key_exists`；候选列表不认领就不进 `node` | `test_hierarchy_crud.py`/`test_multi_gateway.py` 原样绿（`max_nodes_per_gateway=8`、`max_gateways=8` 硬闸不许放宽） |
| 6 | 上传非图片 → 4xx 且不落盘；超尺寸 → 4xx；激活第二版后第一版文件仍在盘上；`GET .../image` 回原字节且 sha256 与库一致；点位在激活版换图后仍可读并标记"参照版本" | 新增 `test_site_plan.py` ≥ 6 条 |
| 7 | ① 帧对拍字节基线未放宽；② **4 节点同频道 ≥ 30 min** 真机窗：缺帧可解释（`missing_prev` 计数与 `relay_denial`/掉线时刻对得上）、时间栅格误差可陈述（±20 ms 或明确"不可比"标签）、拔一块板时图上出现**标注盲区**而不是平滑补齐；③ `mode=median` 回 422 | 五门槛终值 + 真机窗原始记录（`/api/health` 快照 + 导出包） |

**未完成即如实**：L2 整轮重跑（≥1800 s，`crc_errors`/`resync_bytes` 增量为 0）、L3 断网 60 s、L6 真机回退三条真机门槛仍挂在内连接入设计 §4.3，P0 阶段 7 一并收。

---

## 6. 与设计文档的冲突：裁定记录（2026-11-03 已全部结清，闸口解除）

四条由用户拍板（均取推荐项），四条由冻结决策直接推出（无需再问）。**本文其余章节已按本表回改。**

| 编号 | 冲突 | 裁定 | 落到哪 |
|---|---|---|---|
| **C-1 阶段语义** | 用户的 P0（§二~§五 + 7 阶段）实际覆盖设计文档的 **P0 + P1 + P2 + P3 的栅格 + P4 的平面图** | 以"7 阶段顺序"为唯一节奏；设计文档 §8 的 P0–P4 标签降为"内容来源"，不再是排期单位 | 设计文档 §8 待回改（下一轮） |
| **C-2 决策 4 vs 栅格排期** | 决策 4 把 PER_NODE/MAX 放进 P0，设计文档 §8 把频谱数据面排在 P3 / `010_spectrum` | **栅格提前进 P0 阶段 4**：`010_spectrum_grid` + 丢弃分支拆分 + `spectrum-band.js`，`median`/`avg_power` 显式 422 | §2 阶段 4、§3 `010`、§4 第 12/13 条 |
| **C-3 列名** | 决策 5 写 `floor_dbm`/`snr_db`/`peak_dbm`，设计文档写 `floor_db`/`snr_db`/`peak_db` | 以用户命名为准（`floor_dbm`/`snr_db`/`peak_dbm`），设计文档回改 | §3 `009`、§4 第 11 条 |
| **C-4 无固件命令 × boot_id** | 决策 1 不新增 `DEVICE?`/`CLOCK?`，决策 2 又要节点保留 `boot_id` | `boot_id` 由**后端连接会话派生**（每次 `/node` 会话一个 UUID），跨节点时间对齐长期 `unsynced`；退化路径 `restart_detected`（seq 归零 + t_ms 倒退）升为默认路径。这是本期**能力上限**而非缺陷，PER_NODE 图必须带"不可比"标签 | §3 `008`、§5 阶段 7 判据 |
| **C-5 迁移号位与准入表** | 方案 C 设计 §1.2 建 `relay_enrollment` + `relay_denial`，占 `007_inline_relay`；决策 2 定 Gateway 行本身作准入载体 | **`008_relay_ingress`（原记 007，因号位=执行顺序、身份迁移先落而顺延）只含 `wifi_key` + `relay_denial` + 幂等 seed，不建 `relay_enrollment`**；方案 C 文档 §1.1/§1.2/§4.2-10 同批改写。理由：两张表表达同一件事会把"发现即入库"从节点层搬回接入机层，违背 R-3 | §3 `008`、§5 阶段 5 判据 |
| **C-6 `kind` 取值** | 方案 C 写 `kind="inline"`；R-2/决策 2 写 `Gateway.kind="wifi-relay"` | 统一 `wifi-relay`，URL 前缀 `relay://<key>`，`inline` 作为废弃词全文清除 | §2 阶段 4、§3 `007` |
| **C-7 "完整恢复"的边界** | §二 要 18 项逐项映射，但 #11/#16/#17/#18 面板本质是单机侧能力 | **按 A 验收**：恢复 = `/app` 单机侧 18 项全可手测 + 平台侧不删任何能力；#9/#10 的 `centroid_off_hz`/`obw_lo_hz`/`obw_hi_hz`/`peak_off_hz` **随三列一并出列**（同一 `_point()` 通路）；#11/#16/#17 与 #18 的计圈面板平台侧显式不做 | §1 表 #9/#10、§3 `009`、§5 阶段 4 |
| **C-8 频道配置与控制下发** | #3/#4 平台侧"表有 API 无 / 无写面"，写侧要先有 sticky 单写者 | **P0 只做读侧**：候选/身份/频道计划可读，节点级 `START/STOP/MODE/GAIN` 下发留到方案 C 落地后单独一轮 | §4 第 18/19 条、§1 表 #3/#4 |

**阶段 1 出口判据已满足**：本文件 5 份清单齐（§1~§5）+ 每条冲突有裁定（本表）。按用户指令"确认没有与现有设计文档冲突后，再开始实际编码"，**编码闸口解除**，下一步进入阶段 2。

---

## 7. 不动清单（P0 全程可验证）

1. `firmware/**` 零改动（决策 1 + §七 禁擅自新增 C5 命令）。
2. `web/src/**` 与 `web/serve.mjs` 零删除：Web Serial 直连、`serial:`/`uart:`、RH 计时、烧录、功率计、录制回放全保留（§七"不得为平台架构删单机功能"、约束 9）。
3. 协议六帧结构、`frames.h`/`frames.ts`/`frame_parser.py` 三份镜像与 `tests/golden/frames-v1.json` 基线不变；新增栅格层**只读帧不改帧**（约束 8）。
4. 单机控制台不依赖网络：`/app` 挂载只是平台多一个入口，Web Serial 路径与 Pages 发布拓扑不动（§七、约束 10）。
5. 队列全部有界：栅格与 `/ws/spectrum` 沿用丢最旧口径，不引入无界缓冲（约束 5）。
6. 融合只能在同一频率栅格上做（5600–5950 MHz / 1 MHz / 350 bins），**禁止按数组下标跨节点融合**（§七）；`detector` 不同则不合并。
7. UDP 广播不作为准入凭据：`/node?id=<key>` 白名单 + `denied` 留痕才是唯一入账路径（§七、R-3）。
