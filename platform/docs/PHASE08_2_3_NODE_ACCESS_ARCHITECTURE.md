# Phase 08.2.3 — 节点接入架构（Wi-Fi LAN / AP Direct / Gateway 汇聚）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.3 — 节点接入架构设计（Task B + Task C）
> 性质：**设计文档，不修改任何代码、固件或冻结协议**
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前无可靠版本记录

---

## 1. 结论摘要

| 接入形态 | 现有能力 | 判定 | 主要缺口 |
|---|---|---|---|
| Wi-Fi LAN（节点 STA 接入已有 Wi-Fi） | ESP32-S3 relay 镜像可用：`WIFI_MODE_STA` + 主动 WS 拨号 + 串口转发 | `PARTIAL` | 无自动发现、无平台侧 LAN 节点管理、无认证、非 C5 本体 |
| AP Direct（节点 SoftAP + 直连读取） | 无 | `GAP` | SoftAP 编译未启用；节点无 HTTP/WS 服务端；无 AP 配置命令 |
| 多节点 AP 汇聚 | 无 | `GAP` | 需 Gateway 多射频/多网卡与统一聚合；单网卡多 AP 不成立 |

**核心判断：LAN 有可复用底座，AP Direct 完全缺失，且两者都不能在本阶段通过新增协议字段实现。**

---

## 2. Task B — 现有代码能力审计（Q1–Q10）

### 2.1 审计范围与方法

审计对象：`site-server/`、`gateway-agent/`、`site_connector`、数据模型、REST、WebSocket、`repo/web`、`repo/web-desktop`、`repo/firmware/src`（含 `net/`）、现有 Web Serial 接口。

判定只依据仓库代码，不依据硬件手册的“理论上支持”。

### 2.2 十个问题的答案

| # | 问题 | 结论 | 证据等级 | 证据 |
|---|---|---|---|---|
| Q1 | 节点是否已具备 Wi-Fi LAN 接入能力？ | **部分具备**：仅 ESP32-S3 relay 镜像具备 STA 接入；C5 扫描头本体没有 Station | `VERIFIED` | `src/CMakeLists.txt` 22–23 行仅在 `WIDSCAN_RELAY` 下编译 `net/*`；`net/ws_link.c` 使用 `WIFI_MODE_STA`；`sdkconfig.defaults` 32–35 行注明 C5 驱动仅 `WIFI_MODE_NULL` |
| Q2 | 是否具备 SoftAP 能力？ | **不具备** | `VERIFIED` | `sdkconfig.defaults` 35 行、`sdkconfig.esp32s3-relay` 1802 行均为 `# CONFIG_ESP_WIFI_SOFTAP_SUPPORT is not set`；`src/` 无 `WIFI_MODE_AP` / `esp_netif_create_default_wifi_ap` |
| Q3 | 是否具备 AP Direct 数据接口？ | **不具备** | `VERIFIED` | 节点侧无 HTTP/WS 服务端；relay 是**客户端**，仅主动拨号 `ws://host/node?id=...`（`serve.mjs` 12–13 行） |
| Q4 | RF 扫描与网络传输能否并行？ | **分离部署时可并行；同一 C5 未验证** | `DOCUMENTED` + `UNKNOWN` | relay_pipe 从 C5 读取串口后经 WS 上行（`relay_pipe.c`），属“头+中继”双芯片；同芯片并发无实测证据，且 C5 Wi-Fi 驱动仅起 PHY |
| Q5 | 是否已有 40+ 频点目录？ | **没有** | `VERIFIED` | `scan/profile.h` 的 `PROFILE_MAX_CHANNELS=8`；`NET` 默认 `R1–R8`；`CHANNELS` 命令最多 8 项 |
| Q6 | 每个频点能否产生独立 RSSI？ | **8 个配置频点内可以** | `VERIFIED` | METRICS 帧 `channels[]` 每项含 `id/rssi/floor/snr/peak`，解析见 `frame_parser.py` |
| Q7 | 测量是否携带节点 ID 与频点 ID？ | **携带，但频点 ID 为 0 基索引，非频率** | `VERIFIED` | Agent 注入 `device_id`；帧内 `channels[].id` 与 `loKhz`；`Measurement` 表仅存 `device_id + payload_json` |
| Q8 | 能否获得采样时间？ | **能** | `VERIFIED` | 帧头 `tMs` + `seq`；Agent `agent_received_at`；Site Server `created_at`（三时间戳已分离） |
| Q9 | 能否获得扫描周期？ | **能推算，未在 API 暴露** | `VERIFIED` | `TELEMETRY` 含 `rotationHz/rotations/hopsTotal`；每频点周期 ≈ `1/rotationHz` |
| Q10 | 现有数据量是否支持多节点、多频点实时显示？ | **通道可承载，平台不具备** | `INFERRED` + `UNKNOWN` | Agent 逐帧转发并逐帧落库；无聚合、无 Channel 级查询；无真实基准 |

### 2.3 审计明细（按模块）

| 模块 | 现状 | 与 RF 需求的关系 |
|---|---|---|
| 固件 C5 扫描头 | 仅射频与串口；`WIFI_MODE_NULL` | 不具备任何网络接入 |
| 固件 ESP32-S3 relay | STA + WS 客户端 + `NET SSID/URL/ID/ON/OFF/SAVE/STATUS` + 退避 500 ms–30 s | 可作为 LAN 接入底座；无发现、无服务端、无认证 |
| 固件 `CHANNELS` | 最多 8 个命名频点，可配置 `loKhz` | 40+ 目录不成立 |
| Gateway Agent | USB 发现（MAC 优先，`discovery/usb.py` 132–145 行）；TCP/串口 transport；设备状态机；`device.frame` 全帧转发（RAWIQ 除外） | 无 LAN 节点发现；可复用命令/状态/重连 |
| site_connector | 9 连接态、心跳、`command_id` 幂等、事件/测量双队列、指数退避+抖动 | 可整体复用为 Gateway 汇聚上行 |
| Site Server 摄入 | `Measurement` 表按帧存 JSON，`event_id` 幂等去重（245–261 行） | 无 channel 级事实、无频率解析、无 RF 查询 |
| Site Server REST | `/api/v1/*` 无认证；无 RF/频道接口 | 新接口需先解决认证（SP-02） |
| Site Server WS | 仅 `/ws/v1/agents/connect`（机器平面） | 无前端实时推送 |
| `repo/web` | 单设备 Web Serial 频谱/瀑布/缩放；桥接 `/api/endpoints` 列出 relay 节点 | 可作 Console，不承担多节点平台分析 |
| `repo/web-desktop` | Electron 壳 | 不影响接入架构 |

### 2.4 审计重要发现

1. **“Wi-Fi 接入”已有实现，但形态不是需求设想的形态**：现有是“S3 中继 → 桥接服务 → 浏览器 WS”，不是“C5 节点 → 平台”。
2. **relay 节点身份来自 URL `?id=` 字符串**，不是平台 `device_id`；桥接服务内存列表，重启即失，无法作为持久节点目录。
3. **频率不可从测量记录还原**：帧只带 `loKhz + 频点索引`，历史上没有保存当时的频道计划，服务端无法把索引映射为 `frequency_hz`。
4. **RSSI 单位是 dBFS**，不是 dBm；`repo/web` 的 dBm 是前端校准结果。需求中的 `rssi_dbm` 必须区分“原始 dBFS / 校准后 dBm”。
5. **固件协议继续冻结**，因此上述缺口**不能在协议层新增字段**，只能在固件内部、中继、Agent、Server 侧解决。

---

## 3. Task C — 接入架构设计

### 3.1 设计原则

1. **不新增协议字段、不改冻结二进制帧**；节点与平台之间通过现有串口帧 + 外部封装达成。
2. **节点身份与 Agent 身份分离**：`node_id`（RF 节点）、`gateway_id`（Agent）、`site_id`（站点）三级。
3. **接入层不改变设备管理主路径**：所有节点仍经 Agent 的设备注册/命令/事件通道。
4. **不可用即不可用**：任何未实现能力在 UI 与接口中明确标注，不用模拟数据代替。
5. **单网卡约束硬编码进设计**：普通 Wi-Fi 网卡不得宣称可同时连接多个独立 SoftAP。

### 3.2 模式 A — Wi-Fi LAN 节点接入

#### 3.2.1 拓扑

```
RF Node (C5 head)
   │ UART（现有冻结帧）
ESP32-S3 Relay（WIDSCAN_RELAY 镜像）
   │ Wi-Fi STA（现有）
   │ WebSocket client（现有，ws://gateway:port/node?id=<node_key>）
Gateway Agent
   │ Site Connector（现有 9 态 + 双队列 + 幂等）
Site Server
```

#### 3.2.2 Node Discovery

| 方案 | 说明 | 依赖 | 判定 |
|---|---|---|---|
| D1 手动登记（首版） | 在 Agent/平台登记 `host` 或节点 key，落库 | 仅配置，无固件改动 | `PROPOSED`，可先做 |
| D2 Agent 主动探测 | Agent 扫描网段/端口，识别节点应答 | 需要节点有可探测服务端（当前无） | `GAP`，依赖固件 |
| D3 mDNS/SSDP | 节点广播 `_widscan._tcp` | 固件增加 mDNS | `GAP`，需固件变更且协议外 |
| D4 桥接目录 | 复用 `serve.mjs /api/endpoints` 的内存列表 | 仅桥接层，节点仍预配 URL | `PROPOSED`，但非持久 |

**裁定（PROPOSED）**：首版采用 **D1 + D4 组合**：手动登记为主，桥接目录为辅；D2/D3 列入后续。自动发现（PRD-NET-002）在 D3 落地前保持 `GAP`，不得在 UI 中显示“自动发现已启用”。

#### 3.2.3 Node Identity

- 优先级：`INFO` 中的 eFuse MAC → `c5-XXXXXX`（`discovery/usb.py` 132–136 行）。
- 无 MAC 时：固件版本 + 路径哈希，**低置信**，必须显示置信标记。
- relay 的 `?id=` 仅作为**传输层别名**，不得替代 `node_id`；映射关系需落库，避免重启后错认。

#### 3.2.4 Node Registration / Session / State

- 注册：Agent 收到首帧 `INFO`/`HELLO` 后写入 `device` 记录，状态 `pending`。
- 会话：以 `node_id + node_boot_id` 为键；同一 `node_id` 新 `boot_id` = 重启，旧会话关闭。
- 连接状态复用现有 9 态（`DISABLED/STOPPED/CONNECTING/AUTHENTICATING/CONNECTED/RECONNECTING/AUTH_FAILED/ERROR/STOPPING`），但 **LAN 语义需补充**：`DISCOVERED`（已发现未登记）、`UNREACHABLE`（登记但不可达）。
  - 新增状态为 `PROPOSED`，需与 SP 任务一起评审；本阶段不写代码。

#### 3.2.5 Heartbeat / Reconnect

- 心跳：沿用 Agent 设备心跳 + Site Connector 心跳；节点侧 relay 已有 `NET STATUS` 与重连退避。
- 重连：指数退避 + 抖动，上限 30 s；认证失败进入 `AUTH_FAILED` 并停止无意义重试（与现有约定一致）。
- 判定阈值：心跳超时 45 s 为现有 UI 口径；RF 节点建议沿用，不另立标准。

#### 3.2.6 Authentication

- 现状：relay WS **无凭证**（`ws://`，`?id=` 明文）。这是安全缺口。
- 设计（`PROPOSED`）：节点凭证走 **Authorization 头或首条消息**，禁止 URL 查询参数；生产跨主机强制 `wss`；凭证由 Site Server/Agent 下发并支持轮换/撤销（对应 SP-10/SP-16）。
- 节点侧没有 TLS 栈证据，因此 `wss` 依赖 relay 固件能力：`UNKNOWN`，需实测。

### 3.3 模式 B — 单节点 AP Direct

#### 3.3.1 现状

- SoftAP 未编译；节点无服务端；无 AP 配置命令。**全部为 `GAP`。**

#### 3.3.2 目标拓扑（PROPOSED）

```
Laptop / Tablet
   │ Wi-Fi STA 连接节点 SoftAP（SSID 可识别）
RF Node (SoftAP + 本地服务端)
   │ 本地 HTTP/WS（只读测量 + 配置）
用户浏览器 / 本地工具
```

#### 3.3.3 设计要点

| 项 | 设计 | 约束 |
|---|---|---|
| SSID | `widscan-<node_id 后缀>`，可配置 | 需固件启用 SoftAP |
| 身份识别 | AP 信标 + 本地 `/id` 返回 `node_id/node_boot_id/fw` | 需节点服务端 |
| 配置读取 | 只读能力/频道计划/网络状态 | 只读优先，写入需认证 |
| 数据读取 | 本地 WS 推送 METRICS/SPECTRUM，或 HTTP 快照 | 复用现有帧，不加协议字段 |
| 认证 | 一次性 PIN 或预共享令牌，禁止无认证 | `PROPOSED` |
| 连接数 | 单节点 AP 建议 ≤4 | 受固件内存限制，`UNKNOWN` |

#### 3.3.4 明确限制

- AP Direct 是**单节点本地形态**，不做多节点聚合。
- 电脑同时连节点 AP 时会失去场馆 LAN/互联网，需在 UI 说明。
- 该形态**不与平台 Site Server 自动同步**；如需入库，须经 Gateway 或后续离线导入（V1+）。

### 3.4 模式 C — 多节点 AP Gateway 汇聚

#### 3.4.1 硬约束

> 单张普通 Wi-Fi 网卡在同一时刻只能关联一个 AP（同一信道/同一 ESS 的 Mesh/多 BSSID 除外）。因此“一个 Gateway 连多个节点 AP”**不能**用单网卡实现。

#### 3.4.2 可行方案对比

| 方案 | 做法 | 优点 | 缺点 | 判定 |
|---|---|---|---|---|
| C1 多射频/多网卡 | Gateway 挂多个 USB Wi-Fi 适配器，每个关联一个节点 AP | 真正并行 | 成本、驱动、信道隔离、功耗 | `PROPOSED`（V1+） |
| C2 分时轮询 | 单网卡按序切换 AP，逐节点抓取 | 成本最低 | 数据非同时、切换延迟、丢失窗口 | `PROPOSED`（低要求场景） |
| C3 节点侧改 STA | 节点 AP 仅用于配置，数据回 STA | 数据面统一 | 需固件支持双模/切换 | `PROPOSED`，依赖固件 |
| C4 节点 AP + 有线回传 | 节点 AP 仅供本地，数据经以太网/串口到 Gateway | 稳定 | 布线 | `PROPOSED` |

**裁定（PROPOSED）**：V1 不支持“单网卡多 AP”；多节点 AP 汇聚需明确选择 C1 或 C3，并在 Gateway 能力矩阵中标注。任何页面不得暗示单网卡可同时接入多个节点 AP。

#### 3.4.3 Gateway Bridge 设计

- Gateway 作为**唯一汇聚点**：统一管理多节点会话、时间对齐、队列、上行。
- 复用现有 `site_connector`：双队列（事件/测量）、`command_id` 幂等、9 态、退避。
- 每节点独立会话与独立 `node_boot_id`；**不合并**同频点 RSSI。
- 上行保持现有事件信封 `{device_id}:{boot_id}:{frame_type_code}:{sequence}`，不新增类型码。

### 3.5 复用评估

| 现有能力 | 可否复用 | 说明 |
|---|---|---|
| Agent USB 发现 | 部分 | 身份生成逻辑复用；发现路径不适用 LAN |
| Agent 设备状态机 | 是 | 需补 LAN 专属状态语义 |
| Agent 命令通道 | 是 | 节点配置（如频道计划）可走现有命令 |
| site_connector 双队列/幂等/退避 | 是 | 直接作为汇聚上行 |
| Site Server `event_id` 幂等 | 是 | 无需改动 |
| `repo/web` 桥接目录 | 部分 | 仅作辅助发现，不作持久目录 |
| `repo/web` 频谱渲染 | 部分 | 单设备；多节点需新视图（不复制 `/app/`） |

### 3.6 接入架构 GAP 清单

| GAP | 描述 | 阻塞需求 | 影响面 | 是否需固件变更 |
|---|---|---|---|---|
| GAP-NET-01 | 无 SoftAP 编译与 AP 配置 | PRD-NET-008/010 | 固件 | 是 |
| GAP-NET-02 | 节点无本地 HTTP/WS 服务端 | PRD-NET-009 | 固件 | 是 |
| GAP-NET-03 | 无 mDNS/SSDP 自动发现 | PRD-NET-002 | 固件 | 是 |
| GAP-NET-04 | Agent 无 LAN 设备发现/接入路径 | PRD-NET-001/003 | Agent | 否（Agent 侧） |
| GAP-NET-05 | relay WS 无认证、`?id=` 明文 | PRD-NET-005 | 固件/桥接/Agent | 是（节点侧） |
| GAP-NET-06 | 平台无节点接入方式字段 | PRD-NODE-001 | Server/DB | 否 |
| GAP-NET-07 | 单网卡多 AP 不可行，无多射频汇聚 | PRD-NET-011 | Gateway 硬件/Agent | 否（硬件选型） |
| GAP-NET-08 | TLS（wss）在 relay 上未验证 | PRD-NET-005 | 固件 | 待验证 |

---

## 4. 与冻结协议的关系

| 约束 | 本设计是否触碰 |
|---|---|
| 二进制帧结构 | **否**，全部复用现有 METRICS/SPECTRUM/TELEMETRY/INFO |
| 帧类型码 | **否**，不新增 |
| C5 PHY/DSP | **否** |
| 现有 REST | **否**，新接口另立（本阶段仅设计） |
| 现有 WebSocket（Agent 平面） | **否** |
| RF Console `/app/` | **否** |

所有新增能力均列在 `PROPOSED` 或 `GAP`，未写入任何代码。

---

## 5. 审批依赖

- 接入认证依赖 `ADR-08.2.2-04 / -09 / -10 / -11`（均为 `PROPOSED`）。
- 站点范围依赖 `ADR-08.2.1-01 / -03`（均为 `PROPOSED`）。
- 固件变更（SoftAP、mDNS、节点服务端）**不在本阶段授权范围**，须单独立项审批。
