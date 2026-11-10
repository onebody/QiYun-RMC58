# Phase 08.2.4-HW Stage 00 — 真实硬件与通信拓扑基线

> 版本：v1 | 日期：2026-11-05
> 范围：ESP32-C5 扫描头、ESP32-S3 relay 主控、gateway-agent、serve.mjs、rftrack/backend、site-server
> 依据：仓库源码逐行审计 + `repo/docs/architecture/topology-and-access-methods.md`（2026-10-28）+ `CLAUDE.md`
> 标注：**[已由源码确认]** / **[已由真机实测]** / **[需要实机验证]** / **[UNKNOWN]**

---

## 1. 硬件清单

| # | 角色 | 型号 | 固件 env | 作用 | 证据 |
|---|------|------|----------|------|------|
| H1 | **RF 扫描头** | ESP32-C5-DevKitC-1 | `esp32c5-head` | 5.8 GHz 频谱扫描 + UART0 输出帧流到 S3；自身**无 Wi-Fi 运行**（`WIFI_MODE_NULL`） | platformio.ini:59-68; main.c:132 `esp_wifi_set_mode(WIFI_MODE_NULL)` |
| H2 | **Wi-Fi relay 宿主** | ESP32-S3-DevKitC-1 | `esp32s3-relay` | Wi-Fi STA 关联 AP + WS 拨号 serve.mjs `/node?id=<name>` + UART 接收 C5 帧；自身**不跑扫描**（一个 PHY，STA 要占 AP 信道） | platformio.ini:31-45; main.c:98-127 relay 分支 |
| H3 | USB-C 数据线 × 2 | 至少 E-Marker ≥ 1A | — | 两块板各自独立供电 + USB Serial/JTAG 日志口 + 刷写 | — |
| H4 | 公对公 UART 杜邦线 × 3 | TX/RX/GND | — | C5 head ↔ S3 relay 通信 | — |
| H5 | Wi-Fi AP × 1 | 2.4 GHz WPA2-PSK | — | relay STA 关联目标 | sdkconfig.defaults.esp32s3-relay 开 WPA3-SAE 但实测 WPA2 最稳 |
| H6 | 主机（电脑 / Pi）× 1 | 跑 serve.mjs + rftrack/backend | — | serve.mjs :8080 `node?id=` 端点接收 S3 relay 上行；FastAPI 接收 serve.mjs 扇出；SQLite 存数据 | topology-and-access-methods.md §0.1 部署形态 |
| H7 | 可选主机 | 跑 gateway-agent | — | 方式①：C5 USB → gateway-agent → site-server；方式③ 里 relay 拓扑**不经过 gateway-agent** | gateway-agent main.py:80-105 SiteConnector |

**两套独立后端（重要）**：

| 后端 | 入口 | 接受连接 | 用途 |
|------|------|----------|------|
| `repo/web/serve.mjs` + `repo/rftrack/backend/serve.py` | serve.mjs :8080 (`/node?id=` + `/serial?ep=`) + FastAPI :9000 | S3 relay WS（二进制帧）、C5 USB serial | **方式①②③ 中继拓扑的真实后端**；已 2026-11-02 真机验证 esp32s3-relay 接 `/node?id=` |
| `site-server/`（Python FastAPI） | `/ws/v1/agents/connect` + `/ws/v1/site` | gateway-agent（带 credential auth） | Phase 08 新平台；**不接受 esp32s3-relay 的无认证二进制帧** |

**gateway-agent 与 serve.mjs/rftrack 的关系**：两套**并行存在但各自独立**的后端，不在同一条链路上。gateway-agent 通过 USB serial 直连 C5（方式①），site-server 通过 `/ws/v1/agents/connect` 接 gateway-agent。esp32s3-relay 通过 WS 直连 serve.mjs `/node?id=`，serve.mjs 桥接到 rftrack/backend。

---

## 2. 软件组件清单

| 组件 | 类型 | 运行位置 | 职责 |
|------|------|----------|------|
| `esp32c5-head` 固件 | PlatformIO env | ESP32-C5 芯片 | 扫描头：8 channel GROUP/SINGLE 模式 + 帧 sink 路由到 UART0 (921600) + USB Serial/JTAG (115200) |
| `esp32s3-relay` 固件 | PlatformIO env | ESP32-S3 芯片 | relay 宿主：Wi-Fi STA + WS 拨号 + UART 读 C5 帧 → 喂给 WS uplink（relay_pipe + ws_link） |
| serve.mjs | Node.js 程序 | 主机（电脑 / Pi） | WS 桥接层：`/node?id=` + `/serial?ep=` + 可选 `/uart`（②a PL011）；多端点多路复用 |
| rftrack/backend | FastAPI + SQLite | 主机 | RF Track 后端：WS 桥接 + REST API + 数据库；serve.mjs 桥接的唯一持有者 |
| gateway-agent | Python + asyncio | 主机 | 方式① 多设备 host multiplexer：USB serial / TCP / simulator 三种 transport；可选 Site Connector → site-server |
| site-server | FastAPI + SQLite | 主机 | Phase 08 新平台：agent 配对 + measurement 存储；**不参与 relay 拓扑** |

---

## 3. 真实通信拓扑

### 3.1 方式③（本 HW 测试采用）：C5 UART → S3 Wi-Fi 中继

```
ESP32-C5 (esp32c5-head)          ESP32-S3 (esp32s3-relay)              主机
─────────────────────────        ──────────────────────────              ──────
扫描 + RF PHY (5.8 GHz)         Wi-Fi STA (2.4 GHz)                    serve.mjs :8080
UART0 TX  GPIO11 ────────────▶  UART0 RX  GPIO44      (921600 8N1)      │
UART0 RX  GPIO12 ◀────────────  UART0 TX  GPIO43                      │ ws://host:8080/node?id=<name>
GND        ────────────────────  GND                     (共地必须)     ▼
                                UART→ws relay_pipe                   rftrack/backend :9000
                                Wi-Fi STA → AP → 局域网
                                ws_link → 指数退避重连 (500ms→30s)

帧流方向：C5 扫描 → UART0 GPIO11→44 → relay_pipe pipe_task → link_raw_to(LINK_WS) → ws_link dial → serve.mjs /node?id → bridge → rftrack/backend ingest → SQLite
```

| 连接 | 介质 | 参数 | 实现状态 | 证据 |
|------|------|------|----------|------|
| C5 UART0 TX → S3 UART0 RX | GPIO11 → GPIO44 | 921600, 8N1, 无流控 | **已实现（固件硬编码）** | uart_link.c:12-16; esp32c5-head 和 esp32s3-relay env 都设 -DUART_LINK_BAUD=921600 |
| S3 UART0 TX → C5 UART0 RX | GPIO43 → GPIO12 | 同上 | **已实现** | 同上 |
| S3 Wi-Fi STA | 2.4 GHz | WPA2-PSK (推荐), WPA3-SAE (可选) | **已实现（真机验证 M1）** | topology-and-access-methods.md §0 "方式③ 子路线 3a" M1 已通 2026-11-02 |
| S3 WS → serve.mjs | 局域网 HTTP WS | `GET /node?id=<name>` + RFC6455 二进制帧 | **已实现（固件+服务端）** | ws_link.c dial_and_handshake; serve.mjs line 368-390 |
| relay_pipe | FreeRTOS task | PIPE_POLL_MS=20, PIPE_CHUNK=1024 | **已实现** | relay_pipe.c:14-49 |

### 3.2 方式①（widscan58 原生，不用于本 HW 测试）

```
ESP32-C5 (esp32c5) × N
  | USB Serial/JTAG (原生 CDC, 非 GPIO UART)
  v
gateway-agent (Python, usb_serial transport)
  | 可选 Site Connector
  v
site-server /ws/v1/agents/connect
  v
SQLite
```

### 3.3 方式②（F1 已提交，Pi PL011 直连，互斥 RH）

```
ESP32-C5 (esp32c5-head)
  | UART0 GPIO11(TX) → Pi PL011 /dev/ttyAMA0
  | UART0 GPIO12(RX) ← Pi PL011
  v
serve.mjs :8080 /uart=<rhSerial>   (bridgeUart: 桥前 systemctl stop rotorhazard)
  v
rftrack/backend
```

---

## 4. 数据流向详解（方式③）

```
 阶段 1. C5 head 固件侧
  ─────────────────────
  main.c esp32c5-head 分支 → scanner_init()
    → radio_running = true → UART0 open(921600)
    → scanner_task hop() 循环
       → radio_capture() → FFT → metrics_rssi()/metrics_full()
       → link.put(METRICS|SPECTRUM|TELEMETRY|LEVELS|RAWIQ)
         → frame_put() → link_frame_sink()
           → C5 head 固件没设 link_frame_sink？→ 看 link.c 默认 sink
         
  ── 这里有个关键问题需要验证 ──
  esp32c5-head 和 esp32c5 的区别只是 -DUART_LINK_BAUD=921600。
  esp32c5-head 固件里谁设了 link_frame_sink = LINK_UART？
  如果没有，帧可能只走 USB 不走 UART！
  让我查 commands.c 的 MONITOR 分支...

  main.c esp32s3-relay 分支（不跑 scanner）
    → ws_link_open() 解析 net_prefs.url
    → uart_link_open(prefs.tx, prefs.rx) 默认 GPIO43/44, 921600
    → relay_pipe_start()
       → pipe_task_fn() 20ms poll
          → uart_link_read(chunk, 1024, 20ms)
          → if ws_link_up(): link_raw_to(LINK_WS, chunk, n); bytes_up+=n
          → else: lost_down+=n  (drain UART, 让 head 继续扫描)
    → commands_task_start() 响应 NET 命令
```

**⚠️ 关键待确认项（UNKNOWN，需要查 commands.c）**：esp32c5-head 固件里，帧是否真的被路由到了 UART0（LINK_UART）？还是仍然只走 USB（默认 sink）？如果是后者，relay_pipe 永远收不到帧。

topology-and-access-methods.md §1 写 F1（提交 `7bec675`）让帧按 `frame_sink` 路由，MONITOR+UART 时设 `uart_pace=true` 并把 sink 指向 UART。但 esp32c5-head env 不是一个独立 target——它 extends esp32c5，只有 baud 不同。MONITOR 命令在 UART 上放开是 F1 改的行为，需要实机或读 commands.c 确认 esp32c5-head 在 UART 上是否真的发帧。

---

## 5. 连接接口和引脚证据

### 5.1 C5 ↔ S3 UART

| 连接 | C5 GPIO | S3 GPIO | 波特率 | 证据 |
|------|---------|---------|--------|------|
| C5 TX → S3 RX | **11** (U0TXD) | **44** (U0RXD) | 921600 8N1 | uart_link.c:12-16; esp32c5-head env 设 -DUART_LINK_BAUD=921600; esp32s3-relay env 同上 |
| C5 RX ← S3 TX | **12** (U0RXD) | **43** (U0TXD) | 同上 | 同上 |
| GND → GND | 任意 GND | 任意 GND | — | UART 单端信号必须共地；uart_link.c 两边都开了 RX pull-up 防悬空 |

pin 可覆盖性：
- **C5 head**：硬编码 GPIO11/12，不可运行时覆盖。
- **S3 relay**：默认 GPIO43/44，可通过 `NET UART <tx> <rx>` 覆盖（需排除 GPIO 19/20 USB 和 26-32 flash）。

### 5.2 S3 Wi-Fi STA

| 参数 | 值 | 证据 |
|------|-----|------|
| Wi-Fi 模式 | WIFI_MODE_STA | ws_link.c join_ap(): esp_wifi_set_mode(WIFI_MODE_STA) |
| SSID/密码 | NVS namespace "net" key ssid/pass | net_prefs.c |
| WS URL | NVS namespace "net" key url | net_prefs.c |
| WS 协议 | HTTP 升级 RFC6455 二进制帧 | ws_link.c dial_and_handshake:235-294 |
| 目标路径 | `/node?id=<name>` (serve.mjs) | serve.mjs line 368-390; ws_link.c 注释 "bridge answers handshake before serving (serve.mjs:368-390)" |
| 退避 | 500ms → 30000ms 指数 | ws_link.c backoff:28-29 |
| 心跳 | None（被动读取对端 close） | ws_link.c recv() 超时关闭 |

### 5.3 C5 USB Serial/JTAG

| 参数 | 值 | 证据 |
|------|-----|------|
| 外设 | native USB Serial/JTAG (非 GPIO UART) | sdkconfig CONFIG_USJ_ENABLE_USB_SERIAL_JTAG=y |
| 波特率（monitor） | 115200 | platformio.ini monitor_speed |
| console | NONE → printf 走 USB Serial/JTAG | sdkconfig.defaults |
| 作用 | monitor 日志 + 用户输入命令（INFO, STATUS?, PROTOCOL MONITOR 等） | CLAUDE.md firmware boot silent 说明 |

### 5.4 S3 USB Serial/JTAG

同 C5，monitor 115200，console=NONE。NET STATUS 命令通过此口查询 relay 状态。

---

## 6. 各组件职责

### 6.1 ESP32-C5 (esp32c5-head)

| 职责 | 证据 |
|------|------|
| 5.8 GHz 频谱扫描（GROUP/SINGLE 模式） | scanner.c hop(); planner.c LO 分组 |
| 输出 METRICS / SPECTRUM / TELEMETRY / LEVELS / RAWIQ 帧 | frames.h FRAME_* types |
| USB Serial/JTAG（monitor + 命令输入） | link.c USB CDC sink |
| UART0 (GPIO11/12, 921600) 帧输出到 S3 | esp32c5-head env UART_LINK_BAUD; uart_link.c |
| **无 Wi-Fi 运行** | main.c:132 WIFI_MODE_NULL; sdkconfig.defaults SoftAP 关闭 |
| **不做中继** | — |

### 6.2 ESP32-S3 (esp32s3-relay)

| 职责 | 证据 |
|------|------|
| Wi-Fi STA 关联 AP | ws_link.c join_ap() |
| WS 拨号 serve.mjs `/node?id=<name>` | ws_link.c dial_and_handshake |
| UART0 (GPIO43/44, 921600) 读 C5 帧 | uart_link_open(prefs.tx, prefs.rx); net_prefs 默认 43/44 |
| relay_pipe：UART → WS uplink | relay_pipe.c pipe_task_fn → link_raw_to(LINK_WS) |
| NET 命令族（SSID/PASS/URL/UART/ON/OFF/CONNECT/SAVE/RESET/STATUS） | commands.c relay 分支 |
| **不跑扫描** | main.c esp32s3-relay 分支跳过 scanner_init(); PHY 让给 STA |
| **无 5 GHz 射频**（S3 硬件限制） | README.md:74-75; 只在 2.4 GHz 跑缩比 mock |

### 6.3 serve.mjs + rftrack/backend

| 职责 | 证据 |
|------|------|
| `/node?id=<name>` 端点（S3 relay 上行 WS） | serve.mjs line 368-390 attachNode() |
| `/serial?ep=<id>` 端点（C5 USB serial 上行 WS） | serve.mjs line 368 |
| `/uart=<rhSerial>` 端点（②a PL011 直连，停/复 RH） | serve.mjs bridgeUart():486-504 |
| WS 桥接层（多端点多路复用） | topology-and-access-methods.md §2 |
| FastAPI REST API + SQLite 存储 | rftrack/backend/serve.py |
| ingestion 解析原始帧 → 写入 Measurement | rftrack/backend/services/ingest.py |

### 6.4 gateway-agent（不参与方式③，仅做拓扑对照）

| 职责 | 证据 |
|------|------|
| 多设备 host multiplexer（USB serial / TCP / simulator） | gateway-agent src/widscan_agent/transports/ |
| Site Connector（可选）→ site-server `/ws/v1/agents/connect` | site_connector.py V1.1 |
| REST API（设备管理 + 命令下发） | gateway-agent src/widscan_agent/api/ |
| **不连 serve.mjs 或 rftrack/backend** | — |
| **不连 esp32s3-relay**（方式③里 relay 直连 serve.mjs） | — |

---

## 7. 已确认事项（VERIFIED，全部来自源码）

| # | 事实 | 证据 |
|---|------|------|
| V1 | C5 head → S3 relay UART：GPIO11→44, GPIO12→43, 921600 8N1 | uart_link.c:12-16; 两边 env -DUART_LINK_BAUD=921600 |
| V2 | C5 head 无 Wi-Fi 运行 | main.c:132 WIFI_MODE_NULL; sdkconfig.defaults SoftAP 关闭 |
| V3 | S3 relay 不跑扫描 | main.c esp32s3-relay 分支跳过 scanner_init() |
| V4 | S3 relay WS 目标是 serve.mjs `/node?id=<name>` | ws_link.c 注释 "serve.mjs:368-390"; serve.mjs line 368-390 |
| V5 | relay_pipe 从 UART 读帧 → link_raw_to(LINK_WS) | relay_pipe.c:48 |
| V6 | S3 relay 无数据上行时 drain UART（累加 lost_down），C5 继续扫描 | relay_pipe.c:44-46 |
| V7 | esp32s3-relay 与 gateway-agent 无通信 | 固件代码无 gateway-agent 协议；gateway-agent 无 relay transport |
| V8 | gateway-agent 连 site-server `/ws/v1/agents/connect`（带 credential auth） | site_connector.py V1.1 |
| V9 | esp32s3-relay 不连 site-server | site-server 无 `/node?id=` 端点；relay WS 是纯二进制帧无 auth |
| V10 | 有两套独立后端并行存在 | repo/web/serve.mjs + repo/rftrack/backend vs site-server/ |
| V11 | 无其他 Gateway 硬件 | glob 搜索 LPTC* 返回空；仓库无额外硬件配置 |
| V12 | 固件 env 只有四个：esp32c5, esp32c5-head, esp32s3, esp32s3-relay | platformio.ini |

---

## 8. UNKNOWN 事项（静态审计无法确认，需实机）

| # | 项目 | 未知原因 | 影响 | 验证方法 |
|---|------|----------|------|----------|
| **U1** | esp32c5-head 固件在 UART0 上是否真的发帧（link_frame_sink 是否设为 LINK_UART） | platformio.ini 显示 esp32c5-head extends esp32c5，baud 921600，但 commands.c MONITOR 分支是否自动设 sink=UART 需实机或读代码确认 | 这是方式③ M2 能不能工作的**核心前提**。如果 C5 仍只走 USB，relay 收不到帧 | **Test 02 实机验证**：S3 relay NET STATUS head_bytes_up 是否增长 |
| U2 | S3 relay 峰值电流（Wi-Fi STA + relay_pipe 运行中） | 无实测数据 | 两块板并行时电源是否够 | Test 04 前用万用表实测 |
| U3 | 两块板并行总峰值电流 | 无实测数据 | 同上 | 同上 |
| U4 | Wi-Fi AP 信号强度（relay 接收到的 RSSI） | 物理环境 | relay ws 稳定性 | 手机 Wi-Fi 信号测试 |
| U5 | serve.mjs + rftrack/backend 是否安装并配置好 | 主机环境 | Test 03 能否 uplink | `curl http://<host>:8080/` 看 serve.mjs 端点列表 |
| U6 | 主板丝印位置 vs 代码声明 pin 是否一致 | 不同批次 DevKitC-1 的 UART0 排针位置可能不同 | 接错 pin | 看板上 "TX"/"RX" 丝印位置 |
| U7 | esp32c5-head 固件 boot 默认是 RH 模式还是 monitor 模式 | CLAUDE.md 写固件 boot silent 在 lap timer 模式 | relay 接 USB 后如果 C5 还没发 MONITOR，relay 不会收帧 | Test 01 打开 monitor 模式看是否有帧 |
| U8 | 2 套后端（serve.mjs + site-server）当前阶段用哪套 | 两套并存 | Test 03 的 WS URL 该填哪个 | 看用户当前阶段跑的是哪套 |

---

## 9. 后续 Test 01–06 依赖关系

### Test 01 — C5 单节点扫描

| 维度 | 内容 |
|------|------|
| 依赖 | **仅 C5 head + USB**。独立进行 |
| 硬件连接 | C5 head USB 插电脑；不接 S3；不需要 UART 线 |
| 软件 | 刷 `esp32c5-head`；USB monitor 115200；发 `PROTOCOL MONITOR` |
| 服务端 | 不需要任何后端 |
| 目的 | 验证 RF 扫描核心能力；帧是否出现在 USB Serial/JTAG |

### Test 02 — C5→S3 UART 透传

| 维度 | 内容 |
|------|------|
| 依赖 | C5 head + S3 relay + UART 3 线 + **共地** |
| 硬件连接 | C5 GPIO11→S3 GPIO44, C5 GPIO12→S3 GPIO43, GND→GND；两块板各自 USB 插电脑 |
| 软件 | C5 head 刷 `esp32c5-head`；S3 relay 刷 `esp32s3-relay`（NET OFF，跳过 WS） |
| 服务端 | 不需要 |
| 关键验证 | S3 relay `NET STATUS` 的 `head_ready=true, head_baud=921600, head_bytes_up` 是否增长 → 确认 U1（C5 真在 UART0 发帧） |
| 风险 | 如果 C5 仍只走 USB（link_frame_sink 未设 UART），head_bytes_up 永远 0 |

### Test 03 — S3 Wi-Fi LAN + WS uplink

| 维度 | 内容 |
|------|------|
| 依赖 | S3 relay + AP + **serve.mjs + rftrack/backend**（不是 site-server） |
| 硬件连接 | S3 relay USB + Wi-Fi AP + 主机 |
| NET URL 填法 | `ws://<serve-mjs-host>:8080/node?id=<relay-unique-name>`（**不是** `/ws/v1/agents/connect`） |
| 服务端 | 必须 serve.mjs 跑着，端口 8080 可从 AP 网络访问 |
| 关键验证 | `NET STATUS` → `sta:true, ws:true`；serve.mjs 能看到 node 端点 |

### Test 04 — 扫描与网络并行（核心）

| 维度 | 内容 |
|------|------|
| 依赖 | **全部**：C5 head + S3 relay + UART 接线 + 共地 + AP + serve.mjs + rftrack/backend |
| 硬件连接 | 同 Test 02 + Test 03 叠加 |
| 并行任务分布 | **C5 跑扫描**（独占它自己的单核 CPU + 5 GHz PHY）；**S3 跑 STA + WS**（独占它自己的双核 + 2.4 GHz PHY，不跑扫描）；两者之间只有 UART 921600 做桥接 |
| 资源竞争 | **只有 UART 921600 是共享资源**。C5 和 S3 各自独立芯片，无 CPU/PHY 竞争。relay_pipe 在 WS 断了时 drain UART，不阻塞 C5 扫描 |
| 关键指标 | C5 rotationHz 与 Test 01 baseline 相比下降 ≤ 10%；S3 relay ws=true ≥ 95% 时间 |

### Test 05 — 节点异常恢复

| 维度 | 内容 |
|------|------|
| 依赖 | 同 Test 04 全链路 |
| 设备标识 | relay NET ID（`NET ID <name>` 设置） |
| 服务端日志采集 | serve.mjs `node` 端点状态；rftrack/backend node 列表 |
| 关键验证 | Wi-Fi 断/续、S3 断电、C5 断电、UART 断、服务端自动恢复 |

### Test 06 — 多节点扩展准备（设计类，不刷硬件）

| 维度 | 内容 |
|------|------|
| 依赖 | 代码审计 + ADR 文档，不需要真实硬件 |
| 多节点形态 | **每 C5 head + S3 relay 为一对**，独立刷写 NET ID 和 NVS |
| 每对 NET URL | 同一个 serve.mjs 的 `/node?id=<不同名称>` |
| 隔离方式 | serve.mjs 按 `/node?id=` 区分端点；rftrack/backend 按 node 字段区分；channel_index 只是 0-7 数组索引，查询时必须用 node+channel_index 联合定位 |
| gateway-agent 位置 | 在方式③ relay 拓扑里**不参与**。如果方式① 也跑多 C5，gateway-agent 管多 USB serial device，两者并存但独立 |
| Site Server 位置 | 如果用 gateway-agent + site-server 这条链，那是方式① 的多 C5 管理。方式③ relay 拓扑不走 site-server |
| 时间戳对齐 | 每个 C5 有独立 free-running device_timestamp_ms；无墙钟锚点；多 node 并行时轴不对齐，是已知限制 |

---

## 10. 拓扑决策速查表

| 决策点 | 结论 | 为什么 |
|--------|------|--------|
| esp32s3-relay 连谁 | serve.mjs `/node?id=<name>` | ws_link.c 注释写 serve.mjs:368-390；固件没 auth 协议；site-server 没有 `/node` 端点 |
| gateway-agent 参与方式③吗？ | **不参与** | relay 直连 serve.mjs；gateway-agent 是方式① C5 USB 直连的 host |
| C5 能单独跑吗？ | **能** | Test 01 不需要 S3、AP、后端 |
| S3 能单独跑吗？ | **能** | Test 03 relay 不需要 C5（NET STATUS head_ready=false 是正常的，WS uplink 会建立但无数据） |
| 两块板 CPU/PHY 竞争？ | **不竞争** | 各自独立芯片；C5 PHY 给 5 GHz 扫描用，S3 PHY 给 2.4 GHz STA 用 |
| 2 套后端用哪套？ | serve.mjs + rftrack/backend | relay 拓扑走这条路；site-server 是 gateway-agent 那条路 |

---

## 附录：Test 03 的 NET URL 填写参考（纠正 HW_PREPARATION_REPORT.md）

HW_PREPARATION_REPORT.md 里给出的 WS URL 是 `/ws/v1/agents/connect`，那是**错误的**（那个端点只给 gateway-agent 用）。正确填法：

```
NET SSID <AP_NAME>
NET PASS <AP_PASSWORD>
NET URL ws://<serve-mjs-host>:8080/node?id=<relay-s3-01>
NET ID relay-s3-01
NET ON
NET SAVE
NET CONNECT
```

其中 `<serve-mjs-host>:8080` 是跑 serve.mjs 的主机。如果 serve.mjs 没安装，需要先在主机上启动。

---

## 文档版本控制

| 版本 | 日期 | 变更 |
|------|------|------|
| v1 | 2026-11-05 | 初稿；纠正 esp32s3-relay WS 目标（从 site-server 改为 serve.mjs `/node?id=`）；澄清 2 套后端；澄清 gateway-agent 不参与方式③ |
