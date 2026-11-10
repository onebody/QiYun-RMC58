# widscan58：ESP32-C5 ↔ ESP32-S3 双芯片通讯方案（ChatGPT 采集件）

> 由助手通过浏览器自动化向 ChatGPT 网页版提问并原样采集的交付文档；正文为 ChatGPT 回复原文，未经改写。

---

## 一、采集元信息

| 项目 | 内容 |
| --- | --- |
| 平台 | ChatGPT 网页版 https://chatgpt.com/ |
| 账户状态 | 已登录（免费版），复用用户本人浏览器会话，未读取任何凭据 |
| 会话标题 | 双芯片通信方案 |
| 会话链接 | https://chatgpt.com/c/6ae06a2c-b3b0-83ea-bb49-22537505ed8c |
| 采集时间 | 2026-10-27 17:03 – 17:15 CST |
| 采集方式 | 浏览器自动化（独立 Agent 窗口，`bsk` CLI） |
| 回复轮次 | 共 1 轮（回复中不含单选组与交互按钮，无需追问） |
| 正文规模 | 26.6 KB / 9 个一级章节 / 15 个代码块 / 10 张表格 / 6 个内联角标 |
| 项目上下文 | widscan58（本仓库 `repo/firmware`，`esp32c5` 与 `esp32s3` 两个 PlatformIO 环境） |

---

## 二、我的提问（第一轮，原文）

提示词里的资源与协议约束全部取自本仓库现状（`platformio.ini`、`PROJECT-REPORT.md`、`CLAUDE.md`），用于把 ChatGPT 的答案钉在可落地的边界内。

```
我在做一个 ESP32 的 5.8G FPV 视频频段扫描器（开源项目 widscan58），同一份 ESP-IDF 固件用 build flag 编到两块板上：

【ESP32-C5-DevKitC-1 / ESP32-C5-Zero】真实 5.8 GHz 接收端
- PlatformIO + pioarduino 55.03.312-1，ESP-IDF 5.5.5，framework=espidf，4MB flash，单 factory 分区，无 OTA
- Wi-Fi 只以 WIFI_MODE_NULL 启动 PHY，靠混杂模式收原始 IQ/采样，扫描热路径放 IRAM
- 硬约束：esp_wifi_start 之后堆只剩约 5 KB，静态 RAM 只剩几百字节；关闭了控制台/日志/SoftAP
- 对外通信：USB CDC 走自定义二进制帧 + ASCII 命令；另一路 UART0（GP11 TX / GP12 RX，115200 8N1）实现 RotorHazard 串口节点协议 API level 36，这条 UART 的字节流不能被任何别的数据污染
- 关闭 USB CDC 后没有网络栈（TCP/IP/UDP 在 5.8G 扫描时是没跑的）

【ESP32-S3-DevKitC-1】2.4 GHz 线性缩放模拟目标（k = 83.5/296），4MB flash，原生 USB Serial/JTAG，RAM 宽裕，Wi-Fi 全功能可用

现在我想让这两块芯片互相通信：C5 负责 5.8G 侧采集，S3 负责 2.4G/Wi-Fi 侧的桥接、显示和联网上报（例如把 C5 的扫描结果/计圈事件送到 S3，S3 再走 Wi-Fi 到树莓派上的 RotorHazard 或手机 App）。请给我一个完整的通讯方案：

1. 把所有可行的链路做对比表：SPI（master/slave）、UART/RS-422、I2C、I2S、SDIO/EMC、ESP-NOW（2.4G）、Wi-Fi STA+UDP/TCP、BLE、以及有线+无线混合。每条给：实测可达吞吐、单向延迟、C5 侧新增 RAM 与 flash 占用、是否必须在 C5 上打开 Wi-Fi STA（这条最关键，因为开了可能挤掉 5.8G PHY 的采样）、引脚占用、抗干扰与多节点扩展性、代码复杂度。
2. 明确指出哪几条因为「C5 只剩 5KB 堆 / 不能污染 RH UART / 不能离开 WIFI_MODE_NULL」被否掉，理由要落到具体资源数字上。
3. 给出推荐方案的完整设计：物理接线拓扑、帧格式（同步头/版本/类型/序号/长度/CRC/心跳/ACK/背压与丢帧策略）、两端任务划分与队列深度、断连与重连语义、多块 C5 挂一块 S3 的地址分配。
4. 给一份可以照着改的 C 代码骨架（ESP-IDF 5.5 风格，含两端 driver + 帧编解码 + 队列），并说明怎么在现有 native host test 环境里用回环 mock 跑单元测试。
5. 最后给一个分阶段落地计划（先最小验证再扩展），以及每阶段怎么验证链路质量。

请一次给全，包含表格和代码块。
```

---

## 三、第一轮回复（原文整理）

## widscan58：ESP32-C5 ↔ ESP32-S3 双芯片通信完整设计方案

结合你给出的硬件约束，我建议采用 **C5 负责射频采集、S3 负责通信与业务桥接，默认使用有线 UART 或 SPI 连接，S3 再通过 Wi-Fi 与树莓派 RotorHazard 通信** 的双处理器架构。

这里最重要的不是链路理论带宽，而是不能让 C5 的通信功能破坏 5.8 GHz 接收端的运行条件，也不能让普通调试输出污染 RotorHazard 专用 UART。

有一个需要先明确的技术边界：**ESP32-C5 的 5 GHz Wi-Fi PHY 接收能力，不等于它能通过普通 Wi-Fi 混杂模式直接向应用提供连续、通用的原始 IQ 样本流。** 你当前固件的采样方式、PHY 接口和真实接收能力，应以 widscan58 对应源码及实际硬件测试为准。下面把你现有的 5.8 GHz 扫描路径视为已经独立验证的采集子系统，不要求为了芯片间通信改变它。

### 一、总体架构

- **ESP32-C5 · 射频采集端：5.8 GHz 扫描 / RSSI 或采样结果 / 计圈事件**
  WIFI_MODE_NULL · 固定采集热路径 · 专用 RH UART

- **独立有线链路**
  UART（首选验证）或 SPI（后续高吞吐）

- **ESP32-S3 · 通信桥接端：接收解析 / 缓冲 / 显示 / 多设备管理 / Wi-Fi 上报**
  Wi-Fi STA / UDP 或 TCP / 手机 App 接口

- **Wi-Fi 局域网**

- **树莓派**
  RotorHazard / 服务端
  手机 App
  状态、频谱、配置

#### 架构原则

1. C5 只做射频采集、结果整理和必要的有线链路发送，不启动新的无线网络连接。

2. S3 独立管理 Wi-Fi、UDP/TCP、显示、日志和网络重连。

3. C5 的 RotorHazard 专用 UART0（GP11 TX / GP12 RX，115200 8N1）只能由原有 RH 协议模块独占。

4. 其他模块不允许直接向 UART0 写字节；芯片间通信必须走另一组独立引脚。

5. 射频扫描优先级高于通信发送。发送阻塞、S3 断连、网络拥塞都不能反过来阻塞扫描热路径。

以下方案中的吞吐量和 RAM/Flash 数字会明确区分**理论值、工程估算与必须实测的值**，避免把未经你的两块实板验证的数据说成实测结果。

### 二、所有通信链路对比

先看结论：对你当前固件，**UART 最适合第一阶段，SPI 最适合需要更高吞吐量的第二阶段**。ESP-NOW、Wi-Fi STA 和 BLE 都不符合 C5 必须保持 `WIFI_MODE_NULL` 的架构约束。

ESP32-C5 的官方资料确认它具有 UART、SPI、I2C、I2S、SDIO Slave 等外设，并支持 2.4/5 GHz Wi-Fi；但这些硬件能力并不意味着所有外设都适合当前固件。[^1]

#### 2.1 吞吐量、延迟与资源估算

下面的数值是**工程设计预算，不是对你现有板卡的实测数据**。吞吐量按连续有效负载估算；实际值取决于驱动配置、任务调度、DMA、协议开销和引脚布线。

| 链路 | 建议吞吐量目标 | 单向延迟设计目标 | C5 新增 RAM 预算 | C5 Flash 预算 | C5 必须启用 STA？ |
| --- | --- | --- | --- | --- | --- |
| UART 115200 | 8–10 KB/s | 0.5–3 ms | 1–3 KB | 4–12 KB | 否 |
| UART 921600 | 70–90 KB/s | 0.2–2 ms | 1–3 KB | 4–12 KB | 否 |
| UART 2 Mbps | 150–190 KB/s | 0.2–2 ms | 1–4 KB | 4–14 KB | 否 |
| SPI 4 MHz | 0.3–0.45 MB/s | 0.1–1 ms | 1–4 KB | 6–18 KB | 否 |
| SPI 10 MHz | 0.8–1.1 MB/s | 0.1–0.8 ms | 2–5 KB | 6–20 KB | 否 |
| I2C 400 kHz | 25–38 KB/s | 0.5–3 ms | 0.5–2 KB | 3–10 KB | 否 |
| I2S 自定义数字流 | 取决于时钟和 DMA | 0.1–1 ms | 2–6 KB | 8–24 KB | 否 |
| SDIO Slave | 取决于主机驱动及 DMA | 需实测 | 3–10 KB | 10–30 KB | 否 |
| ESP-NOW | 约 0.1–1 MB/s 作为初始验证范围 | 1–10 ms | 额外数 KB 至数十 KB，另有 Wi-Fi 驱动开销 | 20–80 KB 以上 | 需要启用无线数据接口 |
| Wi-Fi STA + UDP | 约 0.5–3 MB/s 作为应用设计目标 | 1–10 ms | 额外数十 KB 级，取决于配置 | 20–80 KB 以上 | 是 |
| Wi-Fi STA + TCP | 约 0.3–2 MB/s 作为应用设计目标 | 2–20 ms | 额外数十 KB 级，含网络缓冲 | 30–100 KB 以上 | 是 |
| BLE GATT | 5–100 KB/s，依连接参数而定 | 7.5–50 ms 级或更高 | 数 KB 至数十 KB | 20–80 KB 以上 | 需要启用 BLE 控制器及 Host |
| 有线 + S3 Wi-Fi | 取决于有线链路 | 有线部分同上 | 取决于 UART/SPI 实现 | 取决于有线实现 | 否，Wi-Fi 由 S3 负责 |

注意事项：

- UART 的 115200、8N1，每个有效字节通常占用 10 个线上比特，因此理论极限为 115200/10=11520 字节/秒。协议头、CRC、ACK 和空闲间隔还会进一步降低净吞吐量。

- SPI 的理论带宽分别为 4 MHz ÷ 8 = 0.5 MB/s、10 MHz ÷ 8 = 1.25 MB/s；这是单方向理想时钟吞吐量，不等于 ESP-IDF 驱动的实测值。

- UART/SPI 的 RAM 预算假设使用固定大小静态缓冲区和 FreeRTOS 静态队列，不包含额外的大型日志系统、动态分配器开销或现有 Wi-Fi PHY 基础占用。

- 无线方案的 Flash/RAM 数字只能作为早期预算。ESP-IDF 具体版本、Kconfig、BLE Host 选择及是否启用加密都会改变最终数值。

**当前最重要的限制是：C5 在 **`esp_wifi_start()`** 后只剩约 5 KB 堆，静态 RAM 也只剩几百字节。** 这意味着即使某个通信方案在芯片硬件上可用，也未必能安全地集成到当前固件。

#### 2.2 引脚、抗干扰、多节点和复杂度

| 链路 | 典型信号线 | 抗干扰 | 多 C5 接一块 S3 | 实现复杂度 |
| --- | --- | --- | --- | --- |
| UART | TX、RX、GND | 中；短线较好 | 每个 C5 独立 UART，或总线收发器 | 低 |
| SPI | SCLK、MOSI、MISO、CS、GND | 中至高，取决于布线 | 高；共享时钟和数据线，每个 C5 独立 CS | 中高 |
| I2C | SDA、SCL、GND | 中低；开漏上拉，易受线长影响 | 可以，共享总线需地址管理 | 中 |
| I2S | BCLK、WS、数据线、GND | 中；时序要求较高 | 可做，但不是普通通用通信的优选 | 高 |
| SDIO | CLK、CMD、DAT 线 | 高速布线要求高 | 理论可行，软件和主机侧复杂 | 很高 |
| ESP-NOW | 无线 | 易受同频拥塞影响 | 可管理多个 peer | 中高 |
| Wi-Fi UDP/TCP | 无线 | 易受信道拥塞、重传和掉线影响 | 可通过网络寻址扩展 | 中 |
| BLE | 无线 | 2.4 GHz 共存干扰 | 可以，但连接数与调度受限 | 高 |
| UART/SPI + S3 Wi-Fi | 有线加无线 | 有线段可控，网络段受无线影响 | 很好，S3 统一管理 | 中 |

上述引脚数只表示典型信号线，不是对 DevKitC-1 或 C5-Zero 的实际引脚可用性确认。你需要避开启动绑带脚、板载 USB/JTAG、Flash/PSRAM 占用脚，以及现有 RH UART 引脚。

尤其要注意：SPI 在共享总线时，每个 C5 都需要独立片选；UART 如果多块 C5 直接共用同一组 TX/RX，会产生总线争用，不能把普通 UART 当成多主机总线。

### 三、哪些方案应当淘汰？

#### 3.1 当前版本直接排除的方案

- **C5 Wi-Fi STA + UDP/TCP**
  必须在 C5 上建立可用于收发网络数据的接口。ESP-IDF 文档说明，WIFI_MODE_STA 会初始化 STA 内部数据结构，提供 Wi-Fi 数据收发能力；WIFI_MODE_NULL 不初始化 STA/AP 数据接口。[^2]
  你现在只剩约 5 KB 堆，无法在未重新核算完整内存账本的情况下承诺 STA、IP、UDP/TCP 及网络缓冲能够共存。因此本版排除。

- **C5 ESP-NOW**
  ESP-NOW 依赖 Wi-Fi 接口处于启用状态，不能视为一个完全独立于 Wi-Fi PHY/驱动的轻量 GPIO 协议。它会增加 peer 管理、发送队列、接收回调等资源，也会改变射频使用行为。[^3]
  对目前以 WIFI_MODE_NULL 保持 5.8 GHz 扫描路径的固件，不应为了连接 S3 而启用 ESP-NOW。

- **C5 BLE**
  BLE 同样会启用额外无线控制器和 Host 相关组件，并占用内部 RAM。虽然 ESP32-C5 支持 BLE，但在堆和静态 RAM 预算如此紧张的情况下，不值得承担这个成本。[^4]

#### 3.2 不直接否决，但当前不推荐

- **I2C：** 可以工作，但 400 kHz 的有效吞吐量有限，且对长线、总线电容和多设备故障恢复较敏感。适合低频状态、配置和控制，不适合作为高频扫描数据主链路。

- **I2S：** 虽然 C5 有 I2S 外设，但要额外设计时钟、帧同步、DMA 缓冲和收发协议。对不需要传输音频或高速同步流的场景，复杂度高于收益。

- **SDIO Slave：** C5 芯片具备 SDIO Slave 能力，但要核对芯片修订版、ESP-IDF 5.5.5 驱动、主机端实现及 DMA 资源。它并不是把 GPIO 接上就能完成的通用高速链路。[^5]

- **SPI：** 值得保留，尤其适合未来要传输高频扫描曲线或大量样本数据的版本。

- **UART：** 当前最适合先验证。只要采用独立 UART、固定静态缓冲和非阻塞发送，就能将对 C5 的侵入降到最低。

#### 3.3 资源预算必须怎样落地？

不能只看某个组件的静态 RAM 大小，也不能仅凭 `heap_caps_get_free_size()` 就判断安全。

你需要在 C5 启动扫描前后分别记录：

```C
#include "esp_heap_caps.h"
#include "esp_log.h"

static void report_memory(const char *stage)
{
    ESP_LOGI("MEM", "%s: free=%u, min=%u, largest=%u",
             stage,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
}
```

注意：如果你关闭了控制台日志，以上数据应通过调试构建、独立测试端口或 RAM 统计区读取，不能把诊断文本写入 RH UART。实际生产构建不必保留这段日志代码。

建议的资源验收门槛：

| 项目 | 初始验收目标 |
| --- | --- |
| C5 链路模块新增动态堆分配 | 0 字节（初始化完成后） |
| C5 链路缓冲 | 固定静态分配，优先控制在 1–2 KB |
| C5 发送路径 | 不等待 S3、不阻塞扫描 |
| C5 扫描频率/时间 | 与未加入链路时相比，变化不超过预先定义的容差 |
| RH UART | 无任何新增输出源 |
| C5 内存 | 保留足够的最坏情况余量，不以“刚好能启动”为通过标准 |

这里的 1–2 KB 是新增链路的目标预算，不是说你现在几百字节的剩余静态 RAM 一定足够。若静态 RAM 已经几乎用尽，就必须先从原有固件中腾出静态内存，或通过编译映射、链接器 map 文件确认能否将专用缓冲放进可用内存区域；不能把所有静态数组直接堆上去。

### 四、推荐的物理连接与接口分配

#### 4.1 第一阶段：UART 双向通信

先用 UART 建立最小闭环。不要直接把它和现有的 RH UART0 复用。

```text
ESP32-C5
独立 UART TX
独立 UART RX
GND
ESP32-S3
独立 UART RX
独立 UART TX
GND
C5 TX
S3 RX
C5 RX
S3 TX
C5 GND
S3 GND
```

电气要求：

- 两块开发板的 UART 都使用 3.3 V TTL 电平，TX 接对方 RX，RX 接对方 TX，必须共地。

- 不要将 UART 信号直接接到 RS-422 差分收发器的 A/B 线上；RS-422 需要收发器。

- 初期采用短线，建议先从 115200 或 460800 baud 开始验证，再升到 921600 baud。

- C5 的新 UART 引脚应从该板实际可用 GPIO 中选择，并在 `menuconfig`、板级配置和原理图中确认。

- 不要默认 GPIO 编号等于排针标号。C5-DevKitC-1 和 C5-Zero 的排针定义并不一定相同。

- S3 原生 USB Serial/JTAG 保留给开发和诊断，业务通信使用另一组 UART 引脚。

**重要：** 你已明确 RH 使用 GP11 TX / GP12 RX。这一对引脚及其 UART 控制器的归属，应由现有 RH 模块继续独占。新链路不得调用该 UART 的 `uart_write_bytes()`，也不得把新链路日志输出到同一 UART。

#### 4.2 第二阶段：SPI 高速链路

如果扫描数据量很大，升级为 SPI。

建议由 S3 做 SPI Master，C5 做 SPI Slave。ESP-IDF 的 SPI Slave 驱动支持事务队列和 DMA，但需要正确管理 DMA 缓冲区、事务长度与从机就绪状态；官方文档还特别建议通过额外 GPIO 做握手，避免主机在从机未准备好时启动事务。[^6]

| 信号 | 方向 | 说明 |
| --- | --- | --- |
| S3 SCLK → C5 SCLK | S3 到 C5 | SPI 时钟 |
| S3 MOSI → C5 MOSI | S3 到 C5 | S3 发送命令、C5 接收 |
| C5 MISO → S3 MISO | C5 到 S3 | C5 返回扫描数据 |
| S3 CS → C5 CS | S3 到 C5 | 片选 |
| C5 READY → S3 GPIO | C5 到 S3 | C5 通知有数据可取 |
| GND ↔ GND | — | 共地 |

建议从 1 MHz 或 4 MHz 起步，验证无误后再提升到 10 MHz。SPI 的数据帧可以继续使用后面定义的逻辑协议，只是物理层改为定长事务或带长度头的事务。

不要把 UART 版协议实现与 UART 驱动写死在一起。应该将它们拆成：

- `link_codec.c`：帧编解码。

- `link_transport_uart.c`：UART 传输。

- `link_transport_spi.c`：SPI 传输。

- `link_endpoint.c`：序号、ACK、重连、背压与消息处理。

这样后续从 UART 切换 SPI，不需要重写上层消息协议。

### 五、统一的芯片间协议设计

#### 5.1 帧格式

建议第一版使用固定小头部、最大 256 字节 Payload、CRC-16/CCITT-FALSE。

| 字段 | 长度 | 说明 |
| --- | --- | --- |
| Sync | 2 B | 0xA5 0x5A |
| Version | 1 B | 当前为 1 |
| Type | 1 B | 消息类型 |
| Source | 1 B | C5 节点 ID 或 S3 ID |
| Flags | 1 B | ACK 请求、是否为响应等 |
| Sequence | 2 B | 消息序号，小端 |
| Length | 2 B | Payload 字节数，小端 |
| Payload | 0–256 B | 消息数据 |
| CRC16 | 2 B | 覆盖 Version 至 Payload |

头部共 10 字节；不含同步头与 CRC 时，元数据字段占 8 字节。最大帧长度为 2+8+256+2=268 字节。

CRC 参数：

- 多项式：`0x1021`

- 初始值：`0xFFFF`

- 输入/输出不反转

- 最终异或：`0x0000`

- CRC 覆盖范围：从 Version 到 Payload 最后一个字节，不包含 Sync 与 CRC 自身

- CRC 在链路中按小端发送

不要直接把 C 结构体强制转换为字节数组发送。编译器可能插入 padding，而且不同平台的字节序可能不同。必须显式序列化每个字段。

#### 5.2 消息类型与语义

| Type | 名称 | 方向 | 可靠性 |
| --- | --- | --- | --- |
| 0x01 | HELLO | 双向 | 请求/响应 |
| 0x02 | HEARTBEAT | 双向 | 周期发送 |
| 0x03 | SCAN_RESULT | C5 → S3 | 允许合并旧数据 |
| 0x04 | LAP_EVENT | C5 → S3 | 必须可靠确认 |
| 0x05 | ACK | 双向 | 可靠消息的确认 |
| 0x06 | FLOW_CONTROL | 双向 | 背压 |
| 0x07 | CONFIG | S3 → C5 | 请求/响应 |
| 0x08 | STATUS | 双向 | 状态查询与响应 |
| 0x09 | ERROR | 双向 | 错误通知 |

建议的 Payload 定义：

- `HELLO`：协议版本、节点 ID、固件版本、能力位。

- `HEARTBEAT`：运行时间、当前状态、待发送队列深度、累计丢帧数。

- `SCAN_RESULT`：频率索引、RSSI/归一化强度、采样时间戳、扫描周期编号。

- `LAP_EVENT`：计圈 ID、节点 ID、圈数、事件时间戳、信号强度、事件来源。

- `ACK`：确认的消息类型、确认的序号、状态码。

- `FLOW_CONTROL`：接收窗口、暂停/恢复标志、允许的最大消息速率。

计圈事件应使用唯一事件 ID，不能只依靠 UART 序号判断是否重复。链路断开重连后，S3 可能重发 ACK 未确认的消息；C5 或 S3 必须能识别已处理事件，避免重复计圈。

#### 5.3 心跳、ACK 与丢帧策略

建议第一版采用以下参数：

| 参数 | 初始值 |
| --- | --- |
| 心跳间隔 | 500 ms |
| 断连判定 | 连续 3 次心跳超时，约 1.5 s |
| ACK 超时 | 100 ms |
| 最大重试 | 3 次 |
| 扫描数据队列 | 8 项 |
| 计圈事件队列 | 8 项 |
| 接收解析缓冲 | 268 B 帧缓冲 + 固定 RX 缓冲 |
| 最大 Payload | 256 B |
| 流控高水位 | 队列 75% |
| 流控低水位 | 队列 25% |

这些值是第一版的工程起点，后续需要依据扫描速率、计圈事件峰值和实测链路质量调整。

丢帧策略要区分数据类别：

- **扫描曲线：** 允许合并或覆盖旧结果。若 S3 来不及接收，应保留最新一帧，而不是让队列无限增长。

- **计圈事件：** 不允许静默丢弃。事件进入固定队列后等待 ACK；超时重发，达到重试上限则记录明确错误并报告给上层。

- **状态和心跳：** 只保留最新状态即可，不需要无限重试。

- **配置命令：** 必须返回成功/失败状态，并携带请求序号。

- **断连恢复：** 先重新握手，再发送当前状态，之后补发仍在保留期内且未确认的计圈事件。

需要特别注意：如果 C5 的 RAM 无法容纳可靠事件队列，不能声称计圈事件具备断电不丢失保证。若要求断电恢复，需要单独设计持久化方案；这与“扫描热路径不分配内存”的约束应分开评估。

#### 5.4 多块 C5 挂一块 S3

UART 点对点拓扑下，每块 C5 占用 S3 的一组 UART，数量会受可用硬件 UART 和 GPIO 限制。

如果后续要连接多块 C5，建议优先考虑：

1. SPI 共享 SCLK、MOSI、MISO，每块 C5 独立 CS 和 READY。

2. 或每块 C5 配置一个 UART，再由 S3 轮询和调度。

3. 如果线长较大或现场电磁干扰较强，考虑每个节点独立 RS-422 收发器，再由 S3 多路 UART 或外部多通道 UART 扩展。

节点 ID 不应仅靠烧录时的临时变量。推荐由 S3 在发现阶段为每块 C5 分配逻辑 ID，或由 C5 使用固化配置的唯一 ID。逻辑 ID 用于消息路由，不能替代物理链路寻址。

### 六、可直接改造的 C 代码骨架

下面给出一套以 ESP-IDF 5.5 为目标的核心实现。第一版采用 UART，使用静态缓冲和静态队列，不创建动态堆缓冲。代码刻意将协议与驱动分离。

需要说明：这是一套可集成的核心骨架，不是已经针对你的 widscan58 仓库编译通过的补丁。GPIO、UART 控制器编号、FreeRTOS 配置及组件依赖仍需根据两块板的实际工程调整。

#### 6.1 建议目录

> 页面代码块标签：纯文本

```text
widscan58/
├── main/
│   ├── app_main.c
│   ├── link_codec.c
│   ├── link_codec.h
│   ├── link_endpoint.c
│   ├── link_endpoint.h
│   ├── link_transport.h
│   ├── link_transport_uart.c
│   ├── c5_bridge.c
│   └── s3_bridge.c
├── test/
│   └── native/
│       ├── test_link_codec.c
│       ├── test_link_loopback.c
│       └── Makefile
└── CMakeLists.txt
```

#### 6.2 帧编解码：link_codec.h

```C
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define LINK_SYNC0       0xA5
#define LINK_SYNC1       0x5A
#define LINK_VERSION     1
#define LINK_MAX_PAYLOAD 256
#define LINK_HEADER_SIZE 10
#define LINK_OVERHEAD    14
#define LINK_MAX_FRAME   (LINK_OVERHEAD + LINK_MAX_PAYLOAD)

enum {
    LINK_HELLO        = 0x01,
    LINK_HEARTBEAT    = 0x02,
    LINK_SCAN_RESULT  = 0x03,
    LINK_LAP_EVENT    = 0x04,
    LINK_ACK          = 0x05,
    LINK_FLOW_CONTROL = 0x06,
    LINK_CONFIG       = 0x07,
    LINK_STATUS       = 0x08,
    LINK_ERROR        = 0x09
};

enum {
    LINK_FLAG_ACK_REQ = 1u << 0,
    LINK_FLAG_RESPONSE = 1u << 1
};

typedef struct {
    uint8_t version;
    uint8_t type;
    uint8_t source;
    uint8_t flags;
    uint16_t sequence;
    uint16_t length;
    uint8_t payload[LINK_MAX_PAYLOAD];
} link_frame_t;

uint16_t link_crc16(const uint8_t *data, size_t len);

bool link_encode(const link_frame_t *f,
                 uint8_t *out, size_t cap, size_t *out_len);

bool link_decode(const uint8_t *in, size_t len, link_frame_t *out);
```

#### 6.3 编解码实现：link_codec.c

```C
#include "link_codec.h"
#include <string.h>

static void put_u16le(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)(v >> 8);
}

static uint16_t get_u16le(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

uint16_t link_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;

    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint16_t)data[i] << 8;

        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000)
                ? (uint16_t)((crc << 1) ^ 0x1021)
                : (uint16_t)(crc << 1);
        }
    }

    return crc;
}

bool link_encode(const link_frame_t *f,
                 uint8_t *out, size_t cap, size_t *out_len)
{
    if (!f || !out || !out_len ||
        f->version != LINK_VERSION ||
        f->length > LINK_MAX_PAYLOAD) {
        return false;
    }

    const size_t total = LINK_OVERHEAD + f->length;
    if (cap < total) {
        return false;
    }

    out[0] = LINK_SYNC0;
    out[1] = LINK_SYNC1;
    out[2] = f->version;
    out[3] = f->type;
    out[4] = f->source;
    out[5] = f->flags;
    put_u16le(out + 6, f->sequence);
    put_u16le(out + 8, f->length);

    if (f->length != 0) {
        memcpy(out + LINK_HEADER_SIZE, f->payload, f->length);
    }

    uint16_t crc = link_crc16(out + 2, 8 + f->length);
    put_u16le(out + LINK_HEADER_SIZE + f->length, crc);

    *out_len = total;
    return true;
}

bool link_decode(const uint8_t *in, size_t len, link_frame_t *out)
{
    if (!in || !out || len < LINK_OVERHEAD) {
        return false;
    }

    if (in[0] != LINK_SYNC0 || in[1] != LINK_SYNC1 ||
        in[2] != LINK_VERSION) {
        return false;
    }

    uint16_t payload_len = get_u16le(in + 8);

    if (payload_len > LINK_MAX_PAYLOAD ||
        len != LINK_OVERHEAD + payload_len) {
        return false;
    }

    uint16_t expected =
        link_crc16(in + 2, 8 + payload_len);

    uint16_t received =
        get_u16le(in + LINK_HEADER_SIZE + payload_len);

    if (expected != received) {
        return false;
    }

    out->version = in[2];
    out->type = in[3];
    out->source = in[4];
    out->flags = in[5];
    out->sequence = get_u16le(in + 6);
    out->length = payload_len;

    if (payload_len) {
        memcpy(out->payload, in + LINK_HEADER_SIZE, payload_len);
    }

    return true;
}
```

这个版本的 `link_decode()` 处理的是一帧完整的数据，因此 UART 接收端还需要一个流式解析器：它从连续字节流中寻找同步头、读取长度、收集完整帧，再调用 `link_decode()`。不能假设一次 `uart_read_bytes()` 就会返回一整帧。

#### 6.4 流式解析器

下面的解析器不使用堆。它逐字节输入，并在接收到完整有效帧时返回 `true`。

```C
#pragma once
#include "link_codec.h"

typedef struct {
    uint8_t buf[LINK_MAX_FRAME];
    size_t used;
    size_t expected;
} link_parser_t;

void link_parser_init(link_parser_t *p);
bool link_parser_feed(link_parser_t *p, uint8_t byte,
                     link_frame_t *out);
```

```C
#include "link_codec.h"
#include "link_parser.h"
#include <string.h>

void link_parser_init(link_parser_t *p)
{
    memset(p, 0, sizeof(*p));
}

bool link_parser_feed(link_parser_t *p, uint8_t b,
                      link_frame_t *out)
{
    if (p->used == 0) {
        if (b == LINK_SYNC0) {
            p->buf[p->used++] = b;
        }
        return false;
    }

    if (p->used == 1) {
        if (b == LINK_SYNC1) {
            p->buf[p->used++] = b;
        } else {
            p->used = (b == LINK_SYNC0) ? 1 : 0;
            if (p->used) p->buf[0] = b;
        }
        return false;
    }

    if (p->used >= sizeof(p->buf)) {
        link_parser_init(p);
        return false;
    }

    p->buf[p->used++] = b;

    if (p->used == LINK_HEADER_SIZE) {
        uint16_t n = (uint16_t)p->buf[8] |
                     ((uint16_t)p->buf[9] << 8);

        if (p->buf[2] != LINK_VERSION || n > LINK_MAX_PAYLOAD) {
            link_parser_init(p);
            return false;
        }

        p->expected = LINK_OVERHEAD + n;
    }

    if (p->expected && p->used == p->expected) {
        bool ok = link_decode(p->buf, p->used, out);
        link_parser_init(p);
        return ok;
    }

    return false;
}
```

这里要注意一个协议实现细节：CRC 错误后直接清空解析状态是最简单的做法，但如果错误帧内部包含另一个同步头，会丢掉这次重叠同步机会。第一版可以接受这种行为；若现场有较高噪声，应升级为滑动窗口重新同步，并统计 CRC 错误和重新同步次数。

#### 6.5 传输层接口

`link_transport.h`：

```C
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    void *ctx;

    // 发送整个缓冲区；返回 false 表示未完整入队或失败。
    bool (*send)(void *ctx, const uint8_t *data, size_t len);

    // 非阻塞接收字节；无数据时返回 false。
    bool (*recv_byte)(void *ctx, uint8_t *out);
} link_transport_t;
```

这里的 `send()` 不是让扫描任务等待 UART 发完所有字节。它应该只把帧提交给独立的发送任务，或提交给已经准备好的固定 TX 缓冲区。实际 UART 驱动负责异步发送。

#### 6.6 C5 UART 驱动骨架

C5 的 UART 实例和引脚需要你按板级实际情况填写。下面演示配置结构；`CONFIG_LINK_UART_NUM`、`CONFIG_LINK_TX_GPIO` 和 `CONFIG_LINK_RX_GPIO` 是你需要在项目配置中定义的宏。

```C
#include "driver/uart.h"
#include "driver/gpio.h"
#include "esp_err.h"

#include "sdkconfig.h"

#ifndef CONFIG_LINK_UART_NUM
#error "Define CONFIG_LINK_UART_NUM for the dedicated C5-S3 link"
#endif

#ifndef CONFIG_LINK_TX_GPIO
#error "Define CONFIG_LINK_TX_GPIO"
#endif

#ifndef CONFIG_LINK_RX_GPIO
#error "Define CONFIG_LINK_RX_GPIO"
#endif

#define LINK_UART_NUM ((uart_port_t)CONFIG_LINK_UART_NUM)
#define LINK_BAUD     460800
#define LINK_RX_BUF   512
#define LINK_TX_BUF   0

static void link_uart_init(void)
{
    const uart_config_t cfg = {
        .baud_rate = LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT
    };

    ESP_ERROR_CHECK(uart_driver_install(
        LINK_UART_NUM, LINK_RX_BUF, LINK_TX_BUF, 0, NULL, 0));

    ESP_ERROR_CHECK(uart_param_config(LINK_UART_NUM, &cfg));

    ESP_ERROR_CHECK(uart_set_pin(
        LINK_UART_NUM,
        CONFIG_LINK_TX_GPIO,
        CONFIG_LINK_RX_GPIO,
        UART_PIN_NO_CHANGE,
        UART_PIN_NO_CHANGE));
}
```

这个初始化函数使用了 ESP-IDF UART 驱动的内部缓冲，因此它仍然会带来额外内存开销。尤其是 `LINK_TX_BUF = 0` 并不代表“整个链路零缓冲”，也不代表可以绕过 UART 驱动的约束。

**针对 C5 只剩 5 KB 堆的现状，实际集成时应优先考虑：**

- 先在测试构建中初始化 UART，测量初始化前后堆变化。

- 评估 UART 驱动内部 RX 环形缓冲、队列和中断分配。

- 如果驱动分配不可接受，采用更精简的专用驱动实现，或先为链路释放足够资源。

- 不要直接在扫描 ISR 中调用 `uart_write_bytes()`。

- 不能为了节省几个 KB 而贸然改写中断驱动；必须验证 IRAM、ISR 延迟和缓存访问安全。

#### 6.7 固定队列与任务划分

建议两端的任务分工如下。

| 任务 | C5 | S3 |
| --- | --- | --- |
| RF Scan | 最高优先级，保持原有实现 | 无 |
| Event Producer | 非阻塞提交扫描结果/计圈事件 | 无 |
| Link RX | 解析 S3 下发命令与 ACK | 解析 C5 帧 |
| Link TX | 异步发送固定缓冲区中的帧 | 异步发送控制命令 |
| Protocol | 维护序号、ACK 和状态 | 路由事件、管理节点、去重 |
| Network | 无 | Wi-Fi STA、UDP/TCP |
| UI | 无 | 显示频谱、连接和错误状态 |

C5 侧推荐队列：

- 扫描结果：8 项，可覆盖最旧数据。

- 计圈事件：8 项，优先级高于扫描结果。

- RX 字节缓冲：512 B 起步。

- TX 帧缓冲：2–4 帧，按 64–268 B 的实际帧长控制。

这里有一个资源矛盾需要你特别注意：如果每个队列元素都直接包含一个 `link_frame_t`，每项结构体约 264 B（还可能有对齐开销），8 项就可能超过 2 KB。因此，**C5 不应该为每个队列项都分配完整的 256 B Payload 结构体**。扫描结果应使用小型专用结构体，计圈事件也应使用紧凑结构体，只有在序列化时才构造完整帧。

例如：

```C
typedef struct {
    uint32_t timestamp_ms;
    uint16_t frequency_index;
    int16_t strength;
} scan_item_t;

typedef struct {
    uint32_t event_id;
    uint32_t timestamp_ms;
    uint16_t lap;
    uint8_t node_id;
    int16_t strength;
} lap_item_t;
```

使用 FreeRTOS 静态队列：

```C
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#define SCAN_QUEUE_LEN 8
#define LAP_QUEUE_LEN  8

static StaticQueue_t scan_queue_ctrl;
static StaticQueue_t lap_queue_ctrl;

static uint8_t scan_queue_storage[
    SCAN_QUEUE_LEN * sizeof(scan_item_t)];

static uint8_t lap_queue_storage[
    LAP_QUEUE_LEN * sizeof(lap_item_t)];

static QueueHandle_t scan_queue;
static QueueHandle_t lap_queue;

static void queues_init(void)
{
    scan_queue = xQueueCreateStatic(
        SCAN_QUEUE_LEN,
        sizeof(scan_item_t),
        scan_queue_storage,
        &scan_queue_ctrl);

    lap_queue = xQueueCreateStatic(
        LAP_QUEUE_LEN,
        sizeof(lap_item_t),
        lap_queue_storage,
        &lap_queue_ctrl);
}
```

如果当前扫描结果是在 ISR 中生成，应使用对应的 `FromISR` API，并检查 `higher_priority_task_woken`；如果是在任务中生成，才使用普通 `xQueueSend()`。

当扫描队列已满时，可以覆盖旧扫描结果，但要增加 `scan_drop_count`。计圈队列满时，不能静默丢弃，必须触发错误状态或保留事件供上层恢复。

#### 6.8 S3 网络桥接

S3 接收到 C5 的 `SCAN_RESULT` 后，不应该直接在 UART RX 回调或 ISR 中执行 UDP/TCP 发送。建议先进入 S3 的业务队列，再由网络任务发送。

- 频谱结果：优先使用 UDP，允许丢弃过时曲线。

- 计圈事件：如果 RotorHazard 服务端有明确的可靠接收 API，可使用 TCP 或应用层 ACK。

- 手机 App：可以在 S3 上运行 WebSocket 服务，或由 App 通过网络请求 S3 的数据接口。

- 多个客户端：由 S3 维护订阅关系，不让 C5 直接管理网络连接。

这里不能假定 RotorHazard 的现有网络接口天然接受本方案定义的二进制帧。你需要实现一个明确的协议适配器，把 `LAP_EVENT` 转换为 RotorHazard 现有节点协议或其实际支持的上报接口。尤其不能将新的二进制帧直接混入现有 RH UART 字节流。

### 七、native host test：不接硬件先验证协议

你现有的 native host test 环境很适合先验证 `link_codec.c`、流式解析器、CRC、队列策略和断连状态机。建议先把协议核心做成不依赖 ESP-IDF 的纯 C 模块，再编译进两个固件。

#### 7.1 推荐的模块边界

> 页面代码块标签：纯文本

```text
                 ┌───────────────────────┐
                 │    link_endpoint.c    │
                 │ ACK / seq / timeout   │
                 │ dedup / backpressure  │
                 └───────────┬───────────┘
                             │
                 ┌───────────▼───────────┐
                 │      link_codec.c     │
                 │ encode / decode / CRC │
                 │ stream parser         │
                 └───────────┬───────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
       ┌────────▼────────┐       ┌────────▼────────┐
       │ UART transport  │       │ Mock transport  │
       │ ESP-IDF         │       │ Native host     │
       └─────────────────┘       └─────────────────┘
```

核心要求是 `link_codec.c` 不包含 `freertos/FreeRTOS.h`、`driver/uart.h` 或 `esp_log.h`。这样同一份源文件可以直接在 macOS/Linux 的 native 测试程序中编译。

#### 7.2 回环 Mock 测试

下面的测试不需要接开发板，可以先验证一帧数据经过编码、逐字节传输、解析和解码后是否保持一致。

`test_link_loopback.c`：

```C
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "link_codec.h"
#include "link_parser.h"

int main(void)
{
    link_frame_t tx = {0};
    tx.version = LINK_VERSION;
    tx.type = LINK_LAP_EVENT;
    tx.source = 1;
    tx.flags = LINK_FLAG_ACK_REQ;
    tx.sequence = 123;
    tx.length = 6;

    const unsigned char payload[] = {
        0x34, 0x12, 0x78, 0x56, 0x01, 0x7F
    };

    memcpy(tx.payload, payload, sizeof(payload));

    unsigned char wire[LINK_MAX_FRAME];
    size_t wire_len = 0;

    assert(link_encode(&tx, wire, sizeof(wire), &wire_len));
    assert(wire_len == LINK_OVERHEAD + tx.length);

    link_parser_t parser;
    link_parser_init(&parser);

    link_frame_t rx = {0};
    int complete_frames = 0;

    for (size_t i = 0; i < wire_len; ++i) {
        if (link_parser_feed(&parser, wire[i], &rx)) {
            complete_frames++;
        }
    }

    assert(complete_frames == 1);
    assert(rx.version == tx.version);
    assert(rx.type == tx.type);
    assert(rx.source == tx.source);
    assert(rx.flags == tx.flags);
    assert(rx.sequence == tx.sequence);
    assert(rx.length == tx.length);
    assert(memcmp(rx.payload, tx.payload, tx.length) == 0);

    puts("PASS: encode -> loopback -> parser -> decode");
    return 0;
}
```

#### 7.3 Makefile

```Makefile
CC ?= cc
CFLAGS ?= -std=c11 -O2 -Wall -Wextra -Werror

TEST_SRCS = \
    test_link_loopback.c \
    ../../main/link_codec.c \
    ../../main/link_parser.c

INCLUDES = -I../../main

all: test_link_loopback

test_link_loopback: $(TEST_SRCS)
	$(CC) $(CFLAGS) $(INCLUDES) $(TEST_SRCS) -o $@

test: test_link_loopback
	./test_link_loopback

clean:
	rm -f test_link_loopback
```

上面假设 `test/native` 的相对目录布局与前面一致。如果你已经有现成的 native test 框架，直接把测试文件加入现有目标即可。

#### 7.4 必须补齐的测试用例

| 测试 | 操作 | 通过条件 |
| --- | --- | --- |
| 基本回环 | 编码后逐字节输入解析器 | 字段与 Payload 完全一致 |
| 最大帧 | 发送 256 B Payload | 正确解码，不越界 |
| 空 Payload | 发送长度为 0 的帧 | 正确解码 |
| CRC 错误 | 修改 Payload 中一个字节 | 拒绝该帧 |
| 错误长度 | 将长度设为 257 | 拒绝且不越界 |
| 截断帧 | 只输入半帧 | 不提前报告成功 |
| 粘连帧 | 连续输入 2 帧 | 收到 2 帧 |
| 噪声前缀 | 帧前加入随机字节 | 能重新同步 |
| 序号回绕 | 65535 → 0 | 正确处理序号回绕 |
| ACK 超时 | 丢弃 ACK | 按配置重试 |
| 重复事件 | 重复发送相同 event ID | 只处理一次 |
| 队列满 | 连续提交扫描数据 | 计数丢帧，不阻塞扫描 |
| 队列满事件 | 填满计圈队列 | 明确报告失败，不静默丢失 |

建议另外加入随机数据测试：向解析器输入大量随机字节，使用 AddressSanitizer 和 UndefinedBehaviorSanitizer 检查越界、未定义行为及内存错误。

例如在 macOS 上：

```Bash
cc -std=c11 -g -O1 \
  -fsanitize=address,undefined \
  -Wall -Wextra -Werror \
  -Imain \
  test/native/test_link_loopback.c \
  main/link_codec.c \
  main/link_parser.c \
  -o test/native/test_link_loopback

./test/native/test_link_loopback
```

这是协议级测试，不会验证 ESP-IDF UART 驱动、GPIO 电气连接、真实调度延迟或 C5 的射频性能。上述项目必须留给硬件测试。

### 八、分阶段实施与链路质量验收

- **01：阶段 1：纯协议与 native 测试** `不改射频固件`
  验收：所有协议测试通过；AddressSanitizer/UndefinedBehaviorSanitizer 无错误。
  - 实现帧编码、解码、CRC、流式解析。
  - 完成回环、截断、粘连、CRC 错误和随机数据测试。
  - 固化协议版本、字段定义和字节序。

- **02：阶段 2：S3 ↔ C5 UART 点对点**
  验收：连续运行至少 30 分钟；无 RH UART 污染；无无法解释的 CRC 错误；连接状态正确。
  - C5 新增专用 UART。
  - S3 使用另一组 UART。
  - 先以 115200 baud 发送 HELLO、HEARTBEAT 和 STATUS。
  - 确认链路与 RH UART0 完全隔离。

- **03：阶段 3：接入扫描结果**
  验收：扫描周期不出现可测量的显著恶化；队列不会持续增长；断连时扫描仍正常工作。
  - C5 将现有扫描结果转换为紧凑 scan_item_t。
  - 由链路任务异步发送。
  - S3 显示频谱，并记录吞吐量、延迟和丢帧数。
  - 分别测试 115200、460800、921600 baud。

- **04：阶段 4：计圈事件可靠传输**
  验收：在规定的队列容量和重试策略内，事件均有明确的成功或失败状态；不得静默丢失。
  - 加入事件 ID、ACK、超时重试和去重。
  - 模拟断开 TX/RX、重启 S3、丢弃 ACK、填满队列。
  - 验证事件不会因重复重传造成重复计圈。

- **05：阶段 5：S3 Wi-Fi 上报与 RotorHazard 适配**
  验收：网络掉线不影响 C5 扫描；网络恢复后状态能重新同步；服务端可以识别重复事件。
  - 先用本地 UDP 接收器验证数据。
  - 再把计圈事件转换成 RotorHazard 实际支持的接口。
  - 最后加入手机 App、WebSocket 或其他上层服务。

- **06：阶段 6：SPI 与多 C5 扩展（可选）**
  验收：测量持续吞吐量、P95/P99 延迟、丢帧率和多节点公平性，再决定是否替换 UART。
  - 先保留 UART 版本作为可靠基线。
  - SPI 从 1–4 MHz 开始，验证握手、事务队列和 DMA。
  - 通过独立 CS/READY 扩展多块 C5。

#### 建议统一记录的链路指标

| 指标 | 测量方法 |
| --- | --- |
| 有效吞吐量 | 接收的有效 Payload 字节数 ÷ 时间 |
| 单向延迟 | 两端时间戳校准后测量，或采用往返时间除以 2 作为近似 |
| P95/P99 延迟 | 保存足够多的单帧延迟样本，计算分位数 |
| CRC 错误率 | CRC 错误帧数 ÷ 收到的完整候选帧数 |
| 丢帧率 | 发送序号/事件 ID 与接收统计对比 |
| 队列水位 | 周期记录当前及最大队列深度 |
| 堆内存 | 初始化前后、扫描过程中及最坏负载下记录 |
| 扫描扰动 | 对比接入通信前后的扫描周期、漏采样次数和结果稳定性 |
| RH UART 污染 | 使用独立逻辑分析仪或串口记录器检查完整字节流 |
| 重连恢复 | 断线、重启和网络故障后测量恢复时间 |

单向延迟测量需要注意两端时钟同步问题。如果没有同步时钟，不能简单相减两个不同 MCU 的毫秒计数器。可以使用 GPIO 触发和逻辑分析仪测量物理时延，或者用往返延迟作为第一阶段的近似指标。

### 九、最终推荐配置

- **当前版本：独立 UART + S3 Wi-Fi**
  先用 115200/460800 baud 验证；稳定后再提高波特率。C5 保持 WIFI_MODE_NULL，不增加 STA、ESP-NOW 或 BLE。

- **后续高速版本：SPI Master/Slave + READY**
  适合高频扫描曲线、批量样本或多 C5 汇聚。协议、事件 ID 和 ACK 机制保持不变，只替换传输层。

- **必须长期保留的安全边界**
  RH UART 独占、扫描路径不阻塞、扫描数据可覆盖、计圈事件有 ACK 和去重、断连不影响采集。

最后提醒一个实际集成中最容易忽略的地方：你现在的 C5 在 `esp_wifi_start()` 之后只剩约 5 KB 堆，因此**不要直接把完整的通用通信框架、多个 256 B 队列和默认 UART 驱动全部叠加进去**。先用 map 文件确认静态内存布局，再测量 UART 初始化后的最小剩余堆和最大连续内存块。如果驱动开销超过预算，就先压缩原有功能或改用经过验证的轻量实现，而不是冒险让射频扫描在高负载时崩溃。

参考资料：

- ESP32-C5 Datasheet：[官方芯片资料](https://documentation.espressif.com/esp-c5_datasheet_en.html?utm_source=chatgpt.com)（也可参阅 [ESP32-C5 系列数据手册](https://documentation.espressif.com/esp32-c5_datasheet_en.html?utm_source=chatgpt.com)）。

- [ESP-IDF 5.5：ESP32-C5 Wi-Fi 驱动说明](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32c5/api-guides/wifi.html?utm_source=chatgpt.com)。

- [ESP-IDF v5.5.5：SPI Slave 驱动说明](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32/api-reference/peripherals/spi_slave.html?utm_source=chatgpt.com)。实际集成 C5 时，还应以 ESP32-C5 对应版本的 SPI Slave 文档和头文件为准。

这套方案的关键是先用最小有线链路验证双芯片架构，再逐步加入可靠事件传输和网络桥接。这样既保留你当前 C5 射频路径的独立性，也给未来的高速 SPI 和多节点扩展留出空间。

#### 本轮内联角标引用

- `[^1]` Espressif 文档中心
- `[^2]` Espressif Documentation
- `[^3]` — ESP-IDF Programming Guide latest documentation
- `[^4]` Espressif 文档中心
- `[^5]` Espressif 文档中心
- `[^6]` — ESP-IDF Programming Guide latest documentation

---

## 附录 A：引用来源

本轮回复没有网页搜索配图，引用全部以内联角标与文末「参考资料」链接两种形式出现。

### A.1 内联文本角标

页面把每个角标渲染成不带 `href` 的徽标（`[class*="Trigger-"]` + `Badge-`），来源 URL 不暴露在 DOM 中。对第一个角标做过真实点击，浏览器新开了标签页，据此确认其指向；其余角标真实点击后未能稳定复现新开标签页（合成事件被浏览器按非用户手势拦截），**URL 未取到，仅保留页面标题**。

| 出现位置 | 页面徽标标题 | 完整 URL | 角标 |
| --- | --- | --- | --- |
| 二、所有通信链路对比（C5 外设能力确认） | Espressif 文档中心 | `https://documentation.espressif.com/esp32-c5_datasheet_en.html`（实测点击新开标签页所得） | `[^1]` |
| 3.1 排除方案 · C5 Wi-Fi STA + UDP/TCP | Espressif Documentation | 未取到，仅保留页面标题 | `[^2]` |
| 3.1 排除方案 · C5 ESP-NOW | ESP-IDF Programming Guide latest documentation | 未取到，仅保留页面标题 | `[^3]` |
| 3.1 排除方案 · C5 BLE | Espressif 文档中心 | 未取到，仅保留页面标题 | `[^4]` |
| 3.2 · SDIO Slave | Espressif 文档中心 | 未取到，仅保留页面标题 | `[^5]` |
| 4.2 · SPI Slave 驱动 | ESP-IDF Programming Guide latest documentation | 未取到，仅保留页面标题 | `[^6]` |

> 徽标原文尾部的 `+1` 计数属于角标样式，采集时已剥掉。

### A.2 回复文末「参考资料」链接

这几条是回复正文里的真 `<a href>`，URL 直接来自 DOM，可信。

| 来源标题 | URL |
| --- | --- |
| 官方芯片资料（ESP32-C5 Datasheet） | `https://documentation.espressif.com/esp-c5_datasheet_en.html` |
| ESP32-C5 系列数据手册 | `https://documentation.espressif.com/esp32-c5_datasheet_en.html` |
| ESP-IDF 5.5 · ESP32-C5 Wi-Fi 驱动说明 | `https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32c5/api-guides/wifi.html` |
| ESP-IDF v5.5.5 · SPI Slave 驱动说明 | `https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32/api-reference/peripherals/spi_slave.html` |

> 上表已去掉 ChatGPT 附加的 `?utm_source=chatgpt.com` 跟踪参数。

### A.3 记忆条目

回复底部未出现「来源」按钮，页面上也没有 `region "记忆"` 侧栏，**本轮未采集到 ChatGPT 记忆条目的引用**。

---

## 附录 B：采集方法与数据完整性说明

### 操作路径

1. `bsk session start` 建立独立 Agent 窗口，`bsk tab create --url https://chatgpt.com/` 打开新标签页；登录态复用用户本人的浏览器会话，全程未读取、未导出任何凭据。
2. `send_prompt.sh` 用 `document.execCommand('insertText')` 把提示词注入 ProseMirror 输入框（`bsk fill` 对 ChatGPT 输入框必失败），提交前校验框内文本与提示词一致。
3. 生成完成（状态位「回答已完成」，耗时 84 s）后**当场**执行 `collect.sh` 提取，中间不刷新页面，避免含搜索引用的回复出现「无法加载此回复」。
4. 回复中不含单选组与交互按钮，故为单轮采集，未进入 `select_options.sh` 追问流程。
5. 内联角标逐个尝试取源 URL（结果见附录 A）；会话链接取自标签页 URL，未采用不可靠的 `document.title`。

### 结构还原

`chatgpt_dom2md.js` 覆盖本轮回复用到的块类型：H1–H3 标题、段落、有序/无序列表、带语言标签的代码块、真 `<table>`、卡片组（方框名 + 标题 + 徽标 + 描述 + 子列表 + 角标）、连线图、内联角标与外链。

本轮采集过程中修正了还原器六处缺陷，前两处会造成**正文成片丢失**：

| # | 缺陷 | 现象 | 修正 |
| --- | --- | --- | --- |
| 1 | `codeBlock()` 抢先匹配任何含 `<code>` 的 DIV | 3.1 排除方案、5.1 帧格式表、5.2 消息类型表、八 阶段卡片、九 推荐配置被压成 2 行代码块，正文整体丢失 | 容器内含 `p`/`li`/`table` 时不视为代码组件，交回卡片与表格分支 |
| 2 | 卡片正文只取「直接子级 P」 | 多层嵌套的方框（总体架构第 5 块、UART 接线图）只输出首段，其余段落静默丢弃 | 改为收集卡片内全部 `p`（排除 `li` 内段落），并把 `h2/h4` 方框名并入标题 |
| 3 | 连线图（方框 + svg 箭头）落到卡片分支 | 箭头上的接线标签（`C5 TX → S3 RX` 等）丢失 | 新增连线图分支：块内存在不属于段落/徽标的文本行时，整块按原样文本保留 |
| 4 | `plain()` 不渲染角标 | 卡片描述里出现 `[CITE2]` 字面量 | 与 `inline()` 统一，输出 `[^n]` |
| 5 | KaTeX 的 MathML 与 HTML 双份渲染 | `2+8+256+2=268` 重复成 `2682+8+256+2=268` | 命中 `.katex` 时只取 `<annotation>` 里的 TeX 源码 |
| 6 | 代码块语言标签被 lowercase/映射 | 页面上写「纯文本」「Bash」，产物里只剩 `text`/`bash` | ASCII 标签原样保留为围栏语言；中文标签映射并补一行「页面代码块标签」 |

修正前后的同一页面提取件都留了档（`round1.broken.json` / `round1.json`）：页面文本长度同为 25627 字符，但修正前正文缺 5 处、公式重复 1 处。

### 完整性校验

`verify_report.py --report … --result round1.json` 实测结果：

| 校验项 | 结果 |
| --- | --- |
| 页面 `innerText` 行覆盖率 | 原文非空行 1008，报告缺失 **0** 行 ✓ |
| 正文 vs DOM 提取逐行 diff | 差异 11 处，全部为标题下沉与围栏标记（脚本判定为正常） |
| 最终结论 | `[verify] 结果: 全部通过` |
| 正文规模 | 26.6 KB / 9 个一级章节 / 36 个标题 / 15 个代码块 / 10 张表格 / 6 个内联角标 |

### 已知限制

1. 内联角标的来源 URL 大多不暴露在 DOM 中，只保留了徽标标题，未凭标题臆造链接（详见附录 A.1）。
2. 表格与代码块里的吞吐量、RAM/Flash 预算、延迟数字是 ChatGPT 自述的**工程设计预算**，回复本身声明不是对这两块实板的实测；落地前仍需按第七、八章给出的方法自行测量。
3. 页面上的三处以图形/等宽方式呈现的内容（4.1 的 UART 点对点接线图、6.1 的目录树、7.1 的模块边界图）在 Markdown 中以等宽代码块原样保留；其中 4.1 的接线图在页面上是方框 + 箭头图形，文字与连线关系完整，但没有矢量图形。如需图片版需另行绘制。
4. 若事后刷新该会话页，含搜索引用的回复可能无法重新加载；本文档以生成当场采集的提取件为准。

---

本文档由 Qoder 通过 browser-skill（`bsk`）驱动用户已登录的浏览器采集生成；正文内容归属 ChatGPT 回复原样，表格与代码块的数值为回复自述的工程预算，非实板实测。
