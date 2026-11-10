# Phase 08.2.3 — Evidence Baseline（证据基线说明）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.3（含 R1 历史对账）
> 文档版本：1.0.0
> 创建日期：2026-11-05
> 最后修改：2026-11-05
> 文档状态：PROPOSED（索引文件；不代表任何验收结论）
> Baseline：NO — 未纳入正式有效基线

---

## 1. 目录用途与当前证据数量

| 目录 | 用途 | 当前证据数 | 状态 |
|---|---|---|---|
| `document_audit/` | 文档存在性、版本、状态、引用的审计索引与前后校验 | 4 个文件：`INDEX.md`、`SHA256_BEFORE.txt`、`SHA256_AFTER.txt`、`R1_VERSION_METADATA_DIFF.patch` | INDEX + CHECKSUM |
| `code_audit/` | 代码侧事实证据（文件路径、修改时间、关键行为） | 1 个索引文件（`INDEX.md`） | INDEX_ONLY |
| `test_results/` | 真实执行的测试输出、日志、覆盖率 | 0 | **EMPTY / NOT VERIFIED** |
| `screenshots/` | 真实浏览器或真机截图 | 0 | **EMPTY / NOT VERIFIED** |
| `hardware/` | 真机实测记录、串口日志、功耗/内存测量 | 0 | **EMPTY / NOT VERIFIED** |
| `traceability/` | 需求到验收的自动/手工追踪输出 | 0（追踪矩阵正文在 `docs/`） | **EMPTY / NOT VERIFIED** |

> 计数口径：`INDEX_ONLY` 表示只有指路索引，没有独立证据文件；`EMPTY` 表示目录内没有任何文件。

---

## 2. 缺失证据清单（必须保持 NOT VERIFIED）

| 缺失证据 | 对应任务 | 为什么重要 | 当前结论 |
|---|---|---|---|
| 4/8 节点 × 40+ 频点的真实采集日志 | 容量与性能、AT-MULTI | 验证吞吐、丢帧、落库速率 | NOT VERIFIED |
| 真机 RSSI 校准与 dBm 换算记录 | A6/A7、AT-RF | `rssi_dbm` 是否可用、是否必须可空 | NOT VERIFIED |
| ESP32-C5 / S3 relay 的 40+ 频点轮转耗时 | A3/A8、AT-RF | 产品目标与固件能力是否匹配 | NOT VERIFIED |
| SoftAP / AP Direct 实测 | A4、AT-AP | AP Direct 是否成立 | NOT VERIFIED |
| Wi-Fi LAN 多节点并发实测 | A5、AT-LAN | LAN 是否为正式部署方案 | NOT VERIFIED |
| 长时间运行（≥24 h）稳定性记录 | AT-STAB | 内存/队列/重启行为 | NOT VERIFIED |
| 三段时间戳对齐实测 | AT-TIME | `tMs` / `agent_received_at` / `created_at` 一致性 | NOT VERIFIED |
| RF 六接口的任何响应样本 | API 设计 | 接口尚未实现 | NOT VERIFIED |
| WP02-D01 修复后的复测记录 | A9 | 状态陈旧是否消除 | NOT VERIFIED（D01 未裁定） |

---

## 3. 关联但不属于本阶段的真实证据（不得混算）

以下证据**真实存在**，但属于历史 Phase 08 / WP-02，不得当作 08.2.3 的 RF 验收证据：

| 证据 | 路径 | 归属 | 使用限制 |
|---|---|---|---|
| WP-02 浏览器验收截图（12 张 PNG） | `docs/evidence/WP02/` | WP-02 | 仅证明 1920×1080 平台 UI 历史状态；D01 仍存在 |
| Phase 08 平台截图（10 张 JPG） | `docs/screenshots/phase08/` | Phase 08.3–08.6 | 模拟设备环境；不是真实 RF 硬件证据 |
| Phase 08 验收报告 | `docs/PHASE08_ACCEPTANCE_REPORT.md` | Phase 08.6 | 自述 CONDITIONAL PASS；不等于 08.2.3 通过 |
| 固件/Agent 源码 | `repo/firmware/`、`gateway-agent/` | 既有实现 | 只能作为“现状证据”，不能作为“已验收证据” |

---

## 4. 证据补录规则（强制）

1. **禁止伪造**：不得生成合成截图、伪造日志、补写“通过时间”。
2. **设计文档不是测试证据**：`PHASE08_2_3_*.md` 只能放 `document_audit/`，不得放入 `test_results/`。
3. **代码审计不是硬件实测**：`code_audit/` 的结论不得升级为 `hardware/` 的 PASS。
4. **NOT VERIFIED 不得改判**：没有新证据时，任何条目保持 `NOT VERIFIED`。
5. **一证据一文件**：文件名格式 `YYYYMMDD_<case-id>_<result>.<ext>`，例如 `20261105_AT-LAN-01_FAIL.log`。
6. **必须写元数据**：每个证据文件旁放置同名 `.meta.md`，记录执行人、环境、设备 ID、命令、时间、原始输出摘要。
7. **模拟环境必须标注**：`simulator` / 模拟设备数据必须在文件名或元数据中显式标注 `SIM`。
8. **不覆盖历史**：新增证据只增不改；删除必须由人工书面批准。

---

## 5. 当前结论

- 目录已建立：**是**（C-03 的结构部分完成）。
- 真实证据内容：`test_results/`、`screenshots/`、`hardware/`、`traceability/` 为 **0 条独立证据**，全部 **EMPTY / NOT VERIFIED**。
- 唯一已有的真实文件是 `document_audit/` 下的版本校验材料（SHA256 前后值 + R1 元数据 diff）与 `code_audit/INDEX.md` 的静态代码索引；它们**不是**测试或硬件证据。
- 任何将本目录存在视为“验收通过”的表述：**均属无效**。

## 6. 关联审计文档

- `docs/PHASE08_2_3_R1_HISTORICAL_RECONCILIATION.md`（历史阶段对账）
- `docs/PHASE08_2_3_R1_VERSION_AUDIT.md`（15 份文档版本审计）
- `docs/PHASE_STATUS_INDEX.md`（统一阶段状态索引）
