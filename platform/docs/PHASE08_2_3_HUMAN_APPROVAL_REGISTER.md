# Phase 08.2.3 — 人工审批登记册（Human Approval Register）

> 项目：QiYun / widscan58
> 文档版本：1.0.0
> 生成日期：2026-11-05
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 建立版本元数据（R1 规范化）；此前仅记为 v1.0，无版本历史记录
> 文档状态：**PROPOSED（待人工审批）**
> 重要声明：本登记册**不包含任何自动批准**。所有审批结果栏保持 `PENDING`，仅可由人工填写。

---

## 1. 审批状态定义

| 状态 | 含义 | 是否可实施 |
|---|---|---|
| `PROPOSED` | 已提出，未审批 | 否 |
| `APPROVED` | 人工明确批准 | 是（仍需对应阶段门禁） |
| `REJECTED` | 人工明确拒绝 | 否 |
| `REVISION REQUIRED` | 退回修改 | 否 |
| `DEFERRED` | 暂缓决定 | 否 |
| `NOT VERIFIED` | 证据不足，无法判断 | 否 |

**只有用户明确作出批准决定，才能把 `PROPOSED` 改为 `APPROVED`。**

---

## 2. A1 — Phase 08.2.1 三份 ADR

### A1-01 `ADR-08.2.1-01` 多站点裁定

| 项 | 内容 |
|---|---|
| 文件 | `docs/PHASE08_2_1_MULTI_SITE_ADR.md` |
| 当前状态 | `PROPOSED` |
| 决策主题 | 单站点现状与多站点目标边界 |
| 决策内容 | 当前保持单站点事实；禁止前端 Mock 多站点；不现在做 Site 切换器；核心表已有 `site_id` 不新增；Device 采用“单 Site 归属 + 显式迁移”；多站点作为独立阶段且排在安全评审之后；旧 Agent 通过凭证绑定 Site 兼容 |
| 影响范围 | Site Server、Agent、平台导航、数据查询 |
| 不批准后果 | 多站点需求无法进入实施；RF 数据的站点归属无法定稿；易出现前端假多站点 |
| 依赖 | ADR-08.2.2-07（Site Scope）、SP-03、SP-13/14 |
| 推荐意见 | 建议 `APPROVED`，并补充“RF 节点/测量/频道计划均属于 `site_id` 范围” |
| 审批结果 | **PENDING** |
| 审批人 / 日期 | — |
| 备注 | — |

### A1-02 `ADR-08.2.1-02` 事件流裁定

| 项 | 内容 |
|---|---|
| 文件 | `docs/PHASE08_2_1_EVENT_STREAM_ADR.md` |
| 当前状态 | `PROPOSED` |
| 决策主题 | Agent→Server 与 Server→Frontend 事件流 |
| 决策内容 | 现在不实现前端 WS；不重构 Agent 事件协议（冻结）；Critical/Measurement 需配额（独立后端阶段）；持久化缓冲待评估；不采用逐事件 ACK，建议批量 ACK；前端 WS 快照未来必需；轮询指数退避需独立前端修复 |
| 影响范围 | 实时刷新、RF 图表 1–2 s 读路径、事件可靠性 |
| 不批准后果 | RF 图表无法确定轮询/推送策略；实时能力无法立项 |
| 依赖 | SP-09（限流）、SP-12（WS 票据）、ADR-08.2.2-11 |
| 推荐意见 | 建议 `APPROVED`，并补充“RF 读路径首版为只读轮询 1–2 s，不依赖前端 WS”（D-08.2.3-07） |
| 审批结果 | **PENDING** |
| 审批人 / 日期 | — |
| 备注 | — |

### A1-03 `ADR-08.2.1-03` 权限与安全裁定

| 项 | 内容 |
|---|---|
| 文件 | `docs/PHASE08_2_1_SECURITY_PERMISSION_ADR.md` |
| 当前状态 | `PROPOSED` |
| 决策主题 | 权限模型与平台/Agent 边界 |
| 决策内容 | 当前不可生产暴露；内网隔离仅作临时缓解；08.3 前完成认证设计但不实现；独立 Security Hardening（08.2.2）；RBAC + Site Scope 同步设计；Owner/Observer 与平台角色两平面隔离；平台禁止直接调用 Agent 命令；后端就绪后才加写 UI |
| 影响范围 | 全部写操作、RF 频道计划/校准写入、节点接入 |
| 不批准后果 | `/rf/*` 无授权模型；写操作无法安全实施 |
| 依赖 | ADR-08.2.2-06/07/09/10、SP-02/03/04 |
| 推荐意见 | 建议 `APPROVED`，并补充 RF 四类资源的读写角色与节点所有权语义 |
| 审批结果 | **PENDING** |
| 审批人 / 日期 | — |
| 备注 | — |

### A1 汇总

| ID | 当前状态 | 推荐 | 依赖 | 审批结果 |
|---|---|---|---|---|
| A1-01 | PROPOSED | APPROVED（含补充） | A2 / SP-03 | PENDING |
| A1-02 | PROPOSED | APPROVED（含补充） | SP-09/12 | PENDING |
| A1-03 | PROPOSED | APPROVED（含补充） | SP-02/03/04 | PENDING |

---

## 3. A2 — Phase 08.2.2 十二份安全 ADR

> 核实结果：实际为 **12 份 ADR**（`ADR-08.2.2-01` ~ `-12`），不是 14 份文件。用户列举的 14 个“核查方向”由这 12 份覆盖。

| ADR | 主题 | 当前状态 | 推荐意见 | 主要依赖 | 审批结果 |
|---|---|---|---|---|---|
| ADR-08.2.2-01 | 用户认证载体：混合（Opaque + Cookie / Bearer） | PROPOSED | APPROVED | SP-01 | PENDING |
| ADR-08.2.2-02 | 会话生命周期、CSRF 与 XSS 防护 | PROPOSED | APPROVED | SP-01/06 | PENDING |
| ADR-08.2.2-03 | 不采用 JWT 作为默认方案 | PROPOSED | APPROVED | SP-01 | PENDING |
| ADR-08.2.2-04 | Agent 凭证体系保留并补齐生命周期 | PROPOSED | APPROVED（需补节点凭证分离） | SP-10 | PENDING |
| ADR-08.2.2-05 | mTLS 仅用于跨互联网多站点 | PROPOSED | APPROVED | SP-16 | PENDING |
| ADR-08.2.2-06 | RBAC 四角色模型 | PROPOSED | APPROVED（需补 RF 资源角色） | SP-04 | PENDING |
| ADR-08.2.2-07 | Site Scope 服务端强制 | PROPOSED | APPROVED | SP-03 | PENDING |
| ADR-08.2.2-08 | Resource Scope 默认关闭、可选启用 | PROPOSED | APPROVED | SP-05 | PENDING |
| ADR-08.2.2-09 | 用户平面与机器平面严格分离 | PROPOSED | APPROVED（需补本地 AP 平面） | SP-02/12 | PENDING |
| ADR-08.2.2-10 | 命令双平面二次授权链 | PROPOSED | APPROVED（需补频道计划/AP 配置） | SP-15 | PENDING |
| ADR-08.2.2-11 | 前端 WS 短时票据、URL 不放长期凭证 | PROPOSED | APPROVED | SP-12 | PENDING |
| ADR-08.2.2-12 | 网络隔离不替代应用安全；审计身份化与限流 | PROPOSED | APPROVED（需补 RF 查询配额） | SP-07/09 | PENDING |

### A2 说明

- 全部保持原始 `PROPOSED`，本轮未改动原文与状态。
- 不批准的直接后果：`/rf/*` 接口、节点接入凭证、RF 页面写操作、任何 WS 实时通道均不得实施。
- 推荐不是批准；审批结果栏必须由人工填写。

---

## 4. A3 — 40+ 频点语义

| 项 | 内容 |
|---|---|
| 决策主题 | “每节点 40+ 频点”的产品语义与固件边界 |
| 事实（VERIFIED） | 固件 `PROFILE_MAX_CHANNELS=8`；默认 R1–R8；`CHANNELS` 最多 8 项 |
| 产品目标 | 目录 ≥40 个逻辑频点（建议 48：A/B/E/F/R/L） |
| 建议决策 | 区分 **Channel Catalog（≥40）** 与 **Concurrent Channels（≤8）**；“40+”指目录覆盖，不是同时观测；节点能力由 `node_capabilities` 动态提供；UI 不写死数量；扫描模式为“离散频点轮转”，**不是连续 FFT**，不得插值伪造 |
| 影响范围 | PRD-RF-002、UI 选择器/热力图、容量模型 |
| 不批准后果 | 无法定义验收口径；容易把目录数当并发能力对外宣称 |
| 依赖 | A8（固件审计）、D-08.2.3-01 |
| 推荐意见 | 建议 `APPROVED`（语义层），实际 40+ 覆盖标 `NOT VERIFIED` 直至固件实测 |
| 待验证 | 轮转调度是否存在；单频道刷新周期；切换开销；40+ 覆盖所需时间 |
| 审批结果 | **PENDING** |
| 审批人 / 日期 | — |

---

## 5. A4 — AP Direct 定位

| 项 | 内容 |
|---|---|
| 建议基线 | 单节点调试、首次配置、维护排障、临时直连 |
| 明确定位 | **不是**多节点正式部署方案；**不表示**多节点并发接入已实现 |
| 正式部署优先 | Wi-Fi LAN |
| 现实现状（VERIFIED） | SoftAP 未编译；无节点 HTTP/WS 服务端；无 AP 配置命令 |
| 影响范围 | PRD-NET-008–012、Node Management、本地工具 |
| 不批准后果 | AP Direct 可能被误当正式接入方案对外承诺 |
| 依赖 | A8、D-08.2.3-03、SP-RF-01 |
| 推荐意见 | 建议 `APPROVED`（定位层），能力实现保持 `GAP` |
| 审批结果 | **PENDING** |
| 审批人 / 日期 | — |

---

## 6. A5 — 多 AP 汇聚方案

| 方案 | 硬件复杂度 | 拓扑 | 并发能力 | 稳定性 | 配置复杂度 | 运维成本 | 安全风险 |
|---|---|---|---|---|---|---|---|
| C0 单电脑连一个节点 AP | 低 | 点对点 | 1 | 中 | 低 | 低 | 中（本地无认证） |
| C1 多节点同一 LAN | 低 | 星型（既有 AP） | 多 | 高 | 低 | 低 | 中（需 TLS/凭证） |
| C2 Gateway 多射频/多网卡汇聚 | 高 | 汇聚 | 多 | 中高 | 高 | 中 | 中 |
| C3 专用 AP 基础设施/Mesh | 高 | 企业级 | 多 | 高 | 中 | 高 | 低（可控） |
| C4 节点改 STA + 有线回传 | 中 | 混合 | 多 | 高 | 中 | 中 | 低 |
| C5 单网卡分时轮询多 AP | 低 | 轮询 | 1（时分） | **低**（丢窗口） | 低 | 低 | 中 |

**建议**：V1 正式部署走 **C1（LAN 优先）**；多 AP 需求仅在 C2/C4 中择一，**明确禁止 C5 单网卡多 AP**。
**不批准后果**：多节点 AP 场景会以不可靠方式实现。
**依赖**：硬件选型、A8、D-08.2.3-04。
**审批结果：PENDING**（本轮只评估，不采购、不实施、不改网络）。

---

## 7. A6 — 数据库新增表与迁移

| 项 | 内容 |
|---|---|
| 必要性 | **必要**：现有 `measurements` 仅存 `payload_json`，无法按 channel/频率/质量查询，无法满足 Node×Channel×Time |
| 复用评估 | 可复用现有 `site_id/gateway_id/node_id/node_boot_id/sequence/双接收时间`；Channel 级事实必须新增 |
| 建议新增 | `channel_catalog`、`node_capabilities`、`node_channel_plans`、`rf_measurements`、`calibration_profiles`、聚合表 |
| 关键字段核查 | node_id ✔、node_boot_id ✔、channel_id（新增）、frequency_hz（新增，依赖计划版本）、rssi_dbm（新增，可空 + 校准）、sample_timestamp（需锚点）、received_timestamp ✔、sequence ✔、sample_quality（新增）、site_id ✔、gateway_id ✔ |
| 索引 | 建议 `(site_id,node_id,channel_key,sample_timestamp)`、`(site_id,channel_key,sample_timestamp)`、`(node_id,node_boot_id,sequence)` |
| 唯一性 | `rf_measurements` 以 `(node_id,node_boot_id,sequence,channel_key)` 幂等 |
| 历史兼容 | 只增不改；原始 `measurements` 保留；历史回填不默认执行 |
| 迁移方案 | 独立新增表；Alembic `upgrade` 建表；需人工批准后编写 |
| 回滚方案 | 独立 `downgrade` 删除新表；不影响旧数据 |
| 待决点 | `rssi_dbm` 是否允许 NULL（建议允许）；`sample_timestamp` 锚点是否 V1 交付 |
| 不批准后果 | RF 查询与三视图无法实现 |
| 依赖 | A1-01、A2-07、SP-RF-04 |
| 推荐意见 | `REVISION REQUIRED`：先补齐具体迁移脚本/回滚脚本与 `rssi_dbm` 空值语义，再批准 |
| 审批结果 | **PENDING** |

---

## 8. A7 — 数据聚合与保留策略

| 项 | 内容 |
|---|---|
| 实测采样速率 | **NOT VERIFIED**（结构推导 ≈493 帧/s/节点） |
| 原始逐帧 | 8 节点 ≈3,944 行/s、≈340.8 M 行/天，**不可长期保留** |
| 建议写入 | 频道 1 Hz、频谱 0.2 Hz、遥测 1 或 0.2 Hz |
| 建议分层 | 1 s（30 d）、1 m（365 d）、1 h（长期） |
| 热力图窗口 | 1 h / 6 h / 24 h / 7 d，桶宽 60–3600 s |
| 清理策略 | 定时降采样后删除过期原始数据；保留聚合 |
| 磁盘容量 | **待实测**，不得编造 GB 数字 |
| 不批准后果 | 存储增长不可控；8 节点场景可能拖垮 SQLite |
| 依赖 | A6、SP-RF-05、A8 |
| 推荐意见 | `DEFERRED`：待 A8 固件实测与压测后决定具体阈值 |
| 审批结果 | **PENDING** |

---

## 9. A8 — RF Node 固件立项

| 审计项 | 现状 | 证据等级 | 结论 |
|---|---|---|---|
| 40+ 频点扫描 | 上限 8 | VERIFIED | 不满足 |
| 扫描周期/轮转 | 有 `rotationHz` 概念 | DOCUMENTED | 未实测 |
| 扫描与 Wi-Fi 并行 | 双芯片可；同 C5 未知 | UNKNOWN | 待测 |
| SoftAP | 未编译 | VERIFIED | 缺失 |
| 节点服务端 | 无 | VERIFIED | 缺失 |
| Wi-Fi LAN 接入 | S3 relay 有 | VERIFIED | 部分满足 |
| 多节点上报 | Agent 可多设备 | PARTIAL | 无 LAN 发现 |
| RSSI 精度/校准 | dBFS，无校准 | VERIFIED | 不满足 dBm |
| 时间戳可靠性 | 单调 ms，无锚点 | VERIFIED | 部分 |
| RAM/Flash/CPU | 未测量 | NOT VERIFIED | 待测 |
| 长时间稳定性 | 未测试 | NOT VERIFIED | 待测 |

**建议**：**建议独立立项**（Phase 08.2.4 固件审计），但**不启动固件开发**。
**理由**：40+ 并发、SoftAP、节点服务端均无法在当前固件上实现；且不得修改冻结协议。
**不批准后果**：R1-B 与 R2 的 40+ 目标无法达成。
**审批结果：PENDING**。

---

## 10. A9 — WP02-D01 处理决定

| 项 | 内容 |
|---|---|
| 事实 | D01（P2）：设备心跳超时后状态仍显示“在线” |
| 影响页面 | Devices、Overview、RF 页面（未来） |
| 陈旧表现 | `devices.status` 持久化为 `online`；启动时仅将旧 `connecting` 改为 `pending`，不按心跳过期 |
| 是否误导 | **是**：运维可能把离线节点当作在线；RF 图表可能继续显示“实时” |
| 新鲜度标识 | 现有页面显示 `last_seen_at`，但无统一 `stale` 徽标 |
| 检测/恢复 | Agent 断线后无自动过期；重连后可恢复 |
| 对后续验收影响 | RF 在线/过期语义直接继承该缺陷，**阻塞 AT-UI-02** |
| 选项 A | 修复 D01：增加心跳超时判定与 `stale/offline` 展示，重跑 WP-02 相关用例 |
| 选项 B | 暂时接受：记录风险、责任人、影响范围、重新评估条件 |
| 推荐意见 | 建议 **A（修复）**；若选 B，必须在 RF 页面验收前提供明确的“数据可能过期”提示与责任人 |
| 审批结果 | **PENDING**（不得由 AI 选择） |

---

## 11. A10 — 是否启动 Phase 08.3

### 11.1 前置条件核查

| 条件 | 状态 |
|---|---|
| A1 三份 ADR 批准 | PENDING |
| A2 十二份安全 ADR 批准 | PENDING |
| 08.2.3 需求/架构基线确认 | PENDING（本报告为 CONDITIONAL PASS） |
| WP-02 验收门禁 | CONDITIONAL PASS（D01 未决） |
| WP02-D01 书面决定 | PENDING |
| 固件可行性评估 | NOT VERIFIED（尚未立项审计） |
| 数据模型/存储方案批准 | PENDING（A6/A7） |
| 阶段编号冲突 C-01 | **未解决** |

### 11.2 建议

**A10 = NO GO（当前）**

理由：

1. 安全 ADR 未批，任何 RF 接口与页面都缺授权基础。
2. 固件可行性未知，40+ 与 AP Direct 无法承诺。
3. 数据方案未批，页面无法确定数据源。
4. D01 未决，在线状态不可信。
5. C-01 阶段编号与既有 08.3/08.4/08.5 文档冲突，需先裁定。

> 若只启动**纯设计**（无代码）可在 A1/A2 批准后考虑 `CONDITIONAL GO`；本报告不自动启动。

| 审批结果 | **PENDING** |
|---|---|
| 审批人 / 日期 | — |

---

## 12. 汇总表

| ID | 决策事项 | 当前状态 | 推荐意见 | 依赖项 | 审批结果 |
|---|---|---|---|---|---|
| A1 | 08.2.1 三份 ADR | PROPOSED | APPROVED（附补充） | A2、SP-03 | PENDING |
| A2 | 08.2.2 十二份安全 ADR | PROPOSED | APPROVED（附补充） | SP-00–17 | PENDING |
| A3 | 40+ 频点语义 | PROPOSED | APPROVED（语义层） | A8 | PENDING |
| A4 | AP Direct 定位 | PROPOSED | APPROVED（定位层） | A8 | PENDING |
| A5 | 多 AP 汇聚 | PROPOSED | C1 优先；禁止 C5 | 硬件选型 | PENDING |
| A6 | 数据表与迁移 | PROPOSED | REVISION REQUIRED | A1/A2 | PENDING |
| A7 | 聚合与保留 | PROPOSED | DEFERRED | A6/A8 | PENDING |
| A8 | 固件立项 | PROPOSED | 建议立项（不开发） | 硬件 | PENDING |
| A9 | WP02-D01 | PROPOSED | A（修复） | WP-02 | PENDING |
| A10 | 启动 Phase 08.3 | PROPOSED | NO GO | 全部上述 | PENDING |

---

## 13. 审批填写区（仅人工）

| ID | 审批结果 | 审批人 | 日期 | 备注 |
|---|---|---|---|---|
| A1 | | | | |
| A2 | | | | |
| A3 | | | | |
| A4 | | | | |
| A5 | | | | |
| A6 | | | | |
| A7 | | | | |
| A8 | | | | |
| A9 | | | | |
| A10 | | | | |

**本登记册未作出任何批准。**
