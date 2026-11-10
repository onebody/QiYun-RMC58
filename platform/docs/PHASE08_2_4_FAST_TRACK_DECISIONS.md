# Phase 08.2.4 — Fast-Track 快速决策收口 + ADR 冲突检查

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `PROPOSED — PENDING HUMAN APPROVAL`
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，落实 R2 快速裁定建议 + A1/A2 冲突核查
> 性质：**决策建议 + 冲突检查。不视为人工批准；不启动任何实施。**

---

## 1. 本轮 Fast-Track 策略说明

Phase 08.2.3-R2 已经把 A1/A2/A3–A10 的逐项审阅做完。本轮目标是**跳过重复的文档审计，直接用代码/固件/协议证据落地**。

对 R2 已给出明确推荐的事项，本轮**不再重新审阅**，只做两件事：

1. 检查 A1/A2 ADR 是否存在与 RF 重基线直接冲突的内容；
2. 用**硬件审计**而非**文档审阅**来裁决剩余不确定项。

---

## 2. C-01 阶段编号

**快速裁定（维持 R2 推荐）：采用方案 C，统一映射索引，不移动历史文档。**

- 历史 `08.1–08.6` 编号保持原样；
- `docs/PHASE_STATUS_INDEX.md` 为唯一状态索引；
- RF 工作线使用独立工作包标签（如 `RF-P1..RF-P4`），不占用或改写历史 `08.x`；
- 历史阶段与 RF 新线不自动继承审批状态；
- G6（阶段编号裁定）**仍为 BLOCKED**；未形成人工书面裁定前不得解除。

**不执行任何编号改名、移动、重排。**

---

## 3. A3 40+ 频点语义

**快速裁定（维持 R2 推荐）：接受 40+ 频点目录覆盖与并发扫描能力分离。**

四类能力分开表述（详见 `PHASE08_2_4_HARDWARE_CAPABILITY_AUDIT.md` B1 章节）：

| 能力 | 固件事实 | 状态 |
|---|---|---|
| 目录覆盖数量 | `profile_from_equiv_khz` 支持 A/B/E/F/R/L 六个族段（`profile.c` `tune_lo_khz=5345000 / tune_hi_khz=5945000`）；固件本身不做目录枚举，目录覆盖数量**由目录层决定** | `PROPOSED`（可设计 ≥48） |
| 单轮实际扫描数量 | `PROFILE_MAX_CHANNELS=8`（`profile.h:8`）；`planner.c:66` 硬截断 `nch > 8` | **8**（VERIFIED，编译期固定） |
| 同时工作的 RF 通道 | ESP32-C5 单 PHY，一次 LO 一个接收；GROUP 模式下 40 MHz span 一 LO 覆盖多 channel，无真正多通道并发 | **单 LO 接收**（VERIFIED） |
| 实际采样更新速率 | C5 实测 ~1050 rotations/s 上限（`scanner.c:28 MAX_ROTATION_HZ=1050`）；默认 8 channel + 4 LO 情况下 ≈60 updates/s per channel（CLAUDE.md Performance 表） | **待实测压测**；当前仅有低负载数据 |

**硬件审计要求确认：** 40+ 目录条目在 ≤8 并发/轮询覆盖下的**完整扫描周期**。

---

## 4. A4 AP Direct

**快速裁定（维持 R2 推荐）：定位为单节点调试、维护及临时接入；正式部署优先 Wi-Fi LAN。**

**代码事实：**

- C5 固件 `sdkconfig.defaults:35` `# CONFIG_ESP_WIFI_SOFTAP_SUPPORT is not set` — **SoftAP 未编译**；
- C5 固件 `main.c:132` `WIFI_MODE_NULL` — Wi-Fi PHY 仅用于扫描，不入任何站点或 AP；
- `ws_link.c` 仅存在于 `RX_WIFI_RELAY`（S3 relay）分支，C5 head 固件无 STA/AP；
- 无 AP 模式下扫描并行的代码路径；无节点服务端。

**当前 AP Direct 在现有固件上**：**物理上不可行**（SoftAP 关闭、节点侧无 Wi-Fi）。若要启用，至少需要：SoftAP 重新编译 + Wi-Fi coexistence（扫描/AP 并行）+ 节点服务端三件事全部补齐。

**本轮决策**：不批准 AP Direct 能力声明；保留定位文档；能力审计列为 `GAP`。

---

## 5. A5 多 AP 汇聚

**快速裁定（维持 R2 推荐）：批准技术评估，不代表批准实施。**

**硬件架构前提（VERIFIED）：**

- 现有架构是 **C5 head（扫描，无 Wi-Fi）—UART 921600— S3 relay（STA + WebSocket uplink）—Wi-Fi LAN — Site Server**；
- relay 固件（`env:esp32s3-relay`）在 build_flags 里明确写着 `RX_WIFI_RELAY=1`、注释 "A relay host never becomes a receiver — and it does not even call scanner_init()"；
- S3 relay **只做网络桥接**，不参与扫描。

**多节点接入结构（VERIFIED）：**

```text
RF Node 1 (C5 head) —UART— S3 relay 1 —STA—┐
RF Node 2 (C5 head) —UART— S3 relay 2 —STA—┤
RF Node 3 (C5 head) —UART— S3 relay 3 —STA—┼── Site Server
RF Node 4 (C5 head) —UART— S3 relay 4 —STA—┘
                                      （同一张 Wi-Fi LAN，或各自独立 LAN 经路由器互联）
```

**禁止方案**：单张普通 Wi-Fi 网卡一张同时直连多个独立 AP（C2/C5 方案）。物理限制：一次关联一个 SSID。

**本轮不做**：采购、硬件选型、网络变更、实施。

---

## 6. A6 数据库新增表/迁移

**快速裁定（维持 R2）：保持 `REVISION REQUIRED`。**

必须补齐的 7 项（沿用 R2 §4.1）：

1. `rssi_dbfs` 原始测量值（必填、单位 dBFS、不得换算覆盖）；
2. `rssi_dbm` 可空性（NULL + `quality='uncalibrated'`、禁止 dBFS 估算填充）；
3. 校准档案字段（`calibration_profiles` 表/字段、有效期、适用范围）；
4. Alembic `upgrade` 脚本（仅新增表，不改旧表）；
5. Alembic `downgrade` 脚本（独立删除新表）；
6. 唯一性约束（建议 `(node_id,node_boot_id,sequence,channel_key)`）；
7. 索引（建议 `(site_id,node_id,channel_key,sample_timestamp)` 等）。

**本轮硬件审计可提供的证据**：帧 payload JSON 字段清单 → 可以作为 Migration 设计的**字段来源依据**（详见 `PHASE08_2_4_RF_DATA_PATH_AUDIT.md`）。但迁移脚本编写不属于本轮范围。

---

## 7. A7 数据聚合与保留

**快速裁定（维持 R2）：保持 `DEFERRED`。**

当前依据仅为结构推导（每节点 ≈493 帧/s、8 节点 ≈3,944 行/s）。**无实测采样速率、无实测写入量、无实测磁盘吞吐**。

---

## 8. A8 固件能力审计

**快速裁定（维持 R2）：仅批准独立硬件与固件能力审计，不批准固件开发。**

本轮 Phase 08.2.4 本身就是这个审计。审计结论见本阶段交付物。

允许范围：
- 只读代码审计（已完成）；
- 编译环境验证（可选，但不自动执行）；
- 硬件测试计划（已编写，见 `PHASE08_2_4_HARDWARE_TEST_CHECKLIST.md`）；
- 硬件验证**需另行人工批准**；
- 不允许直接开始固件开发。

---

## 9. A9 WP02-D01 设备状态陈旧

**快速裁定：维持 PENDING。本轮不重新执行 WP-02，仅记录 D01 在数据链上的影响位置。**

审计发现：Site Server `Device.status` 与 `last_seen_at` 更新依赖 Agent 心跳（`agents/connection.py`），心跳超时后改离线；但设备 `devices.status` 的 `online` 状态来自 Agent 上报的 `device.online` / `device.state_change` 事件，**无独立心跳**。

对 RF 重基线的影响：RF 图表数据链路与 WP-02 同走 Agent→Site Server 事件流，**RF 数据在线/过期语义复用同一机制**。D01 未解决前 RF 页面同样会"看到旧状态当作在线"。

---

## 10. A10 Phase 08.3

**快速裁定：维持 `NO GO`。**

前置门禁（G1–G7 全部 BLOCKED/PENDING/NOT VERIFIED）无变化；C-01 未裁定、A1/A2 未人工批、D01 未决、固件能力未实测、数据方案未定稿。

---

## 11. A1/A2 ADR 冲突检查（本轮 Fast-Track 范围）

**不重新逐份审阅 A1/A2 ADR**。仅检查**与 RF 重基线直接冲突**的项。

### 11.1 A1 三份架构 ADR 与 RF 重基线的冲突检查

| ADR | 主题 | 与 RF 重基线是否冲突 | 说明 |
|---|---|---|---|
| ADR-08.2.1-01 | 多站点裁定 | **不冲突**。RF 重基线采纳"单 Site 归属 + 显式迁移"，与本 ADR 一致；RF 资源归属 `site_id` 可直接纳入本 ADR 补充条款 |
| ADR-08.2.1-02 | 事件流裁定 | **不冲突**。RF 重基线"当前不实现前端 WS、轮询 1–2 s"与本 ADR 一致；协议冻结对 RF 数据链无影响 |
| ADR-08.2.1-03 | 权限与安全裁定 | **不冲突**。RF 重基线"认证/授权设计先行、RBAC+Scope 同步、两平面隔离"与本 ADR 一致；RF 资源角色与本地 AP 平面属 ADR 外扩展 |

**结论**：A1 三份 ADR 与 RF 重基线**无实质性冲突**，可沿用为架构基线。

### 11.2 A2 十二份安全 ADR 与 RF 重基线的冲突检查

| ADR | 主题 | 与 RF 重基线是否冲突 | 说明 |
|---|---|---|---|
| ADR-01 认证载体 | Opaque 会话混合 | **不冲突** |
| ADR-02 会话/CSRF/XSS | 会话策略 | **不冲突**；RF 写操作需挂依赖，与本 ADR 一致 |
| ADR-03 不用 JWT | Opaque 默认 | **不冲突** |
| ADR-04 Agent 凭证 | 保留并补齐 | **不冲突**；RF 节点凭证待分离属补充 |
| ADR-05 mTLS 边界 | 局域网可 `ws://` | **不冲突**；现有 relay 固件就是 `ws://` uplink（`ws_link.c` 未强制 wss），与本 ADR 对局域网的策略一致 |
| ADR-06 RBAC 四角色 | Observer/Operator/Site Admin/System Admin | **不冲突**；RF 资源角色属补充 |
| ADR-07 Site Scope | 服务端强制 | **不冲突**；RF 查询强制 Scope 过滤属本 ADR 设计范围 |
| ADR-08 Resource Scope | 默认关闭 | **不冲突** |
| ADR-09 双平面隔离 | Owner/Observer ≠ 平台角色 | **不冲突**；RF Node 所有权属 Plane M，平台授权属 Plane U |
| ADR-10 命令授权链 | 六步链 | **不冲突**；RF 控制类 UI 需等本 ADR |
| ADR-11 WS 票据 | 30 s 单次 | **不冲突**；RF Workbench 若用 WS 需先满足本 ADR 前置条件 |
| ADR-12 审计/限流 | 原则层 | **不冲突**；RF 多节点上报限流属本 ADR 限流范围 |

### 11.3 结论

A1/A2 全部 15 份 ADR 与 RF 重基线**无实质性冲突**，可沿用为架构基线。

本轮硬件审计**不得擅自实施**任何安全决策的代码变更；但可**在静态证据层面**引用 ADR 中的安全边界来约束硬件能力声明（例如：不得声称"AP Direct 天然安全"，因为 AP Direct 若上线仍需满足 ADR-09 双平面分离与 ADR-12 审计）。

---

## 12. 快速收口状态汇总

| 决策 | 快速裁定 | 允许本轮做 | 禁止本轮做 |
|---|---|---|---|
| C-01 | 方案 C（维持） | 引用索引、核对编号 | 改名/移动/重排 |
| A3 | 40+ 语义分离（维持） | 核实现状、量化四能力 | 宣称 40+ 并发 |
| A4 | AP Direct 定位（维持） | 核实现状、确认 SoftAP 关闭 | 声称能力已具备 |
| A5 | 仅评估（维持） | 绘制接入架构 | 采购/实施 |
| A6 | REVISION REQUIRED（维持） | 从帧 JSON 提取字段依据 | 编写 Migration |
| A7 | DEFERRED（维持） | 结构推导标记 NOT VERIFIED | 编造性能数字 |
| A8 | 审计批准（维持） | 只读审计、测试计划 | 开始固件开发 |
| A9 | PENDING（维持） | 记录 D01 对 RF 影响 | 执行 WP-02 重跑 |
| A10 | NO GO（维持） | 列出门禁状态 | 启动 08.3 |
| A1 | 不冲突、沿用 | 作为架构基线引用 | 改写或批准 |
| A2 | 不冲突、沿用 | 作为架构基线引用 | 改写或批准 |

---

## 13. 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 未修改任何 ADR 状态、SP 状态、WP-02 验收状态。
- 未启动 Phase 08.2.4 实际硬件验证、Phase 08.3、SP-00–SP-17。
- 本文件状态：`PROPOSED — PENDING HUMAN APPROVAL`。
