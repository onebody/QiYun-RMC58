# Phase 08.2.4-HW — 硬件接线与上电手册

> 版本：v1 | 日期：2026-07-05
> 范围：ESP32-C5 RF 扫描头 + ESP32-S3 Wi-Fi relay 主控
> 代码基址：`repo/firmware/`（ESP-IDF 5.5.5 + PlatformIO）
> **未经用户授权，本手册不授权任何自动刷写、自动上电、自动串口操作**

---

## 1. 硬件清单（全部 VERIFIED）

| # | 物品 | 型号 / 规格 | 用途 | 来源 |
|---|------|-------------|------|------|
| 1 | RF 扫描头 | **ESP32-C5-DevKitC-1** | 5.8 GHz 频谱扫描、输出 METRICS/SPECTRUM/TELEMETRY 帧 | `platformio.ini` board=`esp32-c5-devkitc-1` |
| 2 | Wi-Fi relay | **ESP32-S3-DevKitC-1** | Wi-Fi STA 接入 AP、UART↔WS 桥接、Site Server 上行 | `platformio.ini` board=`esp32-s3-devkitc-1`，main.c 注释 "Micro board" |
| 3 | USB-C 数据线 × 2 | 至少 E-Marker ≥ 1A | 两块板各自独立供电 + USB Serial/JTAG 日志 + 刷写 | — |
| 4 | 公对公 UART 杜邦线 × 3 | TX/RX/GND | C5 head ↔ S3 relay 通信 | — |
| 5 | 可选独立 5V/2A 电源 × 1 | USB-C 或 DC 5.5×2.1 | 电脑 USB 口电流不足时使用 | — |
| 6 | Wi-Fi AP × 1 | **2.4 GHz WPA2-PSK（推荐）** | relay STA 接入目标 | sdkconfig.defaults.esp32s3-relay 虽开 WPA3-SAE，但实测用 WPA2-PSK 最稳 |
| 7 | 笔记本电脑 | Python ≥ 3.10 + pyserial + PlatformIO Core ≥ 6.2 | 刷写、monitor、采集日志、Site Server | `platformio.ini` pioarduino 55.03.312-1 要求 |

**未列出的项目（UNKNOWN / 非必须）**：
- 板载射频前端功放：代码未引用，板上如有则自动启用
- 外接天线：C5-DevKitC-1 板载 PCB 天线；如需外接 SMA 转接线请自行确认
- RF 电源表：可选，用于实测扫描电流

---

## 2. 固件环境标识（VERIFIED）

### 2.1 构建目标

| 板 | PlatformIO env | build_flags | 说明 |
|----|----------------|-------------|------|
| C5 head | `esp32c5-head` | `-DRX_TARGET_C5=1 -DUART_LINK_BAUD=921600` | 默认 head 目标，UART0 921600 |
| S3 relay | `esp32s3-relay` | `-DRX_TARGET_S3=1 -DRX_WIFI_RELAY=1 -DUART_LINK_BAUD=921600` | Wi-Fi STA + WS uplink |

### 2.2 刷写命令

```bash
cd repo/firmware

# C5 head
pio run -e esp32c5-head -t upload

# S3 relay
pio run -e esp32s3-relay -t upload
```

- `upload_speed = 921600`（platformio.ini）
- `board_upload.before_reset = usb-reset`（自动 RTS reset，通常无需按 BOOT）
- C5 rev v1.0 已知有 USB reset 卡死 bug（esp-idf#18089），固件已在 main.c 第 74-80 行重开 UART0 时钟修复；如果仍卡死，手动 BOOT + EN 组合进入下载模式

### 2.3 Monitor 命令

```bash
# C5 head
pio device monitor -b 115200 -p /dev/cu.usbserial-xxx  # 或 COMx

# S3 relay
pio device monitor -b 115200 -p /dev/cu.usbserial-yyy
```

- 两块板同时插 USB 时串口枚举顺序可能变，**必须人工确认哪个端口对应哪块板再刷写**
- monitor_speed 115200 是 USB Serial/JTAG 日志口；**不是 UART 通信波特率**

### 2.4 固件版本

```bash
cd repo/firmware
git describe --tags --always --dirty     # PROJECT_VER 运行时可通过 INFO 命令查询
```

启动后在 monitor 里发 `INFO` 可确认：`widscan58|esp32c5|proto|版本号|commit time|PHY version`

---

## 3. UART 接线（VERIFIED，硬编码 pin）

### 3.1 引脚对照表

| 连接 | ESP32-C5-DevKitC-1（head） | ESP32-S3-DevKitC-1（relay） |
|------|---------------------------|----------------------------|
| C5 TX → S3 RX | **GPIO 11**（U0TXD，板丝印 TX） | **GPIO 44**（U0RXD，板丝印 RX） |
| C5 RX ← S3 TX | **GPIO 12**（U0RXD，板丝印 RX） | **GPIO 43**（U0TXD，板丝印 TX） |
| GND → GND | 任意 GND | 任意 GND |

**证据**：
- `uart_link.c` 第 12-13 行 `#define UART_LINK_TX 11 / #define UART_LINK_RX 12`（C5 分支）
- `uart_link.c` 第 15-16 行 `#define UART_LINK_TX 43 / #define UART_LINK_RX 44`（relay 分支）
- ESP32-C5 数据手册："U0TXD 和 U0RXD 是 GPIO11 和 GPIO12 by default"
- ESP32-S3-DevKitC-1 用户指南：UART0 TX=GPIO43, RX=GPIO44

### 3.2 UART 参数（VERIFIED）

```
波特率：921600（两边 build_flags -DUART_LINK_BAUD=921600）
数据位：8
停止位：1
校验位：无
硬件流控：无（uart_link.c .flow_ctrl = UART_HW_FLOWCTRL_DISABLE）
软件流控：无
```

### 3.3 关于 UART pin 的可覆盖性（VERIFIED）

- **C5 head**：UART0 pin 硬编码 GPIO11/12，**不可在运行时覆盖**。`uart_link_init()` 直接用 `UART_LINK_TX/RX` 宏打开驱动。如果实际板卡 UART0 pin 不同，必须修改固件源码（本次冻结期间不允许）
- **S3 relay**：默认 GPIO43/44，但可通过 `NET UART <tx> <rx>` 命令在运行时覆盖（uart_link.c 第 122-137 行 `uart_link_open()`），并通过 `NET SAVE` 写入 NVS 持久化

### 3.4 relay UART pin 安全边界（VERIFIED）

`uart_link.c` 第 89-94 行 `head_pin_ok()` 排除以下 pin：
- GPIO 19 / 20：**native USB Serial/JTAG**（relay 的控制台口，不可占用）
- GPIO 26–32：**SPI flash / PSRAM**（板载 flash，不可占用）

**不要尝试把 relay 的 head UART 改到这些 pin 上**，会导致崩溃或无输出。

### 3.5 共地要求（VERIFIED）

**必须连接 GND → GND**。原因：
- UART TX/RX 是单端信号，需要公共参考电位
- 两块板各自 USB 供电时 GND 可能不共通（部分电脑 USB 端口地线不连）
- `uart_link.c` 第 54 行 C5 head 在 RX pin 开了内部 pull-up，relay 第 111 行同样——未上电的 head 会让 RX 线浮空，pull-up 避免误触发，但共地仍是必须

---

## 4. USB 供电（部分 VERIFIED，UNKNOWN 项已标记）

### 4.1 每块板的 USB 要求

```
电压：5V（USB 标准）
板载 LDO：ESP32-C5-WROOM-1 模块 3.3V / ESP32-S3-WROOM-1U 模块 3.3V
USB 电流（实测数据见 PLAN.md）：
  - C5 head idle：~90 mA（PLAN.md 2026-10-17 测试）
  - C5 head 扫描中（METRICS RSSI 1050 Hz 节流）：~93 mA
  - C5 head RF 全开（无节流）：~99 mA
  - S3 relay Wi-Fi STA active：典型 ~150-300 mA（UNKNOWN，需实测）
  - 两块板合计：典型 ≤ 500 mA；**并行扫描 + STA 可能瞬时到 600-800 mA**
```

### 4.2 供电建议

| 场景 | 供电方式 | 备注 |
|------|----------|------|
| 单 C5 head 测试 | 电脑 USB 口 | 足够 |
| 单 S3 relay 测试 | 电脑 USB 口 | 足够 |
| **两块板同时 + 扫描 + STA 并行** | **独立 5V/2A USB 电源 × 2**（或一个 5V/4A 电源分两路） | 电脑 USB 口单口通常限流 500 mA，可能导致瞬断重启 |

### 4.3 UNKNOWN 电流项

- ESP32-S3 Wi-Fi STA 关联 + 数据传输时的峰值电流：**UNKNOWN**
- 两块板同时 RF active + STA active 的总峰值电流：**UNKNOWN**
- 建议：上电前用万用表 DC 档串联在电源回路里实测

---

## 5. USB Serial/JTAG 日志口（VERIFIED）

### 5.1 硬件

- 两块板都有原生 **USB Serial/JTAG** 控制器
- C5: `CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y`, `CONFIG_ESP32C5_REV_MIN_100=y`（`sdkconfig.esp32c5-head`）
- S3: 同上（`sdkconfig.esp32s3-relay`）
- `main.c` 两者都设置 `console=NONE`（sdkconfig.defaults），**printf 输出走 USB Serial/JTAG**

### 5.2 驱动行为

- 插入 USB 后系统枚举为 `/dev/cu.usbserial-xxxx`（macOS / Linux）或 `COMx`（Windows）
- 波特率 115200 是 monitor 标准；实际上 USB Serial/JTAG 是 USB 2.0 full-speed，"波特率" 对其无物理意义，只是终端软件的配置字段
- C5 rev v1.0 固件启动时重开 UART0 时钟（main.c 第 79-80 行），但 USB Serial/JTAG 不走 UART0，所以 RTS reset 卡死 bug 只影响 UART0，不影响 USB 下载

### 5.3 串口枚举注意

- 两块板同时插 USB 时，macOS 的 `ls /dev/cu.*` 枚举顺序不稳定
- **必须逐块插 USB 确认端口号，再刷写**
- esptool / pio upload 也可以用 `--before no-reset` + 手动 BOOT+EN 组合避免 RTS reset

---

## 6. 上电前检查清单（逐项 PASS 才能上电）

### 6.1 物理检查

- [ ] C5 TX (GPIO11) → S3 RX (GPIO44)：**已接，无虚焊**
- [ ] C5 RX (GPIO12) ← S3 TX (GPIO43)：**已接，无虚焊**
- [ ] GND → GND：**已接，共地**
- [ ] 无裸露 GPIO 引脚互相短路
- [ ] 无 GPIO 接到 5V（全部是 3.3V 域）
- [ ] USB 方向正确（USB-C 正反可插，确认是数据口不是只充电口）
- [ ] UART 线 TX↔RX 已交叉，未直通

### 6.2 固件检查（刷写后）

- [ ] C5 head 刷的是 `esp32c5-head` env（不是 `esp32c5` env）
- [ ] S3 relay 刷的是 `esp32s3-relay` env（不是 `esp32s3` env）
- [ ] 两块板都刷完后，monitor 能看到启动输出（USB Serial/JTAG 正常）
- [ ] 无 `ERR boot ...` 错误（main.c BOOT_CHECK）

### 6.3 网络准备（仅 Test 03+ 需要）

- [ ] Wi-Fi AP 在 S3 附近信号强度良好（预期 RSSI > -60 dBm）
- [ ] AP 使用 **2.4 GHz WPA2-PSK**（最稳）
- [ ] Site Server 已在运行，且可从 AP 网络访问（`curl http://<site-host>:<port>/health`）
- [ ] relay 首次联网需要的 `ws://<site-host>:<port>/ws/v1/agents/connect` URL 已准备好

---

## 7. relay 首次配置流程（NET 命令接口）

relay 固件刷写后 NVS 是空的，默认：SSID="", URL="", on=false。需要通过 USB Serial/JTAG 写入：

```
NET SSID <你的AP名称>
NET PASS <你的AP密码>
NET URL ws://<site-host>:<port>/ws/v1/agents/connect
NET ID <relay唯一名称，如 relay-s3-01>
NET SAVE
NET CONNECT
```

**验证**：`NET STATUS` 应该返回 JSON，其中 `"sta":true,"ws":true`。

**完整 NET 命令列表**（来自 commands.c 第 712-812 行）：

| 命令 | 作用 | RAM/NVS |
|------|------|---------|
| `NET SSID <ssid>` | 设置 AP 名称 | RAM 只 |
| `NET PASS <password>` | 设置 AP 密码 | RAM 只 |
| `NET URL <ws_url>` | 设置 WS 连接地址 | RAM 只 |
| `NET ID <name>` | 设置节点显示名 | RAM 只 |
| `NET UART <tx> <rx>` | 覆盖 head UART pin | RAM + 立即生效 |
| `NET ON` | 下次开机自动连 | 立即写 NVS |
| `NET OFF` | 下次开机不连 | 立即写 NVS |
| `NET CONNECT` | 立即启动 STA + WS | 需要先有 SSID + URL |
| `NET DISCONNECT` | 立即断开 | — |
| `NET SAVE` | 把 RAM 设置写入 NVS | 持久化 |
| `NET RESET` | 清空所有 NET NVS key | 擦除 |
| `NET STATUS` | 打印完整状态 JSON | 只读 |

**注意**：
- `NET PASS` 只写 RAM，不写 NVS。必须 `NET SAVE` 才会持久化密码（代码设计：避免密码意外落盘）
- 如果改了 UART pin 必须先 `NET SAVE` 再重启 relay 才生效（relay 启动时从 NVS 读 prefs.tx/rx）
- `NET RESET` 只擦除 NVS namespace "net" 的 key，不动其它 namespace（CONFIG SAVE 的 lap-timer blob、GPIO 设置）

---

## 8. 风险提示

| # | 风险 | 触发条件 | 后果 | 预防 |
|---|------|----------|------|------|
| R1 | UART TX/RX 接反 | 接线时没交叉 | 两板 UART 都收不到数据，relay pipe 显示 head_ready=false，relay_lost_down 持续增长 | 上电前万用表测 C5 GPIO11→S3 GPIO44，C5 GPIO12→S3 GPIO43 |
| R2 | 未共地 | 两块板 USB 插不同电脑或不同电源回路 | UART TX/RX 电压浮动，relay pipe 收到垃圾帧 | 物理上接一根 GND 线 |
| R3 | 电源不足 | 两块板 + STA + 扫描并行 | S3 STA 断连重连、C5 偶发重启、WS backoff_ms 持续高位 | 用独立 5V/2A 电源 |
| R4 | C5 rev v1.0 RTS reset 卡死 | 刷写前 UART0 时钟被 gate | USB 口死掉，esptool auto-reset 失效 | 手动 BOOT+EN 进下载模式；固件已在启动时重开 UART0 时钟 |
| R5 | relay UART 改到 USB/JTAG 或 flash pin | NET UART 传错 pin | 崩溃或静默无输出 | 不要用 19/20（USB）或 26-32（flash） |
| R6 | 刷错 env | C5 刷 esp32c5 而不是 esp32c5-head | UART0 跑 115200（不是 921600），relay 收到垃圾，crc_errors 攀升 | 刷完后 INFO 命令确认；relay NET STATUS 看 head_baud 应该是 921600 |
| R7 | WPA3-only AP | relay sdkconfig 开了 WPA3-SAE，但 NVS 没存 WPA3 auth_mode | STA 关联失败，NET STATUS 显示 sta=false | 用 WPA2-PSK AP |
| R8 | 两块板 USB 同时刷写混淆 | 同时插 USB，不知道哪个口对应哪个板 | 可能刷错 env | 逐块插 USB，确认 monitor 输出再刷 |

---

## 9. 上电后立即观察点

### C5 head（esp32c5-head）

```
INFO  → 固件版本 + target=esp32c5
STATUS? → 扫描配置、当前 LO 计划、rotationHz、throttle
PROFILE? → 频点列表（默认 R1-R8）
CHANNELS DEFAULT → 恢复默认频点
METRICS RSSI → 最小输出，最快 rotation（~332 Hz per channel，见 PLAN.md）
METRICS FULL → 完整 RSSI + SNR 输出
```

**好现象**：发 `METRICS RSSI` 后 USB Serial/JTAG monitor 看到持续输出二进制帧（非文本），同时 UART0 (GPIO11/12) 也持续输出相同帧到 relay。

**坏现象**：无输出、只看到 `ERR boot ...`、循环重启、UART0 线电平一直低或一直高。

### S3 relay（esp32s3-relay）

```
NET STATUS → 完整 JSON（包含 head_ready/head_baud/head_pins/sta/ws/backoff_ms）
```

**好现象**：刷完后即使不配置 SSID，relay 也会起 head UART 监听（uart_link_open(0,0)），relay_pipe 运行，NET STATUS 显示 `head_ready=true, head_baud=921600, head_pins="43/44"`。WS 没启动时 `ws=false`，但 `head_bytes_up=0`（还没接上 head 或 head 没在输出）。

**坏现象**：`head_ready=false`（UART 线没接好或 pin 不对）、`ERR head_uart`（head UART 打开失败）、循环重启。

---

## 10. 板卡引脚丝印对照表（参考）

**ESP32-C5-DevKitC-1**（根据 Espressif 官方 pinout）：

| 板丝印 | GPIO | 功能 | 连接到 |
|--------|------|------|--------|
| TX | 11 | U0TXD | S3 GPIO44 (RX) |
| RX | 12 | U0RXD | S3 GPIO43 (TX) |
| GND | — | 地 | S3 GND |

**ESP32-S3-DevKitC-1**（根据 Espressif 官方 pinout）：

| 板丝印 | GPIO | 功能 | 连接到 |
|--------|------|------|--------|
| TX | 43 | U0TXD | C5 GPIO12 (RX) |
| RX | 44 | U0RXD | C5 GPIO11 (TX) |
| GND | — | 地 | C5 GND |

**注意**：不同批次 DevKitC-1 的 header 排针顺序可能不同，**以板上丝印为准，不要按排针序号数**。

---

## 11. 参考文档

- 固件构建：`repo/firmware/platformio.ini`
- UART 实现：`repo/firmware/src/proto/uart_link.c`
- 启动流程：`repo/firmware/src/main.c`
- NET 命令：`repo/firmware/src/proto/commands.c`（第 712-812 行）
- relay 桥接：`repo/firmware/src/net/relay_pipe.c`
- WS 链路：`repo/firmware/src/net/ws_link.c`
- 性能实测：`repo/PLAN.md`（Performance 表格）
- sdkconfig：`repo/firmware/sdkconfig.esp32c5-head`, `repo/firmware/sdkconfig.esp32s3-relay`
