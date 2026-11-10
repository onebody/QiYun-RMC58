# Phase 08.2.3 — 需求追踪矩阵

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.3 — 交付文档 ⑨
> 性质：**追踪矩阵，不修改代码**
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前无可靠版本记录

---

## 1. 使用说明

追踪链：

```
需求编号 → 架构设计 → 数据模型 → 接口 → 页面 → 验收用例
```

状态列含义：

| 状态 | 含义 |
|---|---|
| `EXISTING` | 现有代码已具备 |
| `PARTIAL` | 部分具备，需补齐 |
| `GAP` | 设计存在、代码不存在 |
| `PROPOSED` | 新设计，待审批 |

引用简写：

- `PRD` = `PHASE08_2_3_RF_PRODUCT_REQUIREMENTS.md`
- `ARCH` = `PHASE08_2_3_NODE_ACCESS_ARCHITECTURE.md`
- `DM` = `PHASE08_2_3_RF_DATA_MODEL.md`
- `API` = `PHASE08_2_3_RF_ANALYSIS_API_DESIGN.md`
- `IA` = `PHASE08_2_3_RF_WORKBENCH_IA.md`
- `AT` = `PHASE08_2_3_ACCEPTANCE_TEST_PLAN.md`

---

## 2. 用户需求 R1–R3 覆盖

| 用户需求 | 子需求编号 | 架构设计 | 数据模型 | 接口 | 页面 | 验收 | 结论 |
|---|---|---|---|---|---|---|---|
| R1-A Wi-Fi LAN | NET-001/002/003/004/005/006/007 | ARCH §3.2 | DM §3.1/3.2 | API §3 | IA §3 | AT-LAN-01–06 | `PARTIAL` + `GAP` |
| R1-B AP Direct | NET-008/009/010/012 | ARCH §3.3 | DM §3.2 | API（本地，未定义平台接口） | IA §3 | AT-AP-01–06 | `GAP` |
| R1-C 多节点 AP 汇聚 | NET-011 | ARCH §3.4 | DM §3.2 | API §3 | IA §4 | AT-MULTI-03/04 | `GAP` |
| R2 多节点多频点 | RF-001–009 | ARCH §3.4 | DM §3/§5 | API §4/5/6/7 | IA §4/5/6 | AT-RF-01–08 | `GAP`（除幂等） |
| R3 三种分析视图 | UI-001/002/003 | IA §4/5/6 | DM §3.4 | API §4/5/6 | IA §4/5/6 | AT-UI-01/03/05 | `GAP` |

---

## 3. 节点管理

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| NODE-001 节点列表 | ARCH §3.2.4 | `devices` + `node_capabilities`（DM §3.2） | `GET /rf/nodes`（API §3） | Node Management（IA §3） | AT-LAN-01, AT-MULTI-01 | `PARTIAL` |
| NODE-002 稳定身份 | ARCH §3.2.3 | `devices.id`、`identity_confidence` | `GET /rf/nodes/{id}/capabilities` | 详情抽屉 | AT-LAN-01, AT-REL-02 | `PARTIAL` |
| NODE-003 连接管理 | ARCH §3.2.4/3.2.5 | `device_endpoints`、`connection_sessions` | 无写接口（设计） | Node Management 操作区 | AT-LAN-06 | `PARTIAL`/`GAP` |
| NODE-004 能力查询 | ARCH §3.2.4 | `node_capabilities`（DM §3.2） | `GET /rf/nodes/{id}/capabilities` | 详情抽屉 | AT-MULTI-01 | `GAP` |
| NODE-005 重启识别 | ARCH §3.2.4 | `device_boot_id`（DM §3.4） | `GET /rf/nodes` | 详情 | AT-REL-02 | `EXISTING` |

---

## 4. Wi-Fi LAN

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| NET-001 STA 接入 | ARCH §3.2.1 | `node_capabilities.access_modes` | `GET /rf/nodes` | Node Management | AT-LAN-01 | `PARTIAL` |
| NET-002 自动发现 | ARCH §3.2.2（D3） | 无 | 无（设计缺 mDNS） | Node Management | AT-LAN-03 | `GAP` |
| NET-003 手动 IP | ARCH §3.2.2（D1） | 新增登记字段 | 设计：注册端点 | Node Management | AT-LAN-02 | `GAP` |
| NET-004 状态管理 | ARCH §3.2.4 | `connection_sessions` | `GET /rf/nodes` | Node Management | AT-LAN-06 | `PARTIAL` |
| NET-005 接入认证 | ARCH §3.2.6 | 凭证（新增） | 头部/首条消息 | 不呈现凭证 | AT-LAN-04 | `GAP` |
| NET-006 心跳重连 | ARCH §3.2.5 | `last_seen_at`、`reconnect_count` | `GET /rf/nodes` | 状态徽标 | AT-LAN-06 | `PARTIAL` |
| NET-007 并行共存 | ARCH §3.6 | `device_endpoints.transport_type` | 无 | Node Management 接入方式列 | AT-LAN-05 | `UNKNOWN` |

---

## 5. AP Direct

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| NET-008 SoftAP | ARCH §3.3.2 | 无 | 固件本地（未定义） | Node Management 配置 | AT-AP-01 | `GAP` |
| NET-009 身份/配置/数据 | ARCH §3.3.3 | 无 | 固件本地 `/id`、WS（设计） | 本地工具 | AT-AP-02/03 | `GAP` |
| NET-010 AP 配置管理 | ARCH §3.3.3 | 无 | 固件本地（设计） | 本地工具 | AT-AP-04 | `GAP` |
| NET-011 汇聚限制 | ARCH §3.4 | 无 | 无 | IA 说明 | AT-MULTI-03/04 | `PROPOSED` |
| NET-012 AP 认证 | ARCH §3.3.3 | 无 | 本地认证（设计） | 本地工具 | AT-AP-05 | `GAP` |

---

## 6. RF 测量

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| RF-001 独立测量 | ARCH §3.4 | `rf_measurements.node_id`（DM §3.4） | `GET /rf/history` | 全部 RF 页 | AT-RF-04 | `PARTIAL` |
| RF-002 40+ 目录 | ARCH §3.4 / §2 Q5 | `channel_catalog`（DM §3.1） | `GET /rf/channels` | 选择器（IA §4/6） | AT-RF-01/02 | `GAP` |
| RF-003 Node×Channel×Time | ARCH §3.4 | `rf_measurements`（DM §3.4） | `GET /rf/spectrum|trend|heatmap` | 全部 RF 页 | AT-RF-05 | `GAP` |
| RF-004 不合并 RSSI | ARCH §3.4.3 | 行级 node_id | API §1.3/§6 | 图例/热力图 | AT-RF-05, AT-UI-04 | `PROPOSED` |
| RF-005 最多 8 频点 | ARCH §3.4 | 无 | API §1.3 | 选择器（IA §4.2/5.2） | AT-UI-06 | `PROPOSED` |
| RF-006 采样时间/周期 | ARCH §2.2 Q8/Q9 | DM §4.1/§5.1 | API §5/§7 | tooltip/状态条 | AT-RF-08, AT-TIME-01 | `PARTIAL` |
| RF-007 数据质量 | ARCH §3.2.4 | `sample_quality`（DM §3.4） | API `quality` | IA §8 | AT-RF-07, AT-UI-08 | `GAP` |
| RF-008 历史查询 | ARCH §3.4 | 聚合表（DM §3.6） | `GET /rf/history` | IA §5/6 | AT-UI-07 | `GAP` |
| RF-009 RSSI 校准 | ARCH §2.2 Q7 | DM §3.5/§5.8 | API `rssi_dbm` | Y 轴单位/徽标 | AT-RF-07 | `GAP` |

---

## 7. 分析视图

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| UI-001 频谱图 | ARCH §2.2 Q5/Q6 | `rf_measurements` / spectrum bins | `GET /rf/spectrum` | IA §4 | AT-UI-01 | `GAP` |
| UI-002 趋势图 | ARCH §3.4 | `rf_measurements_1s`（DM §3.6） | `GET /rf/trend` | IA §5 | AT-UI-03/04 | `GAP` |
| UI-003 热力图 | ARCH §3.4 | 聚合表 | `GET /rf/heatmap` | IA §6 | AT-UI-05 | `GAP` |
| UI-004 选择器/时间 | — | — | API §1.3 | IA §4.2/5.2/6.2 | AT-UI-06/07 | `GAP` |
| UI-005 颜色/图例/缺失 | — | `sample_quality` | API `quality` | IA §7/§8 | AT-RF-07, AT-UI-08 | `GAP` |
| UI-006 刷新/错误状态 | — | `last_seen_at` | API `meta` | IA §8 | AT-UI-02 | `PARTIAL` |
| UI-007 Node Management | — | `node_capabilities` | API §3 | IA §3 | AT-LAN-01 | `GAP` |
| UI-008 保留原页面/Console | — | — | 现有 | IA §1.1 | AT-UI-11 | `EXISTING` |

---

## 8. 数据质量

| 需求 | 架构 | 数据模型 | 接口 | 页面 | 验收 | 状态 |
|---|---|---|---|---|---|---|
| DQ-001 过期保留 | ARCH §3.2.5 | DM §5.5 | API `quality=stale` | IA §8 | AT-UI-02 | `PARTIAL` |
| DQ-002 重复幂等 | ARCH §3.4.3 | `event_id` 唯一（DM §5.4） | 无（摄入层） | — | AT-DQ-01 | `EXISTING` |
| DQ-003 重启隔离 | ARCH §3.2.4 | `device_boot_id`（DM §5.6） | API §7 | 趋势断点 | AT-REL-02 | `EXISTING` |
| DQ-004 计划变更追溯 | ARCH §3.4 | `node_channel_plans`（DM §3.3） | API §7 | 变更标记 | AT-DQ-03 | `GAP` |
| DQ-005 缺口量化 | ARCH §3.4 | DM §5.3 | API `meta.counts` | 状态条 | AT-DQ-02 | `GAP` |

---

## 9. R1–R3 与验收门禁

| 门禁 | 需求 | 验收用例 | 当前 |
|---|---|---|---|
| G1 LAN 可用 | NET-001/004/006 | AT-LAN-01/06 | `BLOCKED` |
| G2 LAN 可发现 | NET-002/003 | AT-LAN-02/03 | `BLOCKED` |
| G3 AP Direct 可用 | NET-008/009/010 | AT-AP-01–04 | `BLOCKED` |
| G4 多节点 AP 限制明确 | NET-011 | AT-MULTI-03 | `PROPOSED` |
| G5 40+ 目录 | RF-002 | AT-RF-01/02 | `BLOCKED` |
| G6 Node×Channel×Time | RF-003/004 | AT-RF-04/05 | `BLOCKED` |
| G7 三视图 | UI-001/002/003 | AT-UI-01/03/05 | `BLOCKED` |
| G8 数据质量 | RF-007, DQ-001/005 | AT-RF-07, AT-DQ-02 | `BLOCKED` |
| G9 断线恢复 | NET-006, DQ-003 | AT-LAN-06, AT-REL-01/02 | `BLOCKED` |
| G10 长时间稳定 | — | AT-STAB-01–04 | `BLOCKED` |

---

## 10. 矩阵完整性声明

- PRD 中所有编号（NODE-001–005、NET-001–012、RF-001–009、UI-001–008、DQ-001–005）**均已在本矩阵出现**。
- 每个编号均可追溯到架构章节、数据模型表/章节、接口路径、页面章节、验收用例。
- 标 `EXISTING` 的项有代码证据；标 `GAP` 的项**没有实现**，不得在报告中表述为已完成。
