# Phase 08.2.4-HW — 分阶段测试手册

> 版本：v1 | 日期：2026-07-05
> 前置：已完成 `HW_WIRING_AND_SETUP.md` 第 6 节全部上电前检查
> 测试结果只能为 **PASS / CONDITIONAL PASS / FAIL / NOT VERIFIED**
> 本手册不授权自动刷写、自动上电、自动串口操作

---

## 总体测试策略

| 顺序 | 测试 | 依赖 | 目的 |
|------|------|------|------|
| 01 | C5 单节点扫描 | 仅 C5 head + USB | 验证 RF 扫描核心能力 |
| 02 | C5 → S3 UART 透传 | C5 head + S3 relay + UART 接线 | 验证 head→relay 数据链路 |
| 03 | S3 Wi-Fi LAN + WS uplink | S3 relay + AP + Site Server | 验证 relay 联网能力 |
| 04 | 扫描与网络并行 | 全部三件套 + AP + Site Server | **本阶段核心测试** |
| 05 | 节点异常恢复 | 全部 + 可控断电/断网 | 验证弹性 |
| 06 | 多节点扩展准备 | 设计类，不刷硬件 | 架构扩展性确认 |

**原则**：每项测试必须有真实日志证据才能判 PASS。无证据保持 NOT VERIFIED。

---

## Test 01 — C5 单节点 RF 扫描

### 目的

验证 C5 head 固件能否独立执行 5.8 GHz 频谱扫描，持续输出 METRICS 帧到 USB Serial/JTAG 和 UART0。

### 前置条件

- C5 head 单独 USB 插电脑（**不接 S3 relay，不接 UART 线**）
- 已刷 `esp32c5-head` env
- monitor 打开，波特率 115200
- 笔记本电脑 Python ≥ 3.10 + pyserial

### 步骤

1. **确认固件版本**：在 monitor 输入 `INFO`，记录完整响应。确认包含 `esp32c5` target 和非空的 git describe 版本号。
2. **进入 monitor 模式**：如果固件默认 RH（lap timer）模式，发 `PROTOCOL MONITOR` 切换。monitor 模式下才会持续输出扫描帧。
3. **检查当前扫描配置**：发 `STATUS?`，记录 rotationHz、throttle、LO plan（GROUP/SINGLE）、CHANNEL 列表。预期 throttle=1050（默认），8 个 channel（R1-R8）。
4. **设置最小输出配置**（加速验证）：
   ```
   METRICS RSSI          # 最小输出，最快 rotation
   CHANNELS R1=5658 R4=5695 R7=5732 R8=5752   # 4 个代表性频道
   ENABLE 15             # bitmask: R1|R4|R7|R8 开启
   MODE GROUP            # GROUP 模式（默认）
   ```
5. **确认扫描启动**：`STATUS?` 再次查看，rotationHz 应 > 0，enable mask 应反映 4 个 channel。
6. **观察输出**：monitor 应该看到持续二进制帧（不是文本行）。用 `python3 tools/serial_bridge.py -p <port>` 或 `tools/widscan58cli.py monitor` 可以解析帧格式。
7. **连续运行 5 分钟**：记录是否有中断、panic、重启。
8. **可选：测量实际扫描速率**：
   ```python
   # python3
   import serial, struct, time
   ser = serial.Serial('/dev/cu.usbserial-xxx', 115200, timeout=1)
   # 发 PROTOCOL MONITOR 切换模式
   ser.write(b'PROTOCOL MONITOR\n')
   time.sleep(1)
   start = time.time(); count = 0
   while time.time() - start < 10:
       data = ser.read(1024)
       # 简单计数：找 FRAME_SYNC0/1 魔数 0xAA 0x55
       count += data.count(b'\xaa\x55')
   print(f'帧头出现频率: {count/10:.2f} Hz')
   ```

### 通过判定（全部满足）

| # | 条件 | 判定 |
|---|------|------|
| 1 | `INFO` 正常返回，target=esp32c5 | PASS |
| 2 | `PROTOCOL MONITOR` 后开始输出帧 | PASS |
| 3 | `STATUS?` rotationHz > 0（GROUP + METRICS RSSI 预期 ~300+ Hz） | PASS |
| 4 | 连续 5 分钟运行无 panic、无重启 | PASS |
| 5 | device_timestamp_ms 字段单调递增（用 widscan58cli.py monitor 解析） | PASS |
| 6 | **RF 扫描数据有意义**：在实际 VTX 发射时，RSSI 值明显高于无信号底噪 | 需现场验证 |

### 失败诊断

| 现象 | 可能原因 | 检查 |
|------|----------|------|
| monitor 循环打印 `ERR boot ...` | BOOT_CHECK 失败（main.c 第 37-56 行） | 记录 ERR 后的 step 名和 err code，查 ESP-IDF 错误码表 |
| `PROTOCOL MONITOR` 后无输出 | 固件在 RH 模式但没收到 scan 配置 | 发 `METRICS FULL` 强制输出；或 `PROFILE?` 确认 channel 非空 |
| `STATUS?` rotationHz = 0 | 扫描被 `SCAN STOP` | 发 `SCAN START` |
| UART0 线（GPIO11）无输出但 USB 有 | UART_LINK_BAUD 不匹配？但 esp32c5-head 已设 921600 | 用示波器或逻辑分析仪测 GPIO11 电平跳变 |
| 5 分钟内重启 | Brownout、heap 耗尽、RF 调谐异常 | 看重启前最后几行日志；`STATUS?` 的 `heap` 字段 |

### 证据归档

- 原始日志：`docs/evidence/PHASE08_2_4_HW/test01_usb_monitor.log`（`pio device monitor` 输出重定向）
- widscan58cli.py 解析输出：`docs/evidence/PHASE08_2_4_HW/test01_frames.txt`
- STATUS? 快照：`docs/evidence/PHASE08_2_4_HW/test01_status.txt`
- 电流测量（可选）：`docs/evidence/PHASE08_2_4_HW/test01_current.txt`

---

## Test 02 — C5 → S3 UART 数据透传

### 目的

验证 C5 head 发出的扫描帧能否通过 UART0 (921600, 8N1) 被 S3 relay 正确接收，进入 relay_pipe。

### 前置条件

- C5 head + S3 relay USB 各自插电脑
- 按 `HW_WIRING_AND_SETUP.md` 第 3 节 UART 接线接好
- 共地确认
- 两块板分别刷 `esp32c5-head` 和 `esp32s3-relay`
- **relay 不配置 SSID/URL**（跳过 WS uplink，仅测 pipe 收发）

### 步骤

1. **C5 head 端**：发 `PROTOCOL MONITOR` + `METRICS RSSI` + `CHANNELS R1=5658 R4=5695` 启动扫描。等 10 秒确认 USB 端有输出。
2. **S3 relay 端**：打开 USB monitor，发 `NET STATUS`。记录：
   - `head_ready`：应该是 **true**（UART 已打开）
   - `head_baud`：应该是 **921600**
   - `head_pins`：应该是 **"43/44"**
   - `head_bytes_up`：应该开始增长（relay_pipe 在搬运 UART 帧）
   - `head_lost_down`：应该保持 0（WS 没启动，relay_pipe 不走 WS 但 drain UART）
   - `ws`：应该是 **false**（没配置 WS）
3. **运行 10 分钟**：每 30 秒发一次 `NET STATUS`，记录 `head_bytes_up` 增量。
4. **计算丢帧率**：relay_pipe 在 ws=false 时 drain UART 但 lost_down 计数无意义（数据没上行目的地）。改用对比法：
   - 用逻辑分析仪或 Python 脚本直接在 C5 UART0 (GPIO11) 抓包统计 TX 帧数
   - 与 relay 收到的帧数对比（relay 重启后 `head_bytes_up` 清零不方便，用时间窗口增量）
5. **C5 重启测试**：按 C5 reset 按钮（或拔 USB 再插），观察 relay `NET STATUS` 的 `head_ready` 变化：
   - C5 重启期间：`head_ready` 应该短暂 false
   - C5 恢复后：`head_ready` 重新变 true，`head_bytes_up` 继续增长

### 通过判定（全部满足）

| # | 条件 | 判定 |
|---|------|------|
| 1 | relay `NET STATUS` 显示 `head_ready=true, head_baud=921600, head_pins="43/44"` | PASS |
| 2 | `head_bytes_up` 在 C5 扫描期间持续增长 | PASS |
| 3 | 10 分钟运行期间 `head_ready` 无意外掉线（C5 主动重启除外） | PASS |
| 4 | C5 重启后 relay 自动恢复 head_ready | PASS |
| 5 | **无 CRC 错误上升**（relay WS 没启动，CRC 校验走 site-server ingestion，此处间接判定：如果 head_bytes_up 持续增长且无明显 drop，通过） | CONDITIONAL PASS（需 Test 04 在 WS 上行时确认） |

### 失败诊断

| 现象 | 可能原因 | 检查 |
|------|----------|------|
| relay `head_ready=false` | UART 没打开 / pin 不对 / 接线反了 | 1) 接线是否 C5 TX→S3 RX (11→44)? 2) 共地? 3) `NET UART` 命令是否改了 pin? 4) S3 GPIO43/44 是否接了别的? |
| `head_baud=0` | UART driver 没安装成功 | 看 relay 启动日志有没有 `ERR head_uart` |
| `head_baud=115200` 而不是 921600 | C5 刷错 env（esp32c5 而不是 esp32c5-head） | C5 发 INFO 确认 target；重新刷 esp32c5-head |
| `head_bytes_up` 不动 | C5 没在发帧 / UART 波特率不匹配 | 用示波器测 C5 GPIO11 有没有数据帧；或 C5 自己 USB monitor 看有没有输出 |
| C5 重启后 relay head_ready 不恢复 | relay_pipe 没有 poll 恢复 | 重启 relay 看是否恢复；可能需要调查 |

### 证据归档

- C5 monitor 日志：`docs/evidence/PHASE08_2_4_HW/test02_c5_usb.log`
- relay monitor 日志（含 NET STATUS 快照）：`docs/evidence/PHASE08_2_4_HW/test02_relay_usb.log`
- UART TX/RX 对比（如有逻辑分析仪）：`docs/evidence/PHASE08_2_4_HW/test02_uart_probe.txt`
- C5 重启恢复过程：`docs/evidence/PHASE08_2_4_HW/test02_c5_reboot_recovery.log`

---

## Test 03 — S3 Wi-Fi LAN + WS uplink

### 目的

验证 S3 relay 能否独立（无 C5 head）完成 Wi-Fi STA 关联 AP、获取 IP、建立 WebSocket 连接到 Site Server。

### 前置条件

- S3 relay USB 插电脑（**C5 head 不参与，UART 线可接可不接**）
- Wi-Fi AP 在附近，2.4 GHz WPA2-PSK
- Site Server 已启动，可从 AP 网络访问
- Site Server 有 agent connect endpoint：`ws://<site-host>:<port>/ws/v1/agents/connect`

### 步骤

1. **确认 AP 参数**：记录 SSID、密码、Site Server 的 host:port。
2. **配置 NVS**：在 relay USB monitor 输入（每行回车）：
   ```
   NET SSID <你的AP名称>
   NET PASS <你的AP密码>
   NET URL ws://<site-host>:<port>/ws/v1/agents/connect
   NET ID relay-test-01
   NET ON
   NET SAVE
   ```
3. **立即启动连接**：`NET CONNECT`
4. **观察 NET STATUS**（每 5 秒发一次）：
   - **阶段 1**：`sta:false, ws:false`（STA 正在关联 AP）
   - **阶段 2**：`sta:true, ws:false, backoff_ms:500`（STA 拿到 IP，WS 正在拨号）
   - **阶段 3**：`sta:true, ws:true`（**通过**）
   - 如果卡在阶段 1 超过 30 秒：AP 密码/加密方式不对
   - 如果卡在阶段 2 超过 15 秒：Site Server 不可达或 URL 错
5. **断网重连**（30 秒后）：
   - 拔掉 AP 电源（或 relay 离 AP 到信号差的地方）
   - 观察 `sta:false, ws:false, backoff_ms:500→1000→2000→...→30000`（指数退避）
   - 恢复 AP 供电，观察 backoff 从 500 重新开始，sta→true, ws→true
6. **长时间运行 30 分钟**：每 1 分钟 `NET STATUS` 记录一次。统计重连次数（backoff_ms 从 30000 跳回 500 算一次）。

### 通过判定（全部满足）

| # | 条件 | 判定 |
|---|------|------|
| 1 | `NET CONNECT` 后最终达到 `sta:true, ws:true` | PASS |
| 2 | 断网后恢复，自动从 backoff_ms=500 重新拨号并重连成功 | PASS |
| 3 | 30 分钟运行期间重连次数 ≤ 2（排除人工断网测试） | PASS |
| 4 | Site Server 端 agent 连接数增加（验证真正的 uplink 建立） | PASS |
| 5 | 无 `ERR net_start` 或 `ERR net_ring` | PASS |

### 失败诊断

| 现象 | 可能原因 | 检查 |
|------|----------|------|
| 一直 `sta:false` | 密码错 / AP 是 WPA3-only / AP 无 2.4 GHz | 1) 用手机试同一 SSID 能否连上; 2) relay sdkconfig 有 CONFIG_ESP_WIFI_STA_SAE_SUPPORT=y，应该支持 WPA3，但实测 WPA2 最稳 |
| `sta:true` 但 `ws:false` 超过 15 秒 | Site Server 不可达 / URL 格式错 | 1) 在 AP 网络下 `curl http://<site-host>:<port>/health`; 2) NET URL 应该以 `ws://` 开头; 3) 看 NET STATUS 的 `why` 字段 |
| 首次 `NET CONNECT` 报 `ERR net_start` | prefs 缺 SSID 或缺 URL | `NET STATUS` 看 RAM 中的 ssid/url 字段 |
| 重连 backoff 到 30000 后不再恢复 | Site Server 持续不可达 | 等几分钟；如果长期不通，人工 `NET DISCONNECT` 再 `NET CONNECT` |
| relay 重启后不自动连 | NET ON 没持久化 | 重启后 `NET STATUS` 看 `on` 字段是否 true |

### 证据归档

- relay monitor 日志（NET STATUS 连续快照）：`docs/evidence/PHASE08_2_4_HW/test03_relay_usb.log`
- Site Server agent 连接日志：`docs/evidence/PHASE08_2_4_HW/test03_site_agent.log`
- 断网重连恢复记录：`docs/evidence/PHASE08_2_4_HW/test03_reconnection.log`
- 30 分钟运行摘要：`docs/evidence/PHASE08_2_4_HW/test03_summary.md`

---

## Test 04 — RF 扫描与 Wi-Fi 网络并行运行（核心）

### 目的

**验证 C5 持续 RF 扫描 + S3 relay Wi-Fi STA + WS uplink 三者能否稳定并行运行 2 小时以上。**

这是本阶段最关键的测试。之前单独测试都通过不代表并行没问题——ESP32-C5/S3 都是单/双核，Wi-Fi STA 和 RF PHY 可能有资源竞争。

### 前置条件

- **全部三件套**：C5 head + S3 relay + UART 接线 + 共地
- C5 刷 `esp32c5-head`，S3 刷 `esp32s3-relay`
- relay 已配置 NET SSID/PASS/URL 并持久化
- Wi-Fi AP 在附近（RSSI > -60 dBm）
- Site Server 运行中

### 步骤

#### 4.1 基线：单独 C5 扫描（2 分钟）

1. C5 head USB monitor：`PROTOCOL MONITOR` + `METRICS RSSI` + `CHANNELS R1=5658 R4=5695 R7=5732 R8=5752`
2. 记录 `STATUS?` 快照（rotationHz, heap, throttle）
3. 用 Python 脚本抓 C5 USB Serial/JTAG 1 分钟，统计帧速率和丢帧：
   ```python
   # tools/ 目录下如有现成脚本可用；否则：
   import serial, time
   ser = serial.Serial('/dev/cu.usbserial-C5', 115200, timeout=1)
   ser.write(b'PROTOCOL MONITOR\nMETRICS RSSI\nSCAN START\n')
   time.sleep(2)
   start = time.time(); frames = 0; frame_errors = 0
   buf = b''
   while time.time() - start < 60:
       buf += ser.read(2048)
       while len(buf) >= 6:  # min frame size (header 6 bytes)
           idx = buf.find(b'\xaa\x55')
           if idx < 0: break
           if idx > 0: buf = buf[idx:]
           if len(buf) < 6: break
           # 解析 header... 简化版
           frames += 1
           buf = buf[6:]
   print(f'C5-only: {frames} frames in 60s = {frames/60:.1f} Hz')
   ```
4. 保存：`docs/evidence/PHASE08_2_4_HW/test04_c5_only_baseline.txt`

#### 4.2 基线：单独 relay Wi-Fi（2 分钟）

1. S3 relay 不接 C5 UART 线
2. relay USB monitor：`NET CONNECT`
3. 记录 NET STATUS 快照：sta, ws, backoff_ms
4. 记录 Site Server 端 measurements 计数（relay 不接 head 所以无数据上行，但 agent 心跳应正常）
5. 保存：`docs/evidence/PHASE08_2_4_HW/test04_relay_only_baseline.txt`

#### 4.3 全链路并行（2 小时）

1. **接好 C5 ↔ S3 UART 线 + 共地**
2. C5 启动扫描（同 4.1 配置）
3. relay 启动 WS uplink（`NET CONNECT`，如未持久化则重配）
4. **每 5 分钟采集一次**：
   - C5 USB monitor：`STATUS?`（rotationHz, heap）
   - S3 relay USB monitor：`NET STATUS`（完整 JSON）
   - Site Server：`curl http://<site>/api/v1/measurements?limit=1` 或数据库查询最近一条 measurement
   - 用 Python 脚本抓 C5 USB 10 秒统计帧速率（与 4.1 基线对比）
5. **2 小时结束后**：
   - relay `NET STATUS` 看 `backoff_ms`、`sta_downs`（如 ws_link_status 提供）
   - C5 `STATUS?` 看 rotationHz 是否仍稳定
   - Site Server 端：看 measurement 总数是否连续（gap 反映 uplink 中断）
6. **断电恢复**（额外测试项）：
   - 拔掉 S3 relay 的 USB 10 秒
   - 再插回去，观察 relay 自动恢复
   - 拔掉 C5 的 USB 10 秒，插回去，观察 relay head_ready 自动恢复

### 通过判定（核心标准，所有满足才算 PASS）

| # | 条件 | 判定 |
|---|------|------|
| 1 | C5 rotationHz 与 4.1 基线相比下降 ≤ 10% | **通过/失败核心指标** |
| 2 | S3 relay ws=true 持续 ≥ 95% 运行时间（允许 ≤ 5% 短时断连） | **通过/失败核心指标** |
| 3 | Site Server measurement 无超过 30 秒的 gap | PASS |
| 4 | 无任何 panic、无任何板重启（人工断电测试除外） | PASS |
| 5 | S3 relay backoff_ms 从未升到 30000（最大退避），或升到后 ≤ 30 秒恢复 | PASS |
| 6 | C5 断电重启后 relay head_ready 自动恢复 ≤ 10 秒 | PASS |
| 7 | S3 断电重启后自动重连 WS ≤ 60 秒 | PASS |

### 失败诊断

| 现象 | 可能原因 | 检查 |
|------|----------|------|
| rotationHz 下降 > 20% | Wi-Fi STA 和 RF PHY 资源竞争 | ESP32 单 PHY，relay 跑 STA，C5 独立 PHY。**但 relay 不跑 scanner，所以竞争只在 S3 单核之间**；看 S3 CPU 负载 |
| relay 频繁断线重连 | AP 信号差 / S3 heap 不足 | NET STATUS 的 `why` 字段；`heap` 字段 |
| Site Server gap 频繁但 relay 显示 ws=true | relay_pipe 的 relay_lost_down 持续增长 | 这意味着 WS 队列满了，relay 在丢弃 UART 数据——核心瓶颈 |
| C5 和 S3 都重启 | 电源不足导致 brownout | 用独立 5V 电源；电源表测电流峰值 |

### 证据归档

- 2 小时期间 C5 STATUS? 快照：`docs/evidence/PHASE08_2_4_HW/test04_c5_status_series.log`
- 2 小时期间 relay NET STATUS 快照：`docs/evidence/PHASE08_2_4_HW/test04_relay_status_series.log`
- Site Server measurement 时间序列：`docs/evidence/PHASE08_2_4_HW/test04_site_measurements.csv`
- Python 帧速率脚本输出：`docs/evidence/PHASE08_2_4_HW/test04_framerate_comparison.txt`
- 断电恢复测试：`docs/evidence/PHASE08_2_4_HW/test04_recovery.log`

---

## Test 05 — 节点异常恢复

### 目的

**验证系统在各种异常下的自动恢复能力**。

### 前置条件

- 全部三件套稳定运行（Test 04 已通过）
- 笔记本电脑有能力监控两块板的 USB 端口

### 步骤

| # | 测试场景 | 操作 | 预期恢复 | 记录点 |
|---|----------|------|----------|--------|
| 5.1 | Wi-Fi 断 | 拔 AP 电源或走距离 | 指数退避 500ms→30s→500ms，最终 ws=true | relay NET STATUS why, backoff_ms, 恢复耗时 |
| 5.2 | Wi-Fi 弱 | 把 AP 放远或放金属柜后 | sta RSSI 下降但保持连接；如果断了则同 5.1 | relay NET STATUS rssi 字段 |
| 5.3 | S3 断电 30s | 拔 S3 USB 30 秒再插 | 自动起机→STA→WS；C5 继续扫描不受影响 | Site Server 看到 agent offline/online 切换 |
| 5.4 | C5 断电 30s | 拔 C5 USB 30 秒再插 | relay head_ready 短暂 false→true；head_bytes_up 继续 | relay NET STATUS head_ready 变化时间点 |
| 5.5 | 传输中断 | 拔掉 C5→S3 UART 线 10 秒再插 | relay head_ready→false→true；**注意**：relay_pipe 没 UART 数据时 ws 里的帧会断，但 ws 连接不断 | relay NET STATUS；Site Server measurement gap |
| 5.6 | device_boot_id 更新 | 重启 C5 两次 | Site Server ingestion 应该给每次重启分配新的 boot_id（如果实现了） | 查 Site Server Measurement 表的 device_boot_id 字段 |

### 通过判定（每项单独判定）

| 场景 | 通过条件 |
|------|----------|
| 5.1 | 断网恢复 ≤ 2 分钟内 ws=true |
| 5.2 | 弱信号下无 panic，恢复后 ws=true |
| 5.3 | S3 重启后 ≤ 2 分钟 ws=true；C5 继续扫描 |
| 5.4 | C5 重启后 ≤ 10 秒 relay head_ready=true |
| 5.5 | UART 恢复后 ≤ 10 秒 relay head_ready=true；Site Server gap ≤ 15 秒 |
| 5.6 | boot_id 在 C5 重启后变化（如果实现了 boot_id） |

### 证据归档

- 每项测试前后的 NET STATUS 快照
- Site Server agent status 日志
- 全部汇总：`docs/evidence/PHASE08_2_4_HW/test05_summary.md`

---

## Test 06 — 多节点扩展准备（设计类，不刷硬件）

### 目的

**确认现有架构能否扩展到多个独立 RF 节点（每个 C5 head + S3 relay 为一对）。不刷硬件，只做设计验证。**

### 方法

逐点核对以下设计，每项标 VERIFIED / NEEDS CONFIRMATION / GAP。

### 多节点身份分配

**VERIFIED**：
- 每个 S3 relay 有独立 `id` 字段（net_prefs_t.id[33]），可通过 `NET ID` 设置
- 每个 relay 的 `ws://<site>/ws/v1/agents/connect` URL 独立
- site-server ingestion 用 `agent_id + device_timestamp_ms` 或 `agent_id + event_id` 去重（uq_measurement_event 唯一键）

**GAP 需要确认**：
- C5 head 有没有独立的 node_id？查 frames.h 和 scanner 代码。如果没有，需要 site-server 端用 relay 的 agent_id 间接标识
- gateway-agent 端是否支持多 agent 同时注册？

### channel_id 隔离

**VERIFIED**：
- channel_index 只是 0–7 的数组索引（PROFILE_MAX_CHANNELS=8）
- 不同 C5 head 可以有不同的 CHANNELS 配置
- Site Server Measurement 的 payload_json 里存原始 hop_channel_t，包含 channel_index 和 rssi_cdb
- **但 channel_index 0 在不同 head 上可能对应不同频率**（每个 head 有独立的 CHANNELS DEFAULT）
- 正确做法：**Site Server 用 channel_index + agent_id 联合定位**，不能只看 channel_index

### RSSI 独立存储

**VERIFIED**：
- Site Server Measurement 表每条记录携带 agent_id
- 不同 relay 上传的数据天然隔离
- 跨 node 对比需要在查询时按 agent_id 分组

### 时间戳和序号管理

**VERIFIED**：
- device_timestamp_ms 由每个 C5 free-running 生成（无墙钟锚点）
- 每块板重启后 device_timestamp_ms 归零或从上次值继续（取决于代码）
- Site Server 同时存 created_at（服务端时间）、agent_received_at、device_timestamp_ms

**GAP**：
- 多 node 并行时，device_timestamp_ms 之间没有对齐（各自 free-running）
- 这对排序和对齐分析是限制，但对单独 node 的历史回放不影响

### Gateway 管理能力

**VERIFIED**（gateway-agent 代码）：
- gateway-agent 支持多 transport（USB serial, TCP, simulator）
- manager.py 可以管理多个 device
- 每个 device 有独立 identity

**GAP**：
- gateway-agent 和 S3 relay 的关系还需要明确：是 gateway-agent 作为 S3 relay 的 Host（USB Serial/JTAG），还是 S3 relay 直接通过 WS 连 Site Server？
- 当前 relay 直接连 Site Server，绕过 gateway-agent。多 node 场景下这个拓扑是否正确？

### 设计结论

| 项目 | 状态 | 建议 |
|------|------|------|
| 多 node 身份 | VERIFIED（relay id + agent_id） | 每对 relay+head 独立刷写 NET ID |
| channel_index 隔离 | VERIFIED + GAP | Site Server 查询时必须用 agent_id + channel_index 联合 |
| RSSI 独立存储 | VERIFIED | 无需改动 |
| 时间戳对齐 | GAP（已知限制） | Phase 08.3+ 处理，当前接受 |
| Gateway 拓扑 | NEEDS CONFIRMATION | 明确 relay 直连 Site Server 还是 gateway-agent 中转 |

### 证据归档

- 多 node 设计核对：`docs/evidence/PHASE08_2_4_HW/test06_multi_node_design.md`
- 关联 ADR：docs 目录下的架构决策文档（Phase 08.2.1 的 4 份 ADR）

---

## 附录 A：固件启动时的预期日志序列

### C5 head（esp32c5-head）

```
[USB Serial/JTAG 115200]
  (上电后无输出，固件默认 RH 模式 silent)
  (发 PROTOCOL MONITOR 后开始输出)
  INFO → widscan58|esp32c5|proto|版本|时间|PHY version
  STATUS? → {rotationHz=0, mode=GROUP, throttle=1050, ...}
  METRICS RSSI → 开始输出帧
  STATUS? → rotationHz≈300 (GROUP + METRICS RSSI, 8 channels)
```

### S3 relay（esp32s3-relay）

```
[USB Serial/JTAG 115200]
  PROBE pre_wifi heap=... largest=...
  PROBE pre_dial heap=... largest=...
  (如果 NET ON 已配置并连接成功)
  NET STATUS → {"on":true,"sta":true,"ws":true,"backoff_ms":500,"head_ready":true,...}
  (如果 head 接好但没配置 WS)
  NET STATUS → {"head_ready":true,"head_bytes_up":增长中,...,"ws":false,...}
  (如果 head 没接)
  NET STATUS → {"head_ready":false,...}
```

---

## 附录 B：Python 日志采集脚本（需用户确认再执行）

**这些脚本不自动执行。用户需要先连接硬件、跑通 monitor，然后在终端运行。**

### 抓 C5 USB 帧速率

```python
#!/usr/bin/env python3
import serial, time, sys
if len(sys.argv) < 2:
    print(f'用法: {sys.argv[0]} /dev/cu.usbserial-xxx')
    sys.exit(1)
ser = serial.Serial(sys.argv[1], 115200, timeout=1)
ser.write(b'PROTOCOL MONITOR\nMETRICS RSSI\nSCAN START\n')
time.sleep(2)
start = time.time(); frames = 0; buf = b''
while time.time() - start < 60:
    buf += ser.read(4096)
    # 简化计数：FRAME_SYNC0=0xAA FRAME_SYNC1=0x55
    frames += buf.count(b'\xaa\x55')
    buf = buf[-1:]  # 防止跨边界漏计
print(f'60s 内 {frames} 个帧头 → ~{frames/60:.1f} Hz')
```

### 抓 relay NET STATUS 时间序列

```python
#!/usr/bin/env python3
import serial, time, sys, json
if len(sys.argv) < 2:
    print(f'用法: {sys.argv[0]} /dev/cu.usbserial-yyy')
    sys.exit(1)
ser = serial.Serial(sys.argv[1], 115200, timeout=1)
ser.write(b'PROTOCOL MONITOR\n')
time.sleep(1)
while True:
    ser.write(b'NET STATUS\n')
    line = ser.readline().decode(errors='replace').strip()
    if line.startswith('NET {'):
        ts = time.strftime('%H:%M:%S')
        try:
            data = json.loads(line[4:])
            print(f'{ts} sta={data.get("sta")} ws={data.get("ws")} backoff={data.get("backoff_ms")} head={data.get("head_ready")} bytes={data.get("head_bytes_up")}')
        except:
            print(f'{ts} PARSE_FAIL {line[:80]}')
    time.sleep(5)
```

### 分析 Site Server measurement 时间戳 gap

```python
#!/usr/bin/env python3
import urllib.request, json, sys, time
SITE = sys.argv[1]  # http://host:port
req = urllib.request.Request(f'{SITE}/api/v1/measurements?limit=1000')
with urllib.request.urlopen(req) as r:
    data = json.load(r)
items = data.get('data', [])
if not items:
    print('无 measurements')
    sys.exit(0)
# 按 device_timestamp_ms 排序（C5 free-running，相对时间）
items.sort(key=lambda x: x.get('device_timestamp_ms', 0))
gaps = []
for i in range(1, len(items)):
    gap_ms = items[i].get('device_timestamp_ms', 0) - items[i-1].get('device_timestamp_ms', 0)
    if gap_ms > 200:  # 预期帧间隔 < 200 ms
        gaps.append((i, gap_ms))
if gaps:
    print(f'发现 {len(gaps)} 个 gap > 200ms:')
    for idx, g in gaps[:10]:
        print(f'  条目 {idx}: gap {g} ms')
else:
    print(f'连续 {len(items)} 条 measurement，无 gap > 200 ms')
```

---

## 附录 C：Site Server ingestion 统计 SQL（需用户手动执行）

```sql
-- 最近 10 分钟 measurement 数
SELECT COUNT(*), agent_id, MIN(device_timestamp_ms), MAX(device_timestamp_ms)
FROM measurements
WHERE created_at > NOW() - INTERVAL '10 minutes'
GROUP BY agent_id;

-- 按分钟的 measurement 计数（看有没有 gap）
SELECT date_trunc('minute', created_at) as minute, agent_id, COUNT(*)
FROM measurements
WHERE created_at > NOW() - INTERVAL '30 minutes'
GROUP BY 1, 2
ORDER BY 1;

-- boot_id 是否在重启后变化
SELECT DISTINCT device_boot_id, agent_id, MIN(created_at), MAX(created_at)
FROM measurements
GROUP BY device_boot_id, agent_id
ORDER BY agent_id, MIN(created_at);
```
