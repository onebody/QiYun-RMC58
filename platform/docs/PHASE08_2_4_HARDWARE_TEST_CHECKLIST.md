# Phase 08.2.4 — 硬件测试操作清单（仅静态）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.4（硬件与协议能力审计 · Fast-Track）
> **Version**: 1.0.0
> **Created**: 2026-11-05
> **Last Modified**: 2026-11-05
> **Status**: `PROPOSED — PENDING HUMAN APPROVAL AND EXECUTION`
> **Baseline**: NO — 未纳入正式有效基线
> **Version History**: 1.0.0 (2026-11-05) 初版，整理 H-01–H-09 操作步骤 + 通过判定 + 证据归档格式
> 性质：**操作清单。不执行任何操作。**

---

## 1. 执行前确认（必须全部 Yes）

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 本清单经人工批准执行 | □ 是 / □ 否 |
| 2 | H-01 测试用 C5 head 有明确设备标识（mac/serial） | □ 是 / □ 否 |
| 3 | S3 relay 有明确 gateway_id（编译常量或 NVS 内已写入） | □ 是 / □ 否 |
| 4 | Site Server 有测试数据库（不影响生产） | □ 是 / □ 否 |
| 5 | Wi-Fi AP 可承载测试设备；Site Server 可达 | □ 是 / □ 否 |
| 6 | USB 供电充足 | □ 是 / □ 否 |
| 7 | 串口工具已安装：`pio device monitor` / `minicom` / Python pyserial | □ 是 / □ 否 |
| 8 | PlatformIO 编译 `env:esp32c5-head` 和 `env:esp32s3-relay` 均可通过 | □ 是 / □ 否 |

> 任何一项 No，停止对应 H 项；不继续向下执行。

---

## 2. H-01 — C5 单节点启动 & 自检

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | `cd repo/firmware && pio run -e esp32c5-head -t upload` | 编译 OK；刷写 OK | □ |
| 2 | `pio device monitor -b 115200`（或 921600） | 串口有输出 | □ |
| 3 | 启动日志中应看到 scanner_init() / main loop 进入 | 无 panic；无 watchdog reset | □ |
| 4 | 至少 30 s 无 crash | 运行稳定 | □ |
| 5 | 截图保存 | 启动日志截图 | □ |

**通过判定**：1–4 全部 Yes，且截图归档。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H01_boot_log.txt` + `.png`

---

## 3. H-02 — C5 RF 扫描

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | 用 Python pyserial 读 C5 head UART TX 921600 | 收到二进制帧 | □ |
| 2 | 解码（或用现有 Agent frame_parser 片段） | 能解析帧类型 | □ |
| 3 | 连续读 5 min；统计帧类型计数 | METRICS / LEVELS / SPECTRUM 都有；METRICS 速率 ≥ 10 Hz | □ |
| 4 | METRICS 帧 hop_channel_t[] 含 rssi_cdb、channel_index | 8 channel 全部填充 | □ |
| 5 | device_timestamp_ms 持续增长 | 每次采样 > 上一次 | □ |

**通过判定**：1–5 全部 Yes。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H02_frame_stats.txt`

---

## 4. H-03 — UART 921600 透传

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | 编译刷写 S3 relay：`pio run -e esp32s3-relay -t upload` | 刷写 OK | □ |
| 2 | C5 head UART TX → S3 relay UART RX；GND 共地 | 硬件连通 | □ |
| 3 | S3 relay 先不上网（跳过 STA）；C5 head 跑扫描 30 min | 稳定运行 | □ |
| 4 | 在 S3 relay UART RX 侧读数据，统计帧计数 | relay RX 帧计数 ≈ C5 TX 帧计数 | □ |
| 5 | 丢帧率 = (TX - RX) / TX | < 1% | □ |

**通过判定**：丢帧率 < 1%。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H03_uart_drop_rate.txt`

---

## 5. H-04 — S3 relay → Site Server WS uplink

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | Site Server 正常启动（dev 模式：`uvicorn site_server.main:app --reload`） | 服务可达；WS endpoint ready | □ |
| 2 | Site Server 给 relay 注册 gateway_id + credential（环境变量或 DB API） | 创建成功 | □ |
| 3 | S3 relay 通过 `net_prefs` 配 SSID/密码/WS URL | relay 重启后用上 | □ |
| 4 | relay 上电；观察串口日志 | STA 连接 OK；WS 连接 OK；auth OK | □ |
| 5 | Site Server 控制台看 `get_ingestion_stats()` | `persisted > 0`；`duplicate_events` 合理；`integrity_errors = 0` | □ |
| 6 | `curl /api/v1/measurements` 或 DB 直查 | 有新行；frame_type=METRICS | □ |

**通过判定**：persisted > 0 且 integrity_errors = 0。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H04_site_server_log.txt` + ingestion stats

---

## 6. H-05 — 两芯片并行稳定性（2 h）

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | 全套接好（C5 + S3 + AP + Site Server） | C5 扫描；S3 上网 | □ |
| 2 | 运行 2 h，每 10 min 记录一次：WS reconnection 次数、ingestion stats C5 是否 panic、relay 是否重启 | 每 10 min 采一次 | □ |
| 3 | 最终汇总：内存增长（ESP32 heap_info）、WS reconnection 总数 | heap 无持续下降 | □ |

**通过判定**：WS reconnection ≤ 1 次；无 panic；integrity_errors = 0。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H05_parallel_stability.txt`

---

## 7. H-06 — 多节点接入（2–4 对）

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | 每对 relay+head 独立配 gateway_id / SSID / WS URL | 独立身份 | □ |
| 2 | Site Server 逐一添加每对 relay 的凭证 | credential 全部创建 | □ |
| 3 | 全部上电；观察 Site Server WS 连接日志 | 所有 Agent 同时连接 | □ |
| 4 | ingestion stats 按 N 倍速率增长 | 正确 | □ |
| 5 | 30 min 后检查 device 列表 / 测量分布 | 各 relay 的设备都出现 | □ |

**通过判定**：所有 Agent 连接成功；无 duplicate_events 异常；设备列表齐全。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H06_multi_node.txt`

---

## 8. H-07 — 离线/重连

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | S3 relay 断电 30 s | Site Server 标记 offline（45 s 后） | □ |
| 2 | 上电 relay | Site Server 恢复 online；设备状态恢复 | □ |
| 3 | AP 断电 2 min → 恢复 | relay 指数退避后重连 | □ |
| 4 | C5 head 手动重启（按住 RST） | Site Server 看到新 device_boot_id；旧序号不污染 | □ |

**通过判定**：离线/重连语义正确；boot_id 变化；无 integrity_errors。D01 stale device status **在此暴露**——若设备不标记为 stale/offline，记录为 `NOT PASS`（属 D01 问题，不是 H-07 问题）。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H07_offline_reconnect.txt`

---

## 9. H-08 — 72 h 长稳

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | H-05 通过后全套运行 72 h | 无人值守 | □ |
| 2 | 每天采样 3 次：heap_info、WS reconnection 计数、ingestion stats | 每日 3 份快照 | □ |
| 3 | 最终比对：integrity_errors 总数、WS reconnection 总数、heap 终值 | 无显著增长趋势 | □ |

**通过判定**：heap 无持续下降（允许周期性波动）；WS reconnection ≤ 每日 2 次；integrity_errors = 0。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H08_72h_stability.txt` + 每日快照

---

## 10. H-09 — 40+ 目录轮询覆盖时间

| 步骤 | 操作 | 预期 | 记录 |
|---|---|---|---|
| 1 | H-02 测得 rotationHz 实际值 | rotationHz = ? | □ |
| 2 | 产品层目录表（A–F × 各 8 条）→ 48 条 channel | 目录长度 = 48 | □ |
| 3 | 完整轮询时间 = ceil(目录数 / 8) × (channel_count / rotationHz) 秒；或 GROUP 模式 LO 数估算 | 公式结果 | □ |
| 4 | 手动启动目录枚举场景；观察 Site Server Measurement 里 hop_channel_t 的 channel_index 覆盖变化 | 覆盖所有 8 channel | □ |

**通过判定**：完整轮询时间 ≤ 5 min（待人工调整阈值）。

**证据归档位置**：`docs/evidence/PHASE08_2_4/hw/H09_cycle_time.txt`

---

## 11. 证据归档规范

| 类型 | 路径 | 格式 |
|---|---|---|
| 串口日志 | `docs/evidence/PHASE08_2_4/hw/HXX_*.txt` | 纯文本；保留所有行 |
| 截图 | `docs/evidence/PHASE08_2_4/hw/HXX_*.png` | 1920×1080 或原始分辨率 |
| 统计 | `docs/evidence/PHASE08_2_4/hw/HXX_*.json` | JSON 格式（ingestion stats、帧计数等） |
| 测试结果汇总 | `docs/evidence/PHASE08_2_4/hw/INDEX.md` | 汇总表 |

> 与 R1 `docs/evidence/PHASE08_2_3/` 分离；Phase 08.2.4 独立目录。

---

## 12. 风险说明

| 风险 | 等级 | 缓解 |
|---|---|---|
| USB 供电不足导致 ESP32 反复重启 | 中 | 用独立 5V 2A 电源；不依赖 MacBook 直连 |
| UART 波特率/引脚错误 | 中 | 先在 H-01/H-02 确认；固件 `UART_LINK_BAUD=921600` |
| ESP32-C5 首次刷机需特殊下载模式 | 高 | 按住 BOOT + 点 RST + 释放 BOOT 后刷写 |
| Site Server / 凭证未配导致 WS connect 失败 | 中 | 先配凭证再上电 |
| AP 信号弱导致 Wi-Fi 掉线 | 中 | 距离近（< 5m）；避免金属遮挡 |
| 长时间无人值守测试 | 中 | 选择实验室环境；避免断电风险 |

---

## 13. 变更声明

- 未修改任何产品代码、数据库、数据库迁移、固件、设备协议、REST API、WebSocket、前端页面、运行配置。
- 未自动执行任何硬件测试。
- 本文件状态：`PROPOSED — PENDING HUMAN APPROVAL AND EXECUTION`。
