# Phase 08.2.4 — 多节点数据能力验证设计 + 最小硬件测试计划

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `CONDITIONAL PASS`（静态可行性完成；实测 NOT VERIFIED）
> **Baseline**: NO — 未纳入正式有效基线（待人工审批）
> **Version History**: 1.0.0 (2026-11-05) 初版，5 场景可行性 + 9 项最小硬件验证设计
> 性质：**可行性评估 + 测试计划。不自动执行任何硬件操作。**

---

## Part 1 — 5 场景可行性评估

### 场景一：4 节点 × 40+ 逻辑频点

| 维度 | 可行性 | 依据 |
|---|---|---|
| 数据产生 | **CONDITIONAL** | 40+ 逻辑频点需要目录层设计；固件硬限制 8 channel 并发，必须轮询覆盖；单节点完整遍历一轮的时间 **NOT VERIFIED**（= 目录数 / rotationHz） |
| 数据传输 | **VERIFIED（理论）** | 每节点 ≈1–2 KB/s；4 节点 ≈4–8 KB/s；UART/WS/DB 均远超上限 |
| 数据存储 | **VERIFIED** | 4 节点 × 40+ 目录轮询 → 逐帧 1 Measurement → 每秒 ≈60 帧/节点 → 864K/天/节点 → 4 节点 ≈3.5M 行/天；SQLite 可承受；PostgreSQL 轻松（但 A7 聚合/保留 DEFERRED） |
| 查询 | **NOT VERIFIED** | 需从 `payload_json.hop_channel_t[]` 拆行；目录反查逻辑未写 |
| 前端展示 | **NOT VERIFIED** | RF Workbench 未实现；轮询 WS 方式未决 |
| 实时更新 | **NOT VERIFIED** | 轮询/WS 方式未定；WS-U 未实现 |

**最小测试方案**：
- 固件设 8 channel（默认 R1–R8）；运行 30 min；用 Site Server `get_ingestion_stats()` 计丢帧；
- 在产品层设计目录表（48 条），rotationHz 实测后算完整遍历时间。

---

### 场景二：4 节点各自选择 R8 同频点显示 RSSI

| 维度 | 可行性 | 依据 |
|---|---|---|
| 数据产生 | **VERIFIED** | 每个节点的 hop_channel_t 包含 R8 对应 channel_index 的 rssi_cdb |
| 数据传输 | **VERIFIED** | 同上 |
| 数据存储 | **VERIFIED** | 同上 |
| 查询 | **CONDITIONAL** | 需从 payload_json 里筛选 channel_index=R8 对应值，再按 device_id 分组 |
| 前端展示 | **NOT VERIFIED** | RF Workbench 未实现 |
| 实时更新 | **NOT VERIFIED** | 同上 |

**最小测试方案**：
- 4 个 Measurement 行的 payload_json 分别含 R8 channel 的 rssi_cdb；
- REST 查询模拟：`SELECT payload_json->'$.hop_channel_t[?(@.channel_index==X)].rssi_cdb FROM measurements WHERE device_id=Y`（PostgreSQL JSONB）；SQLite 无 JSONB，需应用层解析。

---

### 场景三：最多选择 8 个频点跨多节点

| 维度 | 可行性 | 依据 |
|---|---|---|
| 数据产生 | **VERIFIED（但有约束）** | 固件硬限制 8 channel；**前端最多选 8 个**；选 9 个及以上需固件改动 |
| 数据传输 | **VERIFIED** | 同上 |
| 数据存储 | **VERIFIED** | 同上 |
| 查询 | **CONDITIONAL** | 同上 |
| 前端展示 | **NOT VERIFIED** | RF Workbench 未实现；选择器需 8 上限强制 |
| 实时更新 | **NOT VERIFIED** | 同上 |

**关键约束**："最多选 8 个" 与固件硬限制对齐，不能写 UI 允许选 40+ 再期望后端/固件"自动处理"——固件根本加载不了第 9 个 channel。

---

### 场景四：4 节点 × 8 频点 → 32 条独立数据系列

| 维度 | 可行性 | 依据 |
|---|---|---|
| 数据产生 | **VERIFIED** | 4 节点各 8 channel；每帧 hop_channel_t[8]；32 条系列 = 4×8 的组合 |
| 数据传输 | **VERIFIED** | 同上 |
| 数据存储 | **VERIFIED** | 同上 |
| 查询 | **CONDITIONAL** | 需要从每帧里拆出 8 行；8 帧/秒/节点 → 拆后 64 行/秒/节点 → 256 行/秒/4 节点；若不拆行而是应用层聚合也可行 |
| 前端展示 | **NOT VERIFIED** | 32 条线的折线图在 1920×1080 下可行（1920 宽 / 32 ≈ 60px/系列 ≈ 足够）；但需系列色分配（`--s1..--s8` 已存在，但 4 节点需节点×频点复合色或节点分层展示） |
| 实时更新 | **NOT VERIFIED** | 同上 |

---

### 场景五：8 节点 × 40+ 逻辑频点 → RSSI 热力图

| 维度 | 可行性 | 依据 |
|---|---|---|
| 数据产生 | **CONDITIONAL** | 8 节点固件同时各跑 8 channel 轮询覆盖 40+ 目录；每节点完整遍历时间 ≈ 目录数 / rotationHz；8 节点并行无互斥 |
| 数据传输 | **VERIFIED（理论）** | 8 节点 × 1–2 KB/s ≈ 8–16 KB/s；远低于上限 |
| 数据存储 | **VERIFIED** | 8 节点 × 864K/天 ≈ 6.9M 行/天；PostgreSQL 没问题 |
| 查询 | **NOT VERIFIED** | 热力图需按 channel_id × device_id × 时间桶聚合；A7 聚合方案 DEFERRED；SQLite 性能存疑 |
| 前端展示 | **NOT VERIFIED** | 8 × 40+ ≈ 320 cell 热力图；1080p 单图可行；但需桶宽（时间窗）选择器 |
| 实时更新 | **NOT VERIFIED** | 同上 |

### 场景评估汇总

| 场景 | 数据产生 | 传输 | 存储 | 查询 | 前端 | 实时 | 总评 |
|---|---|---|---|---|---|---|---|
| 1 4×40+ | CONDITIONAL | VERIFIED | VERIFIED | NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | **CONDITIONAL PASS**（需固件实测 + 目录层） |
| 2 4 节点同频 | VERIFIED | VERIFIED | VERIFIED | CONDITIONAL | NOT VERIFIED | NOT VERIFIED | **CONDITIONAL PASS**（查询/UI 待建） |
| 3 8 频点上限 | VERIFIED（硬限制） | VERIFIED | VERIFIED | CONDITIONAL | NOT VERIFIED | NOT VERIFIED | **CONDITIONAL PASS**（前端强制 8 上限） |
| 4 4×8=32 系列 | VERIFIED | VERIFIED | VERIFIED | CONDITIONAL | NOT VERIFIED | NOT VERIFIED | **CONDITIONAL PASS**（UI 层复合色待设计） |
| 5 8×40+ 热力图 | CONDITIONAL | VERIFIED | VERIFIED | NOT VERIFIED | NOT VERIFIED | NOT VERIFIED | **NOT VERIFIED**（多节点 + 热力图聚合） |

---

## Part 2 — 最小硬件验证计划（9 项）

> **边界**：本计划只给"操作步骤 + 通过判定 + 风险说明"；**不自动执行**；每项硬件测试需人工执行并记录结果。

### H-01 — C5 单节点启动 & 自检

| 项 | 内容 |
|---|---|
| 目的 | 确认 C5 head 能编译 + 刷机 + 启动 + 输出串口日志 |
| 前置 | PlatformIO 环境；USB 线；`env:esp32c5-head` 编译 OK |
| 步骤 | 1. `pio run -e esp32c5-head -t upload`；2. 打开串口监视器 115200；3. 观察启动日志 |
| 通过 | 看到 scanner_init() / loop() 启动日志；无 panic；UART TX 有数据 |
| 不通过 | 启动即 panic / 无扫描输出 |
| 风险 | ESP32-C5 首次刷机可能需特殊下载模式 |

### H-02 — C5 RF 扫描能力

| 项 | 内容 |
|---|---|
| 目的 | 确认 C5 head 真正产生 METRICS / LEVELS / SPECTRUM 帧 |
| 前置 | H-01 通过；串口接收到扫描输出 |
| 步骤 | 1. 在 C5 head UART TX 端接 Python 串口读取脚本；2. 连续读 30 min；3. 统计帧类型计数 |
| 通过 | 能看到 METRICS 帧（含 `hop_channel_t[]`、`rssi_cdb`、`device_timestamp_ms`） |
| 不通过 | 只有 LEVELS / SPECTRUM，无 METRICS；或频率极低 |
| 风险 | UART 波特率/引脚定义需与固件 `UART_LINK_BAUD` 匹配 |

### H-03 — UART 921600 数据透传（C5 → S3 relay）

| 项 | 内容 |
|---|---|
| 目的 | 确认 C5 head → S3 relay UART 桥接**无丢帧** |
| 前置 | H-01 / H-02 通过；S3 relay 编译 env:esp32s3-relay + 刷好；两者 UART 连起来 |
| 步骤 | 1. S3 relay 接 AP（或先不上网，只走本地）；2. C5 head 产生帧；3. 用 Python 读 S3 relay UART RX 端，比对帧计数 |
| 通过 | 30 min 内 relay RX 帧计数 ≈ C5 TX 帧计数；误差 < 1% |
| 不通过 | relay 端明显少帧；UART 溢出 |
| 风险 | 921600 波特率对线缆/EMI 敏感；短线路板优先 |

### H-04 — S3 relay → Site Server WS uplink

| 项 | 内容 |
|---|---|
| 目的 | 确认 S3 relay 能连 Wi-Fi + 连 Site Server WS agent + 二进制帧上报 |
| 前置 | H-03 通过；Site Server 运行；AP 可用 |
| 步骤 | 1. Site Server 配置 relay 的 `gateway_id` + 凭证（环境变量或 DB）；2. S3 relay 串口配 SSID/密码/W_URL（`net_prefs`）；3. 上电观察 WS connect 日志；4. Site Server 控制台看 agent 连接事件 + device.frames 入库 |
| 通过 | Site Server `get_ingestion_stats()` 计数增长；`persisted > 0`；`duplicate_events` 合理；`integrity_errors = 0` |
| 不通过 | WS connect 失败 / 帧不入库 / integrity_errors > 0 |
| 风险 | credential 配置路径；Site Server `site_id` 绑定；代理/NAT |

### H-05 — C5 扫描 & S3 上网并行稳定性

| 项 | 内容 |
|---|---|
| 目的 | 验证两芯片并行运行是否稳定、UART 链路无丢帧 |
| 前置 | H-04 通过；C5 + S3 + AP + Site Server 全套接好 |
| 步骤 | 1. 连续运行 2 h；2. 每 10 min 采一次 ingestion stats；3. 记录 CPU/内存/WS 重连次数 |
| 通过 | 无 WS 重连 / 重连 ≤ 1 次（AP 抖动）；无 device.state_change `agent.error`；`integrity_errors = 0` |
| 不通过 | WS 频繁掉线重连；C5 重 panic；relay 重启 |
| 风险 | 长时间运行可能暴露内存泄漏、USB 供电不足 |

### H-06 — 多节点接入（2–4 对 relay + head）

| 项 | 内容 |
|---|---|
| 目的 | 确认多 Agent（relay）→ 同一 Site Server 并行工作 |
| 前置 | H-04 通过；准备 2–4 对 relay+head；AP 可承载 |
| 步骤 | 1. 每对 relay 各自配唯一 `gateway_id`；2. Site Server 逐一添加凭证；3. 全部上电；4. Site Server 控制台观察 |
| 通过 | 所有 Agent 同时连接；ingestion stats 按 4 倍速率增长；无 `duplicate_events` 异常；所有 device 列表能看到 |
| 不通过 | 某对 relay 连不上 Site Server；或 Site Server WS handler 过载丢帧 |
| 风险 | AP 负载；每对 relay 凭证；gateway_id 碰撞 |

### H-07 — 节点离线/重连行为

| 项 | 内容 |
|---|---|
| 目的 | 验证 S3 relay 断电 / 重启 / AP 切换后的重连行为 |
| 前置 | H-04 通过 |
| 步骤 | 1. S3 relay 断电 30 s → 上电；2. Site Server 观察 device/agent 状态变化（离线 → 重连）；3. 断 AP 2 min → 恢复；4. relay 手动重启；5. C5 head 手动重启 |
| 通过 | Site Server 正确标记离线；重连后自动恢复；boot_id 变化；无坏数据入库 |
| 不通过 | 离线不标记；重连不恢复；boot_id 不变；integrity_errors 增加 |
| 风险 | D01 stale device status 会在此暴露 |

### H-08 — 连续运行 72 h 稳定性

| 项 | 内容 |
|---|---|
| 目的 | 验证长稳（内存泄漏、UART 错误计数、WS 重连周期） |
| 前置 | H-05 通过 |
| 步骤 | 1. 全套运行 72 h；2. 每天采样 3 次 ingestion stats + 设备状态；3. 记录 WS reconnection 次数；4. 最终比对 device_id/boot_id 是否变化 |
| 通过 | 内存增长线性趋势可接受（ESP32 heap 检查）；WS reconnection ≤ 每日 2 次；无 panic；integrity_errors = 0 |
| 不通过 | heap 持续下降；panic 重启；WS 频繁掉线 |
| 风险 | 供电；长时间无人值守 |

### H-09 — 40+ 目录轮询覆盖时间

| 项 | 内容 |
|---|---|
| 目的 | 确认目录 ≥48 条 channel 时，8 channel 轮转的完整周期 |
| 前置 | H-02 通过；rotationHz 已知 |
| 步骤 | 1. 固件设 8 channel；2. 产品层准备 48 条目录（A–F 各 8）；3. rotationHz 实测值下算完整轮询时间 = 目录数 / rotationHz；4. 如需多轮（8 channel 一批），按批次算 |
| 通过 | 完整轮询时间 ≤ N min（N 待定义，建议 ≤ 5 min） |
| 不通过 | 目录覆盖一轮超过 10 min（可能超过业务窗口） |
| 风险 | rotationHz 实测偏低；GROUP 模式 hop 数多 |

### 硬件验证通过门禁

| # | 必须通过 | 说明 |
|---|---|---|
| 1 | H-01 / H-02 | C5 扫描能力底线 |
| 2 | H-03 / H-04 | UART + WS uplink 数据链底线 |
| 3 | H-05 | 并行稳定性底线 |
| 4 | H-07 | 离线/重连语义（RF 页面依赖） |

> **未通过 H-01 / H-02 / H-03 / H-04 任何一项，不得进入后续硬件验证。**

---

## 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 未自动执行任何硬件测试。
- 本文件状态：`CONDITIONAL PASS`（静态可行性完成；实测 NOT VERIFIED）。
