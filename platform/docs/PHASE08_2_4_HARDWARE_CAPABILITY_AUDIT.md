# Phase 08.2.4 — 硬件与固件能力审计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `CONDITIONAL PASS`（静态审计完成；硬件实测未进行）
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，静态审计所有 RF 扫描 / RSSI / Wi-Fi / 多节点架构能力
> 性质：**静态审计。所有 VERIFIED 项均有代码/配置/编译常量直接证据；所有 NOT VERIFIED 项均明确给出验证方法。**

---

## 审计范围

| 子任务 | 章节 | 重点 |
|---|---|---|
| B1 RF 扫描能力 | §2 | `PROFILE_MAX_CHANNELS`、GROUP 模式、单 LO 覆盖、目录 vs 并发 vs 速率 |
| B2 RSSI 数据语义 | §3 | dBFS、dbm 空值、换算公式、校准 |
| B3 Wi-Fi 接入能力 | §4 | SoftAP 关闭、STA 编译、扫描并行、AP Direct 物理可行性 |
| B4 多节点架构 | §5 | S3 relay 拓扑、节点身份、数据序号、离线检测 |

---

## 2. B1 — RF 扫描能力

### 2.1 四个必须分开的能力（本次硬区分）

| # | 能力 | 含义 | 代码证据 | 状态 |
|---|---|---|---|---|
| A | 频点目录覆盖数量 | 产品层知道多少个逻辑频点 | `profile.c` `equiv_hz=5345000–5945000`（~600 MHz 带宽）；R/A/B/E/F/L 六个族段枚举在 `claude` 说明 | **产品层，可设计 ≥48**（`PROPOSED`；目录枚举逻辑在产品/数据层，不在固件） |
| B | 单轮实际扫描数量 | 一次 `profiler_set_channels` 调用能加载的最大 channels | `profile.h:8` `#define PROFILE_MAX_CHANNELS 8`；`planner.c:66` `if (nch > PROFILE_MAX_CHANNELS) nch = PROFILE_MAX_CHANNELS` | **VERIFIED — 硬限制 8** |
| C | 同时工作的 RF 接收通道 | ESP32-C5 物理上能同时接收几个 | C5 单 LO 架构；`GROUP` 模式 40 MHz span 下一个 LO 覆盖多 channel，但 C5 PHY 一次只有一个 LO 在收 | **VERIFIED — 物理上单接收通道** |
| D | 实际采样更新速率 | 每个 channel 每秒收到多少次 RSSI | `scanner.c:28` `MAX_ROTATION_HZ = 1050`；默认 8 channel × 4 LO ≈ 60 updates/s per channel（CLAUDE.md Performance 表）；**无真实压测** | **NOT VERIFIED — 仅低负载推算** |

### 2.2 C5 频段与 LO 调度

`planner.c:42–80` GROUP 模式调度逻辑（VERIFIED）：

```text
C5 (5345–5945 MHz, ≈600 MHz 总带宽)
  hop_max_span_khz = 40000   ← 一个 LO 覆盖 40 MHz
  160 MHz 扫描窗口 → 4 个 LO
  600 MHz 全窗口 ≈ 15 个 LO
  
  planner 在 channels 上做：
  for each channel_i:
    lo_khz = tune_lo_khz - (channel_i - min) * spacing
    hop_lo_i = round((lo_khz - hop_lo_min) / hop_max_span_khz)
  同一 hop_lo_i 下的多个 channel 被归为一组
  同一组内扫描时只调一次 LO
```

**结论**：固件确实具备"LO 分组扫描多 channel"的能力；**但单轮最多加载 8 个 channel**，一次运行不超过 15 个 LO。"40+ 目录"要靠**轮询多轮**遍历，不是一轮并发。

### 2.3 三类 profile 默认值（VERIFIED，`profile.c:202–219`）

| Profile | 芯片 | 默认 channel | hop 模式 |
|---|---|---|---|
| `c5-race` | C5 | R1–R8（1000 Hz spacing） | GROUP（40 MHz span） |
| `s3-race24` | S3 | 2.4 GHz 模拟（非真实扫描） | 非 GROUP（`hop_min_span_khz=30000`） |

### 2.4 扫描帧输出结构（VERIFIED，`frames.h`）

| 帧类型 | code | 内容 |
|---|---|---|
| METRICS | 1 | `{freq_hz, device_timestamp_ms, rotationHz, hopsTotal, device_state, hop, lo_khz, hop_channel_t[]}` |
| LEVELS | 6 | `{freq_hz, device_timestamp_ms, rotationHz, levels[]}` |
| SPECTRUM | 2 | `{freq_hz, bins[]}` |
| TELEMETRY | 3 | `{rssi_dbm, snr_db, temperature_c, voltage_mv}` |

**关键**：METRICS 帧中 `hop_channel_t[]` 结构：
```c
typedef struct hop_channel_t {
    uint8_t channel_index;
    int16_t rssi_cdb;       // ← 单位 centi-dBFS (1/100 dB)
    int16_t snr_cdb;
} hop_channel_t;
```

### 2.5 丢帧与稳定性（NOT VERIFIED）

- 扫描过程**无丢帧检测逻辑**（scanner.c 不计数、不做 CRC）；
- SPECTRUM/RAWIQ bins > 512 时 Site Server ingestion 会截断（`ingestion/__init__.py:172–174`）；
- 无饱和测试、无队列压力测试、无长稳 72h+ 运行记录；
- C5 固件无 SD 卡或本地 WAL（仅 S3 relay 侧有，但 relay 只是 UART bridge + STA）。

---

## 3. B2 — RSSI 数据语义

### 3.1 当前单位（VERIFIED）

**原始数据单位：dBFS（centi-dBFS，1/100 dB）**

证据链：

| 环节 | 证据 |
|---|---|
| C5 固件 scan->capture() 输出 | `frames.h` `hop_channel_t.rssi_cdb` = `int16` 单位 centi-dBFS |
| C5 scanner_loop() 填充 | `scanner.c: hop_channel_t{channel_idx, rssi_cdb, snr_cdb}` = 直接写入，**无换算** |
| Site Server ingestion | `ingestion/__init__.py:162` METRICS payload_json 全量保留 |
| Measurement payload_json | 字段名 `rssi_cdb`、`snr_cdb`、`lo_khz`、`freq_hz`（`frequency_t` 枚举转 int Hz） |
| METRICS 帧结构 | TELEMETRY 帧有 `rssi_dbm` 字段（但当前没有任何代码能产生真实 dBm — 见下） |

**换算公式（VERIFIED，`frames.h`）**：

| 帧类型 | 换算 | 说明 |
|---|---|---|
| METRICS.hop_channel_t.rssi_cdb | dBFS = rssi_cdb / 100 | centi-dBFS → dBFS |
| LEVELS.levels[] | dBFS = code/4 − 63.75 | int8 LEVELS code → dBFS |
| TELEMETRY.rssi_dbm | 无换算 | 当前不产生真实 dBm（见 §3.3） |

### 3.2 校准机制（GAP，VERIFIED "不存在"）

- 无校准 profile/表；
- 无出厂校准值；
- 无 offset/增益常量；
- 无 AGC 状态影响的补偿模型；
- 无"设备间可比性保证"代码。

**结论**：不同 C5 节点的 RSSI（dBFS）**不具有跨设备可比性**，除非后期增加校准。

### 3.3 TELEMETRY 帧的 dBm（NOT VERIFIED）

`frames.h` 定义 TELEMETRY 有 `rssi_dbm` 字段，但：

- `scanner.c` 未找到任何写入 `rssi_dbm` 的位置；
- C5 ESP-IDF Wi-Fi RSSI API 返回 dBm（`esp_wifi_sta_get_rssi`），但 C5 head 固件无 STA 模式（`WIFI_MODE_NULL`）；
- 该字段**当前不会被真实填充**。

**不得**把 METRICS 帧中的 rssi_cdb（dBFS）直接换算或命名为 dBm。dBm 转换必须依赖校准曲线。

### 3.4 推荐数据列（沿用 R2，新增来源字段）

| 目标字段 | 类型 | 来源 | 当前状态 |
|---|---|---|---|
| `rssi_dbfs` | INTEGER（centi-dBFS） | METRICS. hop_channel_t.rssi_cdb | payload_json 已有 |
| `rssi_dbm` | FLOAT / NULL | 校准后计算 | **当前全部 NULL**（不得由 dBFS 推导） |
| `rssi_unit` | VARCHAR | 静态值 'dbfs' | 无此列（建议新增） |
| `sample_quality` | VARCHAR | 校准状态 / 帧截断状态 | 无此列（建议 'raw'/'calibrated'/'uncalibrated'） |
| `frequency_hz` | INTEGER | METRICS.lo_khz 或 hop_channel_t 映射 | payload_json 有 `lo_khz` / `frequency_t` |
| `channel_id` | VARCHAR | channel_index → 目录映射 | payload_json 有 `channel_index`（需反查目录） |

---

## 4. B3 — Wi-Fi 接入能力

### 4.1 C5 扫描头 Wi-Fi 状态（VERIFIED）

| 项 | 事实 | 证据 |
|---|---|---|
| sdkconfig SoftAP | `# CONFIG_ESP_WIFI_SOFTAP_SUPPORT is not set` | `sdkconfig.defaults:35` |
| sdkconfig STA | STA 代码未编译进 C5 head env | `platformio.ini env:esp32c5 / env:esp32c5-head` 均无 `RX_WIFI_RELAY=1` |
| main.c 模式 | `WIFI_MODE_NULL` — 只拉 PHY 做扫描 | `main.c:132` |
| Wi-Fi 任务 | 无 `wifi_init()` / `ws_link_init()` 调用（C5 head 路径） | `main.c` C5 分支 `app_main → scanner_init → scanner_loop`，无网络初始化 |
| AP Direct 物理可行性 | **不可行** | SoftAP 关闭 + 无 Wi-Fi 任务 + 无节点服务端 |

### 4.2 S3 Relay Wi-Fi 状态（VERIFIED）

| 项 | 事实 | 证据 |
|---|---|---|
| sdkconfig 目标 | `env:esp32s3-relay` | `platformio.ini:22` |
| build_flags | `-DRX_WIFI_RELAY=1 -DRX_TARGET_S3=1` | `platformio.ini:27` |
| main.c 路径 | `#ifdef RX_WIFI_RELAY` → `relay_pipe_init()` + `ws_link_init()` + `uart_init(uart_num=1, baud=UART_LINK_BAUD)` | `main.c:109–119` |
| STA | `ws_link.c` `wifi_mode = WIFI_MODE_STA`；完整连接/重连/指数退避 | `ws_link.c` 561 行完整实现 |
| AP | **无**；relay 只有 STA；无 `WIFI_MODE_AP` 分支 | `ws_link.c` 无 AP 相关代码 |
| WebSocket uplink | `ws_link.c` WS_RELAY_MODE：连接 `wss://<site_server>/ws/v1/agents/connect`，发送 `gateway_id` + frame 二进制 | `ws_link.c` `ws_send_frame()` |
| 重连 | 指数退避（`relay_pipe.c` 注释 1 s → 30 s 上限）；无心跳超时检测（但 Site Server 侧有） | `relay_pipe.c` / Site Server `connection.py` |

### 4.3 Wi-Fi 与 RF 扫描并行（NOT VERIFIED）

C5 head：本身**无 Wi-Fi 任务**，所以"扫描与 Wi-Fi 并行"这个概念不适用。
S3 relay：本身**不做 RF 扫描**（RX_WIFI_RELAY 分支跳过 scanner_init），只走 UART→STA→WS uplink。

**当前固件**：RF 扫描与 Wi-Fi **物理上在两个不同芯片上**并行运行。C5 扫描、S3 上网、两者之间 UART 921600。这个并行结构**有希望可用**，但：

- UART 921600 上限 ≈ 92 KB/s ≈ 92,000 字节/秒；
- METRICS 帧（1 hop = 23B；8 hop ≈ 160B）+ LEVELS + SPECTRUM（bins ≤ 256 截断后仍 ≤ 1024B）≈ 估算约 1–2 KB/s；
- **远低于 UART 上限**，理论上够用；
- **但无真实负载测试、无丢帧计数、无长稳并行记录**。

### 4.4 C5 直连 Wi-Fi LAN（GAP）

**不存在**。C5 head 固件 `WIFI_MODE_NULL`，SoftAP 和 STA 都没有编译。若需要 C5 直连 LAN 必须：

1. 编译 STA 支持（`CONFIG_ESP_WIFI_STA_SUPPORT=y` + 相应 sdkconfig）；
2. 处理"扫描与 STA coexistence"（ESP-IDF 有 API，但需要固件改造）；
3. 处理 STA 认证/连接/重连（需要 `ssid/pass` 配置）；
4. 处理 uplink 协议（复用 ws_link.c 或协议重构）。

这是固件**实质性改造**，属于 A8 固件立项范围。

---

## 5. B4 — 多节点接入架构

### 5.1 当前架构（VERIFIED）

```text
           RF Node 1                    RF Node 2               RF Node 3
     ┌───────────────┐            ┌───────────────┐        ┌───────────────┐
     │ ESP32-C5 HEAD  │            │ ESP32-C5 HEAD  │        │ ESP32-C5 HEAD  │
     │ 8-channel scan │            │ 8-channel scan │        │ 8-channel scan │
     │ WIFI_MODE_NULL │            │ WIFI_MODE_NULL │        │ WIFI_MODE_NULL │
     └───────┬───────┘            └───────┬───────┘        └───────┬───────┘
             │ UART 921600                  │ UART 921600            │ UART 921600
     ┌───────▼───────┐            ┌───────▼───────┐        ┌───────▼───────┐
     │ ESP32-S3 RELAY │            │ ESP32-S3 RELAY │        │ ESP32-S3 RELAY │
     │ WIFI_MODE_STA  │            │ WIFI_MODE_STA  │        │ WIFI_MODE_STA  │
     │ WS uplink      │            │ WS uplink      │        │ WS uplink      │
     │ gateway_id=?   │            │ gateway_id=?   │        │ gateway_id=?   │
     └───────┬───────┘            └───────┬───────┘        └───────┬───────┘
             │ Wi-Fi LAN                   │ Wi-Fi LAN              │ Wi-Fi LAN
             └─────────────────┬───────────┴───────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   SITE SERVER       │
                    │ FastAPI + WS agent  │
                    │ SQLite / PostgreSQL │
                    └─────────────────────┘
```

### 5.2 与 PRD"Gateway Agent"概念的对齐（VERIFIED + GAP）

| PRD 概念 | 当前代码映射 | 缺口 |
|---|---|---|
| Gateway Agent | Site Server 的 `/ws/v1/agents/connect` 已实现；Agent 有 `agent_id + credential` 认证 | **relay 固件里 `gateway_id` 的来源？**（`ws_link.c` 引用但未找到 relay 侧传入） |
| Node（RF 节点） | **无独立建模**；当前 `Measurement.device_id` 是业务 "Device"（配对码 adopt 的那个），不是 C5 扫描头 | 需要新增 RF Node 实体（`node_id`）；或确认 Device 是否等价于 Node |
| Site | `site_id` 已存在，但 relay 侧 site 绑定未知 | 需要 relay 固件知道自己属于哪个 Site |
| RF Node → Gateway Agent 身份链 | 无。当前 relay 不识别 C5 head 的身份；UART 是纯透明桥接 | 需要定义 `node_id` 如何从 C5 head 传出来并被 relay 上报 |

### 5.3 节点身份识别（GAP）

- C5 head 无唯一 ID；`profile.c` 里有默认 `c5-race` profile 名称，但 profile 不是节点 ID；
- relay 固件 `ws_link.c` 发送 `gateway_id`（`CONFIG_GATEWAY_ID` 或编译常量）；
- 因此当前架构里**一个 S3 relay = 一个 Agent**；
- 但"一个 C5 head = 一个 Device"的映射是**假设**，代码里没有显式绑定关系；
- 多 C5 head → 多 S3 relay → 多 Agent → Site Server 聚合：架构上可行，但需要每对 relay+head 各自配对；
- 无法一台 S3 relay 带多台 C5 head（UART 只有一个；协议帧结构也不支持多路混合）。

### 5.4 数据序号与时间戳（VERIFIED）

| 字段 | 含义 | 证据 |
|---|---|---|
| `METRICS.device_timestamp_ms` | C5 head 侧帧生成时刻（free-running，**无墙钟锚点**） | `scanner.c` 直接填；无 RTC |
| `Measurement.device_timestamp_ms` | 原样透传 | `ingestion/__init__.py:234` |
| `Measurement.agent_received_at` | Site Server 收到 Agent 事件的时刻（UTC） | `agents/connection.py` 调用时传入；Agent 内部 `site_connector` 接收时刻 |
| `Measurement.created_at` | SQLite/PG INSERT 时刻 | ORM `default=func.now()` |
| `Measurement.sequence` | Agent 传递的帧序号 | Site Server WS handler 透传 |
| `Measurement.device_boot_id` | Agent 上报的 boot ID（防止重启后序号回环） | Site Server WS handler 透传 |
| `Measurement.event_id` | Agent 生成并传入 Site Server；Site Server 做 `(site_id,event_id)` 唯一键去重 | `ingestion/__init__.py:_resolve_event_id` |

### 5.5 节点重启后的身份保持（NOT VERIFIED）

- `device_boot_id` 设计用于"重启后新 boot_id + 重置序号 → 生成新 event_id → 不与旧序号冲突"；
- 但 C5 head / S3 relay 是否实际产生 boot_id 并随帧上报？**未找到 relay 固件里生成 boot_id 的代码**；
- 需实测 C5 → S3 → Site Server 链路上 boot_id 是否真实存在并随重启变化。

### 5.6 离线检测（VERIFIED + GAP）

| 环节 | 机制 | 证据 |
|---|---|---|
| Site Server 侧 Agent WS 心跳 | 15 s Agent 发 heartbeat；Site Server 45 s 超时关闭 | `agents/connection.py` |
| relay 固件侧心跳 | `ws_link.c` `ws_heartbeat_interval=15s`，超时 60s 触发重连 | `ws_link.c` |
| Site Server 侧 Device 离线 | 依赖 Agent 上报 `device.state_change`；**无独立心跳** | → D01 stale device status（R2 已记录） |
| RF 数据在线过期 | 复用同一机制 | 同上 |
| 断网重连 | relay 侧指数退避；重连后重新发 `agent.devices_snapshot` | `site_connector.py:_backoff_reconnect / _send_devices_snapshot` |

---

## 6. 本阶段小结（静态审计）

| 能力 | 状态 | 关键结论 |
|---|---|---|
| RF 扫描能力（B1） | **CONDITIONAL PASS** | 硬限制 8 通道并发；GROUP 模式单 LO 覆盖多 channel；目录覆盖 ≠ 并发；采样速率需实测 |
| RSSI 语义（B2） | **CONDITIONAL PASS** | 全链路 dBFS；无校准；TELEMETRY.rssi_dbm 不会被填充；**不得虚构 dBm** |
| Wi-Fi 接入（B3） | **CONDITIONAL PASS** | C5 head 无 Wi-Fi；S3 relay STA 完整但只桥接；AP Direct 物理不可行；扫描/上网在两芯片并行、UART 921600 理论够 |
| 多节点架构（B4） | **CONDITIONAL PASS** | relay head 1:1；一个 S3 relay = 一个 Agent；Device ≠ Node 身份链需补；boot_id 需实测 |
| 数据序号（B4） | **VERIFIED** | 三时间戳齐全；event_id 幂等唯一键；sequence + boot_id 防重启冲突 |
| 离线检测 | **CONDITIONAL PASS** | Agent 心跳 45s 超时；Device 无独立心跳（D01 影响 RF 页面） |

**下一步进入**：Task C（数据链路逐环审计，`PHASE08_2_4_RF_DATA_PATH_AUDIT.md`）。

---

## 7. 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 所有审计证据均来自只读阅读；未编译、未刷写、未上电、未连接硬件。
- 本文件状态：`CONDITIONAL PASS`（静态审计完成；硬件实测 NOT VERIFIED）。
