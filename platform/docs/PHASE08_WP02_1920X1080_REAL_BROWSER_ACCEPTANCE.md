# Phase 08 — WP-02 1920×1080 真实浏览器布局验收

- 任务编号：WP-02
- 任务名称：1920×1080 真实浏览器布局验收
- 执行日期：2026-11-05（CST）
- 执行时间窗口：首轮 12:24:00 — 12:25:22；复验轮 12:34:40 — 12:34:54（连真实数据重截图，见 §8 注）
- 验收对象：当前代码库中的 Platform UI（`/platform/`）与 RF Console（`/app/`）
- 最终状态：**CONDITIONAL PASS**
- 变更声明：本任务未修改产品代码、数据库、API、WebSocket、设备协议、固件；仅新增本文档与 `docs/evidence/WP02/` 截图。

---

## 0. 结论摘要

| 项目 | 结果 |
|---|---|
| 1920×1080 CSS viewport 真实运行 | PASS |
| 七个目标页面检查 | PASS |
| 固定布局（220 / 48 / 28） | PASS，偏差 0 px |
| 表格布局、行高、sticky、长 ID | PASS |
| 正常 / 空 / 错误 / 未实现状态 | PASS（正常状态存在 1 项 P2 状态一致性问题） |
| `/app/` 与 `/platform/` 独立性 | PASS |
| 截图真实可追溯 | PASS（13 张，均为 1920×1080） |
| 未解决 P0 / P1 | 无 |
| 遗留缺陷 | P2 × 1，P3 × 2 |

按任务书第 13 节：只有 P2/P3 时按实际影响判定 PASS 或 CONDITIONAL PASS。本次存在 1 项 P2（设备在线状态与 Agent 心跳超时矛盾），因此判定 **CONDITIONAL PASS**，并列出遗留问题。该 P2 不属于布局缺陷，不影响固定区域、表格、溢出与滚动结论。

---

## 1. 验收环境

### 1.1 Git 状态（VERIFIED）

- 产品根目录 `~/workspace/FPV_space/widscan58-clone` **本身不是 Git 仓库**（无根级 `.git`）。
- `repo/`：分支 `fw-wifi-relay`，commit `c5474987d06f20b06d12e6b9cfbfc618be094812`（2026-11-03 11:43:22 +0800）。工作区存在**既有未提交修改**（firmware、rftrack、web、docs 等），均早于本任务，WP-02 未新增修改。
- `app/`：分支 `master`，commit `b03b8cca385146bd9281a4c99ccb8ec8bcc49a02`（2026-10-27 14:13:08 +0800），仅有既有未跟踪目录 `core/bin/`。
- `site-server/`（Site Server + Platform UI 源码）不在任何 Git 仓库中，因此 Platform UI 无对应 commit ID，记为 **UNKNOWN / 非版本化**。

### 1.2 Site Server 启动方式（VERIFIED）

```bash
cd /tmp/widscan58-phase08
PYTHONPATH=~/workspace/FPV_space/widscan58-clone/site-server/src \
python3 -m widscan_site.main \
  --config /tmp/widscan58-phase08/site.yaml \
  --host 127.0.0.1 --port 8790 --log-level INFO
```

启动日志确认：

- Platform UI mounted at `/platform`（目录 `site-server/web`）
- RF Console mounted at `/app`（目录 `repo/web/dist`）
- 绑定 `127.0.0.1:8790`
- Site `site-001` bootstrap 完成

### 1.3 Platform UI 实际访问地址（VERIFIED）

| 地址 | 状态 |
|---|---|
| `http://127.0.0.1:8790/platform/` | 200 |
| `http://127.0.0.1:8790/app/` | 200 |
| `http://127.0.0.1:8790/` | 307 → `/platform/` |
| `http://127.0.0.1:8790/platform` | 307 → `/platform/` |
| `http://127.0.0.1:8790/app` | 307 → `/app/` |

### 1.4 运行依赖（VERIFIED）

- 本机 Python 3 + FastAPI + Uvicorn + SQLAlchemy 可用，Site Server 正常启动。
- 浏览器：Playwright 自带 Chromium 148.0.7778.96（有头模式）。
- 无需登录、无认证依赖（当前 Platform REST 无认证，与 Phase 08.2.2 审计一致）。
- 页面数据来自真实 REST 接口：`/api/v1/site`、`/api/v1/agents`、`/api/v1/devices`、`/api/v1/measurements/stats`、`/api/v1/diagnostics`、`/api/v1/devices/{id}/measurements` 等。

### 1.5 测试数据库与运行数据来源（VERIFIED）

- 数据库：`/tmp/widscan58-phase08/site.db`
- 文件大小：826,388,480 bytes
- 文件 mtime：`2026-11-05 06:27`，在 WP-02 全部执行前后**未变化** → 本次未写入业务数据。
- 数据规模：

| 表 | 记录数 |
|---|---|
| sites | 1（`site-001`） |
| agents | 1（`gateway-001`） |
| devices | 2（`sim-c5-000`、`sim-c5-001`，均归属 `gateway-001`） |
| measurements | 327,071 |
| agent_credentials | 1 |
| audit_logs | 3 |

数据来源说明：该 SQLite 为 Phase 08.5 本机真实运行产生的验证数据库，设备为系统自带模拟器（`sim-c5`），不是生产硬件。WP-02 未生成、未修改任何记录，只在页面上读取并展示。

为避免写入数据库，本次**未启动 Gateway Agent**。因此：

- 已有历史 Site / Agent / Device / Measurement 数据可用于布局和列表验收（VERIFIED）。
- “Agent 当前在线并持续采集”的实时状态未在本次演练（UNVERIFIED）。
- 由此产生的设备在线状态陈旧问题见缺陷 WP02-D01。

### 1.6 环境结论

环境可启动、可访问、有真实历史数据，满足布局验收所需。除上述限制外无阻塞。

---

## 2. 浏览器与 viewport

| 项目 | 实测值 |
|---|---|
| 浏览器 | Chromium 148.0.7778.96（Playwright，**有头真实窗口**） |
| User-Agent | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36` |
| 操作系统 | macOS 27.2（Build 26B123） |
| 物理屏幕 | 3024 × 1964 Retina；系统 UI 逻辑分辨率 1920 × 1080 @ 60Hz |
| Viewport（CSS px） | **1920 × 1080** |
| `window.innerWidth / innerHeight` | 1920 / 1080 |
| `devicePixelRatio` | 1 |
| `visualViewport.scale` | 1（页面缩放 100%） |
| 浏览器缩放 | 100% |
| DevTools | 未打开，未改变 viewport |
| 测试地址 | `http://127.0.0.1:8790/platform/` |
| 测试时间 | 首轮 2026-11-05 12:24:00 — 12:25:22 CST；复验轮 12:34:40 — 12:34:54 CST |
| 截图像素 | 全部 1920 × 1080（已逐张用 `sips` 核对） |

说明：1920×1080 指 CSS viewport；物理屏幕为 Retina 3024×1964，系统逻辑分辨率为 1920×1080，浏览器以 1920×1080 CSS viewport 运行，DPR=1。历史 1512×724 截图未用于本次验收。

---

## 3. 页面检查清单

| # | 页面 | 路由 | 加载 | 导航激活 | 页头/内容重叠 | 文档溢出 | 主要状态 | 结果 |
|---|---|---|---|---|---|---|---|---|
| 1 | Overview | `#/overview` | 200，正常 | 唯一激活 Overview | 无，页头 bottom 115.9 → 首块 top 131.9（16 px 间距） | 无（scrollW/H = 1920/1080） | 4 张统计卡 + 告警信息；错误状态在此页验证 | PASS |
| 2 | Sites | `#/sites` | 200，正常 | 唯一激活 Sites | 无，16 px 间距 | 无 | 8 列表格 1 行；多站点列表“未实现”说明 | PASS |
| 3 | Gateways | `#/gateways` | 200，正常 | 唯一激活 Gateways | 无，16 px 间距 | 无 | 8 列表格 1 行；Agent 状态“心跳超时” | PASS |
| 4 | Devices | `#/devices` | 200，正常 | 唯一激活 Devices | 无，16 px 间距 | 无 | 9 列表格 2 行；筛选空状态另测 | PASS |
| 5 | Measurements | `#/measurements` | 200，正常 | 唯一激活 Measurements | 无，16 px 间距 | 无 | 未选设备时显示引导空状态；选设备后 11 列 × 100 行 | PASS |
| 6 | System | `#/system` | 200，正常 | 唯一激活 System | 无，16 px 间距 | 无 | 4 张卡片（系统/摄取/保留策略/未实现说明） | PASS |
| 7 | Platform Shell 全局 | `/platform/` | 200，正常 | 6 个导航项，每个页面仅 1 个 `aria-current` | 顶栏 / 侧栏 / 内容 / 状态栏无重叠 | 无 | 固定布局实测见第 4 节 | PASS |

补充：

- 通过逐项**点击左侧导航**验证：Overview → Sites → Gateways → Devices → Measurements → System → Overview，hash、页面标题、激活项均正确。
- 所有页面标题位于内容区顶部 68 px，页头与首个内容块间距统一为 16 px，无文字压叠。
- 所有页面 `document.documentElement.scrollWidth = 1920`、`scrollHeight = 1080`，无非预期横向/纵向页面滚动。
- 页面内容最低点最大为 Measurements 的 999.9 px（分页器底部），状态栏起点 1052 px，无遮挡。
- `#/console`（Platform 内 RF Console 入口页）另测，仅提供外链，无内嵌、无假功能。

---

## 4. 固定布局实际测量结果

实测 CSS Grid：

```
grid-template-columns: 220px 1700px
grid-template-rows: 48px 1004px 28px
```

| 区域 | 设计目标 | 实际值 | 偏差 | 是否影响内容/滚动 |
|---|---|---|---|---|
| 左侧导航栏宽度 | 220 px | **220 px**（x=0，y=48，h=1004） | 0 px | 否 |
| 顶部栏高度 | 48 px | **48 px**（y=0，w=1920） | 0 px | 否 |
| 底部状态栏高度 | 28 px | **28 px**（y=1052，w=1920） | 0 px | 否 |
| 内容区 | 剩余空间 | **1700 × 1004**（x=220，y=48） | — | 否 |

对齐关系（由 `getBoundingClientRect` 计算）：

- 顶栏 bottom = 48 = 导航 top → 无重叠、无缝。
- 导航 right = 220 = 内容区 left → 无覆盖。
- 状态栏 top = 1052，bottom = 1080 = viewport 高度 → 完整可见，无裁切。
- 内容区 bottom = 1052 = 状态栏 top → 内容不进入状态栏区域。
- 所有页面内容最后元素 bottom ≤ 999.9 < 1052 → 状态栏未遮挡任何内容。
- 页面无 `document` 级横向滚动（scrollWidth = 1920），无页面级纵向滚动（scrollHeight = 1080）。
- 1920 宽度未触发样式表中的窄屏媒体查询（`max-width: 1439px`），按桌面 4 列栅格渲染。

结论：固定布局三项尺寸与设计目标完全一致，偏差 0 px，未产生重叠，也未影响内容区或滚动。

---

## 5. 表格检查结果

### 5.1 各页面表格

| 页面 | 表格数 | 列数 | 数据行 | 表头高 | 行高 | 横向溢出 | 内容裁切 | sticky | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| Sites | 1 | 8 | 1 | 40 px | 36 px | 无 | 无 | 表头 + 首列 sticky | 表格宽 1652，容器 1652 |
| Gateways | 1 | 8 | 1 | 40 px | 36 px | 无 | 无 | 表头 + 首列 sticky | 表格宽 1652 |
| Devices | 1 | 9 | 2 | 40 px | 36 px | 无 | 无 | 表头 + 首列 sticky | 容器高 114 px |
| Measurements | 1 | 11 | 100 可见（+100 隐藏详情行） | 40 px | 36 px | 无（scrollW = clientW = 1650） | 无 | 表头 + 首列 sticky | 容器 778 / 3640，纵向滚动 |

### 5.2 Measurements 列宽实测（CSS px）

| 列 | 宽 |
|---|---|
| ID | 72 |
| Frame | 140.2 |
| Device Timestamp | 140 |
| Sequence | 92 |
| Agent 接收时间（接口未返回） | 183.7 |
| Site Received | 204 |
| Agent | 156.6 |
| Boot ID | 178.9 |
| Event ID | 318.7 |
| Payload | 80 |
| Source | 84 |

总表宽 1650，容器可视宽 1650，**1920 viewport 下无横向滚动需求**。

### 5.3 滚动与 sticky

- Measurements 表格容器 `clientHeight = 778`、`scrollHeight = 3640`，可纵向滚动。
- 设置 `scrollTop = 400` 后：表头 top = 176.9，容器 top = 175.9（差值 1 px 为边框）→ **表头保持吸顶**。
- 首列同理保持吸顶；本次 `scrollLeft = 0`，因 1920 下无需横向滚动，首列吸顶未被实际横向拖动触发（列宽已通过列宽表验证）。
- 100 行数据滚动流畅，无跳行、无错位、无内容穿透表头。

### 5.4 长标识展示

- Measurements 的 Boot ID 与 Event ID 列使用单行省略：`.pf-trunc` 元素 `clientWidth = 200`、`scrollWidth = 250`，`truncated = true`，视觉显示省略号。
- 每个省略元素均带 `title`，内容为完整标识，例如：`sim-c5-000:boot-01e80c29:1:19422`。
- 鼠标悬停可查看完整值（以 DOM `title` 属性验证；浏览器原生 tooltip 不参与页面截图）。
- Event ID 为 37 字符长标识，在 318.7 px 列内按 200 px 截断，无换行、无撑破列宽、无相邻列文字重叠。

### 5.5 空数据与分页

- 通过真实 API 过滤 `#/devices?status=identity_conflict`（接口返回空数组）：表格保留表头，显示单行“无数据（接口成功返回空）”，无伪造数据。
- Measurements 分页：offset 0 → 100，点击“下一页”后 URL 数据请求变为 `offset=100`，首行标识更新；分页器明确提示“后端未提供总数”。

### 5.6 表格结论

列宽、行高、表头高度、sticky、长标识省略、空数据、分页均符合预期；1920 宽度下无横向溢出、无文字重叠、无裁切。

---

## 6. 正常 / 空 / 错误 / 未实现状态结果

### A. 正常状态（PASS，含 1 项 P2）

- 全部页面 REST 请求返回 200。
- Overview 统计卡展示：Site 1、Gateways 1/1、Devices 2/2、Alerts 2、Measurements 327,071。
- Sites 展示 `site-001` 的 ID、名称、时区、状态、计数、创建/更新时间。
- Gateways 展示 `gateway-001`、版本 `1.1.0`、平台 `darwin`、OS、最后心跳、设备数 2。
- Devices 展示 2 台设备、类型 `esp32c5`、归属 `gateway-001`、ownership、最后心跳。
- Measurements 展示 11 列真实记录、UTC 时间、Frame、Sequence、Payload 大小、Source。
- 时间均按 UTC 展示并带 `UTC` 后缀，格式一致；未发现字段错位或空白单元格（无值处显示 `—`）。
- **异常项**：Devices 显示“在线”，而 Gateways 已显示“心跳超时”，且 `last_seen` 为 2026-11-04 22:20 UTC。见缺陷 WP02-D01（P2）。

### B. 空状态（PASS）

- Devices 过滤空结果：表头 + “无数据（接口成功返回空）”，页面不崩溃、不显示假数据。
- Measurements 未选择 Device：显示引导空状态“请选择 Device …”，不发起无意义的数据请求。
- 空状态区域尺寸正常，无布局跳动，底部状态栏位置不变。

### C. 错误状态（PASS）

测试方法：页面加载成功后停止 Site Server，再点击页面“刷新”，采集真实网络不可达状态。

- 顶栏徽标变为 `Site Server 不可达`（critical 样式）。
- 各数据区出现 5 条错误提示：`/api/v1/site`、`/api/v1/agents`、`/api/v1/devices`、`/api/v1/measurements/stats`、`/api/v1/diagnostics`，均含“网络不可达…重试”。
- 页面保留上一次成功数据（统计卡仍显示 1 / 1/1 / 2/2 / 2 / 327071），未清空、未崩溃、无未处理异常（`hasPageError = false`）。
- 布局不受影响：viewport 仍 1920×1080，doc 仍 1920×1080，固定区域仍 48 / 220 / 28。
- 重启服务后重新加载：徽标恢复“Site Server 已连接”，错误条数量 0（PASS）。

### D. 未实现状态（PASS）

以下能力在当前 UI 中均有明确“未实现/未接入”标注，未展示假功能或伪成功状态：

- Overview：告警数据源未接入；时间范围过滤未实现。
- Sites：多站点列表未实现（V1 单站点）。
- Gateways：新增/配对向导未实现。
- Devices：transport 筛选未实现。
- Measurements：时间范围过滤未实现；“Agent 接收时间（接口未返回）”列明确标注接口缺失。
- System：多站点环境总览、日志/备份/迁移等未实现说明。

---

## 7. Console 独立性检查

| 检查项 | 结果 |
|---|---|
| `/app/` 独立访问 | 200；标题 `Widscan58`；渲染独立 Console UI |
| `/app/` 是否加载平台壳 | 否（`hasPlatformShell = false`，`hasPlatformNav = false`） |
| `/app/` 是否含 iframe | 无 |
| `/app/` 自有资源 | `assets/index-*.js`、`assets/index-*.css`、favicon、manifest、icons、`firmware/index.json` 均 200 |
| `/platform/` 独立访问 | 200；标题 `WIDSCAN58 Platform`；平台壳存在 |
| 平台是否强制加载 Console | 否；无 iframe；Console 入口为外链 `href="/app/"`、`target="_blank"` |
| Platform 自有资源 | `/platform/styles.css`、`/platform/app.js` 200 |
| 两个入口资源是否互相覆盖 | 否；路径前缀独立，未发现交叉加载 |
| 原有设备连接入口 | Console 内 `Connect`、`Flash device`、Simulator 等入口仍存在 |
| 实际设备连接能力 | 本次未接硬件、未启动 Agent/模拟器连接 → 未做端到端设备连接验证（UNVERIFIED，不属 WP-02 布局范围） |

补充发现：`/app/` 启动时会请求 `/api/mode`，当前 Site Server 未提供该端点，返回 404；Console UI 仍正常渲染。见缺陷 WP02-D03（P3）。

---

## 8. 截图索引

目录：`docs/evidence/WP02/`
所有截图：1920×1080，Chromium 148.0.7778.96 有头模式，DPR=1，缩放 100%。

| 文件 | 路由 / 场景 | 时间（CST） | 结果 | 关联缺陷 |
|---|---|---|---|---|
| `wp02_overview.png` | `/platform/#/overview`（兼作 Platform Shell 全局布局证据） | 12:34:40 | PASS | — |
| `wp02_sites.png` | `#/sites` | 12:34:42 | PASS | — |
| `wp02_gateways.png` | `#/gateways` | 12:34:43 | PASS | D01（状态对照） |
| `wp02_devices.png` | `#/devices` | 12:34:44 | PASS（布局） | D01 |
| `wp02_measurements.png` | `#/measurements`（未选设备） | 12:34:46 | PASS（空状态） | — |
| `wp02_system.png` | `#/system` | 12:34:47 | PASS | — |
| `wp02_measurements_device.png` | `#/measurements?device=sim-c5-000` | 12:34:49 | PASS；长 ID 省略 + title | — |
| `wp02_measurements_table_scrolled.png` | 同上，表格 `scrollTop=400` | 12:34:49 | PASS；sticky 表头 | — |
| `wp02_devices_empty.png` | `#/devices?status=identity_conflict` | 12:34:50 | PASS；空数据行 | — |
| `wp02_measurements_empty.png` | `#/measurements` 引导空状态 | 12:34:52 | PASS | — |
| `wp02_app_rf_console_entry.png` | `/app/` | 12:34:54 | PASS；独立 Console | D03 |
| `wp02_error_state.png` | `#/overview`，Site Server 停止后刷新 | 12:24:48 | PASS；错误状态 | — |
| `wp02_console_entry_page.png` | `#/console` 平台内入口页 | 12:25:22 | PASS；仅外链 | — |

截图均为真实浏览器页面截图，未裁切、未缩放、未拼接、未做图片编辑。

**复验轮记录（2026-11-05 12:34）**：应人工要求重启 Site Server 复验。首轮启动时因进程 cwd 位于产品根目录，而 `site.yaml` 中 `database.url` 为相对路径 `sqlite:///./site.db`，导致服务在根目录新建了一个仅含引导数据的空库（233 KB，0 设备 / 0 测量）；已停止该实例并将空库移入废纸篓恢复原状，随后以 `/tmp/widscan58-phase08` 为 cwd 重启，接入真实数据库（2 设备、327,071 条测量）后重新执行全部页面测量与截图。复验结论与首轮完全一致：220/48/28 固定布局偏差 0，sticky 表头/首列生效（scrollTop=400），200 行 / 11 列表格无横向溢出，长 ID 200px 省略 + title，`/app/` 独立性不变。上表 11 张截图为复验轮重生成（时间戳 12:34:40 — 12:34:54）；`wp02_error_state.png` 与 `wp02_console_entry_page.png` 本轮未重跑，保留首轮时间戳。此相对路径配置行为仅作记录，未修改任何产品代码。

---

## 9. 缺陷列表

### WP02-D01 — P2：Agent 心跳超时但设备仍显示“在线”

- 缺陷 ID：WP02-D01
- 页面路由：`#/gateways`、`#/devices`
- 复现步骤：
  1. 使用 08.5 遗留的真实 SQLite 启动 Site Server；
  2. 不启动 Gateway Agent；
  3. 打开 `#/gateways`，再打开 `#/devices`。
- 实际结果：
  - Gateways：`gateway-001` 状态为“心跳超时”，最后心跳 `2026-11-04 22:27:27 UTC`；
  - Devices：`sim-c5-000`、`sim-c5-001` 状态仍为“在线”，最后心跳 `2026-11-04 22:20:27 UTC`。
- 预期结果：Agent 心跳超时/离线时，其名下设备应显示“离线”或“未知”，不应显示“在线”。
- 截图证据：`wp02_gateways.png`、`wp02_devices.png`
- 严重等级：P2
- 是否影响 WP-02 验收：**不阻塞布局验收**（固定区域、表格、滚动、溢出均正常）；影响第 6 节“正常状态字段/状态合理性”，形成 CONDITIONAL PASS 遗留项。
- 备注：该状态来自上次异常终止后持久化的 `devices.status = online`，启动时未做状态对账；本次为避免写库未启动 Agent，因此无法通过实时心跳刷新状态。

### WP02-D02 — P3：Platform 页面 `/favicon.ico` 返回 404

- 缺陷 ID：WP02-D02
- 页面路由：全部 `/platform/` 页面
- 复现步骤：打开任一 Platform 页面，观察浏览器控制台或服务端日志。
- 实际结果：浏览器自动请求 `/favicon.ico`，返回 404；页面视觉与布局不受影响。
- 预期结果：提供 favicon，或显式声明图标避免 404。
- 截图证据：无单独截图（不影响视觉）；服务端日志与浏览器控制台记录为证。
- 严重等级：P3
- 是否影响 WP-02 验收：否。
- 备注：每页面产生 2 条 404 控制台错误（Chromium 行为），不产生未处理异常。

### WP02-D03 — P3：RF Console `/app/` 请求 `/api/mode` 返回 404

- 缺陷 ID：WP02-D03
- 页面路由：`/app/`
- 复现步骤：打开 `/app/`，观察网络请求。
- 实际结果：`GET /api/mode` 返回 404；Console 自身正常渲染，设备连接入口可用。
- 预期结果：Site Server 提供该端点，或 Console 在 404 时静默降级。
- 截图证据：`wp02_app_rf_console_entry.png`（页面渲染正常）；服务端日志记录 404。
- 严重等级：P3
- 是否影响 WP-02 验收：否；不影响 `/app/` 独立访问结论。
- 备注：属 Console 与 Site Server 的既有集成差异，非本次布局问题。

说明：未将 Phase 08.2.2 已记录的安全问题（如 REST 无认证、诊断暴露数据库路径等）重复列入本布局验收缺陷表；该部分归属安全阶段文档。

---

## 10. 验收结论

### 10.1 验收标准逐项

| 验收标准 | 结果 |
|---|---|
| 1920×1080 CSS viewport 真实运行 | PASS（innerWidth/Height = 1920×1080，DPR=1，scale=1） |
| 七个目标页面全部完成检查 | PASS（6 页面 + Platform Shell） |
| 固定布局通过 | PASS（220 / 48 / 28，偏差 0 px，无重叠） |
| 表格布局通过 | PASS（无横向溢出、行高 36、表头 40、sticky 正常） |
| 长标识展示通过 | PASS（单行省略 + title 完整值） |
| 正常 / 空 / 错误 / 未实现状态均有证据 | PASS（其中正常状态含 P2 一致性问题） |
| `/app/` 与 `/platform/` 独立性通过 | PASS |
| 所有截图真实可追溯 | PASS（13 张，1920×1080，带路由与时间） |
| 不存在未解决 P0 / P1 | PASS（0 项 P0/P1） |
| 验收报告完整 | PASS |

### 10.2 最终状态

**WP-02 = CONDITIONAL PASS**

理由：布局、固定区域、表格、长标识、空/错误/未实现状态与入口独立性均通过，无 P0/P1；但存在 1 项 P2（设备在线状态与 Agent 心跳超时不一致）与 2 项 P3（favicon 404、`/api/mode` 404）。

### 10.3 对 Phase 08.3 工程前置条件的结论

- 按任务书“WP-02 PASS 必须同时满足……不存在未解决 P0/P1”的门禁，本次**无 P0/P1**，布局门禁未阻塞。
- 但最终状态为 CONDITIONAL PASS，而非 PASS，因此**不能自动视为已满足 Phase 08.3 的严格 WP-02 前置条件**；需人工审核决定：
  1. 接受 P2 为已知遗留（由非正常关停/未启动 Agent 导致的状态陈旧），按 CONDITIONAL PASS 放行；或
  2. 要求先修复/对账设备在线状态后重跑 WP-02。
- 本任务不进入 Phase 08.3，等待人工审核。

### 10.4 本次限制

- 未启动 Gateway Agent（避免写库），因此未验证“实时在线 + 持续采集”状态；使用历史真实数据完成列表/表格/布局验收。
- 未连接真实 ESP32-C5 硬件；仅验证 UI 与现有数据，不涉及设备协议。
- 未在多站点、多用户、非本机网络环境验证（当前单站点单实例、`127.0.0.1`）。

---

## 11. 变更声明

- 未修改任何产品代码（Site Server、Platform UI、RF Console、Agent、固件）。
- 未修改数据库：`site.db` 大小与 mtime 在验收前后一致（826,388,480 bytes / 2026-11-05 06:27）。
- 未修改 API、WebSocket、设备协议、固件。
- 未启动 Phase 08.3。
- 未修复本次发现的 UI/状态缺陷。
- 仅新增：
  - `docs/PHASE08_WP02_1920X1080_REAL_BROWSER_ACCEPTANCE.md`
  - `docs/evidence/WP02/*.png`（13 张）
- 临时验收脚本位于工作区外 `/tmp/wp02/`，不属于交付物。
