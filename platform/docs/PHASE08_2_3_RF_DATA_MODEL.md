# Phase 08.2.3 — RF 数据模型设计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.3 — Task D
> 性质：**数据模型设计，不创建表、不写迁移、不修改数据库**
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前无可靠版本记录

---

## 1. 设计结论摘要

当前数据库**没有** `Channel` 级事实表，也没有频点目录、节点能力、频道计划版本、校准档案。所有测量都在 `measurements.payload_json` 里，服务端无法还原某条记录对应的频率与频道标识。

因此 `Node × Channel × Time` 需求 **不能通过现有表满足**，必须新增一组表；本阶段只给设计，不落库。

| 需求字段 | 现状 | 结论 |
|---|---|---|
| `site_id` | `measurements.site_id` | **已存在** |
| `gateway_id` | `measurements.agent_id`（语义即 Gateway） | **已存在**，需明确别名 |
| `node_id` | `measurements.device_id` | **已存在** |
| `node_boot_id` | `measurements.device_boot_id` | **已存在** |
| `channel_id` | 无 | **新增** |
| `frequency_hz` | 无（仅 `payload_json.loKhz` + 索引） | **新增 + 需计划版本** |
| `rssi_dbm` | 无（原始为 dBFS） | **新增 + 校准** |
| `sample_timestamp` | `device_timestamp_ms` | **部分存在**（缺墙钟锚点） |
| `received_timestamp` | `agent_received_at` / `created_at` | **已存在（双时间戳）** |
| `sequence` | `measurements.sequence` | **已存在** |
| `sample_quality` | 无 | **新增** |

---

## 2. 概念模型

```
Site (1)
  └── Gateway Agent (N)                    agents
        └── RF Node (N)                    devices（语义别名 rf_node）
              ├── Node Capability (N)      node_capabilities（新增）
              ├── Channel Plan Version (N) node_channel_plans（新增）
              │      └── Channel Key ──▶ Channel Catalog（新增，全局目录）
              ├── Calibration Profile (0..N) calibration_profiles（新增，可选）
              └── RF Measurement (N)       rf_measurements（新增）

现有 raw frame store: measurements（保留，作为原始帧/审计来源）
```

**原则**：

1. `measurements` 继续保存原始帧，**不重写、不删除**。
2. `rf_measurements` 是 channel 级派生事实表，一行 = 一个节点 × 一个频道 × 一次采样。
3. `Channel Catalog` 是**全局目录**（跨节点共享频率定义）；`Node Channel Plan` 是**节点当时的实际映射**（含版本与生效区间）。
4. 聚合表是可选派生，不得替代原始事实。

---

## 3. 新增表设计

### 3.1 `channel_catalog`

| 字段 | 类型 | 说明 | 约束 |
|---|---|---|---|
| `channel_key` | VARCHAR(32) PK | 稳定键，如 `F1`、`R5` | 全局唯一 |
| `band` | VARCHAR(16) | `A/B/E/F/R/L` | 索引 |
| `frequency_hz` | BIGINT | 中心频率 | 非空 |
| `channel_width_hz` | INTEGER | 标称带宽 | 可空 |
| `label` | VARCHAR(64) | 展示名 | 可空 |
| `region` | VARCHAR(32) | 适用区域 | 默认 `global` |
| `catalog_version` | INTEGER | 目录版本 | 非空 |
| `is_active` | BOOLEAN | 是否启用 | 默认 true |
| `created_at` / `updated_at` | DATETIME | | |

- 目录可容纳 **48 条**（A/B/E/F/R/L 5.8 GHz 常见频点），满足 PRD-RF-002 的“40+ 目录”**条目数**。
- **目录存在 ≠ 节点可同时观测 40+**。节点能力由 `node_capabilities.max_concurrent_channels` 表达（现状 8）。

### 3.2 `node_capabilities`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(64) PK | |
| `device_id` | VARCHAR(64) FK `devices.id` | 节点 |
| `plan_version_id` | VARCHAR(64) FK `node_channel_plans.id` | 对应计划版本，可空 |
| `min_frequency_hz` / `max_frequency_hz` | BIGINT | 可观测范围 |
| `max_concurrent_channels` | INTEGER | 当前固件为 8 |
| `supports_spectrum` | BOOLEAN | 是否有 SPECTRUM 帧 |
| `spectrum_bins` | INTEGER | 默认 256 |
| `spectrum_span_hz` | INTEGER | 默认 40 MHz |
| `scan_rotation_hz` | FLOAT | 来自 TELEMETRY |
| `access_modes` | JSON | `["usb_serial","wifi_lan","ap_direct"]` 的子集 |
| `source` | VARCHAR(16) | `reported` / `probed` / `configured` |
| `observed_at` | DATETIME | 采集时间 |
| `is_current` | BOOLEAN | 当前有效 |

### 3.3 `node_channel_plans`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(64) PK | 计划版本 ID |
| `device_id` | VARCHAR(64) FK | 节点 |
| `version` | INTEGER | 单调递增 |
| `channel_key` | VARCHAR(32) FK `channel_catalog.channel_key` | |
| `channel_index` | SMALLINT | 帧内 0 基索引 |
| `lo_khz` | INTEGER | 本振 |
| `offset_hz` | INTEGER | 相对本振偏移（如可推导） |
| `frequency_hz` | BIGINT | 解析后中心频率 |
| `applied_at` | DATETIME | 生效 |
| `superseded_at` | DATETIME | 失效，可空 |
| `applied_by` | VARCHAR(64) | user/agent |
| `source` | VARCHAR(16) | `command` / `report` |

**作用**：解决“历史数据频率无法还原”和“频点映射变更”。测量只存 `plan_version_id + channel_index`，频率通过 join 得到。

### 3.4 `rf_measurements`（核心事实表）

| 字段 | 类型 | 必填 | 说明 | 来源 |
|---|---|---|---|---|
| `id` | BIGINT PK | 是 | 自增 | 新增 |
| `site_id` | VARCHAR(64) | 是 | 站点 | `measurements.site_id` |
| `gateway_id` | VARCHAR(64) | 是 | Agent | `measurements.agent_id` |
| `node_id` | VARCHAR(64) | 是 | RF 节点 | `measurements.device_id` |
| `node_boot_id` | VARCHAR(64) | 否 | 启动周期 | `measurements.device_boot_id` |
| `plan_version_id` | VARCHAR(64) | 否 | 频道计划版本 | 新增 |
| `channel_key` | VARCHAR(32) | 是 | 频道 | 新增 |
| `channel_index` | SMALLINT | 是 | 帧内索引 | 新增（自 payload） |
| `frequency_hz` | BIGINT | 是 | 频率 | 新增（由 plan 解析） |
| `rssi_dbfs` | FLOAT | 是 | 原始电平 | 自 `payload.channels[].rssi` |
| `rssi_dbm` | FLOAT | 否 | 校准后 | 新增；无校准为 NULL |
| `cal_profile_id` | VARCHAR(64) | 否 | 校准档案 | 新增 |
| `sample_timestamp_ms` | BIGINT | 否 | 设备单调毫秒 | `device_timestamp_ms` |
| `sample_timestamp` | DATETIME | 否 | 墙钟采样时间 | 新增（需锚点） |
| `received_timestamp` | DATETIME | 否 | Agent 接收 | `agent_received_at` |
| `site_received_at` | DATETIME | 是 | 站点接收 | `created_at` |
| `sequence` | INTEGER | 否 | 帧序号 | `sequence` |
| `scan_cycle_seq` | INTEGER | 否 | 扫描轮次 | 新增（可选） |
| `sample_quality` | VARCHAR(16) | 是 | 质量枚举 | 新增 |
| `source` | VARCHAR(16) | 是 | `hardware/simulator/replay` | 现有 |
| `raw_measurement_id` | BIGINT | 否 | 回溯原始帧 | 新增 |

`sample_quality` 枚举：
`ok` / `missing` / `stale` / `duplicate_dropped` / `uncalibrated` / `reboot_boundary` / `plan_unknown`。

索引建议：
- `(site_id, node_id, channel_key, sample_timestamp)`
- `(site_id, channel_key, sample_timestamp)`
- `(node_id, node_boot_id, sequence)`（幂等/重启）
- `(plan_version_id)`

> **重要**：`rssi_dbm` **不得**由 `rssi_dbfs` 直接改名或估算填充。dBFS 无绝对参考，跨节点不可比。未校准必须为 NULL 且 `sample_quality='uncalibrated'`。

### 3.5 `calibration_profiles`（可选，V1+）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | VARCHAR(64) PK | |
| `device_id` | VARCHAR(64) FK | |
| `antenna` | VARCHAR(32) | 天线/端口 |
| `band` | VARCHAR(16) | |
| `offset_db` | FLOAT | dBFS→dBm 偏置 |
| `curve_json` | JSON | 频响曲线（可选） |
| `uncertainty_db` | FLOAT | 不确定度 |
| `valid_from` / `valid_to` | DATETIME | |
| `method` | VARCHAR(32) | `factory/signal_generator/field_ref` |
| `approved_by` | VARCHAR(64) | |

### 3.6 聚合表（可选）

| 表 | 粒度 | 用途 |
|---|---|---|
| `rf_measurements_1s` | node × channel × 1 s | 趋势图/热力图默认数据源 |
| `rf_measurements_1m` | node × channel × 1 min | 长窗口与保留策略 |

聚合必须记录 `agg_method`（`avg/min/max/last/count`）、`sample_count`、`expected_count`，且**永不跨节点聚合**。

---

## 4. 现有字段映射

| 需求字段 | 现有位置 | 可用性 | 说明 |
|---|---|---|---|
| `site_id` | `measurements.site_id` | 直接可用 | |
| `gateway_id` | `measurements.agent_id` | 直接可用 | 需文档化别名，避免与 `devices.owner_agent_id` 混淆 |
| `node_id` | `measurements.device_id` | 直接可用 | |
| `node_boot_id` | `measurements.device_boot_id` | 直接可用 | Agent 生成 |
| `channel_id` | `payload_json.channels[].id` | 需解析 | 0 基索引，非稳定键 |
| `frequency_hz` | `payload_json.loKhz` | 不完整 | 缺频道中心偏移与计划版本 |
| `rssi` | `payload_json.channels[].rssi` | 可用但单位为 dBFS | |
| `sample_timestamp` | `device_timestamp_ms` | 部分 | 单调时间，非墙钟 |
| `received_timestamp` | `agent_received_at` | 可用 | |
| `site_received_at` | `created_at` | 可用 | |
| `sequence` | `measurements.sequence` | 可用 | |
| `sample_quality` | 无 | 新增 | |

### 4.1 采样时间缺口

`device_timestamp_ms` 是设备启动后的单调毫秒，不是墙钟。要得到 `sample_timestamp`，需要每个会话的锚点：

```
sample_timestamp ≈ session.boot_wallclock_anchor + device_timestamp_ms
```

- 现有 `connection_sessions` 只有 `connected_at`，没有 `boot_wallclock_anchor`。
- 设计：新增 `boot_wallclock_anchor`（Agent 收到首帧时的墙钟 - `tMs`），并记录 `anchor_uncertainty_ms`。
- 未获得锚点时，`sample_timestamp = NULL`，`sample_quality='stale'` 或专用标记；**不得用 `created_at` 冒充采样时间**。

> 该锚点属于新增字段，**不在本阶段实施**。

---

## 5. 数据质量分析

### 5.1 扫描周期

- `TELEMETRY` 提供 `rotationHz`（整轮每秒次数）与 `hopsTotal`。
- 单频道采样周期：

```
T_channel = channels_per_rotation / rotation_hz   （秒）
```

- 8 频道、60 rotations/s → `T_channel = 8/60 ≈ 133 ms`，单频道 ≈ 7.5 Hz。
- 若 rotationHz 缺失，周期为 `UNKNOWN`，UI 显示“周期未知”，不估算。

### 5.2 采样时间偏差

| 来源 | 偏差性质 |
|---|---|
| 设备 `tMs` | 1 ms 分辨率，随启动漂移 |
| Agent 接收 | 网络/调度延迟，无上界 |
| Site 接收 | Agent→Server 传输延迟 |
| 锚点估算 | 首帧 RTT 的一半，误差可达数十毫秒 |

结论：**三个时间戳分别保留**，不合并；跨节点对齐仅能到“秒级/轮次级”，不能宣称毫秒级同步。

### 5.3 数据缺失

- 期望样本数：

```
expected = window_sec × (rotation_hz / channels_per_rotation) × selected_channels
```

- 实际样本数来自行数；缺口 = expected - actual。
- 缺失单元格必须是 **NULL / 空白**，不得补 0 dBFS 或插值伪线。

### 5.4 重复数据

- 现有 `event_id` 唯一约束已去重。
- `rf_measurements` 以 `(node_id, node_boot_id, sequence, channel_key)` 作为幂等键，重复写入被拒绝或计数，不覆盖。
- 重放（`source=replay`）必须与真实数据区分，不进入默认视图。

### 5.5 数据过期

- 过期阈值建议：`max(3 × T_channel, 5 s)`，可配置。
- 状态：`live` / `stale` / `no_data`。
- 节点离线后保留最后值并显示“最后更新于 …”，不得继续画成实时。

### 5.6 节点重启

- `node_boot_id` 变化即新周期；`sequence` 归零允许。
- 跨重启的查询必须按 `boot_id` 分组，趋势线断开而非直连。
- 现有 `device_boot_id` 已具备该能力。

### 5.7 频点映射变更

- 频道计划变更生成新 `node_channel_plans` 版本，旧测量引用旧 `plan_version_id`。
- 历史查询按旧版本解释频率；UI 在时间轴标记“计划变更”点。
- 无计划版本的历史数据标 `sample_quality='plan_unknown'`。

### 5.8 RSSI 校准与可比性

| 事实 | 影响 |
|---|---|
| 帧内 RSSI 为 dBFS | 不能直接称 dBm |
| `repo/web` 的 dBm 来自前端校准曲线 | 平台不继承该曲线 |
| 不同节点/天线/前端增益不同 | **跨节点绝对值不可直接比较** |
| 未校准时 | `rssi_dbm=NULL`；UI 标注“未校准（dBFS）” |
| 校准后 | 仍保留 `uncertainty_db`；图表可显示误差带或至少在图例标注 |

**结论**：需求中的 `rssi_dbm` 是**目标字段**，不是现有事实。跨节点比较应优先使用**同一时间窗的相对差值**，并显式显示校准状态。

---

## 6. 与现有模型的兼容策略

| 策略 | 说明 |
|---|---|
| 只增不改 | 新表追加；`devices/measurements` 不删列、不改语义 |
| 原始保留 | `measurements` 继续作为原始帧与审计来源 |
| 回填可选 | 历史帧可在离线任务中解析入 `rf_measurements`，但**不默认执行** |
| 迁移可回滚 | 所有新增表均独立，可 `downgrade` 删除而不影响旧数据 |
| 不伪造 | 无法解析的字段留 NULL，不估算 |

---

## 7. 数据模型 GAP 清单

| GAP | 描述 | 影响需求 |
|---|---|---|
| GAP-DM-01 | 无 `channel_catalog` | PRD-RF-002 |
| GAP-DM-02 | 无 `node_capabilities` | PRD-NODE-004 |
| GAP-DM-03 | 无 `node_channel_plans`，历史频率不可还原 | PRD-RF-003/PRD-DQ-004 |
| GAP-DM-04 | 无 `rf_measurements` | PRD-RF-001/003 |
| GAP-DM-05 | 无 `sample_timestamp` 墙钟锚点 | PRD-RF-006 |
| GAP-DM-06 | RSSI 为 dBFS，无校准档案 | PRD-RF-009 |
| GAP-DM-07 | 无 `sample_quality` | PRD-RF-007 |
| GAP-DM-08 | 无聚合表，长期保留不可持续 | PRD-RF-008、容量文档 |
| GAP-DM-09 | 节点接入方式无独立字段 | PRD-NODE-001 |

---

## 8. 审批与依赖

- 新增表需要 Alembic 迁移，**须人工批准**（项目规则：schema 变更需讨论与迁移计划）。
- 依赖 `ADR-08.2.1-01`（站点范围）与 `ADR-08.2.2-07`（Site Scope 服务端强制），二者均为 `PROPOSED`。
- 本阶段**未创建任何表、未编写任何迁移**。
