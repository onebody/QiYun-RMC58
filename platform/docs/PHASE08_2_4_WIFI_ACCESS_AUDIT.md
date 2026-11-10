# Phase 08.2.4 — Wi-Fi 接入能力独立审计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `CONDITIONAL PASS`（静态核查完成；并行运行 NOT VERIFIED）
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，Wi-Fi LAN / AP Direct / 并行运行 / 节点发现 独立于 B3 的详细拆解
> 性质：**能力核查。不修改任何配置、固件或代码。**

---

## 1. 四个必须分开的 Wi-Fi 能力

| # | 能力 | 含义 | 审计对象 |
|---|---|---|---|
| W1 | Wi-Fi LAN（S3 relay STA 上行） | S3 relay 关联既有 Wi-Fi AP，WebSocket 上行 Site Server | `ws_link.c` + `env:esp32s3-relay` |
| W2 | Wi-Fi AP Direct | 节点（C5 head）开 SoftAP，运维电脑直连调试 | C5 head 固件 + sdkconfig |
| W3 | Wi-Fi 与 RF 扫描并行 | 扫描任务运行时能否稳定执行网络通信 | C5 head / S3 relay coexistence |
| W4 | 多节点接入 | 多个 C5 + 多个 S3 relay → 同一 Site Server | 架构层 |

---

## 2. W1 — Wi-Fi LAN（VERIFIED，代码完整）

### 2.1 S3 relay STA 实现（VERIFIED）

| 项 | 事实 | 证据 |
|---|---|---|
| 编译目标 | `env:esp32s3-relay`（`platformio.ini:22–29`） | `build_flags = -DRX_WIFI_RELAY=1 -DRX_TARGET_S3=1 -DUART_LINK_BAUD=921600` |
| STA 模式 | `ws_link.c` `wifi_init` 里 `wifi_mode = WIFI_MODE_STA` | 无 AP 模式分支 |
| SSID/密码 | 从 `CONFIG_WIFI_SSID` / `CONFIG_WIFI_PASSWORD` 读取；或 `NVS` 存储 | `ws_link.c: wifi_connect_ap()` |
| 连接状态管理 | `WIFI_EVENT_CONNECTED` / `WIFI_EVENT_DISCONNECTED` / `IP_EVENT_STA_GOT_IP` 全部处理 | `ws_link.c` event handler |
| WebSocket uplink | `ws://<site_server>/ws/v1/agents/connect`；binary frame 传输 | `ws_send_frame(frame_bytes, len)` |
| 心跳 | 15 s ping；Site Server 45 s 超时关闭 | `ws_link.c` heartbeat timer |
| 指数退避重连 | 1 s → 2 s → 4 s → 8 s → 16 s → 32 s → 上限 60 s | `relay_pipe.c` 重连逻辑 |
| 凭证 | relay 侧传 `gateway_id`（编译常量或 NVS）；**Site Server 侧 credential 校验链完整**（Agent WS 首消息 auth） | Site Server `agents/connection.py` |

### 2.2 已知限制

| 项 | 限制 | 说明 |
|---|---|---|
| WPA3 | sdkconfig 开启 `CONFIG_ESP_WIFI_ENABLE_WPA3_SAE=y`（`sdkconfig.defaults.esp32s3-relay`） | 理论支持，但 sdkconfig 本身不是 STA env 唯一入口；需实测 |
| 漫游 | 无 802.11r/k/v 显式配置 | 基站切换依赖 ESP-IDF 默认行为 |
| Site Server WS URL | `ws_link.c` 有 `CONFIG_WS_URL`；无 TLS 强制；局域网 `ws://` 符合 ADR-05 | ADR-05 允许局域网 `ws://` |
| 代理/VPN | 无代理/穿透代码；Site Server 必须可达 | 无 NAT 穿透 |
| 多 AP 同一 Site | 可行：多 relay → 同一 Site Server | Site Server 按 `site_id` 聚合 |

### 2.3 状态：**VERIFIED（静态完整；联调实测 NOT VERIFIED）**

---

## 3. W2 — AP Direct（VERIFIED 物理不可行）

| 项 | 事实 | 证据 |
|---|---|---|
| SoftAP 编译 | `# CONFIG_ESP_WIFI_SOFTAP_SUPPORT is not set` | `sdkconfig.defaults:35`（C5 head 和 esp32s3-relay 两份） |
| AP 初始化 | C5 head 固件 `main.c` 无 `wifi_init()`；S3 relay 固件只有 STA | 无任何 `WIFI_MODE_AP` 或 `WIFI_MODE_APSTA` 分支 |
| 网络任务 | 无 | C5 head 只有 scanner_loop；S3 relay 只有 relay_pipe + ws_link |
| 节点服务端 | 无 HTTP server；无 REST 端口监听 | firmware/src/net/ 下只有 `ws_link.c` / `net_prefs.c`（NVS 存储），无 server |
| Web 管理页 | 无 | |
| AP + STA 并行 | ESP32-C5 芯片支持，但固件未启用 | ESP-IDF WiFi API 支持 `WIFI_MODE_APSTA`；但需 sdkconfig + 初始化代码 |

### 3.1 若要 AP Direct，至少需要补齐

| # | 项目 | 性质 |
|---|---|---|
| 1 | 重新编译 SoftAP 支持 | sdkconfig 改动（`CONFIG_ESP_WIFI_SOFTAP_SUPPORT=y`） |
| 2 | ESP32-C5 APSTA 模式 | `wifi_set_mode(WIFI_MODE_APSTA)` + SSID/密码 + STA 连接流程 |
| 3 | AP 模式下 RF 扫描并行 | ESP-IDF 有 coexistence API；需固件任务调度改动 |
| 4 | 节点服务端（REST / Web 页面） | 全新代码 |
| 5 | 本地鉴权 | 无现有方案；属 A2-09 本地 AP 平面补充 |
| 6 | AP 直连数据同步 | 离线采集 + 后续导入 Site Server；无自动同步路径 |

### 3.2 状态：**GAP — 物理上不可行（现状）**

---

## 4. W3 — Wi-Fi 与 RF 扫描并行（NOT VERIFIED）

### 4.1 C5 Head

- C5 head **不跑 Wi-Fi 任务**（`WIFI_MODE_NULL`），所以"C5 扫描"和"C5 上网"不存在并行问题；
- C5 head 唯一的通信口是 **UART TX/RX 921600**。

### 4.2 S3 Relay

- S3 relay **不跑 RF 扫描**（RX_WIFI_RELAY 分支跳过 `scanner_init()`）；
- S3 relay 只有 UART 接收 + STA + WS uplink；
- 所以"S3 relay Wi-Fi"和"S3 relay RF 扫描"不存在并行问题。

### 4.3 两芯片跨芯片并行架构（VERIFIED + NOT VERIFIED）

```text
C5 head (扫描)  ──── UART 921600 ────  S3 relay (STA + WS)
   │                                       │
   └── 纯扫描任务，无网络                    └── 纯网络任务，无扫描
```

- UART 921600 ≈ 92,000 字节/秒，METRICS + LEVELS + SPECTRUM（截断后）估算 ≈ 1–2 KB/s；**理论上远超上限**；
- 但"理论够"不代表"实测稳"：UART FIFO 溢出、电磁干扰、线缆长度、DMA 配置、Python/ESP32 两侧 UART 处理延迟都可能导致丢帧；
- Site Server ingestion 是唯一能**事后统计是否丢帧**的环节（`get_ingestion_stats()` 里有 `persisted` / `duplicate_events` / `integrity_errors` 计数，但没单独的 `uart_drop` 计数）。

### 4.4 状态：**NOT VERIFIED — 需硬件实测 UART 丢帧率**

---

## 5. W4 — 多节点接入（VERIFIED 架构可行 + NOT VERIFIED 实测）

### 5.1 拓扑选项

| 方案 | 结构 | 可行性 | 说明 |
|---|---|---|---|
| **优先：多 relay → 同一 LAN** | 每 C5 head 配一个 S3 relay；所有 relay STA 到同一 Wi-Fi AP；同一 Site Server 聚合 | **架构可行** | 每 relay = 每 Agent；Site Server 已支持多 Agent WS 接入 |
| 备选：多 relay → 多 LAN | 每个局域网里各放若干 relay；通过路由互联到 Site Server | **架构可行** | 路由可达即可；Site Server 不感知物理 LAN |
| 备选：relay 兼 AP | 一个 relay STA 到 Site Server + AP 给其他 relay | **架构可行** | 但 relay 固件当前只有 STA；AP 要重新编译 |
| **禁止：单网卡多 AP** | 一台普通电脑一张卡同时直连多个独立 AP | **物理不可行** | 关联限制 |
| **禁止：一个 S3 relay 带多个 C5** | UART 只有一个；协议帧不支持多路混合 | **物理不可行** | 串口唯一性 |

### 5.2 节点身份问题（GAP）

| 项 | 现状 |
|---|---|
| relay → Agent | 每 S3 relay 有唯一 `gateway_id`（编译常量或 NVS）；Site Server 以此识别 Agent |
| C5 → Device | C5 head 无唯一 ID；**靠配对码 + Site Server adopt**（当前流程） |
| device_id 与 node_id | 代码中**无显式等价关系**；Device = 业务概念，Node = RF 重基线概念 |
| 多 C5 + 多 S3 relay | 架构上每对各自配对；无"批量发现 + 自动登记"流程 |

### 5.3 状态：**VERIFIED（架构层）+ NOT VERIFIED（实测层）**

---

## 6. 节点发现与 IP 获取（GAP）

| 能力 | 现状 |
|---|---|
| relay 自动发现 Site Server | 无 mDNS；需手动配置 `CONFIG_WS_URL` |
| Site Server 发现 relay | 通过 WS connect 事件；无广播 |
| relay IP 获取 | DHCP（ESP32 默认）；无静态 IP 配置路径 |
| relay IP 变化处理 | 无持久化配置；重新联网获新 IP |
| 设备注册 | Agent WS connect 后发送 `agent.devices_snapshot`；Site Server 对每个 Device 按 `device_id` 登记 |

### 6.1 状态：**GAP — 无自动发现流程**

---

## 7. 断线重连（VERIFIED，静态完整）

| 环节 | 机制 | 证据 |
|---|---|---|
| S3 relay → AP | ESP-IDF 默认自动重连；指数退避 | `ws_link.c` 无额外处理 |
| S3 relay → Site Server WS | `relay_pipe.c` 指数退避（1–60 s）；重连后重发 `gateway_id + credential + devices_snapshot` | Site Server 端可观测 |
| Site Server → Agent WS 心跳 | 15 s ping；45 s 超时关闭 | `agents/connection.py` |
| Agent 设备离线 | 无独立设备心跳；靠 Device 事件流 + timeout（D01 问题） | → RF 页面复用同一机制 |

### 7.1 状态：**VERIFIED（静态完整；重连后的设备状态恢复 NOT VERIFIED）**

---

## 8. Wi-Fi 接入能力总评

| 能力 | 状态 | 关键证据 | 下一步 |
|---|---|---|---|
| W1 Wi-Fi LAN（relay STA） | **VERIFIED** | 完整 ws_link.c 实现；无 AP 干扰 | 实测联调 + 压测 |
| W2 AP Direct | **GAP** | SoftAP 关闭 + 无服务端 | 立项后固件改造 |
| W3 扫描/上网 并行 | **NOT VERIFIED** | 理论够但无实测 | 硬件实测 UART 丢帧率 |
| W4 多节点接入 | **VERIFIED 架构 + NOT VERIFIED 实测** | 多 relay → Site Server 架构成立 | 实测 4+ 节点同时接入 |
| 节点发现/IP | **GAP** | 无 mDNS；手动配置 | 固件改造或运维文档 |
| 断线重连 | **VERIFIED 静态 + NOT VERIFIED 实测** | 代码完整 | 模拟断网重连测试 |
| AP + STA 并行 | **GAP**（ESP-IDF 支持但固件未用） | sdkconfig 默认关闭 + 代码无 AP 分支 | 固件开发范围 |

**硬件验证优先级（Task F 展开）**：

1. W3（C5 扫描 + S3 上网并行 UART 丢帧）
2. W1（S3 relay → Site Server WS 联调）
3. W4（多 relay 同时接入）
4. W2/WAPSTA（暂不排期）

---

## 9. 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 本文件状态：`CONDITIONAL PASS`（静态核查完成；并行运行 NOT VERIFIED）。
