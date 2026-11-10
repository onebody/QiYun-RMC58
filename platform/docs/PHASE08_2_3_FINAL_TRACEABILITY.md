# Phase 08.2.3 — 最终追踪矩阵（需求 → 架构 → ADR → 数据模型 → API → UI → 测试 → 验收）

> 项目：QiYun / widscan58
> 文档版本：1.0.1
> 生成日期：2026-11-05
> **Version**: 1.0.1
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前仅记为 v1.0，无版本历史记录；1.0.1 (2026-11-05) 追加 §8 R1 对账注记
> 文档状态：**PROPOSED（待人工审批）**
> 前置：十份 08.2.3 交付物、08.2.1 ADR、08.2.2 安全 ADR、SP 矩阵

---

## 1. 图例

| 标记 | 含义 |
|---|---|
| `EXISTING` | 代码已具备 |
| `PARTIAL` | 部分具备 |
| `GAP` | 设计存在、实现不存在 |
| `PROPOSED` | 新设计/决策待批 |
| `NOT VERIFIED` | 无证据 |

引用：

- `ARCH` = `PHASE08_2_3_NODE_ACCESS_ARCHITECTURE.md`
- `DM` = `PHASE08_2_3_RF_DATA_MODEL.md`
- `API` = `PHASE08_2_3_RF_ANALYSIS_API_DESIGN.md`
- `IA` = `PHASE08_2_3_RF_WORKBENCH_IA.md`
- `AT` = `PHASE08_2_3_ACCEPTANCE_TEST_PLAN.md`
- `CHG` = `PHASE08_2_3_ARCHITECTURE_CHANGE_IMPACT.md`
- `DR` = `PHASE08_2_3_DECISION_REGISTER.md`

---

## 2. 主追踪表

| 需求 | 架构 | ADR / 决策 | 数据模型 | API | UI | 测试 | 验收门禁 | 状态 |
|---|---|---|---|---|---|---|---|---|
| PRD-NODE-001 | ARCH §3.2.4 | ADR-08.2.1-01 | `devices` + `node_capabilities` | API §3 | IA §3 | AT-LAN-01, AT-MULTI-01 | G1/G2 | `PARTIAL` |
| PRD-NODE-002 | ARCH §3.2.3 | ADR-08.2.1-01 | `devices.id`、`identity_confidence` | API §3 | IA §3.2 | AT-LAN-01, AT-REL-02 | G1 | `PARTIAL` |
| PRD-NODE-003 | ARCH §3.2.4/3.2.5 | ADR-08.2.1-03 | `device_endpoints`、`connection_sessions` | 无写接口（设计） | IA §3.3 | AT-LAN-06 | G2 | `GAP` |
| PRD-NODE-004 | ARCH §3.2.4 | D-08.2.3-01 | `node_capabilities` | API §3 | IA §3.2 | AT-MULTI-01 | G4 | `GAP` |
| PRD-NODE-005 | ARCH §3.2.4 | ADR-08.2.1-02 | `device_boot_id` | API §3/§7 | IA §5.3 | AT-REL-02 | G3 | `EXISTING` |
| PRD-NET-001 | ARCH §3.2.1 | D-08.2.3-02 | `node_capabilities.access_modes` | API §3 | IA §3 | AT-LAN-01 | G1 | `PARTIAL` |
| PRD-NET-002 | ARCH §3.2.2 | D-08.2.3-02 | 无 | 无 | IA §3 | AT-LAN-03 | G4 | `GAP` |
| PRD-NET-003 | ARCH §3.2.2 | D-08.2.3-02 | 新增登记字段 | 设计：注册端点 | IA §3 | AT-LAN-02 | G1 | `GAP` |
| PRD-NET-004 | ARCH §3.2.4 | ADR-08.2.1-03 | `connection_sessions` | API §3 | IA §3 | AT-LAN-06 | G2 | `PARTIAL` |
| PRD-NET-005 | ARCH §3.2.6 | ADR-08.2.2-04/09/10 | 凭证（新增） | 头部/首条消息 | IA §8 | AT-LAN-04 | G2 | `GAP` |
| PRD-NET-006 | ARCH §3.2.5 | ADR-08.2.1-02 | `last_seen_at`、`reconnect_count` | API §3 | IA §3/§8 | AT-LAN-06 | G3 | `PARTIAL` |
| PRD-NET-007 | ARCH §3.6 | D-08.2.3-12 | `device_endpoints.transport_type` | 无 | IA §3.2 | AT-LAN-05 | G4 | `NOT VERIFIED` |
| PRD-NET-008 | ARCH §3.3.2 | D-08.2.3-03 | 无 | 固件本地（未定义） | IA §3 | AT-AP-01 | G4 | `GAP` |
| PRD-NET-009 | ARCH §3.3.3 | D-08.2.3-03 | 无 | 固件本地（未定义） | IA §3 | AT-AP-02/03 | G4 | `GAP` |
| PRD-NET-010 | ARCH §3.3.3 | D-08.2.3-03 | 无 | 固件本地（未定义） | IA §3 | AT-AP-04 | G4 | `GAP` |
| PRD-NET-011 | ARCH §3.4 | D-08.2.3-04 | 无 | 无 | IA §4 | AT-MULTI-03/04 | G8 | `GAP` |
| PRD-NET-012 | ARCH §3.3.3 | ADR-08.2.2-10 | 无 | 本地认证（设计） | IA §8 | AT-AP-05 | G2 | `GAP` |
| PRD-RF-001 | ARCH §3.4 | ADR-08.2.3-01 | `rf_measurements.node_id` | API §7 | IA §4/5/6 | AT-RF-04 | G5 | `PARTIAL` |
| PRD-RF-002 | ARCH §2.2 Q5 | D-08.2.3-01 | `channel_catalog` | API §2 | IA §4.2/§6 | AT-RF-01/02 | G4 | `GAP` |
| PRD-RF-003 | ARCH §3.4 | ADR-08.2.3-04 | `rf_measurements` | API §4/5/6 | IA §4/5/6 | AT-RF-05 | G5 | `GAP` |
| PRD-RF-004 | ARCH §3.4.3 | ADR-08.2.3-04 | 行级 node_id | API §1.3/§6 | IA §6.2 | AT-RF-05, AT-UI-04 | G8 | `PROPOSED` |
| PRD-RF-005 | ARCH §3.4 | D-08.2.3-08 | 无 | API §1.3 | IA §4.2/5.2 | AT-UI-06 | G7 | `PROPOSED` |
| PRD-RF-006 | ARCH §2.2 Q8/Q9 | ADR-08.2.3-01 | DM §4.1/§5.1 | API §5/§7 | IA §5.4 | AT-RF-08, AT-TIME-01 | G5 | `PARTIAL` |
| PRD-RF-007 | ARCH §3.2.4 | ADR-08.2.3-05 | `sample_quality` | API `quality` | IA §8 | AT-RF-07, AT-UI-08 | G5 | `GAP` |
| PRD-RF-008 | ARCH §3.4 | ADR-08.2.3-04 | 聚合表 | API §7 | IA §5/6 | AT-UI-07 | G5 | `GAP` |
| PRD-RF-009 | ARCH §2.2 Q7 | ADR-08.2.3-05 | `calibration_profiles` | API `rssi_dbm` | IA §4/§7 | AT-RF-07 | G5 | `GAP` |
| PRD-UI-001 | ARCH §2.2 Q5/Q6 | ADR-08.2.3-06 | `rf_measurements`/bins | API §4 | IA §4 | AT-UI-01 | G7 | `GAP` |
| PRD-UI-002 | ARCH §3.4 | ADR-08.2.3-06 | `rf_measurements_1s` | API §5 | IA §5 | AT-UI-03/04 | G7 | `GAP` |
| PRD-UI-003 | ARCH §3.4 | ADR-08.2.3-06 | 聚合表 | API §6 | IA §6 | AT-UI-05 | G7 | `GAP` |
| PRD-UI-004 | — | D-08.2.3-08 | — | API §1.3 | IA §4.2/5.2/6.2 | AT-UI-06/07 | G7 | `GAP` |
| PRD-UI-005 | — | ADR-08.2.3-05/06 | `sample_quality` | API `quality` | IA §7/§8 | AT-RF-07, AT-UI-08 | G7 | `GAP` |
| PRD-UI-006 | — | ADR-08.2.1-02 | `last_seen_at` | API `meta` | IA §8 | AT-UI-02 | G3 | `PARTIAL` |
| PRD-UI-007 | — | ADR-08.2.1-01 | `node_capabilities` | API §3 | IA §3 | AT-LAN-01 | G7 | `GAP` |
| PRD-UI-008 | — | ADR-08.2.3-06 | — | 现有 | IA §1.1 | AT-UI-11 | — | `EXISTING` |
| PRD-DQ-001 | ARCH §3.2.5 | ADR-08.2.1-02 | DM §5.5 | API `stale` | IA §8 | AT-UI-02 | G3 | `PARTIAL` |
| PRD-DQ-002 | ARCH §3.4.3 | — | `event_id` 唯一 | 摄入层 | — | AT-DQ-01 | — | `EXISTING` |
| PRD-DQ-003 | ARCH §3.2.4 | — | `device_boot_id` | API §7 | IA §5.3 | AT-REL-02 | — | `EXISTING` |
| PRD-DQ-004 | ARCH §3.4 | ADR-08.2.3-04 | `node_channel_plans` | API §7 | IA §5.3 | AT-DQ-03 | G5 | `GAP` |
| PRD-DQ-005 | ARCH §3.4 | — | DM §5.3 | API `meta.counts` | IA §5.4 | AT-DQ-02 | G5 | `GAP` |

---

## 3. 用户需求 R1–R3 顶层映射

| 用户需求 | 需求编号 | 架构 | ADR/决策 | 数据模型 | API | UI | 验收 | 状态 |
|---|---|---|---|---|---|---|---|---|
| R1-A Wi-Fi LAN | NET-001–007 | ARCH §3.2 | D-02, D-12 | DM §3.1/3.2 | API §3 | IA §3 | AT-LAN-01–06 | `PARTIAL`/`GAP` |
| R1-B AP Direct | NET-008–010/012 | ARCH §3.3 | D-03 | DM §3.2 | 本地（未定义） | IA §3 | AT-AP-01–06 | `GAP` |
| R1-C 多 AP | NET-011 | ARCH §3.4 | D-04 | — | — | IA §4 | AT-MULTI-03/04 | `GAP` |
| R2 多节点多频点 | RF-001–009 | ARCH §3.4 | D-01, D-05, D-06 | DM §3/§5 | API §4–7 | IA §4–6 | AT-RF-01–08 | `GAP`（幂等除外） |
| R3 三视图 | UI-001–003 | IA §4–6 | D-08 | DM §3.4/3.6 | API §4–6 | IA §4–6 | AT-UI-01/03/05 | `GAP` |

---

## 4. ADR → 需求反向追踪

| ADR/决策 | 影响需求 | 说明 |
|---|---|---|
| ADR-08.2.1-01 | NODE-001/002, NET-001–004, UI-007 | 站点归属与节点归属 |
| ADR-08.2.1-02 | RF-006, UI-006, DQ-001 | 事件流与刷新 |
| ADR-08.2.1-03 | NODE-003, NET-004, UI-008 | 权限与只读边界 |
| ADR-08.2.2-04/09 | NET-005/012 | 凭证与双平面 |
| ADR-08.2.2-06/07 | 全部 `/rf/*` | RBAC 与 Site Scope |
| ADR-08.2.2-10 | NET-012, 未来写操作 | 命令授权链 |
| ADR-08.2.2-11 | 未来 WS | 票据 |
| ADR-08.2.2-12 | `/rf/*` 限流 | 审计与限流 |
| D-08.2.3-01 | RF-002, NODE-004 | 40+ 语义 |
| D-08.2.3-03 | NET-008–010 | AP Direct 定位 |
| D-08.2.3-04 | NET-011 | 多 AP 限制 |
| D-08.2.3-05 | RF-003, DQ-004 | 事实表 |
| D-08.2.3-06 | RF-009, UI-005 | dBFS/dBm |
| D-08.2.3-08 | RF-005, UI-004 | 8 频点上限 |
| D-08.2.3-09 | RF-008, DQ-005 | 聚合保留 |

---

## 5. 无法建立完整关系的条目（GAP）

| GAP | 需求 | 缺失环节 | 影响 | 处理 |
|---|---|---|---|---|
| GAP-TR-01 | PRD-NET-002 | 无固件发现广播 → 无架构可实现路径 | 自动发现无法验收 | 依赖 SP-RF-01 |
| GAP-TR-02 | PRD-NET-008/009/010 | 无固件 SoftAP/服务端 → 无平台 API | AP Direct 全链路不可测 | 独立固件立项 |
| GAP-TR-03 | PRD-RF-002 | 固件 8 频点上限 → 无 40+ 并发路径 | 产品目标无法达成 | A3 + A8 |
| GAP-TR-04 | PRD-RF-009 | 无校准档案/流程 | 无法输出可信 dBm | SP-RF-08 |
| GAP-TR-05 | PRD-RF-008 | 无聚合表与保留策略 | 历史查询不可持续 | A7 |
| GAP-TR-06 | PRD-UI-001/002/003 | 无 `/rf/*` 接口 | 页面无法取数 | SP-RF-06 |
| GAP-TR-07 | PRD-UI-007 | 无节点接入管理写接口 | 只能只读 | A1/A2 |
| GAP-TR-08 | PRD-DQ-005 | 无缺口统计实现 | 只能显示原始计数 | SP-RF-05 |
| GAP-TR-09 | A10/后续路线 | 阶段编号冲突 C-01 | 路线不可执行 | 人工裁定 |
| GAP-TR-10 | 全部验收 | 0 条用例执行 | 无法判定功能 | A 系列批准后执行 |

---

## 6. SP → 需求映射（建议切片）

| 任务 | 覆盖需求 | 依赖 | 状态 |
|---|---|---|---|
| SP-RF-01 固件能力扩展 | NET-002/008/009/010, RF-002 | A8 | PROPOSED |
| SP-RF-02 LAN 发现与登记 | NET-001/002/003 | SP-10 | PROPOSED |
| SP-RF-03 节点接入认证 | NET-004/005/012 | SP-00/10/16 | PROPOSED |
| SP-RF-04 RF 数据模型迁移 | RF-001/003/006, DQ-004 | A6 | PROPOSED |
| SP-RF-05 聚合与保留 | RF-007/008, DQ-005 | A7 | PROPOSED |
| SP-RF-06 RF 只读接口 | RF-001–009, UI-001–007 | SP-02/03/04 | PROPOSED |
| SP-RF-07 RF Workbench | UI-001–007 | SP-RF-06 | PROPOSED |
| SP-RF-08 校准与质量 | RF-009, DQ-001/005 | SP-RF-04 | PROPOSED |
| SP-RF-09 容量与多节点验收 | 全部 P0 | SP-RF-05/06/07 | PROPOSED |

---

## 7. 完整性声明

- PRD 全部编号（NODE-001–005、NET-001–012、RF-001–009、UI-001–008、DQ-001–005）均在本矩阵出现，共 **39 条**。
- 每条均有架构/数据/接口/页面/测试/验收的映射，或明确标为 GAP。
- 标 `EXISTING` 的 4 条（NODE-005、UI-008、DQ-002、DQ-003）有代码证据。
- 标 `GAP` 的条目**不代表已实现**。
- 本矩阵不改变任何 ADR/SP/审批状态。

---

## 8. R1 对账注记（2026-11-05，Version 1.0.1）

> 本节只追加，不修改 §1–§7 的任何映射与结论。

1. **阶段编号歧义影响 Gate 字段**：本文件“Gate”列的 `08.3` 指 RF 新路线下的 UI 阶段，而非历史平台 UI 的 `PHASE08_3_UI_DESIGN_SYSTEM.md`。两者同名不同义，C-01 未裁定前不得互相引用作为完成证据。
2. **历史文档不得作为 RF 需求的实现证据**：08.4 平台 UI 代码存在，但不实现任何 `RF-001–009` 接口；08.5 的 CONDITIONAL PASS 不覆盖 RF 数据。
3. **编号与状态以统一索引为准**：`docs/PHASE_STATUS_INDEX.md` 登记真实状态；本矩阵只做需求映射。
4. **39 条需求映射数量不变**；C-02（版本元数据）与 C-03（证据目录）不改变任何 GAP 判定。
5. 未启动 Phase 08.3、08.2.4 审计执行或 SP-00–SP-17；未修改任何 ADR 审批状态。
