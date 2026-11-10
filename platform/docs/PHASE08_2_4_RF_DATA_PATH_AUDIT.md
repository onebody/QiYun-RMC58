# Phase 08.2.4 — RF 数据链路逐环审计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `CONDITIONAL PASS`（静态映射完成；端到端实测 NOT VERIFIED）
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，把 RF 目标数据模型逐字段映射到现有代码/库表/JSON
> 性质：**字段级映射。不修改任何库表或代码。**

---

## 1. 数据链 5 环概览

```text
[1] RF Hardware Node (ESP32-C5)   协议帧 →
[2] UART 921600 (C5 ↔ S3 relay)   二进制透传 →
[3] Gateway Agent (Site Server WS handler)   payload_json →
[4] Measurement Store (SQLite / PostgreSQL)   JSON 存储 →
[5] RF Analysis Service (即将设计)   解析 JSON 出列 →
[6] Platform UI (Web)
```

**关键结论（VERIFIED）**：

- 环 1 帧字段齐全；
- 环 2 UART 透传无协议损耗；
- 环 3 Site Server WS handler 做解码 + event_id/boot_id/sequence 透传；
- **环 4 只存 `payload_json`（整帧原始 JSON），不拆出独立列**；
- 环 5 不存在（RF Analysis Service 是下一阶段）；
- 环 6 不存在（RF Workbench 是下一阶段）。

---

## 2. 目标数据模型（Task C 给定）

```python
Measurement {
    site_id,
    gateway_id,
    node_id,
    node_boot_id,
    channel_id,
    frequency_hz,
    rssi_raw,
    rssi_unit,
    rssi_calibrated,
    sample_timestamp,
    received_timestamp,
    sequence,
    sample_quality
}
```

---

## 3. 逐字段映射（VERIFIED / NOT VERIFIED / GAP）

| # | 目标字段 | 环 1 帧来源 | 环 3 Agent → Site Server | 环 4 现有 Measurement 列 | 环 5 未来 RF Service | 状态 |
|---|---|---|---|---|---|---|
| 1 | `site_id` | 无（C5 不知道 Site） | Site Server WS 连接建立时由 Agent 身份绑定 | `Measurement.site_id`（已存在，VERIFIED） | 直读列 | **VERIFIED** |
| 2 | `gateway_id` | 无帧字段 | Site Server WS handler 传入 `agent_id` | `Measurement.agent_id`（VERIFIED；**语义等价于 gateway_id**） | 直读列 | **VERIFIED** |
| 3 | `node_id` | **无帧字段** | Agent 上报 `device_id`（`device.online` 事件里） | `Measurement.device_id`（VERIFIED；**语义上是 Device，不是 RF Node**） | 待拆解 | **SEMANTIC GAP**（需确认 Device=Node 或新增 Node 实体） |
| 4 | `node_boot_id` | **无帧字段** | Agent 上报 `device_boot_id`（`_device_boot_id()` 函数产生） | `Measurement.device_boot_id`（VERIFIED） | 直读列 | **VERIFIED**（但需实测 C5 → S3 → Agent 链路真实存在并随重启变化） |
| 5 | `channel_id` | METRICS 帧 `hop_channel_t.channel_index`（0–7） | payload_json 原样保留 | **在 `payload_json` 里**；无独立列 | 从 `payload_json.channel_index` 反查目录 | **CONDITIONAL**（目录表未存在；反查逻辑待写） |
| 6 | `frequency_hz` | METRICS 帧 `hop_channel_t` 无直接 freq；需由 `lo_khz` + `channel_index` + `spacing_hz` 反算；或 METRICS 帧有顶层 `lo_khz` / `hop_lo_freq_hz` | payload_json 保留 | **在 `payload_json` 里**；`lo_khz` 已有；无独立列 | 反算：`freq_hz = lo_khz*1000 + channel_index*spacing_hz` | **NOT VERIFIED**（反算逻辑待设计；spacing 需 profile 定义） |
| 7 | `rssi_raw` | METRICS 帧 `hop_channel_t.rssi_cdb`（centi-dBFS） | payload_json 键名 `rssi_cdb` 原样保留 | **在 `payload_json` 里**；无独立列 | 提取 `payload_json.rssi_cdb` | **VERIFIED**（但无独立列；需 JSON 解析） |
| 8 | `rssi_unit` | **无帧字段** | 无 | **无列**（全部 VERIFIED） | 硬编码 'dbfs'（来自固件语义） | **GAP**（需在 RF Service 层硬编码或列里写明） |
| 9 | `rssi_calibrated` | **无帧字段** | 无 | **无列**（全部 NULL，禁止推导） | 预留字段 | **GAP**（需校准数据上线后填充） |
| 10 | `sample_timestamp` | METRICS 帧 `device_timestamp_ms` | 透传为 `Measurement.device_timestamp_ms` | `Measurement.device_timestamp_ms`（VERIFIED） | 直读列 | **VERIFIED**（但 C5 是 free-running ms，**无墙钟锚点**） |
| 11 | `received_timestamp` | 无帧字段 | Site Server 接收时刻 UTC | `Measurement.agent_received_at` + `Measurement.created_at`（VERIFIED） | 直读列（建议用 `agent_received_at`，无则用 `created_at`） | **VERIFIED**（有两列可选） |
| 12 | `sequence` | METRICS 帧 `sequence`（uint32） | Site Server WS handler 透传 | `Measurement.sequence`（VERIFIED） | 直读列 | **VERIFIED** |
| 13 | `sample_quality` | **无帧字段** | 无 | **无列** | 从 `device_timestamp_ms` 是否存在 / `rssi_cdb` 是否存在 / 是否截断推导；或 hard-code 'raw' | **GAP**（枚举值需 RF 层设计） |

---

## 4. 字段保留能力汇总

| 目标 | 是否能实现 | 路径 | 说明 |
|---|---|---|---|
| 保留 node_id | **能（条件）** | `Measurement.device_id` | Device=Node 语义待确认；或需新增 Node 实体 |
| 保留 channel_id | **能（条件）** | `payload_json.channel_index` + 目录反查 | 目录与反查逻辑待设计 |
| 保留原始 RSSI | **能** | `payload_json.rssi_cdb` | centi-dBFS 单位 |
| 保留采样时间 | **能** | `Measurement.device_timestamp_ms` | free-running ms，无墙钟锚点 |
| 保留 Agent 接收时间 | **能** | `Measurement.agent_received_at` | UTC |
| 保留 Site 接收时间 | **能** | `Measurement.created_at` | UTC |
| 区分不同 node_id 的相同 channel_id | **能** | `device_id + device_timestamp_ms + channel_index` 联合查询 | payload_json 内部；REST 查询需 JSON 提取 |
| 处理节点断线/重启 | **能（条件）** | `device_boot_id` + Agent 离线检测 | Device 心跳在 D01 未解决前不可靠；boot_id 需实测 |

---

## 5. payload_json 结构细节（VERIFIED）

`Measurement.payload_json` 当前存的是**经 Agent adapter 解码后的完整帧 JSON**。典型 METRICS 帧内容（从 ingestion `build_measurement_payload_from_frame` 逻辑反推）：

```json
{
    "frame_type": "METRICS",
    "device_timestamp_ms": 1715436789001,
    "sequence": 12345,
    "rotationHz": 60,
    "hopsTotal": 4,
    "hop": 0,
    "lo_khz": 5350000,
    "hop_channel_t": [
        {"channel_index": 0, "rssi_cdb": -1234, "snr_cdb": 150},
        {"channel_index": 1, "rssi_cdb": -1456, "snr_cdb": 30},
        ... 最多 8 条
    ]
}
```

**注意**：

- 一个 Measurement 行 = 一帧 = 最多 8 个 channel 的 RSSI；
- 如果要"32 条独立数据系列"（4 node × 8 channel），需要**一帧一个 Measurement**，且**后续 RF Service 从每个 Measurement 的 payload_json.hop_channel_t 拆出 8 行**；
- SPECTRUM 帧 ingestion 会截断 `bins[]` > 512 时保留前 256 + 截断标记；
- RAWIQ 默认丢弃（`retention_rawiq_enabled=False`）；
- event_id 由 Agent 传入（或缺失时派生），Site Server 做 `uq_measurement_event(site_id,event_id)` 唯一键去重。

---

## 6. 当前 REST API 现状（VERIFIED，无 RF 接口）

| 接口 | 现状 |
|---|---|
| GET `/api/v1/measurements` | 返回整帧 JSON；白名单不含 `agent_received_at`（08.5 实测）；**无按 channel 查询** |
| GET `/api/v1/devices` | 设备列表（业务 Device，含 `device_id`） |
| POST/PATCH/DELETE（写端点） | 开放（无鉴权，G-03 已记录） |
| `/api/v1/rf/*` | **不存在**（404） |
| `/ws/v1/site` | **不存在**（404） |

RF 重基线 6 个目标接口（`docs/PHASE08_2_3_RF_ANALYSIS_API_DESIGN.md`）**全部未实现**。

---

## 7. 端到端验证清单（硬件实测阶段）

| # | 项目 | 当前状态 | 验证方法 |
|---|---|---|---|
| 1 | C5 → S3 UART 数据不丢帧 | NOT VERIFIED | 30 min 压力测试 + 帧计数 |
| 2 | S3 → Site Server WS uplink 不丢帧 | NOT VERIFIED | 同上 |
| 3 | boot_id 在 C5 → S3 → Agent 链路真实存在 | NOT VERIFIED | 重启后比对 |
| 4 | device_id 与 node_id 等价关系 | NOT VERIFIED | 多 C5 + 多 S3 relay 实验 |
| 5 | payload_json 字段完整性 | VERIFIED（静态） | 需动态验证真实帧内容 |
| 6 | ingestion 幂等性 | VERIFIED（静态） | 需重放同一 event_id 验证 |
| 7 | RAWIQ 默认丢弃行为 | VERIFIED（静态） | 需确认 C5 是否产生 RAWIQ 帧 |
| 8 | SPECTRUM 截断阈值生效 | VERIFIED（静态） | 需验证实测 bins 长度 |

---

## 8. 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 本文件状态：`CONDITIONAL PASS`（静态字段映射完成；端到端实测 NOT VERIFIED）。
