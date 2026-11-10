# Phase 08.2.4-HW — 硬件实测准备报告

> 版本：v1 | 日期：2026-07-05
> 作者：静态审计（无硬件操作）
> 边界：**不修改任何产品代码/固件/DB/协议/API/前端**；**不自动刷写/自动上电/自动串口操作**

---

## 0. 阶段状态

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 08.2 | CONDITIONAL PASS | 架构、安全、前端、集成全部静态通过 |
| Phase 08.2.3 | CONDITIONAL PASS | 决策收口、版本冻结、ADR 零冲突 |
| Phase 08.2.4 静态审计 | CONDITIONAL PASS | 7 份文档已交付 |
| **Phase 08.2.4-HW 硬件实测** | **NOT VERIFIED** | 本阶段目标 |

---

## 1. 已确认的硬件事实（100% VERIFIED）

以下事实全部来自源代码直接读取或 Espressif 官方数据手册，**无推断**。

### 1.1 板卡型号

| 角色 | PlatformIO board | 实际硬件名称 | 来源 |
|------|-----------------|--------------|------|
| C5 head | `esp32-c5-devkitc-1` | ESP32-C5-DevKitC-1 | `platformio.ini` env:esp32c5-head |
| S3 relay | `esp32-s3-devkitc-1` | ESP32-S3-DevKitC-1 Micro | `platformio.ini` env:esp32s3-relay + main.c 注释 |

### 1.2 固件构建环境

| env | board | build_flags | 用途 | 来源 |
|-----|-------|-------------|------|------|
| esp32c5-head | esp32-c5-devkitc-1 | `-DRX_TARGET_C5=1 -DUART_LINK_BAUD=921600` | C5 RF 扫描头（无 Wi-Fi 运行） | platformio.ini line 59-68 |
| esp32s3-relay | esp32-s3-devkitc-1 | `-DRX_TARGET_S3=1 -DRX_WIFI_RELAY=1 -DUART_LINK_BAUD=921600` | S3 relay（Wi-Fi STA + WS uplink，不跑 scanner） | platformio.ini line 31-45 |

构建框架：ESP-IDF 5.5.5（pioarduino 55.03.312-1），PlatformIO Core ≥ 6.2

### 1.3 UART 引脚（硬编码）

| 连接 | C5 GPIO | S3 GPIO | 证据 |
|------|---------|---------|------|
| C5 TX → S3 RX | **11**（U0TXD） | **44**（U0RXD） | `uart_link.c` line 12, 16；Espressif 数据手册确认默认 pin |
| C5 RX ← S3 TX | **12**（U0RXD） | **43**（U0TXD） | 同上 |
| GND → GND | 任意 GND | 任意 GND | UART 单端信号必须共地 |

UART 参数：**921600, 8N1, 无硬件流控**（两边 build_flags `-DUART_LINK_BAUD=921600`）

pin 可覆盖性：
- C5 head：UART0 pin **硬编码 GPIO11/12，不可运行时覆盖**
- S3 relay：默认 GPIO43/44，可通过 `NET UART <tx> <rx>` 命令覆盖（需排除 GPIO 19/20 USB 和 GPIO 26-32 SPI flash）

### 1.4 启动流程

**C5 head**（main.c line 129-141）：
```
NVS init → gpio_out_init → status_led_init → link_init → esp_event_loop_create_default
→ esp_wifi_init → esp_wifi_set_mode(WIFI_MODE_NULL) → esp_wifi_start
→ radio_init → rh_init → scanner_init → commands_task_start
```
- Wi-Fi 只给 PHY 用（WIFI_MODE_NULL），不跑 STA/SOFTAP
- `link_init()` 自动调 `uart_link_init()`（line 112）→ UART0 在 GPIO11/12 打开

**S3 relay**（main.c line 98-127）：
```
NVS init → gpio_out_init → status_led_init → link_init → esp_event_loop_create_default
→ net_prefs_load → rh_init → ws_link_open → uart_link_open(prefs.tx, prefs.rx)
→ relay_pipe_start → commands_task_start
→ (如果 net_prefs.on && ssid && url) ws_link_start(&prefs)
```
- 不跑 `scanner_init()`（main.c 注释：一个 PHY，STA 要占 AP 信道）
- Wi-Fi STA + WS uplink 由 ws_link.c 管理

### 1.5 硬件参数

| 参数 | 实际值 | 来源 |
|------|--------|------|
| Flash | 4 MB（全部 env） | platformio.ini board_build.flash_size=4MB |
| SPI Flash | dio 80 MHz | sdkconfig |
| CPU | C5: 单核 240 MHz; S3: 双核 240 MHz | sdkconfig CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y |
| Brownout LVL | 7（两边一致） | sdkconfig |
| USB | USB Serial/JTAG（两边） | sdkconfig CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y |
| console | NONE → printf 走 USB Serial/JTAG | sdkconfig.defaults |

### 1.6 NET 命令接口（relay 分支）

| 命令 | 作用 | 存储 |
|------|------|------|
| NET SSID/PASS/URL/ID | 配置参数 | RAM 只（PASS 不落 NVS） |
| NET UART tx rx | 覆盖 UART pin | RAM + 立即生效 |
| NET ON/OFF | 自动连接开关 | 立即写 NVS |
| NET CONNECT | 启动 STA + WS | 运行时 |
| NET DISCONNECT | 停止连接 | 运行时 |
| NET SAVE | RAM → NVS 持久化 | NVS |
| NET RESET | 清空 NET NVS | NVS namespace "net" |
| NET STATUS | 打印完整 JSON | 只读 |

### 1.7 WS 链路参数

| 参数 | 值 | 来源 |
|------|-----|------|
| 重连退避 | 500ms → 30000ms（指数） | ws_link.c line 28-29 |
| 握手超时 | 4000 ms | ws_link.c line 31 |
| 接收超时 | 200 ms | ws_link.c line 30 |
| relay_pipe poll | 20 ms, 1 KB chunk | relay_pipe.c line 14-15 |
| WS 下行无连接时 | drain UART + 累加 lost_down | relay_pipe.c line 44-46 |

### 1.8 RF 扫描硬限制

| 项目 | 值 | 来源 |
|------|-----|------|
| 最大 channel 数 | **8**（编译期 PROFILE_MAX_CHANNELS） | profile.h:8, planner.c:66 |
| 实际物理 RF 通道 | 1（ESP32-C5 单 LO 接收） | ESP32-C5 数据手册 |
| GROUP 模式 | 40 MHz span 一个 LO 覆盖多 channel | planner.c |
| rotationHz 上限 | 1050 Hz（MAX_ROTATION_HZ） | scanner.c |
| RSSI 单位 | **dBFS / centi-dBFS**，无校准，无跨设备可比性 | frames.h, code review |

---

## 2. 需要人工确认的事项（非代码可判定）

以下项目需要用户拿到实际硬件后人工确认或操作：

| # | 事项 | 为什么需要人工 | 确认方法 |
|---|------|----------------|----------|
| M1 | 实际拿到的 C5 开发板型号 | 仓库 platformio.ini 声明 esp32-c5-devkitc-1，但 CLAUDE.md 也提到 XIAO ESP32-C5；两者 UART0 默认 pin 相同（GPIO11/12），但丝印位置可能不同 | 开箱看板上丝印 + USB VID/PID；查板上的 silkscreen "TX"/"RX" 排针位置 |
| M2 | 实际拿到的 S3 开发板型号 | 同上，esp32-s3-devkitc-1 Micro vs 其他变体 UART0 默认 pin 一致（GPIO43/44），但排针顺序可能不同 | 查板上 "TX"/"RX" 丝印；**以丝印为准，不按排针序号数** |
| M3 | 两块板的 USB 口枚举顺序 | 两块同时插 USB 时 /dev/cu.* 顺序不稳定 | 逐块插 USB → `ls /dev/cu.*` → 对应关系记录 |
| M4 | AP 信号强度 | 代码无法知道物理环境 | 手机 Wi-Fi 信号强度实测 |
| M5 | 独立 5V 电源是否必要 | 需实测扫描 + STA 并行时电脑 USB 口电流 | 万用表串联测电流峰值；或先用电脑 USB，看会不会 brownout |
| M6 | VTX 测试设备是否可用 | 用于验证 Test 01 RSSI 有意义 | 如果没有 VTX，用 USB 信号发生器或跳过"有信号 RSSI 高于底噪"的验证项 |

---

## 3. UNKNOWN 清单（静态审计无法确认）

| # | 项目 | 未知原因 | 影响 | 处理 |
|---|------|----------|------|------|
| U1 | ESP32-S3 Wi-Fi STA 关联峰值电流 | 无实测数据 | Test 04 并行稳定性可能受电源限制 | Test 04 用独立 5V/2A 电源 |
| U2 | 两块板同时 RF + STA 峰值电流 | 无实测数据 | 同上 | 同上 |
| U3 | C5 board 是否有射频前端功放 | 代码未引用，板上如有则自动启用 | 不影响软件逻辑，影响 RF 灵敏度 | 看板上是否有 PA 芯片丝印；不阻塞测试 |
| U4 | C5 board 是否有板载 PCB 天线 | DevKitC-1 通常有，但具体批次不确定 | 影响扫描距离和灵敏度 | 不阻塞测试；USB 距离测试 |
| U5 | Site Server 端 device_boot_id 实现 | 需看 ingestion 代码 | Test 05.6 验证项 | Test 05 时查 Site Server Measurement 表字段 |
| U6 | gateway-agent 与 S3 relay 拓扑 | relay 直连 Site Server vs gateway-agent 中转未明确 | Test 06 多 node 设计 | 架构确认 |

---

## 4. 可以立即执行的测试（不依赖 Wi-Fi / Site Server）

以下两项测试**不依赖 AP 或 Site Server**，硬件接线和固件刷完后就能跑，建议最先执行：

| 优先级 | 测试 | 依赖 | 验证目标 |
|--------|------|------|----------|
| **1** | Test 01 C5 单节点扫描 | 仅 C5 head + USB | RF 扫描核心能力是否正常 |
| **2** | Test 02 C5→S3 UART 透传 | C5 + S3 + UART 3 线 + 共地 | UART 921600 数据链路是否通 |

**为什么先跑这两个**：
- Test 01 可以验证 C5 固件能否独立扫描出有意义的 RF 数据——如果这步不过，后面所有测试都没意义
- Test 02 可以验证 C5↔S3 的 UART 桥接是否通——如果 UART pin 接反或波特率不匹配，Test 03/04/05 全部会卡住
- 这两个过了之后，Test 03（Wi-Fi）、Test 04（并行）、Test 05（恢复）才有基础

---

## 5. Build 命令清单（Verified）

```bash
cd repo/firmware

# C5 head（第一次刷）
pio run -e esp32c5-head -t upload

# S3 relay（第一次刷）
pio run -e esp32s3-relay -t upload

# 验证固件版本
pio device monitor -b 115200 -p /dev/cu.usbserial-C5   # 手动在终端输入 INFO
pio device monitor -b 115200 -p /dev/cu.usbserial-S3   # 手动在终端输入 INFO

# relay 首次 NET 配置（刷完后 NVS 是空的）
NET SSID <AP名称>
NET PASS <AP密码>
NET URL ws://<site-host>:<port>/ws/v1/agents/connect
NET ID relay-test-01
NET ON
NET SAVE
NET CONNECT
# 观察 NET STATUS 变成 sta:true, ws:true
```

**刷写注意事项**：
- 两块板 USB 同时插时逐块刷，**确认哪个串口对应哪块再刷**
- C5 rev v1.0 已知 RTS reset 卡死 bug：固件 main.c 已修；手动 BOOT+EN 可进入下载模式
- pio upload 会自动 usb-reset，通常不需要按 BOOT
- 固件启动**默认 RH（lap timer）模式，silent 无输出**——发 `PROTOCOL MONITOR` 才开始输出扫描帧

---

## 6. 接线风险 TOP 5（必须避免）

| # | 风险 | 概率 | 后果 | 预防 |
|---|------|------|------|------|
| **R1** | UART TX↔RX 接反 | 高（接线常见错误） | C5 head 正常扫描，relay NET STATUS head_ready=false，head_bytes_up 永远 0 | 接线前对照 HW_WIRING_AND_SETUP.md 第 10 节 pinout；上电前万用表测 |
| **R2** | 未共地 | 中 | UART 信号浮动，relay pipe 收到垃圾帧或无帧 | 接一根 GND 线 |
| **R3** | 刷错 env | 中 | esp32c5 刷成 esp32c5-head 没问题（只是 baud 不同），但 esp32c5-head 刷成 esp32c5 会导致 UART0 跑 115200 → relay 收到噪声；esp32s3-relay 刷成 esp32s3 会完全没有 WS STA 功能 | 刷完后 INFO 命令确认 target；relay NET STATUS 看 head_baud 应该是 921600 |
| **R4** | 电源不足 brownout | 低→中（取决于电脑 USB 口） | S3 重启、WS 断连、scan 中断 | 先用电脑 USB 试，如果 relay backoff_ms 持续升或板重启，换独立 5V/2A 电源 |
| **R5** | relay UART pin 改到 USB/JTAG 或 flash | 低（需要手动命令输入错误 pin） | 崩溃或 silent | 不要用 19/20 (USB) 或 26-32 (flash)；relay head_pin_ok() 会拒绝 |

---

## 7. 本阶段不做的事

以下操作在本阶段**被明确禁止**，必须等待用户授权：

- [ ] 自动刷写固件（用户手动）
- [ ] 自动上电（用户手动）
- [ ] 自动串口操作（脚本只是参考，用户手动跑）
- [ ] 修改固件源码（保持冻结）
- [ ] 修改 NVS 以外的任何配置（除了 relay 首次 NET 配置）
- [ ] 修改 Site Server / gateway-agent / Web UI
- [ ] 创建任何产品代码分支
- [ ] 自动进入 Phase 08.3

---

## 8. 交付清单

本阶段新增 4 份文档：

| # | 文件 | 内容 | 状态 |
|---|------|------|------|
| 1 | [PHASE08_2_4_HW_WIRING_AND_SETUP.md](~/workspace/FPV_space/widscan58-clone/docs/PHASE08_2_4_HW_WIRING_AND_SETUP.md) | 硬件清单、UART pin 对照表、NET 命令接口、上电检查 | ✅ 已交付 |
| 2 | [PHASE08_2_4_HW_TEST_RUNBOOK.md](~/workspace/FPV_space/widscan58-clone/docs/PHASE08_2_4_HW_TEST_RUNBOOK.md) | 6 项测试完整步骤、通过判定、失败诊断 | ✅ 已交付 |
| 3 | [PHASE08_2_4_HW/README.md](~/workspace/FPV_space/widscan58-clone/docs/evidence/PHASE08_2_4_HW/README.md) | 测试记录模板 + 归档结构 | ✅ 已交付 |
| 4 | 本报告（PREPARATION_REPORT） | 全部核查汇总 | ✅ 已交付 |

**不修改任何既有文档**（上一轮 Phase 08.2.4 静态审计的 7 份文档保持不动）

---

## 9. 下一步行动（由用户决定）

### 9.1 用户需要做的事

1. **确认拿到的硬件型号**：对照第 2 节 M1/M2，看板上丝印
2. **刷写两块板**：对照第 5 节 Build 命令，用户手动刷
3. **接 UART 线 + 共地**：对照 HW_WIRING_AND_SETUP.md 第 3/10 节
4. **Test 01 + Test 02**：先跑这两个，记录证据到 evidence/PHASE08_2_4_HW/
5. **配置 relay NET 参数**（Test 03 前）：SSID/PASS/URL
6. **Test 03 + Test 04 + Test 05**：逐项执行
7. **Test 06**：设计核对，不刷硬件
8. **回传证据**：把测试日志文件放回 evidence/PHASE08_2_4_HW/，更新 README.md 里的结论栏

### 9.2 本阶段到此停止

**不自动进入 Phase 08.3**。等待用户实测结果后，再根据 PASS/CONDITIONAL PASS/FAIL 状态决定下一步。

### 9.3 冻结声明

| 冻结对象 | 冻结范围 | 解冻条件 |
|----------|----------|----------|
| 固件代码 | repo/firmware/src/ | Phase 08.2.4-HW 全部 6 项测试通过后 |
| 固件配置 | sdkconfig*, platformio.ini | 同上 |
| Site Server | site-server/src/ | 同上 |
| gateway-agent | gateway-agent/src/ | 同上 |
| 协议 | frames.h, uart_link.h/c, ws_link.h/c | 同上 |
| 数据库 | alembic/versions/ | 同上 |
| Web UI | web/src/ | 同上 |

---

**先让真实硬件跑通，再开始平台功能开发。**
