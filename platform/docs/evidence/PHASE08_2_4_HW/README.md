# Phase 08.2.4-HW — 测试记录

> 本目录存放 Phase 08.2.4 硬件实测阶段的原始证据。
> 每条记录必须有真实日志才能判定 PASS。
> 没有真实测试证据的项目 **必须保持 NOT VERIFIED**。

---

## 测试总结

| 测试 | 日期时间 | 硬件型号 | 固件版本 | 接线方式 | 测试条件 | 原始日志 | 结果 | 异常 | 结论 |
|------|----------|----------|----------|----------|----------|----------|------|------|------|
| Test 01 C5 单节点扫描 | — | — | — | 仅 C5 head USB | — | `test01_usb_monitor.log` | — | — | **NOT VERIFIED** |
| Test 02 C5→S3 UART 透传 | — | — | — | C5↔S3 UART 3 线 + 共地 | relay 不连 WS | `test02_*.log` | — | — | **NOT VERIFIED** |
| Test 03 S3 Wi-Fi + WS uplink | — | — | — | 仅 S3 relay USB + AP + Site Server | relay config NVS | `test03_*.log` | — | — | **NOT VERIFIED** |
| Test 04 扫描与网络并行 | — | — | — | 全部三件套 + AP + Site Server | 2 小时连续 | `test04_*` | — | — | **NOT VERIFIED** |
| Test 05 节点异常恢复 | — | — | — | 全部三件套 | Wi-Fi 断/弱/S3 断电/C5 断电/UART 断 | `test05_*.log` | — | — | **NOT VERIFIED** |
| Test 06 多节点扩展准备 | — | — | — | 设计类，不刷硬件 | 代码 + ADR 文档核对 | `test06_multi_node_design.md` | — | — | **NOT VERIFIED** |

**结论值域**（只能用以下四个之一）：
- **PASS** — 所有通过判定条件满足，有真实证据
- **CONDITIONAL PASS** — 核心条件满足但有非关键限制或证据不完整
- **FAIL** — 关键条件不满足
- **NOT VERIFIED** — 未测试、无真实证据

---

## Test 01 — C5 单节点扫描 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| C5 开发板型号 | ESP32-C5-DevKitC-1（VERIFIED） |
| C5 固件 env | esp32c5-head（VERIFIED） |
| C5 固件版本 | `git describe` = ____ |
| flash size | 4 MB |
| CPU freq | 240 MHz |
| USB 供电 | 电脑 USB / 独立 5V |
| 辅助设备 | 无 / VTX 发射 ____ MHz |

### 原始数据

| 指标 | 实际值 | 预期值 | 判定 |
|------|--------|--------|------|
| INFO 返回 target | — | esp32c5 | — |
| rotationHz（METRICS RSSI） | — | ≥ 300 Hz | — |
| rotationHz（METRICS FULL） | — | ≥ 100 Hz | — |
| heap free（STATUS?） | — | ≥ 30 KB | — |
| largest free block | — | ≥ 10 KB | — |
| 运行时长 | — 分钟 | ≥ 5 分钟 | — |
| panic 次数 | — | 0 | — |
| 重启次数 | — | 0 | — |
| RSSI 有信号差异（有 VTX 时） | — | 明显高于底噪 | — |

### 异常记录

- [ ] 无异常
- [ ] ____

### 日志文件

| 文件 | 内容 |
|------|------|
| `test01_usb_monitor.log` | C5 USB Serial/JTAG monitor 完整输出 |
| `test01_status.txt` | STATUS? 快照 |
| `test01_frames.txt` | widscan58cli.py 解析的帧（可选） |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：____
```

---

## Test 02 — C5→S3 UART 透传 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| C5 head 固件版本 | `git describe` = ____ |
| S3 relay 固件版本 | `git describe` = ____ |
| UART 接线 | C5 GPIO11→S3 GPIO44, C5 GPIO12→S3 GPIO43, GND 共地 |
| 波特率 | 921600 8N1（VERIFIED 两边 build_flags） |
| 运行时长 | ____ 分钟 |

### 原始数据

| 指标 | 实际值 | 预期值 | 判定 |
|------|--------|--------|------|
| relay head_ready | — | true | — |
| relay head_baud | — | 921600 | — |
| relay head_pins | — | "43/44" | — |
| head_bytes_up 10 分钟增量 | — KB | ≥ ____（C5 输出速率） | — |
| C5 重启后 relay 自动恢复耗时 | — 秒 | ≤ 10 秒 | — |
| head_ready 意外掉次数 | — | 0 | — |

### 异常记录

- [ ] 无异常
- [ ] ____

### 日志文件

| 文件 | 内容 |
|------|------|
| `test02_c5_usb.log` | C5 monitor 输出 |
| `test02_relay_usb.log` | relay monitor + NET STATUS 快照 |
| `test02_c5_reboot_recovery.log` | C5 重启 + relay 恢复过程 |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：____
```

---

## Test 03 — S3 Wi-Fi LAN + WS uplink 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| AP SSID | ____ |
| AP 加密 | WPA2-PSK / WPA3-SAE |
| AP 2.4 GHz 信号强度 | ____ dBm（手机实测） |
| Site Server 地址 | `http://____:____` |
| WS URL | `ws://____:____/ws/v1/agents/connect` |
| relay NET ID | ____ |
| 运行时长 | ____ 分钟（baseline 30 分钟） |

### 原始数据

| 指标 | 实际值 | 预期值 | 判定 |
|------|--------|--------|------|
| NET CONNECT 到 ws=true 耗时 | — 秒 | ≤ 15 秒 | — |
| sta=true 持续时间占比 | — % | ≥ 95% | — |
| ws=true 持续时间占比 | — % | ≥ 95% | — |
| 重连次数（排除人工断网） | — | ≤ 2 | — |
| 断网后自动恢复耗时 | — 秒 | ≤ 120 秒 | — |
| Site Server agent 连接 | — | 已建立 | — |
| ERR net_start / ERR net_ring | — | 0 | — |

### 异常记录

- [ ] 无异常
- [ ] ____

### 日志文件

| 文件 | 内容 |
|------|------|
| `test03_relay_usb.log` | relay monitor + NET STATUS 连续快照 |
| `test03_site_agent.log` | Site Server agent 连接日志 |
| `test03_reconnection.log` | 断网→恢复过程 |
| `test03_summary.md` | 30 分钟运行摘要 |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：____
```

---

## Test 04 — RF 扫描与 Wi-Fi 网络并行 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| 全部三件套接线 | 确认无误 |
| AP / Site Server | 同 Test 03 |
| C5 扫描配置 | CHANNELS ____ / ENABLE ____ / MODE GROUP / METRICS RSSI |
| 运行时长 | ____ 小时（≥ 2 小时） |
| 供电方式 | 电脑 USB / 独立 5V 电源 |

### 基线数据

| 指标 | C5-only (Test 04.1) | 并行运行中 | 下降率 | 判定 |
|------|---------------------|------------|--------|------|
| C5 rotationHz | — | — | — % | ≤ 10% = PASS |
| C5 heap largest | — | — | — | 保持稳定 = PASS |
| relay ws=true 占比 | — | — | — | ≥ 95% = PASS |

### Site Server measurement 连续性

| 指标 | 实际值 | 预期值 | 判定 |
|------|--------|--------|------|
| measurement 总数 | — | 持续增长 | — |
| gap > 30 秒次数 | — | ≤ 2 | — |
| 最大连续 gap | — 秒 | ≤ 60 秒 | — |

### 异常记录

- [ ] 无异常
- [ ] panic / 崩溃：____
- [ ] 板重启：____
- [ ] 电源不稳：____
- [ ] 丢帧：relay head_lost_down 增长 ____ KB（WS 下行时）

### 断电恢复测试

| 场景 | 恢复耗时 | 是否自动 | 判定 |
|------|----------|----------|------|
| S3 断电 10s → 恢复 WS | — 秒 | 自动 | — |
| C5 断电 10s → relay head_ready 恢复 | — 秒 | 自动 | — |
| 断电期间 Site Server gap | — 秒 | ≤ 15 秒（UART 断了 measurement 自然停） | — |

### 日志文件

| 文件 | 内容 |
|------|------|
| `test04_c5_only_baseline.txt` | C5-only 扫描基线 |
| `test04_relay_only_baseline.txt` | relay-only Wi-Fi 基线 |
| `test04_c5_status_series.log` | 2 小时内每 5 分钟 C5 STATUS? |
| `test04_relay_status_series.log` | 2 小时内每 5 分钟 relay NET STATUS |
| `test04_site_measurements.csv` | Site Server measurement 时间序列 |
| `test04_framerate_comparison.txt` | Python 帧速率脚本输出，baseline vs. 并行 |
| `test04_recovery.log` | 断电恢复过程 |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：____
核心指标（rotationHz 下降 ≤ 10%, ws=true ≥ 95%）：____
```

---

## Test 05 — 节点异常恢复 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| 前置 | Test 04 已通过，三件套稳定运行 |

### 逐项记录

| # | 场景 | 操作 | 恢复耗时 | 是否自动 | 现象 | 判定 |
|---|------|------|----------|----------|------|------|
| 5.1 | Wi-Fi 断 | 拔 AP 电源 | — s | 自动 | backoff 500→30000→500 | — |
| 5.2 | Wi-Fi 弱 | AP 远/金属柜 | — s | 自动 | RSSI 下降 / 可能掉 | — |
| 5.3 | S3 断电 30s | 拔 S3 USB | — s | 自动 | 起机→STA→WS | — |
| 5.4 | C5 断电 30s | 拔 C5 USB | — s | 自动 | head_ready 短暂 false→true | — |
| 5.5 | UART 断 10s | 拔 C5→S3 UART 线 | — s | 自动 | head_ready 短暂 false→true | — |
| 5.6 | device_boot_id | 重启 C5 × 2 | — | — | Site Server boot_id 变化? | — |

### 异常记录

- [ ] 无异常
- [ ] ____

### 日志文件

| 文件 | 内容 |
|------|------|
| `test05_summary.md` | 每项测试前后快照 + 结论 |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：____
恢复能力总结：____
```

---

## Test 06 — 多节点扩展准备 详细记录

### 基本信息

| 字段 | 值 |
|------|-----|
| 测试日期时间 | 2026-__-__ __:__ UTC+8 |
| 类型 | 设计类，不刷硬件 |
| 资料来源 | 固件代码 + gateway-agent 代码 + site-server 代码 + Phase 08.2 ADR 文档 |

### 设计核对结果

| 项目 | VERIFIED / NEEDS CONFIRMATION / GAP | 依据 |
|------|-------------------------------------|------|
| 多 node 身份（relay id） | VERIFIED | net_prefs_t.id[33]; NET ID 命令 |
| 多 node agent_id | VERIFIED | gateway-agent manager 支持多 device; Site Server per-agent ingestion |
| channel_index 隔离 | VERIFIED + GAP | channel_index 只是 0-7 数组; 查询时必须 agent_id + channel_index 联合 |
| RSSI 独立存储 | VERIFIED | Measurement 表 per-agent_id |
| 时间戳对齐 | GAP（限制） | 每个 C5 free-running device_timestamp_ms; 无墙钟锚点; Phase 08.3+ 处理 |
| Gateway 拓扑 | NEEDS CONFIRMATION | relay 直连 Site Server vs gateway-agent 中转，需架构确认 |
| 每对刷写独立配置 | VERIFIED | 每对 relay 独立 NET ID + NET SAVE |

### 建议

- 首批部署：2 对 relay+head 并行
- 配置脚本：每对独立刷写后，`NET ID relay-s3-0<N>` + `NET SAVE`
- Site Server 查询：必须按 agent_id 分组
- Phase 08.3 前：时间戳对齐问题接受为已知限制

### 日志文件

| 文件 | 内容 |
|------|------|
| `test06_multi_node_design.md` | 完整核对记录 |

### 结论

```
PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED

判定理由：设计可扩展到多 node，有 2 项 GAP 接受为已知限制
```

---

## 全局异常与问题登记

| # | 日期 | 影响测试 | 问题 | 根因 | 解决 | 状态 |
|---|------|----------|------|------|------|------|
| 1 | — | — | — | — | — | — |
| 2 | — | — | — | — | — | — |

---

## 归档结构

```
docs/evidence/PHASE08_2_4_HW/
├── README.md                          ← 本文件（记录模板 + 结果汇总）
├── test01_usb_monitor.log             ← C5 head USB monitor 完整输出
├── test01_status.txt                  ← STATUS? 快照
├── test01_frames.txt                  ← widscan58cli.py 解析输出（可选）
├── test01_current.txt                 ← 电流实测（可选）
├── test02_c5_usb.log                  ← C5 monitor 输出
├── test02_relay_usb.log               ← relay monitor + NET STATUS 快照
├── test02_c5_reboot_recovery.log      ← C5 重启 + relay 恢复
├── test03_relay_usb.log               ← relay NET STATUS 连续快照
├── test03_site_agent.log              ← Site Server agent 日志
├── test03_reconnection.log            ← 断网→恢复
├── test03_summary.md                  ← 30 分钟摘要
├── test04_c5_only_baseline.txt        ← C5-only 扫描基线
├── test04_relay_only_baseline.txt     ← relay-only Wi-Fi 基线
├── test04_c5_status_series.log        ← 2 小时内 C5 STATUS?
├── test04_relay_status_series.log     ← 2 小时内 relay NET STATUS
├── test04_site_measurements.csv       ← Site Server measurement 序列
├── test04_framerate_comparison.txt    ← Python 帧速率对比
├── test04_recovery.log                ← 断电恢复
├── test05_summary.md                  ← 节点异常恢复汇总
└── test06_multi_node_design.md        ← 多 node 设计核对
```

---

**无真实证据的测试项必须保持 NOT VERIFIED。本 Phase 到此停止，等待人工操作后再更新结果。**
