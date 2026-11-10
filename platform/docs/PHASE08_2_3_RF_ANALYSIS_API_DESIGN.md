# Phase 08.2.3 — RF Analysis 数据接口设计

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.3 — Task E
> 性质：**接口设计，不实现、不注册路由、不修改现有 REST**
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前无可靠版本记录

---

## 1. 通用约定

### 1.1 前缀与版本

- 新接口统一前缀：`/api/v1/rf`
- 现有 `/api/v1/*` **保持不变**。
- 所有接口只读（`GET`）；本阶段不设计写入端点。

### 1.2 响应信封

```json
{
  "data": { },
  "meta": {
    "generated_at": "2026-11-05T04:00:00Z",
    "window": { "from": "...", "to": "..." },
    "aggregation": { "method": "avg", "bucket_sec": 1 },
    "counts": { "returned": 0, "expected": 0, "missing": 0 },
    "quality": { "stale": 0, "uncalibrated": 0, "plan_unknown": 0 }
  }
}
```

错误格式沿用现有：

```json
{
  "error": {
    "code": "RF_CHANNEL_LIMIT_EXCEEDED",
    "message": "最多允许 8 个频点同时对比",
    "details": [{ "field": "channels", "message": "收到 12 个" }]
  }
}
```

### 1.3 通用约束

| 项 | 约束 |
|---|---|
| 最大节点数 | 8 |
| 最大频点数 | 8（超过返回 400，不静默截断） |
| 站点范围 | 必须由服务端强制（Site Scope），不接受客户端跨站点取值 |
| 认证 | 必须已认证；未认证 401，无权限 403（依赖 SP-02/SP-04） |
| 缺失语义 | 统一 `null` + `quality` 标记；**禁止用 0 代替** |
| 时间格式 | ISO-8601 UTC；所有窗口为 `[from, to)` |
| 分页 | 历史接口用 `cursor`（不透明游标），不使用裸 offset 深分页 |
| 缓存 | 只读；允许 ETag；实时窗口不缓存 |

### 1.4 权限矩阵（目标，非现状）

| 接口 | Viewer | RF Analyst | Site Operator | Site Admin |
|---|---|---|---|---|
| 目录/能力 | 读 | 读 | 读 | 读 |
| 频谱/趋势/热力图/历史 | 读 | 读 | 读 | 读 |
| 修改频道计划 | 无 | 无 | 写（SP-15） | 写 |
| 校准档案 | 无 | 只读 | 写 | 写 |

> 现状：Site Server REST 无认证中间件，上表为 `PROPOSED`，不得当作已具备的安全能力。

---

## 2. 频点目录查询

### `GET /api/v1/rf/channels`

**用途**：返回频道目录，供选择器与频率解析。

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `band` | string | 否 | `A/B/E/F/R/L`，逗号分隔 |
| `active_only` | bool | 否 | 默认 `true` |
| `catalog_version` | int | 否 | 缺省用最新 |

**响应**

```json
{
  "data": {
    "catalog_version": 1,
    "channels": [
      { "channel_key": "R1", "band": "R", "frequency_hz": 5658000000,
        "channel_width_hz": 20000000, "label": "R1", "region": "global" },
      { "channel_key": "F1", "band": "F", "frequency_hz": 5740000000,
        "channel_width_hz": 20000000, "label": "F1", "region": "global" }
    ]
  },
  "meta": { "counts": { "returned": 2 } }
}
```

- 分页：目录规模 ≤ 100，不分页。
- 缺失语义：目录缺失返回空数组 + `catalog_version=null`，不返回伪造条目。
- 性能：p95 < 50 ms（内存/缓存）。
- 权限：任何已认证角色可读。

---

## 3. 节点能力查询

### `GET /api/v1/rf/nodes?site_id=&access_mode=&status=`
### `GET /api/v1/rf/nodes/{node_id}/capabilities`

**列表参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `site_id` | string | 否 | 缺省取当前站点 |
| `access_mode` | string | 否 | `usb_serial/wifi_lan/ap_direct` |
| `status` | string | 否 | `online/offline/stale/pending` |
| `page` / `limit` | int | 否 | 列表分页，limit ≤ 100 |

**能力响应**

```json
{
  "data": {
    "node_id": "c5-a1b2c3",
    "node_boot_id": "a1b2c3d4",
    "access_mode": "wifi_lan",
    "capabilities": {
      "min_frequency_hz": 5640000000,
      "max_frequency_hz": 5940000000,
      "max_concurrent_channels": 8,
      "supports_spectrum": true,
      "spectrum_bins": 256,
      "spectrum_span_hz": 40000000,
      "scan_rotation_hz": 60.0,
      "channel_plan_version_id": "pl-001",
      "access_modes": ["usb_serial", "wifi_lan"]
    },
    "observed_at": "2026-11-05T03:59:50Z",
    "source": "reported",
    "quality": { "confidence": "high", "stale": false }
  }
}
```

- `capabilities` 缺失时返回 `null` + `quality.confidence="unknown"`，不按硬件手册填充。
- 性能：p95 < 100 ms。
- 权限：读。

---

## 4. 多节点频谱数据查询

### `GET /api/v1/rf/spectrum`

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `node_ids` | string | 是 | 逗号分隔，≤ 8 |
| `mode` | string | 是 | `bins`（频谱箱）或 `channels`（频道级） |
| `channels` | string | 条件 | `mode=channels` 时必填，≤ 8 |
| `from` / `to` | datetime | 是 | 窗口，跨度 ≤ 5 min（实时模式） |
| `bucket_sec` | int | 否 | 聚合桶，默认 1 s |
| `agg` | string | 否 | `avg/min/max/last`，默认 `avg` |
| `include_quality` | bool | 否 | 是否返回质量明细 |

**响应（bins 模式）**

```json
{
  "data": {
    "series": [
      {
        "node_id": "c5-a1b2c3",
        "node_label": "Node-01",
        "channel_key": "R1",
        "lo_hz": 5658000000,
        "bin_hz": 156250,
        "start_offset_hz": -20000000,
        "points": [
          { "frequency_hz": 5638000000, "rssi_dbfs": -71.2, "rssi_dbm": null,
            "sample_timestamp": "2026-11-05T03:59:59.900Z", "quality": "uncalibrated" }
        ]
      }
    ]
  },
  "meta": {
    "aggregation": { "method": "last", "bucket_sec": 1 },
    "counts": { "returned": 2048, "expected": 2048, "missing": 0 }
  }
}
```

**响应（channels 模式）**

```json
{
  "data": {
    "series": [
      { "node_id": "c5-a1b2c3", "channel_key": "R1",
        "points": [{ "sample_timestamp": "...", "rssi_dbfs": -71.2,
                     "rssi_dbm": null, "quality": "uncalibrated" }] }
    ]
  }
}
```

- **聚合**：bins 模式按时间桶取 `last`（频谱为瞬时快照，平均会抹平窄带干扰）；channels 模式可 `avg`。
- **缺失**：某节点某桶无样本 → 该点 `null` 且不进入连线；`meta.counts.missing` 计数。
- **过期**：`quality="stale"`，仍返回最后值但带 `last_sample_at`。
- **序列数上限**：`len(node_ids) × (bins 模式 1 条/节点；channels 模式 len(channels))` ≤ 64。
- **性能目标**：8 节点 × 256 bins × 5 min @1 s 桶，p95 < 300 ms（**待实测**）。
- **权限**：读。

> 注：`bin_hz` / `start_offset_hz` 由帧 `SPECTRUM` 头推导；`frequency_hz` 由 `lo_hz + offset` 计算，**不是固件直接给出**。

---

## 5. RSSI 趋势数据查询

### `GET /api/v1/rf/trend`

**两种模式**

| 模式 | 参数 | 语义 |
|---|---|---|
| 同频点多节点 | `mode=channel_by_nodes`，`channels`（≤8），`node_ids`（≤8） | 每个“节点×频道”一条线 |
| 单节点多频点 | `mode=node_by_channels`，`node_ids` 必须 1 个，`channels` ≤ 8 | 每个频道一条线 |

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | 上述二选一 |
| `node_ids` | string | 是 | |
| `channels` | string | 是 | ≤ 8 |
| `from` / `to` | datetime | 是 | 跨度 ≤ 24 h |
| `bucket_sec` | int | 否 | 1/5/10/60/300，默认自适应 |
| `agg` | string | 否 | `avg/min/max`，默认 `avg` |
| `max_points` | int | 否 | 默认 2000，≤ 5000 |

**响应**

```json
{
  "data": {
    "series": [
      { "series_key": "c5-a1b2c3|R1", "node_id": "c5-a1b2c3",
        "channel_key": "R1", "label": "Node-01 · R1",
        "points": [{ "t": "2026-11-05T03:00:00Z", "rssi_dbfs": -70.5,
                     "rssi_dbm": null, "n": 7, "quality": "uncalibrated" }] }
    ]
  },
  "meta": {
    "aggregation": { "method": "avg", "bucket_sec": 5 },
    "counts": { "returned": 0, "expected": 0, "missing": 0, "gap_ranges": [] }
  }
}
```

- **降采样**：`bucket_sec` 自适应；`max_points` 超限时按桶合并，并在 `meta` 声明 `downsampled=true`。
- **缺失语义**：空桶返回 `null` 点并进 `gap_ranges`；不插值、不画 0。
- **重启**：跨 `node_boot_id` 的线在重启点断开（`break_reason="reboot"`）。
- **性能**：8 节点 × 8 频点 × 24 h @60 s 桶 = 64 线 × 1440 点 = 92,160 点，p95 < 400 ms（**待实测**）。
- **权限**：读。

---

## 6. 节点 × 频点热力图查询

### `GET /api/v1/rf/heatmap`

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `node_ids` | string | 是 | ≤ 8 |
| `channels` | string | 是 | ≤ 8 |
| `from` / `to` | datetime | 是 | 跨度 ≤ 7 d |
| `bucket_sec` | int | 否 | 默认 60；常用 60/300/3600 |
| `stat` | string | 否 | `avg/min/max/p95`，默认 `avg` |
| `calibrated_only` | bool | 否 | 默认 `false`；true 时未校准单元格为空 |

**响应**

```json
{
  "data": {
    "rows": [
      {
        "node_id": "c5-a1b2c3",
        "node_label": "Node-01",
        "cells": [
          { "channel_key": "R1", "frequency_hz": 5658000000,
            "value_dbfs": -71.2, "value_dbm": null,
            "sample_count": 300, "expected_count": 360,
            "quality": "uncalibrated", "last_sample_at": "..." },
          { "channel_key": "R2", "frequency_hz": 5698000000,
            "value_dbfs": null, "value_dbm": null,
            "sample_count": 0, "expected_count": 360,
            "quality": "missing" }
        ]
      }
    ]
  },
  "meta": {
    "aggregation": { "method": "avg", "bucket_sec": 60 },
    "counts": { "returned": 0, "expected": 0, "missing": 0 },
    "legend": { "min_dbfs": -90, "max_dbfs": -30 }
  }
}
```

- **单元格**：每个 node × channel 一个值；**绝不跨节点聚合**。
- **缺失**：`value=null`，`quality="missing"`；UI 用空白/斜纹，不填 0。
- **数据量**：8 × 8 = 64 单元格/时间桶；若渲染多桶则以 `bucket_sec` 控制。
- **性能**：64 单元格 × 7 d @3600 s（168 桶）= 10,752 单元格，p95 < 500 ms（**待实测**）。
- **权限**：读。

---

## 7. 历史数据查询

### `GET /api/v1/rf/history`

**请求参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `node_ids` | string | 否 | 缺省=当前站点全部（≤8） |
| `channels` | string | 否 | ≤ 8 |
| `from` / `to` | datetime | 是 | 跨度 ≤ 30 d（原始）/ ≤ 365 d（聚合） |
| `resolution` | string | 否 | `raw/1s/1m/1h`，默认 `1m` |
| `include_missing` | bool | 否 | 默认 `false` |
| `cursor` | string | 否 | 分页游标 |
| `limit` | int | 否 | 默认 5000，≤ 20000 |

**响应**

```json
{
  "data": {
    "items": [
      { "node_id": "...", "channel_key": "R1", "frequency_hz": 5658000000,
        "rssi_dbfs": -71.2, "rssi_dbm": null,
        "sample_timestamp": "...", "received_timestamp": "...",
        "sequence": 1024, "quality": "uncalibrated" }
    ]
  },
  "meta": {
    "next_cursor": "opaque-token",
    "has_more": true,
    "window": { "from": "...", "to": "..." },
    "retention": { "raw_days": 30, "rollup_days": 365 }
  }
}
```

- **分页**：游标基于 `(sample_timestamp, id)`，禁止深 offset。
- **聚合**：`resolution=raw` 返回原始行；其余读聚合表；必须返回 `sample_count/expected_count`。
- **缺失**：默认不返回缺失行；`include_missing=true` 返回 `quality="missing"` 的占位行，便于统计。
- **权限**：读；如果未来引入按站点成员限制，需 SP-03/SP-04。
- **性能**：`limit=5000`、`resolution=1m` 时 p95 < 300 ms（**待实测**）。
- **保留**：原始 30 d / 聚合 365 d 为 `PROPOSED`，需人工批准（磁盘容量待测）。

---

## 8. 接口与现有实现的差距

| 接口 | 现有最近能力 | GAP |
|---|---|---|
| `/rf/channels` | 无 | 目录表缺失 |
| `/rf/nodes/{id}/capabilities` | `GET /api/v1/devices/{id}` 有 `capabilities JSON` | 无能力结构化字段、无频点上限、无接入方式 |
| `/rf/spectrum` | `GET /api/v1/devices/{id}/measurements` 按帧分页 | 无多节点、无频率解析、无时间窗口、无聚合 |
| `/rf/trend` | 无 | 完全缺失 |
| `/rf/heatmap` | 无 | 完全缺失 |
| `/rf/history` | 上述 measurements 接口 | 无 channel/时间过滤、无游标、无质量字段 |

### 8.1 与现有 `measurements` 接口的关系

- 现有接口**不改**；`/rf/*` 是新增只读投影。
- 短期可先由 `measurements` 在查询时解析 `payload_json`，但**不推荐**作为长期方案：无法按频道索引、无法聚合、频率不可还原。
- 正式方案依赖 `rf_measurements` 表（见数据模型文档）。

---

## 9. 安全与性能要求

| 项 | 要求 |
|---|---|
| 认证 | 全部 401/403 语义，依赖 SP-02 |
| 站点隔离 | 服务端强制，忽略客户端越权 `site_id` |
| 限流 | 每客户端 20 req/s，聚合接口 5 req/s（建议值，待压测） |
| 响应大小 | 单响应 ≤ 5 MB；超限返回 413 并提示缩小窗口 |
| 缓存 | 历史/聚合允许 5–60 s；实时不允许 |
| 可观测 | 记录查询耗时、扫描行数、截断原因 |
| 错误码 | `RF_CHANNEL_LIMIT_EXCEEDED`、`RF_NODE_LIMIT_EXCEEDED`、`RF_WINDOW_TOO_LARGE`、`RF_TIME_RANGE_INVALID`、`RF_NOT_CALIBRATED`（仅提示，不阻断） |

---

## 10. 明确声明

- 以上接口**均未实现**，当前 `site-server` 不含 `/api/v1/rf/*` 路由。
- 本阶段未修改任何现有 REST 定义。
- 所有性能数字为**设计目标**，不是实测基准；无真实数据时标 `待实测`。
