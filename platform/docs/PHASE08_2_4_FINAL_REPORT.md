# Phase 08.2.4 — Final Report（硬件与协议能力审计最终报告）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `CONDITIONAL PASS`（静态审计完成；硬件实测 NOT VERIFIED；无任何代码/固件/DB/协议变更）
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，6 类能力最终状态 + 下一阶段建议
> 性质：**最终报告。仅静态审计，无硬件实测，无任何代码变更。**

---

## 1. 本阶段交付物

| # | 文档 | 内容 | 行数 |
|---|---|---|---|
| 1 | `PHASE08_2_4_FAST_TRACK_DECISIONS.md` | 快速决策收口 + A1/A2 冲突核查 | ~270 |
| 2 | `PHASE08_2_4_HARDWARE_CAPABILITY_AUDIT.md` | B1–B4 硬件/固件/Wi-Fi/多节点架构审计 | ~280 |
| 3 | `PHASE08_2_4_RF_DATA_PATH_AUDIT.md` | 数据链 6 环 + 目标字段逐字段映射 | ~220 |
| 4 | `PHASE08_2_4_WIFI_ACCESS_AUDIT.md` | W1–W4 Wi-Fi LAN / AP Direct / 并行 / 多节点 | ~230 |
| 5 | `PHASE08_2_4_MULTI_NODE_VALIDATION_PLAN.md` | 5 场景可行性 + H-01–H-09 最小测试计划 | ~280 |
| 6 | `PHASE08_2_4_HARDWARE_TEST_CHECKLIST.md` | H-01–H-09 操作步骤 + 通过判定 + 风险 | ~250 |
| 7 | **本报告** | 6 类能力最终状态 + 下一阶段建议 | — |

**未做**：编译、刷写、硬件上电、连接、任何运行测试。

---

## 2. 审计范围

| 子任务 | 已完成 | 依赖文件 |
|---|---|---|
| A 快速决策收口 | ✓ | A1/A2 ADR + R2 决策文件 |
| B 硬件/固件审计 | ✓ | `firmware/src/**/*.c` + `platformio.ini` + `sdkconfig*` |
| C 数据链路审计 | ✓ | Site Server `models.py` + `ingestion/__init__.py` + Agent `adapter.py` |
| D Wi-Fi 接入 | ✓ | `ws_link.c` + `relay_pipe.c` + sdkconfig |
| E 多节点验证计划 | ✓ | B/C/D 证据综合 |
| F 硬件测试清单 | ✓ | E 的实施操作化 |
| G 最终报告 | ✓ | 本文件 |

---

## 3. 六个能力类最终状态

### 3.1 RF 扫描能力 — `CONDITIONAL PASS`

| 能力 | 状态 | 关键证据 |
|---|---|---|
| 目录覆盖数量 | PROPOSED | 固件不做目录枚举；产品层可设计 ≥48 |
| 单轮实际扫描数量 | **VERIFIED** | `PROFILE_MAX_CHANNELS=8`（编译期硬限制） |
| 物理接收通道 | **VERIFIED** | ESP32-C5 单 LO 接收；GROUP 模式 40 MHz span 一 LO 覆盖多 channel |
| 实际采样速率 | **NOT VERIFIED** | `MAX_ROTATION_HZ=1050` 是上限；默认 8ch × 4 LO ≈ 60 updates/s per channel 需实测 |
| 丢帧/长稳 | **NOT VERIFIED** | 无丢帧计数逻辑；无长稳记录 |
| 40+ 完整覆盖 | **NOT VERIFIED** | 目录数 / rotationHz = 完整轮询时间，需要实测 |

**已确认的关键约束**：
- 固件硬限制 8 channel；**不能宣称"40+ 并发"**。
- GROUP 模式有效；单轮最多加载 8 channel、遍历最多 15 LO。
- 扫描帧 METRICS / LEVELS / SPECTRUM 三种都输出；RAWIQ 默认被 Site Server ingestion 丢弃。

---

### 3.2 Wi-Fi 接入能力 — `CONDITIONAL PASS`

| 能力 | 状态 | 关键证据 |
|---|---|---|
| S3 relay STA（W1） | **VERIFIED** | `ws_link.c` 561 行完整实现 |
| AP Direct（W2） | **GAP（物理不可行）** | SoftAP 编译关闭；无 AP 代码；无服务端 |
| 扫描/上网 并行（W3） | **NOT VERIFIED** | 两芯片跨芯片 UART 921600 理论够；无实测丢帧 |
| 多节点接入（W4） | **VERIFIED（架构）+ NOT VERIFIED（实测）** | 多 relay → 同一 Site Server 架构成立；实测联调未做 |
| 节点发现/IP | **GAP** | 无 mDNS；手动配置 `CONFIG_WS_URL` |
| 断线重连 | **VERIFIED（静态）+ NOT VERIFIED（实测）** | 指数退避完整；重连后设备状态恢复未测 |
| AP + STA 并行 | **GAP** | ESP-IDF 支持但固件未用 |

---

### 3.3 多节点接入能力 — `CONDITIONAL PASS`

| 能力 | 状态 | 关键证据 |
|---|---|---|
| 拓扑：多 C5 → 多 S3 relay → 同一 Site Server | **VERIFIED** | 架构层成立 |
| 单 relay 身份识别 | **VERIFIED** | `gateway_id` 编译常量/NVS |
| C5 head 身份识别 | **GAP** | 无唯一 ID；靠配对码 + adopt |
| device_id ≡ node_id | **SEMANTIC GAP** | 代码中无显式等价关系；Device 业务概念 vs Node RF 概念 |
| 一个 relay 带多 C5 | **物理不可行** | UART 只有一个 |
| boot_id 在 C5→S3→Agent 链路真实存在 | **NOT VERIFIED** | Site Server 透传；relay 侧是否真实生成/上报需实测 |
| 节点离线检测 | **CONDITIONAL PASS** | Agent 心跳 45 s 超时；Device 无独立心跳（D01 影响 RF 页面） |

---

### 3.4 数据链路 — `CONDITIONAL PASS`

| 能力 | 状态 | 关键证据 |
|---|---|---|
| C5 → S3 UART 数据不丢帧 | **NOT VERIFIED** | 理论够但无实测 |
| S3 → Site Server WS uplink | **VERIFIED（代码完整）** | `ws_link.c` 完整 + Site Server WS handler 完整 |
| METRICS 帧字段完整 | **VERIFIED** | hop_channel_t[] / rssi_cdb / device_timestamp_ms / lo_khz 全有 |
| SPECTRUM 帧 bins > 512 截断 | **VERIFIED** | ingestion 代码有 |
| RAWIQ 默认丢弃 | **VERIFIED** | ingestion 代码有 |
| event_id 幂等去重 | **VERIFIED** | `uq_measurement_event(site_id,event_id)` |
| boot_id + sequence 防重启冲突 | **VERIFIED（设计）** | Site Server 透传；relay 侧是否真实产生待实测 |
| device_timestamp_ms 无墙钟锚点 | **VERIFIED（事实）** | free-running ms；不得用 created_at 冒充 |

**关键事实（VERIFIED）**：`Measurement.payload_json` 是整帧 JSON；目标 RF 列全部在 JSON 里，无独立列（A6 REVISION REQUIRED）。

---

### 3.5 数据模型 — `CONDITIONAL PASS`

| 字段 | 状态 | 来源 |
|---|---|---|
| site_id | VERIFIED | 现有 Measurement 列 |
| gateway_id | VERIFIED | 现有 `agent_id`（语义等价） |
| node_id | **SEMANTIC GAP** | 现有 `device_id`；Device ≠ Node 概念需明确 |
| node_boot_id | VERIFIED | 现有 `device_boot_id` |
| channel_id | **CONDITIONAL** | payload_json.channel_index + 目录反查 |
| frequency_hz | **NOT VERIFIED** | 需 lo_khz + channel_index + spacing 反算 |
| rssi_raw | VERIFIED | payload_json.rssi_cdb |
| rssi_unit | **GAP** | 无列；RF 层硬编码 'dbfs' |
| rssi_calibrated | **GAP** | 当前全部 NULL；禁止 dBFS 推导 |
| sample_timestamp | VERIFIED | 现有 `device_timestamp_ms`（无墙钟锚点） |
| received_timestamp | VERIFIED | 现有 `agent_received_at` + `created_at` |
| sequence | VERIFIED | 现有列 |
| sample_quality | **GAP** | 无列；RF 层设计 |

---

### 3.6 硬件实测准备 — `PROPOSED — PENDING HUMAN APPROVAL AND EXECUTION`

| 项目 | 内容 |
|---|---|
| 测试计划 | 9 项最小测试 H-01–H-09 |
| 通过门禁 | H-01/H-02/H-03/H-04 必须全过才能继续 H-05+ |
| 证据归档 | `docs/evidence/PHASE08_2_4/hw/` 独立目录 |
| 执行状态 | **未执行**；需人工批准后执行 |

---

## 4. 本阶段最终状态判定

| 能力 | 状态 | 解释 |
|---|---|---|
| RF 扫描能力 | `CONDITIONAL PASS` | 静态审计完成；固件硬限制明确；实测未做 |
| Wi-Fi 接入能力 | `CONDITIONAL PASS` | 静态审计完成；AP Direct 不可行；扫描/上网并行未实测 |
| 多节点接入能力 | `CONDITIONAL PASS` | 架构层成立；身份链 GAP；实测未做 |
| 数据链路 | `CONDITIONAL PASS` | 字段映射完整；端到端丢帧未实测 |
| 数据模型 | `CONDITIONAL PASS` | 现有库表 + payload_json 足够作为 RF 层字段来源；迁移仍需 A6 补齐 |
| 硬件实测准备 | `PROPOSED — PENDING HUMAN APPROVAL` | 计划齐全；未执行 |

**本阶段总状态：`CONDITIONAL PASS`**

> 条件 = 静态审计完成，未发现阻塞性硬限制（除 AP Direct 物理不可行）；所有"VERIFIED"结论均有代码/配置/编译常量直接证据；所有"NOT VERIFIED/GAP"项均有明确的验证或补齐路径；**未执行任何硬件测试**，也**不得据此宣称已具备硬件能力**。

---

## 5. 本阶段未做 / 明确禁止

| # | 禁止事项 | 执行情况 |
|---|---|---|
| 1 | 修改任何产品代码 | 未做 ✓ |
| 2 | 修改任何数据库 / 执行迁移 | 未做 ✓ |
| 3 | 修改任何固件 | 未做 ✓ |
| 4 | 修改任何设备通信协议 | 未做 ✓ |
| 5 | 修改任何 REST API / WebSocket / 前端页面 / 运行配置 | 未做 ✓ |
| 6 | 自动刷写设备 / 自动上电测试 | 未做 ✓ |
| 7 | 启动 Phase 08.3 | 未做 ✓ |
| 8 | 启动 SP-00–SP-17 | 未做 ✓ |

---

## 6. 关键发现（必须人工知道）

| # | 发现 | 影响 |
|---|---|---|
| **F-01** | `PROFILE_MAX_CHANNELS=8` 是编译期硬限制；CHANNELS 命令给再多也被截断到 8 | 任何"40+ 并发扫描"的声称都是假的；必须把"目录覆盖"和"并发扫描"分开 |
| **F-02** | ESP32-C5 head 固件 `WIFI_MODE_NULL`；SoftAP 编译关闭；无 AP Direct 物理可行性 | AP Direct 定位保留但能力为 GAP；正式部署走 Wi-Fi LAN |
| **F-03** | 现有架构是 **C5 head（无 Wi-Fi）—UART 921600— S3 relay（STA+WS uplink）**；C5 直连 LAN 不存在 | 若要 C5 直连 LAN 需固件实质性改造（编译 STA + coexistence） |
| **F-04** | RSSI 全链路 dBFS（centi-dBFS）；TELEMETRY.rssi_dbm 不会被真实填充；**无校准机制** | 不得虚构 dBm 或跨设备可比性；dBFS 原始值保留 |
| **F-05** | Device（业务概念）≠ Node（RF 重基线概念）；Device=Node 等价关系在代码中无显式绑定 | 需在 RF 层明确，或新增 RF Node 实体 |
| **F-06** | Site Server Measurement 表无独立 RF 列（channel_id / frequency_hz / rssi_raw / rssi_unit）；全部在 `payload_json` 里 | A6 REVISION REQUIRED 不能解除；RF Service 层要先解析 JSON 或先 Migration |
| **F-07** | 无 RF /api/v1/rf/* 接口；无 WS-U 前端 WS | RF Analysis Service 是全新后端模块；RF Workbench 是全新前端模块 |
| **F-08** | 一个 S3 relay = 一个 Agent；一个 C5 head ≈ 一个 Device；**一个 relay 带多 C5 head 物理不可行** | 多节点按 "每 C5 head 配一个 S3 relay" 执行 |
| **F-09** | Device 无独立心跳；RF 页面复用 Device 在线状态（D01 影响） | RF 页面"数据过期"标记需要 D01 裁定（A9） |
| **F-10** | Site Server ingestion 有 `get_ingestion_stats()` 可事后统计丢帧（persisted/duplicate/integrity_errors） | H-03/H-04/H-05 可直接用这个指标 |

---

## 7. 下一阶段建议（非启动指令）

本阶段**不启动**任何后续阶段；下列建议只是供人工决策参考。

### 7.1 必须先做的（Gate）

| # | 项目 | 为什么 |
|---|---|---|
| 1 | H-01–H-09 硬件实测 | 所有"NOT VERIFIED"项只有实测才能关闭；RF Workbench 开发前必须知道真实采样速率和丢帧率 |
| 2 | A6 Migration 补齐 + 批准 | RF Service 层无论走"先解析 JSON"还是"先 Migration 建列"都需要决策 |
| 3 | A9 WP-02 D01 裁定 | RF 页面在线/过期语义复用同一机制 |
| 4 | C-01 阶段编号裁定 | 后续 RF 工作包标签需要 |
| 5 | A1/A2 人工批准 | 架构基线必须确认 |

### 7.2 可并行的（实施）

| # | 项目 | 依赖 |
|---|---|---|
| 1 | RF 目录表设计（48 条 A–F） | 无 |
| 2 | RF Analysis Service 数据模型 + 层设计 | A6 或"临时 JSON 解析策略"被批准 |
| 3 | RF Workbench UI 信息架构 | RF 数据模型 + 8 channel 硬限制明确 |
| 4 | 设备离线检测独立心跳（修复 D01） | A9 选 A |
| 5 | ADR-11 WS 票据实现（为未来 WS-U） | A2-01/02/06/07/12 全部批准 |

### 7.3 暂不启动的

| # | 项目 | 原因 |
|---|---|---|
| 1 | Phase 08.3 | A10 NO GO；所有前置门禁未满足 |
| 2 | AP Direct 固件改造 | SoftAP 编译关闭 + coexistence + 服务端三件全需固件；属 A8 立项范围 |
| 3 | C5 直连 Wi-Fi LAN | STA + coexistence + uplink 协议重构 |
| 4 | 40+ 并发扫描 | PROFILE_MAX_CHANNELS=8 是编译硬限制，固件不重构无法改 |
| 5 | SP-00–SP-17 实施包 | A2 未批 |

---

## 8. 验收核对（Phase 08.2.4）

| 验收项 | 结果 |
|---|---|
| 6 份（+本报告共 7 份）文档全部完成 | ✓ |
| 静态审计覆盖 B1–B4 / W1–W4 / 数据链 6 环 | ✓ |
| 无伪造"已实现"声明（所有 VERIFIED 有代码证据） | ✓ |
| 无编造性能数字（所有 NOT VERIFIED 有验证方法） | ✓ |
| AP Direct 物理不可行被诚实暴露 | ✓ |
| PROFILE_MAX_CHANNELS=8 硬限制被明确标注 | ✓ |
| 未修改任何代码/固件/数据库/协议/API/前端/配置 | ✓ |
| 未自动执行任何硬件测试 | ✓ |
| 未启动 Phase 08.3 / SP-00–SP-17 | ✓ |
| 所有 ADR/SP/WP-02 状态保持 | ✓ |
| 最终状态判定 | `CONDITIONAL PASS` |

---

## 9. 最终声明

**Phase 08.2.4 — Hardware & Protocol Capability Audit（Fast-Track Edition）** 已完成。

- 7 份文档已写入 `docs/`；
- 无任何产品代码、数据库、固件、协议、API、前端、运行配置变更；
- 硬件实测计划已准备但未执行，需人工批准后按 `PHASE08_2_4_HARDWARE_TEST_CHECKLIST.md` 执行；
- 最终状态 **`CONDITIONAL PASS`**；
- **停止，等待人工下一步指令。**

不得自行进入 Phase 08.3 或任何后续开发阶段。
