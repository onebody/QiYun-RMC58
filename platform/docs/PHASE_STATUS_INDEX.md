# QiYun / widscan58 — 统一阶段状态索引（Phase Status Index）

> 项目：QiYun / widscan58
> 文档版本：1.0.0
> 创建日期：2026-11-05
> 最后修改：2026-11-05
> 文档状态：**PROPOSED（索引文件，待人工确认）**
> Baseline：NO — 本索引只登记真实状态，不是路由批准文件

> **权威性说明**：本索引是阶段编号与状态的唯一登记处（方案 C）。它**不改变**任何历史文件名、正文或审批状态。若本索引与任何单份文档冲突，以“更保守（更低）的状态”为准，并记录冲突。

---

## 1. 状态定义

| 状态 | 含义 |
|---|---|
| `PROPOSED` | 已提出，未获人工批准 |
| `APPROVED` | 人工书面批准（**本索引当前 0 条**） |
| `REJECTED` | 人工书面拒绝 |
| `REVISION REQUIRED` | 退回修改 |
| `DEFERRED` | 暂缓决定 |
| `NOT VERIFIED` | 无充分证据 |
| `BLOCKED` | 存在阻断性前置条件 |
| `HISTORICAL_DRAFT` | 历史设计稿，无审批、无独立验收 |
| `IMPLEMENTED_NOT_ACCEPTED` | 有代码/截图，但未获无条件验收 |
| `IN_PROGRESS` | 正在进行 |
| `ACCEPTED` | 人工书面无条件验收（**本索引当前 0 条**） |

---

## 2. 阶段总表

| 阶段 | 历史编号/文件 | 真实状态 | 证据 | 备注 |
|---|---|---|---|---|
| Phase 08.1 | `PHASE08_1_FRONTEND_ASSET_AUDIT.md` | `HISTORICAL_DRAFT` | 文件存在（birth 00:49:50） | 平台 UI 资产审计，无独立验收 |
| Phase 08.2 | `PHASE08_2_*.md`（5 份） | `HISTORICAL_DRAFT` / `UNKNOWN`（先后关系） | 5 份文件存在；无审批记录 | 架构设计稿；未证明先于 08.4 实现 |
| Phase 08.2.1 | `PHASE08_2_1_*.md`（5 份，含 3 ADR） | **`PROPOSED`** | `PHASE08_2_1_IMPLEMENTATION_DEPENDENCY_MATRIX.md`；3 份 ADR 首行状态 | 三份 ADR 全部未批准 |
| Phase 08.2.2 | `PHASE08_2_2_*.md`（8 份，含 12 ADR） | **`PROPOSED`** | `PHASE08_2_2_SECURITY_DECISION_RECORD.md` §0 | 12 份 ADR 全部未批准 |
| **Phase 08.2.3（RF 重基线）** | `PHASE08_2_3_*.md`（14 份）+ `PHASE08_2_4_*`（1 份） | **阶段 `CONDITIONAL PASS`；文档 `PROPOSED`** | `PHASE08_2_3_FINAL_CLOSURE_REPORT.md` §8/§10 | 设计交付齐全，A1–A10 未批 |
| Phase 08.2.3-R1 | `PHASE08_2_3_R1_*.md`、本索引、证据目录 | **`PROPOSED`** | 本文件与 R1 两份审计 | 只处理 C-01/02/03 |
| Phase 08.2.4（固件审计） | `PHASE08_2_4_RF_FIRMWARE_AUDIT_CHARTER.md` | **`PROPOSED`（仅立项建议，未启动）** | 章程 §治理声明 | 不得视为审计已执行 |
| Phase 08.3（历史平台 UI 设计） | `PHASE08_3_UI_DESIGN_SYSTEM.md` | `HISTORICAL_DRAFT` | 文件 birth 01:16:19；无验收文档 | 设计规范；非 RF 阶段 |
| Phase 08.4（历史平台 UI 实现） | `PHASE08_4_PLATFORM_UI_INTEGRATION.md` | `IMPLEMENTED_NOT_ACCEPTED` | `site-server/web/`、`test_phase08_platform_ui.py`；`PHASE08_ACCEPTANCE_REPORT.md` | 代码存在；验收仅 CONDITIONAL |
| Phase 08.5（历史真实数据接入） | `PHASE08_5_REAL_DATA_INTEGRATION.md` | `IMPLEMENTED_NOT_ACCEPTED` | 文档自述 CONDITIONAL PASS；`docs/screenshots/phase08/`（SIM） | 模拟设备；运行证据在 `/tmp` 未归档 |
| Phase 08.6（历史验收） | `PHASE08_ACCEPTANCE_REPORT.md` | `IMPLEMENTED_NOT_ACCEPTED` | 报告自述 CONDITIONAL PASS | 无独立 08.6 文档；无硬件 |
| WP-02 | `PHASE08_WP02_1920X1080_REAL_BROWSER_ACCEPTANCE.md` | **`CONDITIONAL PASS`（存在未决缺陷）** | 12 张真实截图 `docs/evidence/WP02/` | D01 未裁定 |
| SP-00 – SP-17 | `PHASE08_2_2_IMPLEMENTATION_DEPENDENCY_MATRIX.md` | **全部 `PROPOSED` / 未启动** | 矩阵 §1、§0 第 7 条 | 见 §5 |
| RF 后续阶段（建议） | 无文件 | **未立项、未启动** | 无 | 标签待人工确认（见 §8） |

**两条阶段线说明：**

- **线 A（历史平台 UI）**：08.1 → 08.2 → 08.2.1 → 08.2.2 → 08.3 → 08.4 → 08.5 → 08.6。
- **线 B（RF 重基线）**：08.2.3 → 08.2.4（建议）。
- 两条线的“08.3”同名不同义。**未人工裁定前，任何跨线引用均视为无效完成证据。**

---

## 3. Phase 08.2.3 文档状态

| 文档 | 版本 | 状态 | Baseline |
|---|---|---|---|
| `PHASE08_2_3_RF_PRODUCT_REQUIREMENTS.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_NODE_ACCESS_ARCHITECTURE.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_RF_DATA_MODEL.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_RF_ANALYSIS_API_DESIGN.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_RF_WORKBENCH_IA.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_CAPACITY_AND_PERFORMANCE.md` | 1.0.0 | `PROPOSED`（内容 `NOT VERIFIED`） | NO |
| `PHASE08_2_3_ARCHITECTURE_CHANGE_IMPACT.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_ACCEPTANCE_TEST_PLAN.md` | 1.0.0 | `PROPOSED`（0 条执行） | NO |
| `PHASE08_2_3_REQUIREMENTS_TRACEABILITY_MATRIX.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_DECISION_REGISTER.md` | 1.0.0 | `PROPOSED` | NO |
| `PHASE08_2_3_FINAL_CLOSURE_REPORT.md` | 1.0.1 | `PROPOSED`（阶段 CONDITIONAL PASS） | NO |
| `PHASE08_2_3_HUMAN_APPROVAL_REGISTER.md` | 1.0.0 | `PROPOSED`（A1–A10 待批） | NO |
| `PHASE08_2_3_PROJECT_FREEZE_AND_GATES.md` | 1.0.1 | `PROPOSED` | NO |
| `PHASE08_2_4_RF_FIRMWARE_AUDIT_CHARTER.md` | 1.0.0 | `PROPOSED`（未启动） | NO |
| `PHASE08_2_3_FINAL_TRACEABILITY.md` | 1.0.1 | `PROPOSED` | NO |

---

## 4. ADR 状态

### 4.1 Phase 08.2.1（3 份）

| ADR | 主题 | 状态 | 文件 |
|---|---|---|---|
| `ADR-08.2.1-01` | 多站点裁定 | **`PROPOSED`** | `PHASE08_2_1_MULTI_SITE_ADR.md` |
| `ADR-08.2.1-02` | 事件流裁定 | **`PROPOSED`** | `PHASE08_2_1_EVENT_STREAM_ADR.md` |
| `ADR-08.2.1-03` | 权限与安全裁定 | **`PROPOSED`** | `PHASE08_2_1_SECURITY_PERMISSION_ADR.md` |

### 4.2 Phase 08.2.2（12 份）

| ADR | 主题 | 状态 |
|---|---|---|
| `ADR-08.2.2-01` | 用户认证载体（Opaque + Cookie / Bearer） | **`PROPOSED`** |
| `ADR-08.2.2-02` | 会话生命周期、CSRF 与 XSS | **`PROPOSED`** |
| `ADR-08.2.2-03` | 不采用 JWT 作为默认方案 | **`PROPOSED`** |
| `ADR-08.2.2-04` | Agent 凭证体系保留并补齐生命周期 | **`PROPOSED`** |
| `ADR-08.2.2-05` | mTLS 仅用于跨互联网多站点 | **`PROPOSED`** |
| `ADR-08.2.2-06` | RBAC 四角色模型 | **`PROPOSED`** |
| `ADR-08.2.2-07` | Site Scope 服务端强制 | **`PROPOSED`** |
| `ADR-08.2.2-08` | Resource Scope 默认关闭、可选启用 | **`PROPOSED`** |
| `ADR-08.2.2-09` | 用户平面与机器平面严格分离 | **`PROPOSED`** |
| `ADR-08.2.2-10` | 命令双平面二次授权链 | **`PROPOSED`** |
| `ADR-08.2.2-11` | 前端 WS 短时票据、URL 不放长期凭证 | **`PROPOSED`** |
| `ADR-08.2.2-12` | 网络隔离不替代应用安全；审计身份化与限流 | **`PROPOSED`** |

**合计 15 份 ADR，`APPROVED = 0`。**

---

## 5. SP-00 – SP-17 状态

> 来源：`PHASE08_2_2_IMPLEMENTATION_DEPENDENCY_MATRIX.md` §1；矩阵明确“本阶段不执行任何任务”。

| ID | 任务 | 状态 | 实施 |
|---|---|---|---|
| SP-00 | 人工审批安全架构 | `PROPOSED` | 未开始 |
| SP-01 | 认证基础设施 | `PROPOSED` | 未开始 |
| SP-02 | REST 全量认证中间件 | `PROPOSED` | 未开始 |
| SP-03 | Site Scope 服务端强制 | `PROPOSED` | 未开始 |
| SP-04 | RBAC 四角色 + 站点成员 | `PROPOSED` | 未开始 |
| SP-05 | Resource Scope（可选） | `PROPOSED` | 未开始 |
| SP-06 | CSRF / Origin / Host / CORS | `PROPOSED` | 未开始 |
| SP-07 | 审计日志身份化 | `PROPOSED` | 未开始 |
| SP-08 | 错误/诊断收敛、关闭 docs | `PROPOSED` | 未开始 |
| SP-09 | 速率限制与请求体上限 | `PROPOSED` | 未开始 |
| SP-10 | Agent 凭证轮换/撤销/读端点认证 | `PROPOSED` | 未开始 |
| SP-11 | Agent 上报设备归属校验 | `PROPOSED` | 未开始 |
| SP-12 | 前端 WS 票据与订阅授权 | `PROPOSED` | 未开始 |
| SP-13 | 多站点后端 | `PROPOSED` | 未开始 |
| SP-14 | 多站点 UI | `PROPOSED` | 未开始 |
| SP-15 | 设备命令控制 | `PROPOSED` | 未开始 |
| SP-16 | 传输安全（wss/mTLS） | `PROPOSED` | 未开始 |
| SP-17 | WP-02 1920×1080 真实验收 | `PROPOSED` | 未开始 |

> 说明：`site-server/.../auth/__init__.py` 中存在 Agent 配对/凭证哈希代码，但早于 SP-10 且不覆盖 SP-10 要求；**不得据此判定 SP-10 已实施**。

---

## 6. WP-02 状态

| 项 | 状态 |
|---|---|
| 阶段判定 | **CONDITIONAL PASS** |
| D01（P2）设备状态陈旧 | **未裁定**（A9 待人工选择 A 修复+复测 / B 书面接受风险） |
| D02（P3）favicon 404 | 已知，未修 |
| D03（P3）`/api/mode` 404 | 已知，未修 |
| 真实证据 | `docs/evidence/WP02/` 12 张 PNG |
| 能否作为 08.3 硬门禁 | **不能**（D01 未决） |

---

## 7. A1–A10 审批状态

| ID | 事项 | 当前状态 | 推荐意见 | 审批结果 |
|---|---|---|---|---|
| A1 | 08.2.1 三份 ADR | `PENDING` | 建议批准 | 待人工 |
| A2 | 08.2.2 十二份安全 ADR | `PENDING` | 建议批准（先补 WS 票据/限流细节） | 待人工 |
| A3 | 40+ 频点语义 | `PENDING` | 批准语义；固件能力 `NOT VERIFIED` | 待人工 |
| A4 | AP Direct 定位 | `PENDING` | 批准（单节点调试/维护/临时） | 待人工 |
| A5 | 多 AP 汇聚评估 | `PENDING` | 批准（Wi-Fi LAN 优先） | 待人工 |
| A6 | 新增表与迁移 | `PENDING` | `REVISION REQUIRED` | 待人工 |
| A7 | 聚合与保留 | `PENDING` | `DEFERRED`（无实测） | 待人工 |
| A8 | 固件立项 | `PENDING` | 建议立项，不启动开发 | 待人工 |
| A9 | WP02-D01 | `PENDING` | 提供 A/B 两案，AI 不选 | 待人工 |
| A10 | 是否启动 Phase 08.3 | `PENDING` | 当前 `NO GO` | 待人工 |

---

## 8. C-01 / C-02 / C-03 处理状态

| ID | 问题 | 状态 | 处理结果 | 仍需人工 |
|---|---|---|---|---|
| C-01 | 阶段编号冲突 | **`BLOCKED`** | 已出历史清单、时间线、分类、方案 A/B/C（推荐 C） | 是：选择 A/B/C 并书面记录 |
| C-02 | 15 份无版本元数据 | **`PASS`** | 已补齐并留存前后 diff/SHA | 否（但文档仍 `PROPOSED`） |
| C-03 | 证据目录缺失 | **`PASS`（结构）/ `NOT VERIFIED`（内容）** | 6 子目录 + README + 2 索引 | 是：补真实证据 |

**RF 后续阶段命名（建议，未批准）：**

- `RF-P1` UI 设计系统（若沿用历史 08.3 则需人工确认）
- `RF-P2` Platform Shell 与节点接入
- `RF-P3` RF 数据接入
- `RF-P4` RF 分析工作台
- `RF-P5` 运行时与多节点验收

> 上述标签只是避免“两套 08.3”歧义的建议，**不是已批准路线**，也不表示任何阶段已启动。

---

## 9. 门禁状态（承接冻结文档）

| 门禁 | 状态 | 依据 |
|---|---|---|
| G0 文档完整性 | PASS | 10/10 + 5 收口 |
| G1 A1 ADR | PENDING | 3 份 ADR `PROPOSED` |
| G2 A2 安全 ADR | PENDING | 12 份 ADR `PROPOSED` |
| G3 D01 裁定 | PENDING | A9 未决 |
| G4 固件可行性 | NOT VERIFIED | 无硬件实测 |
| G5 数据方案 | PENDING | A6/A7 未批 |
| G6 阶段编号 | **BLOCKED** | C-01 |
| G7 08.3 启动 | NO GO | 当前 |
| G8 多节点验收 | NOT VERIFIED | 无真实硬件 |

---

## 10. 变更记录

| 日期 | 版本 | 变更 | 作者 |
|---|---|---|---|
| 2026-11-05 | 1.0.0 | 初版：登记两条阶段线、15 份文档、15 份 ADR、18 个 SP、WP-02、A1–A10、C-01–C-03 | R1 审计 |
