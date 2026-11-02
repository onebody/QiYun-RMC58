# S3 Wi-Fi 中继 M2（C5 扫频头 → S3 字节管道 → 平台）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** C5 扫频头的全部帧字节经 S3 的 UART(921600) → Wi-Fi STA → `ws://…/node?id=` 原样进 RF Track 平台并上屏，S3 不解析、不封包。

**Architecture:** S3 中继镜像新增一条"扫频头字节管道"：一个读任务把 `uart_link_read` 拿到的字节直接 `link_raw_to(LINK_WS, …)`，复用 M1 已有的 32 KiB 有界队列 + drops 账 + 单条 WS 写任务；C5 侧只改两处——UART 线路速率与由它换算出的 pace 钳位（@115200 换算结果与今天的 9000 B/s、512 bins 逐值相同）。命令面加 `HEAD <line>` 做显式转发、`NET UART <tx> <rx>` 做引脚现场可改。

**Tech Stack:** ESP-IDF 5.5.5 / pioarduino 55.03.312-1、PlatformIO 6.2.0（`/Users/fcj/.local/bin/pio`，**不用** `~/.platformio/penv/bin/pio`）、Unity（`pio test -e native`）、FastAPI + SQLAlchemy + pytest、Node 20（`node --test`、`web/serve.mjs` 桥 0 改动）。

**Spec:** `docs/superpowers/specs/2026-11-01-s3-wifi-relay-design.md`（§1 分歧 A/B、§3 带宽预算、§4 改动清单、§6 门槛表、§8 开放项）
**分支：** `fw-wifi-relay`（M1 已在此分支上，远端 tip 用 `git rev-parse fork/fw-wifi-relay` 现取）

---

## 0. 本计划相对 spec 的四处偏差（先记账，别默默偏离）

| # | spec 原口径 | 本计划 | 理由（一手证据） |
|---|---|---|---|
| D1 | §6 L4：页面经 WS 发裸 `PROTOCOL MONITOR` | 发 `HEAD PROTOCOL MONITOR`，裸命令仍由中继自己应答 | 中继的 `INFO`/`PROTOCOL?` 应答的是**中继自己**（`commands.c:264` 的 `relay_allowed()` 白名单）。裸转发会让"哪块板在答"失去唯一答案；显式 `HEAD` 是对齐「多对象界面要把当前是哪个写进状态栏」那条既有偏好 |
| D2 | §4 表：C5 波特率 `115200→921600` | 新增 `[env:esp32c5-head]`（`extends = env:esp32c5` + `-DUART_LINK_BAUD=921600`），`[env:esp32c5]` 不动 | 同一个 C5 env 的 115200 是 Pi 上 RotorHazard 的 ②a 路（`7bec675`，真机实测 6.80 KiB/s）。原地改波特率=改已上线路径；「替代硬件类特性必须单独切分支/env」这条既有约束优先 |
| D3 | §8 开放项 1 待答："S3 是哪块板 / 排针可用情况" | 引脚做成运行时可配（`NET UART <tx> <rx>`，NVS 持久，默认 U0TXD=43 / U0RXD=44） | 2026-11-02 实测：esptool 只给得出 `ESP32-S3 (QFN56) rev v0.2 / 4 MB flash / USB-Serial/JTAG / MAC 已记录在 /tmp，不进仓库`——**芯片层给不出排针表**；`platformio.ini` 里"Micro 板"那句出自上游 `17e48d6`（Bryan Mayland, 2026-10-10），不是本机实测。猜错一次=重烧一次 |
| D4 | §6 门槛表未含"关联失败要可见" | 新增 L1.5：`NET STATUS` 必须给得出 STA 为什么没起来 | M1 收尾实况：`NET ON` 后 `sta:false / why:""` 在 39 s 轮询与一次 OFF→ON 后 29 s 轮询里毫无变化。代码原因（本计划 Task 7 修）：`dial_task_fn` 的 `if (!sta_up)` 分支不写 `why`（`ws_link.c:383-386`），而 `on_wifi_event` 把 `void *data` 直接 `(void)data;` 丢弃，**`wifi_event_sta_disconnected_t.reason` 从未被记录**（`ws_link.c:59-72`） |

同批取证事实（写计划时已实测，实现阶段不必重测）：

- 板上跑的镜像 = `rftrack-baseline-v0.1.0-37-gd11|2026-11-02T01:04:35+08:00|712,87e8c20e`，含 M1 全部修复；凭据仍在 NVS（`on:true`）。
- C5 报 `Web-v0.10|2026-10-29T00:07:24+08:00` —— `git describe` 落在 `Web-v0.10`（即 `7bec675` 的 tag）且无 `-N-g` 后缀 ⇒ **C5 就是 F1 那份、带 UART 路、波特率 115200**，spec §8 开放项 4 结案：M2 需要给 C5 重烧一份 head env。
- 中继的 `link_raw_to(LINK_WS, …)` 已自带 32 KiB 有界队列与 `drops++`（`link.c:176-189`），管道的"有界+计数"红线靠复用它在 `ws_link.c` 之外一行都不用写。
- 中继镜像的 sdkconfig：`CONFIG_ESP_CONSOLE_NONE=y` + `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y`（`sdkconfig.esp32s3-relay:1653-1661`）⇒ **GPIO43/44 没有被控制台占着**；`CONFIG_SOC_UART_BITRATE_MAX=5000000` ⇒ 921600 合法。
- S3 的 USB 口走 native USB-Serial/JTAG ⇒ **GPIO19/20 不可用作 UART**（那是我们自己的控制台）。
- `link_ready(LINK_UART)` 就是 `uart_link_ready()`（`link.c:94-97`）⇒ 中继上 `uart_link_open()` 成功之后，UART 会自动变成"ready"。这正是 Task 6 Step 5 必须挡住 `cmd_uart` 的原因，也是 `link.c` 本计划不用改的原因。
- 平台侧解析器叫 `FrameStream`，吐的是 `StreamItem`（`kind` ∈ `frame`/`text`，字段 `type`/`payload`/`line`），计数器是 `crc_errors` / `resync_bytes` / `bytes_in`；**没有 `flush()`**——Task 8 的原始草稿按 `StreamParser.push()/flush()` 写是错的，本计划按真名重写。
- `CapturePipeline.status()["endpoints"]` **今天是整数**，并且被 `tests/test_capture.py:552` 断言（`== 0`）⇒ 按端点明细必须落在**新键** `endpoint_stats` 上，把 `endpoints` 改成列表等于改契约。
- `/tmp/w58-probe/dual-C5.bin` 2026-11-02 实测存在（L1 这轮就能跑）；M1 那个 `/tmp/w58-probe/hold_node_pipe.py` **已不在**，L4 回环脚本到那时现写。

## 1. 文件结构（改动地图）

| 文件 | 责任 | 本计划里发生什么 |
|---|---|---|
| **新** `firmware/src/proto/line_pace.h` | 一条串口的 pace 换算，纯算术 | 新建：`pace_budget()` / `pace_spec_bins()`，@115200 必须复现 9000 / 512 |
| `firmware/src/proto/uart_link.{c,h}` | 传输层：谁在另一端 | 三态化（C5 / 中继 head / 扫描 env 空桩），波特率可 `-D` 覆盖，中继侧 `uart_link_open(tx, rx)` |
| `firmware/src/proto/link.c` | 队列与写任务 | `link_init()` 在中继 env 不自动开 head（引脚要等 NVS） |
| `firmware/src/proto/commands.c` | 命令面 | pace 改用换算；`cmd_uart` 读任务挡在中继 env 外；新增 `NET UART` 与 `HEAD ` |
| `firmware/src/scan/scanner.c` | 采样节拍 | 删掉写死的 `UART_STREAM_BUDGET 9000`，读线路速率 |
| **新** `firmware/src/net/relay_pipe.{c,h}` | 扫频头 → 上行 的字节管道（单向读 + 计数） | 新建：一个读任务、`relay_pipe_send()`、`relay_pipe_status()` |
| `firmware/src/net/ws_link.c` | STA + WS | 关联失败可见：`sta_reason` / `sta_downs` / `why:"no_ip"` |
| `firmware/src/net/net_prefs.{c,h}` | NVS namespace `net` | 加两把 u16（`tx`/`rx`） |
| `firmware/src/CMakeLists.txt` | 编译隔离门 | `net/relay_pipe.c` 进 `if(DEFINED ENV{WIDSCAN_RELAY} …)` 那一支 |
| `firmware/src/main.c` | 中继启动 | `net_prefs_load` 之后开 head、起管道 |
| `firmware/platformio.ini` | env | 新 `[env:esp32c5-head]` |
| **新** `firmware/test/test_pace/test_main.c` | native 门槛 | 换算的钉值用例 |
| `rftrack/backend/tests/test_frame_parity.py` | 帧解析 | 加"分块不变性"用例（管道依赖的那条性质） |
| **新** `rftrack/tools/relay_chunk_replay.mjs` | L1 假中继 | 真 C5 字节段按管道的切块喂 `/node?id=` |
| `rftrack/backend/app/services/capture.py` | 平台 M2 收尾（Task 10） | 按端点明细快照 `endpoint_stats`（`routers/health.py` **不改**，它整节透出） |
| `rftrack/backend/app/routers/nodes.py` + `schemas.py` | 平台 M2 收尾（Task 11） | `PUT /api/nodes/{id}` 能力声明；`NodeOut` 不动 |

**不在本计划里：** `web/serve.mjs`（spec §4 已判 0 改动）、C5 的扫描/射频代码、RAWIQ 上 WS（`link.h:19-21` 的规则不许动）、M3（C5 独立第二 UART）。

---

## 2. 任务

### Task 1: 取证结案——把 §8 两个开放项的答案写进 spec

**Files:**
- Modify: `docs/superpowers/specs/2026-11-01-s3-wifi-relay-design.md`（§8 第 1、4 两条）

- [ ] **Step 1: 复核引脚约束的两条一手据**

Run: `cd repo/firmware && grep -nE "CONFIG_ESP_CONSOLE" sdkconfig.esp32s3-relay | head -8`
Expected: `CONFIG_ESP_CONSOLE_NONE=y` 与 `CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG=y` 两行都在（43/44 未被控制台占用这条成立才继续）。

Run: `cd repo && git log -1 --format='%h %an %ad %s' --date=short -S 'Micro" board' -- firmware/platformio.ini`
Expected: `17e48d6 Bryan Mayland 2026-10-10 …`——证明"Micro 板"是上游描述而非本机实测，D3 的理由成立。

- [ ] **Step 2: 改写 §8 第 1、4 两条**

把第 1 条整段替换为（保持文档其余不动）：

```markdown
1. **S3 是哪块板 / 排针可用情况**：**芯片层给不出答案**——2026-11-02 实测 esptool 只报 `ESP32-S3 (QFN56) rev v0.2 / 4 MB flash / USB-Serial/JTAG / MAC 已记录在 /tmp，不进仓库`，而 `platformio.ini` 里 "Micro 板" 那句出自上游 `17e48d6`（Bryan Mayland, 2026-10-10），不是本机取证。已确认的约束只有：GPIO19/20 = native USB（我们的控制台，不可占），GPIO26–32 = SPI flash/PSRAM，GPIO43/44 = U0TXD/U0RXD 且中继镜像的控制台是 `CONFIG_ESP_CONSOLE_NONE` + secondary USB-Serial/JTAG（不占 43/44）。⇒ 裁定：引脚做成运行时可配（M2 计划的 `NET UART <tx> <rx>`，默认 43/44），第一次接线时用「哪一对脚能收到 C5 的字节」当场判出来，而不是猜一版烧一版。
```

把第 4 条整段替换为：

```markdown
4. **C5 烧的是哪份固件**：已结案。2026-11-02 的 `INFO` 报 `Web-v0.10|2026-10-29T00:07:24+08:00|109,edb400d6`——`git describe` 落在 `Web-v0.10`（打在 `7bec675`，即 F1 UART 链路那版）且**无 `-N-g` 后缀**，说明就建在该提交上。末段 `109,edb400d6` 是 `radio_phy_version()`（`commands.c:419-420`），不是提交号。⇒ M2 需要给 C5 重烧一份 `[env:esp32c5-head]`（921600）；115200 那份继续是 Pi 的 ②a 路。
```

- [ ] **Step 3: 提交**

```bash
cd repo && git add docs/superpowers/specs/2026-11-01-s3-wifi-relay-design.md
git commit -m "docs(relay): close spec open items 1 and 4 with measured evidence"
```

---

### Task 2: `line_pace.h`——把 pace 钳位变成线路速率的函数（TDD）

**Files:**
- Create: `firmware/src/proto/line_pace.h`
- Create: `firmware/test/test_pace/test_main.c`
- Modify: `firmware/test/test_pace/` 是新目录，`pio test` 按 `test/test_*` 自动收

- [ ] **Step 1: 写失败的测试**

Create `firmware/test/test_pace/test_main.c`:

```c
/* Host tests for the UART pacing arithmetic. The numbers here are pinned to
 * what the 115200 path measured on real hardware (F1: 9000 B/s budget, spectra
 * clamped to 512 bins), so a parameterisation that changes them is a behaviour
 * change on the Pi's RotorHazard line, not an improvement.
 * pio test -e native. GPL-3.0-or-later. */
#include <stdint.h>
#include <unity.h>
#include "../../src/proto/line_pace.h"

void setUp(void) {}
void tearDown(void) {}

static void test_budget_at_115200_is_today_number(void) {
    TEST_ASSERT_EQUAL_UINT32(9000, pace_budget(115200));
}

static void test_budget_at_921600(void) {
    /* 921600 8N1 = 92160 B/s line; the same 25/32 share F1 used. */
    TEST_ASSERT_EQUAL_UINT32(72000, pace_budget(921600));
}

static void test_bins_cap_at_115200_is_512(void) {
    /* The clamp commands.c:478 wrote as a literal until now. */
    TEST_ASSERT_EQUAL_UINT16(512, pace_spec_bins(115200, 1024));
}

static void test_bins_cap_at_921600_is_ring_bound(void) {
    /* At 921600 the line no longer binds: the C5's 1024 B TX ring does. */
    TEST_ASSERT_EQUAL_UINT16(992, pace_spec_bins(921600, 1024));
    /* A bigger ring runs into link.c's FRAME_UART_MAX (1024 bins) instead. */
    TEST_ASSERT_EQUAL_UINT16(1024, pace_spec_bins(921600, 2048));
}

static void test_line_bytes_is_8n1(void) {
    TEST_ASSERT_EQUAL_UINT32(11520, pace_line_bytes(115200));
    TEST_ASSERT_EQUAL_UINT32(92160, pace_line_bytes(921600));
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_budget_at_115200_is_today_number);
    RUN_TEST(test_budget_at_921600);
    RUN_TEST(test_bins_cap_at_115200_is_512);
    RUN_TEST(test_bins_cap_at_921600_is_ring_bound);
    RUN_TEST(test_line_bytes_is_8n1);
    return UNITY_END();
}
```

- [ ] **Step 2: 跑到它失败**

Run: `cd repo/firmware && /Users/fcj/.local/bin/pio test -e native 2>&1 | tail -20`
Expected: 编译失败 `fatal error: ../../src/proto/line_pace.h: No such file or directory`。

- [ ] **Step 3: 写实现**

Create `firmware/src/proto/line_pace.h`:

```c
/* One serial line's worth of pacing, as pure arithmetic so the host can pin
 * it. Two numbers used to be literals chosen for 115200 (a 9000 B/s budget and
 * a 512-bin spectrum clamp); both are derived from the line rate here instead,
 * and at 115200 the derivation gives the same two numbers back. That identity
 * is the whole reason the Pi's RotorHazard path is safe: it is parameterised,
 * not changed - test/test_pace asserts it.
 * GPL-3.0-or-later. */
#pragma once
#include <stdint.h>

/* 8N1 puts 10 bits on the line per byte. */
static inline uint32_t pace_line_bytes(uint32_t baud) { return baud / 10; }

/* Frames may use 25/32 of the line: 11520 B/s * 25/32 = 9000, which is the
 * budget the F1 measurement ran with. Divide first so both anchors stay
 * exact in integers. */
static inline uint32_t pace_budget(uint32_t baud) { return pace_line_bytes(baud) / 32 * 25; }

/* A SPECTRUM frame is 6 (header) + 20 (spectrum header) + bins + 4 (crc);
 * scanner.c's own per-rotation accounting rounds that overhead to 32, so this
 * clamp uses 32 too rather than introducing a second number for the same
 * thing. 512 bins at 115200 was the measured-good clamp; the line scales
 * linearly, and the two ceilings below are what actually stops it growing. */
#define PACE_FRAME_OVERHEAD 32
#define PACE_BINS_IN_FRAME_BUF 1024 /* link.c: FRAME_UART_MAX's payload ceiling */

static inline uint16_t pace_spec_bins(uint32_t baud, uint32_t tx_ring_bytes) {
    uint32_t by_line = 512u * (baud / 115200u);
    uint32_t by_ring = tx_ring_bytes - PACE_FRAME_OVERHEAD;
    uint32_t cap = by_line < PACE_BINS_IN_FRAME_BUF ? by_line : PACE_BINS_IN_FRAME_BUF;
    if (by_ring < cap) cap = by_ring;
    return (uint16_t)cap;
}
```

- [ ] **Step 4: 跑到通过**

Run: `cd repo/firmware && /Users/fcj/.local/bin/pio test -e native 2>&1 | tail -12`
Expected: 全绿，且总数从 M1 的 **29** 变成 **34**（`test_pace` 5 条）。

- [ ] **Step 5: 提交**

```bash
cd repo && git add firmware/src/proto/line_pace.h firmware/test/test_pace/test_main.c
git commit -m "feat(firmware): derive UART pacing from line rate, pinned to the 115200 numbers"
```

---

### Task 3: `uart_link` 三态化——C5 / 中继 head / 扫描 env 空桩

**Files:**
- Modify: `firmware/src/proto/uart_link.h`（全文重写）
- Modify: `firmware/src/proto/uart_link.c:1-76`
- Modify: `firmware/platformio.ini:40`（`[env:esp32s3-relay]` 的 head 波特率，见 Step 2b）

`link.c:99-104` 那处无条件的 `uart_link_init()` **不改**：中继支里它返回 false，`link_ready(LINK_UART)` 走 `uart_link_ready()`，真正开驱动的是 main.c 读完 NVS 之后的 `uart_link_open()`（Task 5 Step 4）。在中继 env 里提前装 43/44 反而会抢掉一次"引脚还没定"的安装。

- [ ] **Step 1: 重写头文件**

`firmware/src/proto/uart_link.h` 全文替换为：

```c
/* The UART0 link. Two images have one and mean different things by it: the C5
 * sends its frames *out* to whatever listens (the Pi's RotorHazard, or the S3
 * relay), the relay image reads a sweep head's bytes *in*. The esp32s3 scanner
 * image has neither and keeps the stub. GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Install UART0 on this target's fixed pair (C5: GP11/GP12; relay: GPIO43/44 =
 * U0TXD/U0RXD). False where the target has no such link. */
bool uart_link_init(void);
/* True once the driver is installed: the C5 carries replies straight (no
 * queue), so this is all link_ready needs to know. */
bool uart_link_ready(void);
/* Blocks until the bytes reach the TX ring; the UART shifts them out at line
 * rate whether or not anything listens, so this cannot hang. */
int uart_link_write(const void *data, size_t n);
int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms);
/* What the line actually carries, so the pacing clamp can be derived from it
 * (line_pace.h) instead of assumed to be 115200. */
uint32_t uart_link_line_rate(void);
unsigned uart_link_tx_ring_bytes(void);
/* Relay only: (re)open the head link on a given pair; 0 keeps the compiled
 * default, and a pair already in use returns true without touching anything.
 * Safe to call from the command task: nothing else owns the driver there, and
 * the pipe reads what is in the ring after a delete-and-install anyway. */
bool uart_link_open(int tx, int rx);
void uart_link_pins(int *tx, int *rx);
```

- [ ] **Step 2: 把 uart_link.c 改成三支**

`firmware/src/proto/uart_link.c` 全文替换为：

```c
/* GPL-3.0-or-later. */
#include "driver/gpio.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "link.h"
#include "uart_link.h"

#define UART_LINK_PORT UART_NUM_0

#if RX_TARGET_C5
#define UART_LINK_TX 11 /* the C5-Zero's U0TXD, silk "TX" */
#define UART_LINK_RX 12 /* U0RXD, silk "RX" */
#elif RX_WIFI_RELAY
#define UART_LINK_TX 43 /* ESP32-S3 U0TXD; NET UART overrides it at runtime */
#define UART_LINK_RX 44 /* U0RXD. 19/20 are this board's native USB console and
                         * 26..32 the SPI flash, so those are refused outright. */
#endif

#if RX_TARGET_C5 || RX_WIFI_RELAY
#ifndef UART_LINK_BAUD
#define UART_LINK_BAUD 115200 /* the Pi's RotorHazard line; -D raises it */
#endif
#endif

#if RX_TARGET_C5
/* The reader polls every 20 ms, so 1 KB rides out 80 ms of delay at line rate.
 * The C5's heap cannot afford more: see commands_task_start in commands.c. */
#define UART_LINK_RX_BYTES 1024
#define UART_LINK_TX_BYTES 1024
static bool installed;
/* The UART carries replies straight from the caller (the C5 has no heap for a
 * queue plus a writer task). Command and scanner replies can overlap there, so
 * serialize whole writes. */
static SemaphoreHandle_t write_lock;

bool uart_link_init(void) {
    const uart_config_t cfg = {
        .baud_rate = UART_LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    write_lock = xSemaphoreCreateMutex();
    if (!write_lock) return false;
    if (uart_param_config(UART_LINK_PORT, &cfg) != ESP_OK) return false;
    if (uart_set_pin(UART_LINK_PORT, UART_LINK_TX, UART_LINK_RX, -1, -1) != ESP_OK) return false;
    if (uart_driver_install(UART_LINK_PORT, UART_LINK_RX_BYTES, UART_LINK_TX_BYTES, 0, NULL, 0) != ESP_OK)
        return false;
    /* The Pi's TX floats while it is off; without the pull-up the RX line
     * fills the buffer with noise. */
    gpio_pullup_en(UART_LINK_RX);
    installed = true;
    return true;
}

bool uart_link_ready(void) { return installed; }

int uart_link_write(const void *data, size_t n) {
    if (!installed) return -1;
    xSemaphoreTake(write_lock, portMAX_DELAY);
    int written = uart_write_bytes(UART_LINK_PORT, data, n);
    xSemaphoreGive(write_lock);
    return written;
}

int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    if (!installed) return -1;
    return uart_read_bytes(UART_LINK_PORT, buf, capacity,
                           timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms));
}

uint32_t uart_link_line_rate(void) { return UART_LINK_BAUD; }
unsigned uart_link_tx_ring_bytes(void) { return UART_LINK_TX_BYTES; }

#elif RX_WIFI_RELAY
/* The relay's sweep-head link. The rings are bigger than the C5's because this
 * chip has the RAM (72 KiB link queue already proves it) and because a head at
 * 921600 puts ~90 KB/s on the line: 4 KiB is ~45 ms of absorption, which is
 * what a short WS stall costs us instead of bytes on the floor. */
#define UART_LINK_RX_BYTES 4096
#define UART_LINK_TX_BYTES 2048
static bool installed;
static int pin_tx = UART_LINK_TX;
static int pin_rx = UART_LINK_RX;

static bool head_pin_ok(int p) {
    if (p < 0 || p >= SOC_GPIO_PIN_COUNT) return false;
    if (p == 19 || p == 20) return false; /* native USB-Serial/JTAG: our console */
    if (p >= 26 && p <= 32) return false; /* SPI flash / PSRAM */
    return true;
}

static bool head_install(void) {
    const uart_config_t cfg = {
        .baud_rate = UART_LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    if (uart_param_config(UART_LINK_PORT, &cfg) != ESP_OK) return false;
    if (uart_set_pin(UART_LINK_PORT, pin_tx, pin_rx, -1, -1) != ESP_OK) return false;
    if (uart_driver_install(UART_LINK_PORT, UART_LINK_RX_BYTES, UART_LINK_TX_BYTES, 0, NULL, 0) != ESP_OK)
        return false;
    /* Same reason as the C5 branch: an unpowered head leaves the line floating
     * and the ring fills with noise that the uplink would pay to carry. */
    gpio_pullup_en(pin_rx);
    installed = true;
    return true;
}

/* link_init() does not open this on the relay: the pins live in NVS, and NVS is
 * only read later in main.c. So uart_link_init() is the "compiled defaults"
 * entry point main.c never uses, kept only so the S3 scanner env still finds a
 * symbol if anything ever calls it. */
bool uart_link_init(void) { return false; }

bool uart_link_open(int tx, int rx) {
    if (tx == 0) tx = pin_tx;
    if (rx == 0) rx = pin_rx;
    if (!head_pin_ok(tx) || !head_pin_ok(rx)) return false;
    if (tx == pin_tx && rx == pin_rx) return installed; /* already armed */
    if (installed) {
        uart_driver_delete(UART_LINK_PORT);
        installed = false;
    }
    pin_tx = tx;
    pin_rx = rx;
    return head_install();
}

void uart_link_pins(int *tx, int *rx) {
    *tx = pin_tx;
    *rx = pin_rx;
}

bool uart_link_ready(void) { return installed; }

int uart_link_write(const void *data, size_t n) {
    if (!installed) return -1;
    return uart_write_bytes(UART_LINK_PORT, data, n);
}

int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    if (!installed) return -1;
    return uart_read_bytes(UART_LINK_PORT, buf, capacity,
                           timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms));
}

uint32_t uart_link_line_rate(void) { return UART_LINK_BAUD; }
unsigned uart_link_tx_ring_bytes(void) { return UART_LINK_TX_BYTES; }

#else
/* The esp32s3 scanner image has no head and no Pi: it must keep building with
 * no UART sink, and it must not gain a driver on 43/44 - that would change an
 * image whose bytes are a recorded baseline. */
bool uart_link_init(void) { return false; }
bool uart_link_ready(void) { return false; }
bool uart_link_open(int tx, int rx) {
    (void)tx;
    (void)rx;
    return false;
}
void uart_link_pins(int *tx, int *rx) {
    *tx = -1;
    *rx = -1;
}
int uart_link_write(const void *data, size_t n) {
    (void)data;
    (void)n;
    return -1;
}
int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    (void)buf;
    (void)capacity;
    (void)timeout_ms;
    return -1;
}
uint32_t uart_link_line_rate(void) { return 0; }
unsigned uart_link_tx_ring_bytes(void) { return 0; }
#endif
```

- [ ] **Step 2b: 中继 env 必须把 head 波特率钉在 921600（2026-11-02 执行时补入计划）**

`[env:esp32s3-relay]` 今天只有 `-DRX_TARGET_S3=1 -DRX_WIFI_RELAY=1`（`platformio.ini:40`）。Step 2 的 `#ifndef UART_LINK_BAUD / #define UART_LINK_BAUD 115200` 对它成立 ⇒ **中继会按 115200 装 head 的 UART，而 M2 的 C5 头是 `[env:esp32c5-head]` 的 921600**——两端都"在跑"，收到的全是乱码，`crc_errors` 一路涨，看起来像飞线质量问题。这是 M2 数据面的第一号坑，必须在计划里显式写死。

`firmware/platformio.ini` 的 `[env:esp32s3-relay]` 那行 build_flags 改成：

```ini
build_flags = -DRX_TARGET_S3=1 -DRX_WIFI_RELAY=1 -DUART_LINK_BAUD=921600
```

并注明两端是一对：`esp32c5-head`(921600) ↔ `esp32s3-relay`(921600)，`esp32c5`(115200) ↔ Pi 的 ②a 路。波特率**不做**运行时可配——引脚之所以运行时可配是"芯片层给不出排针表"（§8.1），波特率不是未知量，spec §3 已经把它定成 M2 的固定搭配；再加一个 NVS 键只会多出一处"两端忘了配对齐"的机会。判据：`NET STATUS` 的 `head_baud` 必须是 `921600`（Task 14 的 L2 前置检查读它）。

- [ ] **Step 3: 三个 env 都编译**

Run:
```bash
cd repo/firmware && for e in esp32s3 esp32c5 esp32s3-relay; do \
  /Users/fcj/.local/bin/pio run -e $e 2>&1 | tail -3 | grep -E "SUCCESS|Error" | sed "s/^/  $e: /"; done
```
Expected: 三行都 `SUCCESS`。（**不并行跑 pio**——并发会死锁，逐条 `for`。）
若报 `SOC_GPIO_PIN_COUNT` 未定义，在 `uart_link.c` 顶部加 `#include "soc/soc.h"` 再重跑；这是唯一允许的机械修正，别改判据。

- [ ] **Step 4: 扫描 env 没多出 UART 符号**

Run: `cd repo/firmware && nm -C .pio/build/esp32s3/firmware.elf | grep -c "uart_link_open"`
Expected: `0`（空桩支里 `uart_link_open` 是 no-op，被内联掉；若为 1 也不算回归，但要在提交信息里写明体积差）。
更硬的判据留 Task 12 的体积账，别在这里放宽。

- [ ] **Step 5: 提交**

```bash
cd repo && git add firmware/src/proto/uart_link.c firmware/src/proto/uart_link.h
git commit -m "feat(firmware): three-way uart_link - C5 sink, relay head link, scanner stub"
```

---

### Task 4: 接上换算——删掉两处写死，加 `[env:esp32c5-head]`

**Files:**
- Modify: `firmware/src/scan/scanner.c:662-678`
- Modify: `firmware/src/proto/commands.c:471-480`（PROTOCOL MONITOR 的 bins 钳位）与 `:580-582`（`SPEC` 命令的同第二处，见 Step 2b）
- Modify: `firmware/platformio.ini`（`[env:esp32c5]` 之后插入新 env）

- [ ] **Step 1: scanner.c 用线路速率**

`firmware/src/scan/scanner.c:662-678` 里把预算常量换成函数：

```c
/* Rotation cap that keeps a frame stream on the UART inside the line rate:
 * the budget divided by the bytes one rotation emits (a metrics/levels frame
 * per hop, a spectrum every spec_every hops). 0 = unrestricted. The budget is
 * no longer 9000 by fiat: it is line_pace's share of whatever the line
 * actually runs at, so a 921600 head is paced for 921600. */
static uint32_t uart_stream_cap(const scan_config_t *c, const plan_t *plan) {
    if (!c->uart_pace) return 0;
    unsigned maxch = 0;
    for (unsigned j = 0; j < plan->nhops; j++)
        if (plan->hop[j].nch > maxch) maxch = plan->hop[j].nch;
    if (!maxch) return 0;
    size_t per_hop = c->metrics_mode == METRICS_FULL
                         ? sizeof(frame_header_t) + 4 + sizeof(frame_metrics_t) + maxch * sizeof(frame_channel_t)
                         : sizeof(frame_header_t) + 4 + sizeof(frame_levels_t) + maxch * sizeof(frame_level_t);
    size_t per_rot = (size_t)plan->nhops * per_hop;
    if (c->spec_every) per_rot += ((size_t)plan->nhops + c->spec_every - 1) / c->spec_every * (32 + c->spec_bins);
    const uint32_t budget = pace_budget(uart_link_line_rate());
    return budget / per_rot;
}
```

并在 `scanner.c` 的 include 段加上（与 `#include "link.h"` 同组）：

```c
#include "line_pace.h"
#include "uart_link.h"
```

- [ ] **Step 2: commands.c 的 bins 钳位跟着换**

`firmware/src/proto/commands.c:471-480` 的这段：

```c
        set_proto(monitor ? PROTO_MONITOR : PROTO_RH);
        if (monitor && sink == LINK_UART) {
            /* Pace the stream to the line rate and clamp spectra so the
             * largest frame fits link.c's UART buffer. Applies to the config
             * that is current now, whichever branch set_proto took. */
            scan_config_t c;
            scanner_get_config(&c);
            c.uart_pace = true;
            if (c.spec_bins > 512) c.spec_bins = 512;
            scanner_set_config(&c);
        }
```

改成：

```c
        set_proto(monitor ? PROTO_MONITOR : PROTO_RH);
        if (monitor && sink == LINK_UART) {
            /* Pace the stream to the line rate and clamp spectra so the
             * largest frame fits link.c's UART buffer. Applies to the config
             * that is current now, whichever branch set_proto took.
             * Both limits come from the baud now; at 115200 they evaluate to
             * the 512 this used to hard-code (test/test_pace), so the Pi's
             * RotorHazard line is unaffected by a 921600 head existing. */
            scan_config_t c;
            scanner_get_config(&c);
            const uint16_t bins = pace_spec_bins(uart_link_line_rate(), uart_link_tx_ring_bytes());
            c.uart_pace = true;
            if (c.spec_bins > bins) c.spec_bins = bins;
            scanner_set_config(&c);
        }
```

并在 `commands.c` 顶部 include 段加 `#include "line_pace.h"`。

- [ ] **Step 2b: 同一个钳位的第二处——`SPEC` 命令（2026-11-02 执行时补入计划）**

`commands.c:582` 是**另一个**写死的 512，注释还把它说成"link.c 的帧缓冲顶到 512-bin"，但 `link.c:43` 的 `FRAME_UART_MAX` 实为 `frame_header_t + frame_spectrum_t + 1024 + 4`——缓冲能吃下 **1024** bins，512 从来只是 @115200 的**节拍选择**。这一段不改，921600 的头就永远只能在 `PROTOCOL MONITOR` 那条路上拿到 992、操作员随后一句 `SPEC 4 992 MAX` 又被打回 512（且 `b <= 1024` 的入参校验本来就放行）。

`commands.c:580-582` 从

```c
        /* SPEC <every_n_hops (0=off)> <bins> <span_hz one-sided> <MAX|MEAN> */
        /* The UART frame buffer (link.c) tops out at 512-bin spectra. */
        if ((sink == LINK_UART || link_frame_sink() == LINK_UART) && b > 512) b = 512;
```

改成

```c
        /* SPEC <every_n_hops (0=off)> <bins> <span_hz one-sided> <MAX|MEAN> */
        /* Same clamp as PROTOCOL MONITOR, same derivation: what the line and the
         * TX ring allow, which at 115200 is still 512. The buffer itself holds
         * 1024 bins (link.c: FRAME_UART_MAX), so this number is pacing, not fit. */
        const uint16_t bins_ceiling = pace_spec_bins(uart_link_line_rate(), uart_link_tx_ring_bytes());
        if ((sink == LINK_UART || link_frame_sink() == LINK_UART) && b > bins_ceiling) b = bins_ceiling;
```

`pace_spec_bins` 与 `uart_link_*` 在 Step 1/2 已经 include 过，这里不新增头文件依赖。

- [ ] **Step 3: 新 env**

`firmware/platformio.ini` 在 `[env:esp32c5]` 段之后插入：

```ini
[env:esp32c5-head]
; The C5 as a sweep head for the S3 relay (spec M2): same firmware, UART0 at
; 921600 instead of the Pi's 115200. Its own env, not a flag on esp32c5,
; because 115200 is what the RotorHazard line on the Pi is measured at - an
; image that changes it changes a shipped path. RX_TARGET_C5 is repeated
; because `extends` merging of build_flags differs between pio versions: a
; duplicated -D is harmless, a lost one builds the wrong target.
extends = env:esp32c5
build_flags = -DRX_TARGET_C5=1 -DUART_LINK_BAUD=921600
```

Step 4 之后补一条取证，确认这个 env 真是按 C5 编的（而不是丢了 `-DRX_TARGET_C5` 编成 S3 镜像）：

Run: `cd repo/firmware && grep -m1 "Target: espressif32\|board" /tmp/w58-probe/m2-esp32c5-head.log; nm .pio/build/esp32c5-head/firmware.elf | grep -c "c5"`
Expected: 日志里板子是 `esp32-c5-devkitc-1`（`extends` 继承来的），ELF 里能数出 c5 侧符号。若 `RX_TARGET_C5` 丢了，`uart_link.c` 会走空桩支、`gpio_pullup_en` 与 GP11/12 都不在——现象是"烧进去能起但 UART 一个字节都不出"，所以这条判据不许跳。

**执行期更正（2026-11-02，波特率这条判据的取证方法）**：`-DUART_LINK_BAUD` 落在哪**不能**用这两条判据，都会假阴性——
- `.pio/build/<env>/compile_commands.json`：IDF 自己生成的那份只有 8 个 `-D`，**不记录 PlatformIO 的 build_flags**（`esp32c5` 也一样查不到 `-DRX_TARGET_C5`，而它在真机上是生效的）。
- 在 `.o`/`.elf` 里按小端字搜 `921600`/`115200`：波特率是喂给 `uart_param_config()` 的立即数（RISC-V 编成 `lui`），不是数据段里的字，搜到 115200 也多半来自别处。

**能用的三条**：①`pio run -e <env> -v` 首行 `Processing …(build_flags: …)` 会原样回显解析后的 env（`extends` 合并结果一并可见）；②`nm -u .pio/build/<env>/src/proto/uart_link.c.o | grep -cE "uart_driver_install|uart_param_config|gpio_pullup_en"`——C5/中继支是 `3`、扫描 env 空桩支是 `0`，这条同时证明 `-DRX_TARGET_C5` 真的进了编译器；③真机 `NET STATUS` 的 `head_baud`（Task 14 的前置检查）。波特率的**数值**只有 ③ 能定案，本轮如实标"未实测"。

- [ ] **Step 4: 两个 C5 env 编过，且只有 head env 改了速率**

Run:
```bash
cd repo/firmware && /Users/fcj/.local/bin/pio run -e esp32c5 2>&1 | tail -3; \
  /Users/fcj/.local/bin/pio run -e esp32c5-head 2>&1 | tail -3
```
Expected: 两个 `SUCCESS`，且 `esp32c5` 的 `firmware.bin` 字节数与 M1 记录的 **687,680** 一致（差值只应来自 `line_pace.h` 的内联与 `uart_link.c` 的新函数，Task 12 逐项对账）。

**本机踩到的新 env 首建坑（2026-11-02 实测）**：第一次 `pio run -e esp32c5-head` 会为新 env 走 LPE 依赖解析，试图从网络装 `throwtheswitch/Unity@^2.6.1`（`test/` 目录带进来的），本机直连失败报 `HTTPClientError`；挂上代理后改报 `Failed to install Python dependencies into penv`（penv 是 Python 3.14.5，pioarduino 不接受）。**不要**为此动 penv——`.pio/libdeps/<env>/` 只是按 env 分开的缓存，把同依赖的兄弟 env 拷过来即可：
```bash
cd repo/firmware/.pio/libdeps && cp -R esp32c5/Unity esp32c5-head/Unity && cp esp32c5/integrity.dat esp32c5-head/integrity.dat
```
`integrity.dat` 存的是依赖声明原文（`throwtheswitch/Unity@^2.6.1`），不是哈希，所以同依赖可直接复用。换机器/在 Pi 上重跑时这条要么联网装要么同样拷。

- [ ] **Step 5: 提交**

```bash
cd repo && git add firmware/src/scan/scanner.c firmware/src/proto/commands.c firmware/platformio.ini
git commit -m "feat(firmware): pace from line rate; esp32c5-head env at 921600"
```

---

### Task 5: `relay_pipe`——扫频头字节进上行

**Files:**
- Create: `firmware/src/net/relay_pipe.h`
- Create: `firmware/src/net/relay_pipe.c`
- Modify: `firmware/src/CMakeLists.txt`（relay 那一支）
- Modify: `firmware/src/main.c:96-115`

- [ ] **Step 1: 头文件**

Create `firmware/src/net/relay_pipe.h`:

```c
/* The relay's sweep-head byte pipe: every byte the head's UART emits goes out
 * the uplink unchanged, and command lines go back the other way. No parsing, no
 * envelope - the frames carry their own 0xA5 sync and CRC and the platform's
 * stream parser owns resync (spec 分歧 B; the same choice web/serve.mjs's
 * attachNode() makes). GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>

void relay_pipe_start(void);
/* One read chunk of the head's bytes as one WS binary frame. Concatenation,
 * not message boundaries, is what carries the stream - so a frame may straddle
 * two chunks and rftrack/backend/app/services/frame_parser.py has to cope;
 * Task 8 is the test that says it does. */
void relay_pipe_status(char *out, size_t cap);
/* "HEAD <line>" - the rest of the command line, verbatim, to the head. */
bool relay_pipe_send(const char *line);
```

- [ ] **Step 2: 实现**

Create `firmware/src/net/relay_pipe.c`:

```c
/* GPL-3.0-or-later. */
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "link.h"
#include "relay_pipe.h"
#include "uart_link.h"
#include "ws_link.h"

/* One chunk is what one uart_link_read returns; 1024 matches the C5's own TX
 * ring so a whole spectrum frame never needs two reads at the source, and it
 * is well inside the WS queue's largest item. */
#define PIPE_CHUNK 1024
#define PIPE_POLL_MS 20

static TaskHandle_t task;
static volatile uint32_t bytes_up;   /* forwarded to the uplink queue */
static volatile uint32_t lost_down;  /* uplink was not up: these bytes are gone */

static void pipe_task_fn(void *arg) {
    (void)arg;
    uint8_t buf[PIPE_CHUNK];
    for (;;) {
        if (!uart_link_ready()) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        const int n = uart_link_read(buf, sizeof(buf), PIPE_POLL_MS);
        if (n <= 0) continue;
        /* Bounded by the queue link_raw_to already is: with the socket down we
         * drain the UART (so the head keeps sweeping and the gap in the
         * recording is real) and count what we could not send. Parking here
         * instead would let noise accumulate in the ring and then blast it at
         * the uplink on reconnect. */
        if (!ws_link_up()) {
            lost_down += (uint32_t)n;
            continue;
        }
        if (link_raw_to(LINK_WS, buf, (size_t)n)) bytes_up += (uint32_t)n;
        /* else: link_raw_to counted a drop of its own (queue full). Two
         * ledgers, two owners - not this task's to double-count. */
    }
}

void relay_pipe_start(void) {
    if (task) return;
    if (xTaskCreate(pipe_task_fn, "head_pipe", 4096, NULL, 5, &task) != pdPASS) task = NULL;
}

void relay_pipe_status(char *out, size_t cap) {
    int tx = -1, rx = -1;
    uart_link_pins(&tx, &rx);
    /* A JSON fragment, same shape as ws_link_status(): NET STATUS splices
     * these into one object. head_baud is here because "why is the head quiet"
     * is nearly always a baud or a pin pair, and the board is the only witness. */
    snprintf(out, cap, "\"head_ready\":%s,\"head_baud\":%" PRIu32 ",\"head_pins\":\"%d/%d\","
                      "\"head_bytes_up\":%" PRIu32 ",\"head_lost_down\":%" PRIu32,
             uart_link_ready() ? "true" : "false", uart_link_line_rate(), tx, rx, bytes_up, lost_down);
}

bool relay_pipe_send(const char *line) {
    if (!uart_link_ready() || !line || !line[0]) return false;
    char cmd[128];
    const int n = snprintf(cmd, sizeof(cmd), "%s\n", line);
    if (n <= 0 || (size_t)n >= sizeof(cmd)) return false;
    return uart_link_write(cmd, (size_t)n) == n;
}
```

- [ ] **Step 3: 进编译隔离门**

`firmware/src/CMakeLists.txt` 里那支 `if(DEFINED ENV{WIDSCAN_RELAY} OR WIDSCAN_RELAY)` 改成：

```cmake
if(DEFINED ENV{WIDSCAN_RELAY} OR WIDSCAN_RELAY)
    list(APPEND sources "net/ws_codec.c" "net/ws_link.c" "net/net_prefs.c" "net/relay_pipe.c")
endif()
```

- [ ] **Step 4: main.c 先开 head 再起管道**

`firmware/src/main.c:96-115` 的中继分支里，在 `commands_task_start();` **之前**插入（并把 include 段补上 `#include "net/relay_pipe.h"` 与 `#include "proto/uart_link.h"`）：

```c
    /* The head link after the prefs: NET UART says which pair the wire is
     * actually on, and 0/0 keeps U0TXD/U0RXD. Failing here is not fatal - the
     * relay still uplinks its own replies, and NET STATUS shows head_ready. */
    if (!uart_link_open(prefs.tx, prefs.rx)) link_printf("ERR head_uart\n");
    relay_pipe_start();
```

- [ ] **Step 5: 编过 + 扫描 env 没进 relay_pipe**

Run:
```bash
cd repo/firmware && /Users/fcj/.local/bin/pio run -e esp32s3-relay 2>&1 | tail -3; \
  find .pio/build/esp32s3 -name "relay_pipe*" | wc -l
```
Expected: `SUCCESS`，且 `0`（扫描 env 里不存在这个对象文件——这条是「编译期隔离优先」的真判据，不是注释）。

- [ ] **Step 6: 提交**

```bash
cd repo && git add firmware/src/net/relay_pipe.c firmware/src/net/relay_pipe.h firmware/src/CMakeLists.txt firmware/src/main.c
git commit -m "feat(firmware): relay byte pipe - sweep head UART to WS, counted, unparsed"
```

---

### Task 6: 命令面——`HEAD <line>` 与 `NET UART`，并把 `cmd_uart` 挡在中继外

**Files:**
- Modify: `firmware/src/proto/commands.c:258-269`（`relay_allowed`）
- Modify: `firmware/src/proto/commands.c` NET 分支（`commands.c:694-762` 附近）
- Modify: `firmware/src/proto/commands.c:748-762`（`NET STATUS` 拼接）
- Modify: `firmware/src/proto/commands.c:926-927`（`cmd_uart` 生成）
- Modify: `firmware/src/net/net_prefs.h`（struct 两字段）
- Modify: `firmware/src/net/net_prefs.c`（`tx`/`rx` 两把键）

- [ ] **Step 1: 两道闸都放行 HEAD**

`handle()`（`commands.c:393-403`）在中继上对一条 ASCII 连过**两**道闸：先 `proto == PROTO_RH && !rh_allowed(line)`，再 `!relay_allowed(line)`。而 `proto` 的初值是 `PROTO_RH`（`commands.c:116`），所以只改 `relay_allowed` 的话 `HEAD` 会先被顶回 `ERR rh_mode`——必须两处一起放。

先在 `is_net()`（`commands.c:255`）旁边加一个同形状的判据：

```c
static bool is_head(const char *line) { return !strcmp(line, "HEAD") || !strncmp(line, "HEAD ", 5); }
```

`commands.c:257-269` 的 `relay_allowed()` 改成（注释也换，理由是新增的）：

```c
/* The relay image has no scanner: main.c never calls scanner_init(), so its
 * mutex does not exist and scanner_get_config() would block the command task
 * forever - and the radio belongs to the Wi-Fi station, which a scan would
 * retune away from the AP. So only these lines get answered at all. PROTOCOL
 * MONITOR is refused on purpose (set_proto() reads and writes that same
 * config); "HEAD <line>" is the sweep head's control path (M2): forwarding is
 * opt-in, so a typo can never look like an answer from a board nobody asked
 * anything of - and the relay's own INFO still answers about the relay. */
static bool relay_allowed(const char *line) {
    static const char *const exact[] = {"INFO", "PROTOCOL?", "REBOOT"};
    if (is_net(line) || is_head(line)) return true;
    for (unsigned j = 0; j < sizeof(exact) / sizeof(exact[0]); j++)
        if (!strcmp(line, exact[j])) return true;
    return false;
}
```

`rh_allowed()`（`commands.c:297-300`）的中继支同步加一行，否则默认 RH 模式下 `HEAD` 到不了分发链：

```c
static bool rh_allowed(const char *line) {
#if RX_WIFI_RELAY
    if (is_net(line) || is_head(line)) return true;
#endif
```

- [ ] **Step 2: net_prefs 多两把键**

`net_prefs.h` 的 struct 末尾（`bool on;` 之前）加：

```c
    uint16_t tx, rx; /* relay: the head link's pair. 0 = compiled defaults */
```

并在 include 段加 `#include <stdint.h>`。

`net_prefs.c`：`net_prefs_load` 里在 `on` 之前加

```c
    uint16_t tx = 0, rx = 0;
    nvs_get_u16(h, "tx", &tx); /* missing key = 0 = "use the default pair" */
    nvs_get_u16(h, "rx", &rx);
    p->tx = tx;
    p->rx = rx;
```

`net_prefs_store` 里在 `nvs_set_u8(h, "on", …)` 之前加

```c
    if (e == ESP_OK) e = nvs_set_u16(h, "tx", p->tx);
    if (e == ESP_OK) e = nvs_set_u16(h, "rx", p->rx);
```

`net_prefs_erase` 的键表改成

```c
    static const char *keys[] = {"ssid", "pass", "url", "id", "on", "tx", "rx"};
```

- [ ] **Step 3: `NET UART` 子命令**

`commands.c` 的 NET 分支里，在 `} else if (!strcmp(arg, "ON")) {` **之前**插入：

```c
        } else if (!strncmp(arg, "UART ", 5)) {
            /* Which pair the wire is actually on. Runtime because the board's
             * breakout is not something the chip can tell us (spec 8.1): each
             * guess would otherwise be a reflash. Takes effect at once - and
             * only survives a reboot with NET SAVE. */
            int t = 0, r = 0;
            if (sscanf(arg + 5, "%d %d", &t, &r) != 2 || t <= 0 || r <= 0) { err = "net_uart"; }
            else if (!uart_link_open(t, r)) { err = "net_uart_pin"; }
            else {
                net_ram.tx = (uint16_t)t;
                net_ram.rx = (uint16_t)r;
            }
        }
```

（`uart_link_open` 自己拒 19/20、26–32 和越界号，所以固件不会把控制台或 flash 线让出去。）

- [ ] **Step 4: `HEAD` 转发与 NET STATUS 的 head 块**

在 `handle()` 的分发链里、`commands.c:687` 那支 `} else if (is_net(line)) {` **之前**插入：

```c
#if RX_WIFI_RELAY
    } else if (is_head(line)) {
        if (!strcmp(line, "HEAD")) {
            /* Bare HEAD is the state read, the way NET STATUS is: which pair,
             * which baud, how much has gone up and how much was dropped while
             * the uplink was down. */
            char head[180];
            relay_pipe_status(head, sizeof(head));
            reply_add("HEAD {%s}\n", head);
            reply_send();
            return;
        }
        if (!relay_pipe_send(line + 5)) reply_text("ERR head_down\n");
        /* No local OK on success: the head's own reply *is* the reply, and a
         * second line would look like a second board answering. */
        return;
    }
#endif
```

`NET STATUS` 那段（`commands.c:748-756`）从

```c
            char up[200];
            ws_link_status(up, sizeof(up));
            reply_add("NET {\"on\":%s,\"ssid\":\"%s\",\"url\":\"%s\",\"id\":\"%s\",%s,"
                      "\"queue_permille\":%u,\"out_drops\":%" PRIu32 "}\n",
                      net_ram.on ? "true" : "false", net_ram.ssid, net_ram.url, net_ram.id, up,
                      link_fill_permille_to(LINK_WS), link_drops());
```

改成（head 块进同一条 JSON；`pass` 仍不进回复）：

```c
            char up[200], head[180];
            ws_link_status(up, sizeof(up));
            relay_pipe_status(head, sizeof(head));
            reply_add("NET {\"on\":%s,\"ssid\":\"%s\",\"url\":\"%s\",\"id\":\"%s\",%s,%s,"
                      "\"queue_permille\":%u,\"out_drops\":%" PRIu32 "}\n",
                      net_ram.on ? "true" : "false", net_ram.ssid, net_ram.url, net_ram.id, up, head,
                      link_fill_permille_to(LINK_WS), link_drops());
```

并把 include 段补 `#include "relay_pipe.h"`（与 `ws_link.h` 同一组、同一 `#if RX_WIFI_RELAY` 条件里）。

- [ ] **Step 5: 中继不给 `cmd_uart` 派读任务**

`commands.c:926-927` 的

```c
    if (link_ready(LINK_UART))
        xTaskCreatePinnedToCore(commands_task, "cmd_uart", 6144, (void *)(intptr_t)LINK_UART, 6, NULL,
                                portNUM_PROCESSORS - 1);
```

改成：

```c
#if !RX_WIFI_RELAY
    if (link_ready(LINK_UART))
        xTaskCreatePinnedToCore(commands_task, "cmd_uart", 6144, (void *)(intptr_t)LINK_UART, 6, NULL,
                                portNUM_PROCESSORS - 1);
#else
    /* Not on the relay: its UART carries the head's *frames*, and a command
     * reader there would answer a binary stream with ERR command and talk
     * nonsense back at the sweep head. relay_pipe.c owns those bytes. */
#endif
```

- [ ] **Step 6: 编过 + native 不退步**

Run:
```bash
cd repo/firmware && /Users/fcj/.local/bin/pio run -e esp32s3-relay 2>&1 | tail -3 && \
  /Users/fcj/.local/bin/pio run -e esp32c5 2>&1 | tail -3 && \
  /Users/fcj/.local/bin/pio test -e native 2>&1 | tail -6
```
Expected: 两个 `SUCCESS`，native `34` 条全绿。

- [ ] **Step 7: 提交**

```bash
cd repo && git add firmware/src/proto/commands.c firmware/src/net/net_prefs.c firmware/src/net/net_prefs.h
git commit -m "feat(firmware): HEAD forwarding and NET UART pins; no command reader on the relay's head line"
```

---

### Task 7: STA 为什么起不来——`sta_reason` / `sta_downs` / `why:"no_ip"`

**Files:**
- Modify: `firmware/src/net/ws_link.c:59-72`（`on_wifi_event`）
- Modify: `firmware/src/net/ws_link.c:376-403`（`dial_task_fn`）
- Modify: `firmware/src/net/ws_link.c:478-486`（`ws_link_status`）

- [ ] **Step 1: 事件里留原因**

`ws_link.c` 的状态变量区（`static` 那一片，靠近 `why`）加：

```c
/* The driver's own words about why the station is not up. M1's last run left
 * `sta:false` with `why:""` for minutes: on_wifi_event took `void *data` and
 * threw the disconnect reason away, and the dial task never wrote `why` while
 * it waited for an IP. Two counters, not one string, because the reason code is
 * the driver's and `why` is ours - overwriting one with the other loses both. */
static volatile uint8_t sta_reason;
static volatile uint32_t sta_downs;
```

`on_wifi_event` 改成：

```c
static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = (const wifi_event_sta_disconnected_t *)data;
        sta_reason = d ? d->reason : 0;
        sta_downs++;
        sta_up = false;
        /* Reconnection is the driver's own job: a second retry loop here would
         * keep interrupting it. want_up only gates it so NET OFF stays off. */
        if (want_up) esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        sta_up = true;
        sta_reason = 0; /* an association that worked is not a failure to explain */
    } else {
        (void)data;
    }
}
```

- [ ] **Step 2: 等待也要有名**

`dial_task_fn` 的 `if (!sta_up)` 分支（`ws_link.c:383-386`）改成：

```c
        if (!sta_up) {
            /* `why` is the dial loop's field, and the dial loop never ran: say
             * so instead of leaving an empty string that reads like "fine".
             * Only the first pass writes it - sta_reason carries the detail. */
            if (!why[0]) set_why("no_ip");
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
```

（`ws_link_stop()` 里把 `why[0] = 0;` 加进 `sta_up = false;` 那一行之后，NET OFF 后重新 ON 才能重新命名一次；同函数 `commands.c` 的 `NET OFF` 已调 `ws_link_stop()`，不需额外改。）

- [ ] **Step 3: 状态带上这两个数**

`ws_link_status` 的 `snprintf` 改成：

```c
    snprintf(out, cap, "\"sta\":%s,\"ws\":%s,\"rssi\":%d,\"backoff_ms\":%" PRIu32
                    ",\"in_drops\":%" PRIu32 ",\"why\":\"%s\",\"sta_reason\":%" PRIu8
                    ",\"sta_downs\":%" PRIu32,
             sta_up ? "true" : "false", ws_up ? "true" : "false", rssi, backoff_ms, rx_drops, why,
             sta_reason, sta_downs);
```

`NET STATUS` 的 `up[200]` 缓冲在 Step 3 后至少需要 232 字节才不截尾——把 `commands.c` 那处 `char up[200], head[180];` 改成 `char up[256], head[180];`。**这条是真会咬人的**：`snprintf` 截了尾会让 JSON 少一个键而不是报错。

- [ ] **Step 4: 编过并检查 `why` 的缓冲区还够**

Run:
```bash
cd repo/firmware && grep -n "static char why\|char why\[" src/net/ws_link.c; \
  /Users/fcj/.local/bin/pio run -e esp32s3-relay 2>&1 | tail -3
```
Expected: `why` 是 `static char why[24]`（或更大），`SUCCESS`。若 `why` 小于 `"no_ip"`+NUL 之外的余量，把它的尺寸写进 Step 3 的提交信息而不是悄悄扩容。

- [ ] **Step 5: 提交**

```bash
cd repo && git add firmware/src/net/ws_link.c firmware/src/proto/commands.c
git commit -m "fix(relay): record the STA disconnect reason - why:\"\" hid a dead association"
```

---

### Task 8: 平台侧的管道前提——分块不变性

**Files:**
- Modify: `rftrack/backend/tests/test_frame_parity.py`（新增一条参数化用例，不动既有用例）

- [ ] **Step 1: 写测试**

追加到 `rftrack/backend/tests/test_frame_parity.py` 末尾（该文件已有 `STREAMS`、`STREAM_NAMES`、`FrameStream` 三个现成的名字，本用例只用它们，不新造夹具）：

```python
@pytest.mark.parametrize("name", STREAM_NAMES)
@pytest.mark.parametrize("rechunk", [1, 7, 1024])
def test_rechunking_a_stream_changes_nothing(name, rechunk):
    """中继的字节管道按 UART 一次读到的块切（relay_pipe.c 的 PIPE_CHUNK=1024），
    一条帧可以跨过两个 WS 二进制帧——所以"块怎么切"必须不影响"解出什么"。

    期望值仍然取 TS 导出的那一份（`want["items"]` / `want["counters"]`），**不是**
    把整块喂一次让实现自己的输出当期望：那样这条用例只能证明解析器自洽，证明不了
    它与设备切块相容。夹具来自权威、切块来自设备，两条叠起来才是管道的地基。
    """
    want = STREAMS[name]
    blob = b"".join(bytes.fromhex(c) for c in want["feed"])
    stream = FrameStream()
    got = []
    for i in range(0, len(blob), rechunk):
        for item in stream.push(blob[i : i + rechunk]):
            got.append(
                {"kind": "text", "line": item.line}
                if item.kind == "text"
                else {
                    "kind": "frame",
                    "type": item.type,
                    "payload_hex": item.payload.hex(),
                    "payload_len": len(item.payload),
                }
            )
    assert got == want["items"], f"{name} @ {rechunk}B"
    assert (stream.crc_errors, stream.resync_bytes, stream.bytes_in) == (
        want["counters"]["crcErrors"],
        want["counters"]["resyncBytes"],
        want["counters"]["bytes"],
    )
```

- [ ] **Step 2: 跑到通过**

Run: `cd repo/rftrack/backend && python -m pytest tests/test_frame_parity.py -k rechunk -q --junit-xml=/tmp/w58-probe/chunk.xml`
Expected: `24` 条（8 条 golden 流 × 3 种切块）全 PASS。
**这条性质今天已经实测成立**：`/tmp/w58-probe/chunk_probe.py`（2026-11-02，本计划写就前）对全部 8 条流按 1/7/1024 重切，逐项与 `want` 相同、无一行 MISMATCH。所以它不是"新缺陷"，是给 Task 5 的前提上锁。
若实现完反而 FAIL，那说明 `FrameStream` 被顺手改过——**停下来上报**，不要绕过去改测试的期望值。

- [ ] **Step 3: 提交**

```bash
cd repo && git add rftrack/backend/tests/test_frame_parity.py
git commit -m "test(rftrack): stream parser must be invariant to relay chunk boundaries"
```

---

### Task 9: L1 假中继——真 C5 字节按管道的切块灌进平台

**Files:**
- Create: `rftrack/tools/relay_chunk_replay.mjs`
- Test: 后端 health 快照（脚本内断言，不落库）

- [ ] **Step 1: 写脚本**

Create `rftrack/tools/relay_chunk_replay.mjs`:

```js
// L1: pretend to be the relay. The bytes are a real C5 capture (never
// synthetic - inventing scan frames is the red line), chunked the way
// relay_pipe.c chunks them, paced at the C5's measured 37.0 KB/s, and sent to
// the bridge's node endpoint exactly as the device would. Proves the byte path
// and the platform's parser; proves nothing about the radio or the wire.
//
// usage: node rftrack/tools/relay_chunk_replay.mjs --file /tmp/w58-probe/dual-C5.bin \
//          --url ws://127.0.0.1:8011/node?id=fake-head --seconds 30 [--chunk 1024]
const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : dflt;
};
const file = opt("file", "");
const url = opt("url", "");
const seconds = Number(opt("seconds", "30"));
const chunk = Number(opt("chunk", "1024"));
const RATE = 37.0 * 1024; // the C5's measured USB-path throughput
if (!file || !url) {
  console.error("need --file and --url");
  process.exit(2);
}
const { readFileSync } = await import("node:fs");
const all = readFileSync(file);
const WS = (await import("ws")).default;

const sock = new WS(url);
let sent = 0;
let off = 0;
await new Promise((res, rej) => {
  sock.on("open", res);
  sock.on("error", rej);
});
const step = Math.max(1, Math.round((RATE * 0.05) / 1000 / chunk)); // 50 ms ticks
const stop = Date.now() + seconds * 1000;
await new Promise((res) => {
  const timer = setInterval(() => {
    for (let i = 0; i < step && off + chunk <= all.length; i++) {
      sock.send(all.subarray(off, off + chunk));
      off += chunk;
      sent += chunk;
    }
    if (Date.now() >= stop || off >= all.length) {
      clearInterval(timer);
      res();
    }
  }, 50);
});
sock.close();
console.log(JSON.stringify({ sent, source_bytes: all.length, chunk, seconds }));
```

- [ ] **Step 2: 起桥与后端，跑一次，取 health**

夹具就位（2026-11-02 实测 `/tmp/w58-probe/dual-C5.bin` 在，2,215,611 B，是 ①USB 真机录制的那一段）：

```bash
cd repo && mkdir -p /tmp/w58-probe && \
  node web/serve.mjs --port 8011 > /tmp/w58-probe/m2-serve.log 2>&1 &
cd repo/rftrack/backend && RFTRACK_DB_PATH=/tmp/w58-probe/m2-l1.db RFTRACK_PORT=9020 \
  /opt/homebrew/bin/python3 serve.py > /tmp/w58-probe/m2-backend.log 2>&1 &
sleep 8
```

**必须走 `serve.py`**：`rftrack/deploy/README.md:6-7` 明写直接 `uvicorn app.main:create_app` 起的实例不建表，症状是"插件注册表没同步"这类极难倒推回启动顺序的现象。`RFTRACK_DB_PATH` 指到 `/tmp` 的新库，别碰演示环境那份。

建一条能拨到桥的网关（`base_url` 只在 PATCH/PUT 上，`kind` 用 `mcu-relay`）：

```bash
B=http://127.0.0.1:9020
S=$(curl -s --noproxy '*' -X POST $B/api/sites -H 'content-type: application/json' \
    -d '{"name":"M2 L1","slug":"m2l1"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["site_id"])')
R=$(curl -s --noproxy '*' -X POST $B/api/relays -H 'content-type: application/json' \
    -d "{\"site_id\":$S,\"name\":\"塔\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["relay_id"])')
G=$(curl -s --noproxy '*' -X POST $B/api/gateways -H 'content-type: application/json' \
    -d "{\"relay_id\":$R,\"name\":\"s3-relay\",\"kind\":\"mcu-relay\"}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["gateway_id"])')
curl -s --noproxy '*' -X PATCH $B/api/gateways/$G -H 'content-type: application/json' \
  -d '{"base_url":"http://127.0.0.1:8011"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["base_url'])'
```

然后放字节（真字节，不合成）：

```bash
cd repo && node rftrack/tools/relay_chunk_replay.mjs --file /tmp/w58-probe/dual-C5.bin \
  --url ws://127.0.0.1:8011/node?id=fake-head --seconds 30
```

拨号循环是按 TTL 重取桥的端点表的，所以脚本跑完**先别关桥**：`node:fake-head` 要在桥上活着，后端才拨得到。上面脚本 30 s 后自己关 socket——把 `--seconds 120` 用作判据窗口，health 在窗口里读。

- [ ] **Step 3: 判据**

Run: `curl -s --noproxy '*' http://127.0.0.1:9020/api/health | python3 -m json.tool | sed -n '/capture/,/}/p'`
Expected:
- `capture.frames` 增量 > 0
- `crc_errors` = 0、`resync_bytes` = 0、`skipped_frames` 无新增类型
- Task 10 做完按端点快照后，这组数字要能在 `capture.endpoint_stats[]` 里归到 `node:fake-head` 那一条上（Task 10 之前只有汇总，归因缺口是已申报的）。
- 读完把两个后台进程收掉：`pkill -f "serve.mjs --port 8011"; pkill -f "RFTRACK_PORT=9020"` 不成就用 `lsof -ti tcp:8011 -i tcp:9020 | xargs kill`——**只杀这两个端口上的**，演示环境的 8010/9000 不许碰。

- [ ] **Step 4: 门槛口径写清楚**

本条只到 **仅完成模拟测试**：代码路径证据，不是无线电证据。收尾报告里不许升档。

- [ ] **Step 5: 提交**

```bash
cd repo && git add rftrack/tools/relay_chunk_replay.mjs
git commit -m "test(relay): L1 fake relay replay of a real C5 capture at pipe chunk sizes"
```

---

### Task 10: 平台 M2 收尾①——按端点的采集明细进 `/api/health`

**Files:**
- Modify: `rftrack/backend/app/services/capture.py`（`_Endpoint:139-161` 加两把计数、`_frame:619-624` 记账、新增 `_refresh_endpoints()`、`status():1115-1124` 加新键）
- Modify: `rftrack/backend/tests/test_capture.py`（用例 + 一条 health 透传断言）
- **不改** `app/routers/health.py`：它把 `capture.status()` 整节透出（`health.py:100` 取、`:145` 放），新键自动到位。

**三条先钉住的约束（都是一手读出来的，不是设计偏好）：**

1. `status()["endpoints"]` 今天**是整数**，而且被 `tests/test_capture.py:552` 断言（`== 0`）。按端点明细因此必须落在**新键 `endpoint_stats`** 上——把 `endpoints` 改成列表等于改契约，还要顺手动那条断言，属于"顺手放宽判据"。
2. `status()` 是同步端点，**跑在线程池里**，而 `_endpoints` 在事件循环上一直变（`capture.py:1107-1113` 把这条写成显式纪律）。所以明细必须是循环侧**整份换出的元组**，照 `_refresh_queues()`（`capture.py:467-473`，调用点 `:462`/`:497`/`:562`）那一套来；直接在线程里迭代 `_endpoints` 会偶发 `dictionary changed size during iteration`，而发生的时刻正是板子最多、有人在盯健康看的时刻。
3. 会话结束（`_forget_endpoint:473-483`）也要换快照，否则"拔掉的那条"会以 0 帧的形态留在明细里——那是"在列表但不说话"的假死，spec §6 明令禁止的形态。

- [ ] **Step 1: 写失败的测试**

追加到 `rftrack/backend/tests/test_capture.py`（复用该文件既有的 `chain` / `make_pipeline` / `run` 三个夹具与 `METRICS`/`LOG` 字节常量，不新造）：

```python
def test_endpoint_detail_names_which_link_a_frame_came_from(client, chain):
    """2026-11-01 那次 99,465 个 CRC 错误归不到任何一条流上，只能整场重接。
    `crc_errors=0` 从此要说清是**哪条**链路的 0。"""
    pipeline = make_pipeline(client)
    main, away = chain["main"], chain["away"]
    run(
        client,
        pipeline,
        (main["gateway_id"], main["ep"], METRICS),
        (main["gateway_id"], main["ep"], LOG),
        (away["gateway_id"], away["ep"], METRICS),
    )
    rows = {r["endpoint"]: r for r in pipeline.status()["endpoint_stats"]}
    assert set(rows) == {main["ep"], away["ep"]}
    assert rows[main["ep"]]["frames"] == 2, "LOG 也是帧：按帧数记，不按能不能落库记"
    assert rows[main["ep"]]["skipped"] == 1
    assert rows[main["ep"]]["gateway_id"] == main["gateway_id"]
    assert rows[away["ep"]]["frames"] == 1
    for row in rows.values():
        assert set(row) == {
            "endpoint", "gateway_id", "source", "frames", "skipped",
            "crc_errors", "resync_bytes", "last_frame_mono",
        }
    # 另一条已经没了：会话散了就不能在明细里留一个 0 帧的尸首
    pipeline.untap(Link(gateway_id=away["gateway_id"], ep=away["ep"], url="ws://x/uart"))
    assert {r["endpoint"] for r in pipeline.status()["endpoint_stats"]} == {main["ep"]}


def test_endpoint_detail_rides_through_health(client, chain):
    """/api/health 是运营者唯一会去点的地方；管道侧算得再对，不透出去等于没有。"""
    body = client.get("/api/health").json()
    assert "endpoint_stats" in body["capture"]
    assert isinstance(body["capture"]["endpoints"], int), "旧键的形态是契约，不许顺手改"
```

- [ ] **Step 2: 跑到失败**

Run: `cd repo/rftrack/backend && python -m pytest tests/test_capture.py -k endpoint_detail -q --junit-xml=/tmp/w58-probe/eps.xml`
Expected: 两条 FAIL，`KeyError: 'endpoint_stats'`。`Link` 本文件 `:42` 已 import、`:548` 那条用例就是用它收会话的，不用新造公开方法。

- [ ] **Step 3: 实现**

`_Endpoint`（`capture.py:139-161`）末尾加两把计数：

```python
    #: 按端点的帧/跳过账。全局总数回答"这场一共多少帧"，这一对回答"**是哪条流**给的"——
    #: 2026-11-01 的 99,465 个 CRC 只能整场重接，就是因为当时只有全局。
    frames_seen: int = 0
    skipped_seen: int = 0
```

`_frame()`（`capture.py:619-624`）里 `self.frames += 1` 之后加 `endpoint.frames_seen += 1`；`decoded is None or "channels" not in decoded` 那一支里、`self._count_skipped(...)` 之前加 `endpoint.skipped_seen += 1`（`_count_skipped` 的签名不动：它只管全局那份快照）。

`__init__`（`capture.py:298-300` 那两个"发布出去就只读"的快照旁边）加：

```python
        self._endpoint_stats: tuple[dict[str, Any], ...] = ()
```

`_refresh_queues()`（`capture.py:467`）旁边加一个同形状、同理由的发布函数：

```python
    def _refresh_endpoints(self) -> None:
        """按端点明细发布成**新**元组。规则与 `_refresh_queues()` 一字不差：
        `/api/health` 在另一个线程里迭代它，而这边随时可能新增或拔掉一条端点。"""
        self._endpoint_stats = tuple(
            {
                "endpoint": endpoint.ep,
                "gateway_id": endpoint.gateway_id,
                "source": endpoint.source,
                "frames": endpoint.frames_seen,
                "skipped": endpoint.skipped_seen,
                "crc_errors": endpoint.stream.crc_errors,
                "resync_bytes": endpoint.stream.resync_bytes,
                "last_frame_mono": endpoint.last_frame_mono,
            }
            for endpoint in sorted(
                self._endpoints.values(), key=lambda e: (e.gateway_id, e.ep)
            )
        )
```

`sorted(...)` 是**有意的**：`/api/endpoints` 的表序会变，明细按 `(gateway_id, ep)` 定序，两次 health 才能逐行 diff。

三个 `self._refresh_queues()` 调用点（`:462`/`:497`/`:562`）后面各加一行 `self._refresh_endpoints()`，`_forget_endpoint`（`:473-483`）在 `self.crc_errors += ...` 那两行之后也加一行——拔掉的当场消失。

`status()`（`:1124` 那行 `"endpoints": len(self._endpoints),` 之后）加：

```python
            "endpoint_stats": list(self._endpoint_stats),
```

收会话的公开路径已经有了：`untap(link)`（`capture.py:421`）内部就是 `_forget_endpoint`，所以 Step 1 用 `untap` 触发的正是上面 `_forget_endpoint` 那行新加的发布——不需要、也不许新发明一个 `forget()`。

- [ ] **Step 4: 跑到通过并跑全量**

Run: `cd repo/rftrack/backend && python -m pytest -q --junit-xml=/tmp/w58-probe/m2-pytest.xml`
Expected: 全绿、1 skipped（唯一 skip 仍是 `test_settings_isolation.py:258`），总数 = 587 + 2。

- [ ] **Step 5: 提交**

```bash
cd repo && git add rftrack/backend
git commit -m "feat(rftrack): per-endpoint capture detail in /api/health"
```

---

### Task 11: 平台 M2 收尾②——`PUT /api/nodes/{id}`（操作员显式声明能力）

**Files:**
- Modify: `rftrack/backend/app/routers/nodes.py`（今天只有两个 GET，`:50`/`:65`）
- Modify: `rftrack/backend/app/schemas.py`（加一个入参模型；`NodeOut:112-128` **不动**）
- Modify: `rftrack/backend/tests/test_nodes_read.py`（`chain` 夹具 `:21-63` 就在本文件，复用不复制）

**三条一手依据（决定形状，不是风格）：**

1. `NodeOut` 里**没有** `declared` 这一族字段，也没有"来源"位。所以响应就用现成的 `as_out()`（`nodes.py:31-47`）——给它加 `declared.source` 等于为一条命令造新契约，本任务否掉。操作员是谁写的，记在提交信息和文档里，不记进 API。
2. `reconcile()`（`ingest.py:85-160`）**从不写** `capabilities_json` / `fw_version` / `protocol_version`：它只动 `display_name/transport_kind/stops_rh/status/last_seen`。⇒ 新的 PUT 是这三列的唯一写入口，且不会被下一轮拨号对账抹掉。这条正是"声明不会被端点表冲掉"的证据，写成一条测试。
3. 汇聚实例的写侧口径是 `require_writable_session`（`deps.py:44`），405 的 `detail` 形状在 `test_settings_isolation.py:410-412` 有整段现成断言可抄。

- [ ] **Step 1: 写失败的测试**

追加到 `rftrack/backend/tests/test_nodes_read.py`：

```python
def test_put_declares_the_three_columns_the_ui_has_always_rendered_unsupported(chain, client):
    """`levels/spectrum/rawiq` 与 `fw_version`/`protocol_version` 自 001 就在表里，
    页面上恒是 unsupported——因为今天没有任何写路径。M2 补的就是这一条。
    spectrum 是有界能力：只有 921600 的下行才配得上 992 bins（line_pace 的上限）。"""
    chain["add"]("起点塔", 7, "中继头的板", "node-ws", "node:fake-head")
    before = client.get("/api/nodes/7").json()["capabilities"]
    assert before["spectrum"] == {"supported": False, "reason": "undeclared"}

    row = client.put(
        "/api/nodes/7",
        json={
            "fw_version": "Web-v0.10",
            "protocol_version": 1,
            "capabilities": {
                "levels": True,
                "spectrum": {"supported": True, "max_bins": 992},
                "rawiq": {"supported": False},
            },
        },
    )
    assert row.status_code == 200, row.text
    out = row.json()
    assert out["declaration_error"] is None
    assert out["capabilities"]["levels"] == {"supported": True}
    assert out["capabilities"]["spectrum"] == {"supported": True, "max_bins": 992}
    assert out["capabilities"]["rawiq"] == {
        "supported": False,
        "reason": "declared_unsupported",
    }
    assert out["fw_version"] == "Web-v0.10"
    assert out["protocol_version"] == 1
    # 形状没被发明：响应就是 GET 的那一套键
    assert set(out) == set(client.get("/api/nodes/7").json())


def test_a_declaration_survives_the_next_endpoint_sweep(chain, client):
    """`reconcile()` 每轮都会重跑。它要是把声明冲掉，操作员填一次就永远白填——
    而现象是"能力位过一会儿自己变回 unsupported"，最难倒推。"""
    from app.services.ingest import reconcile
    from app.config import Settings
    from app.models import Gateway
    from sqlalchemy import select

    chain["add"]("起点塔", 8, "扫频头", "node-ws", "node:head-8")
    client.put(
        "/api/nodes/8",
        json={"capabilities": {"levels": True, "spectrum": {"supported": True, "max_bins": 512}}},
    )
    with client.app.state.db.scope() as session:
        gateway = session.get(Gateway, chain["gateways"]["起点塔"]["gateway_id"])
        reconcile(
            session,
            gateway,
            [
                {
                    "id": "node:head-8",
                    "kind": "node",
                    "label": "扫频头",
                    "online": True,
                    "busy": False,
                    # 端点表里塞一份"能力"：平台一个字都不许从这里取
                    "capabilities": {"levels": False, "rawiq": True},
                    "fw_version": "从端点表偷来的",
                }
            ],
            Settings(max_nodes_per_gateway=8),
        )
    out = client.get("/api/nodes/8").json()
    assert out["fw_version"] is None, "端点表里那句 fw_version 一个字都不许进账"
    assert out["capabilities"]["rawiq"]["supported"] is False
    assert out["capabilities"]["spectrum"]["max_bins"] == 512


def test_put_rejects_a_declaration_that_is_not_an_object(chain, client):
    """不是对象的声明会渲染成 `not_an_object` 且全部 unsupported——那就写进去一份
    读不出来的东西。入侧就拒，比让页面显示"声明有问题"诚实。"""
    chain["add"]("起点塔", 9, "a", "usb-native", "/dev/a")
    assert client.put("/api/nodes/9", json={"capabilities": [1, 2]}).status_code == 422


def test_the_aggregator_cannot_declare(chain, client, db_path):
    """声明是改这台实例看到的东西，只在场地实例上写（与另外 21 个写端点同口径）。"""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    chain["add"]("起点塔", 10, "a", "usb-native", "/dev/a")
    reader = TestClient(
        create_app(Settings(db_path=db_path, dialer_enabled=False, instance_role="aggregator"))
    )
    row = reader.put("/api/nodes/10", json={"capabilities": {"levels": True}})
    assert row.status_code == 405, (row.status_code, row.text)
    assert row.json()["detail"]["reason"] == "aggregator_read_only"
```

- [ ] **Step 2: 跑到失败**

Run: `cd repo/rftrack/backend && python -m pytest tests/test_nodes_read.py -k "put or declare or aggregator" -q --junit-xml=/tmp/w58-probe/declare.xml`
Expected: 前四条 FAIL（405/404/422 都不是"路由正常返回 200"）——`Method Not Allowed` 就是"路由还不存在"的证据。

- [ ] **Step 3: 实现**

`schemas.py` 在 `NodeOut` 之后加一个**入参**模型（不出参，避免与 `NodeOut` 混用）：

```python
class NodeDeclaration(BaseModel):
    """`PUT /api/nodes/{id}` 的入参：只有这三列，全部由操作员显式声明。

    没有 `capabilities_json: str` 这种"把 JSON 塞进字符串"的形状——那是把校验推给
    服务端猜。类型本身就是对象，Pydantic 直接拒掉 `[1,2]` 与 `"{}"`。
    """

    capabilities: dict | None = None
    fw_version: str | None = Field(default=None, max_length=64)
    protocol_version: int | None = Field(default=None, ge=0, le=255)
```

`routers/nodes.py`：`from fastapi import ... Depends` 已在，追加 `from ..deps import get_session, require_writable_session`、`from ..schemas import NodeDeclaration`（`NodeOut` 同行），并把 `not_found` 那行扩成 `from ..services.hierarchy import get_or_404, not_found`：

```python
@router.put("/{node_id}", response_model=NodeOut)
def declare_node(
    node_id: int, body: NodeDeclaration, session: Session = Depends(require_writable_session)
) -> NodeOut:
    """能力/版本 = 操作员声明，唯一写入口。

    `reconcile()`（services/ingest.py:85）从不碰这三列，所以这一行不会被下一轮端点
    对账冲掉；反过来，这里也**一个字都不从帧或端点表取**——按帧回填是 spec §1 明确
    否决过的形态：帧里没有"这块板能不能发 RAWIQ"这种事实，只有它今天发了什么。
    """
    node = get_or_404(session, Node, node_id, "node")
    if body.capabilities is not None:
        node.capabilities_json = json.dumps(body.capabilities, ensure_ascii=False, sort_keys=True)
    if body.fw_version is not None:
        node.fw_version = body.fw_version
    if body.protocol_version is not None:
        node.protocol_version = body.protocol_version
    flush_or_conflict(session, "node_declaration")
    row = session.execute(_query().where(Node.node_id == node_id)).first()
    return as_out(*row)
```

补 `import json`（该文件现在没有），并把 `flush_or_conflict` 加进 `..services.hierarchy` 那行。
`display_name/transport` **不在入参里**：它们是 `reconcile()` 的事实，人改不了，改了也会被下一轮盖回去。

- [ ] **Step 4: 跑到通过并跑全量**

Run: `cd repo/rftrack/backend && python -m pytest -q --junit-xml=/tmp/w58-probe/m2-pytest2.xml`
Expected: 全绿（数 = 587 + Task 10 的 2 条 + 本任务 4 条），1 skipped 不变。

- [ ] **Step 5: 提交**

```bash
cd repo && git add rftrack/backend
git commit -m "feat(rftrack): operator-declared node capabilities (PUT /api/nodes/{id})"
```

---

### Task 12: 回退与隔离账——三门槛、三 env、体积差逐项可解释

**Files:**
- 无代码改动（除非门槛不过）

- [ ] **Step 1: 后端门槛**

Run: `cd repo/rftrack/backend && python -m pytest -q --junit-xml=/tmp/w58-probe/gate-m2-backend.xml`
Expected: 全绿、1 skipped；**不许用 skip 换绿**（`pytest -rs` 复核那条 skip 的 reason 还在）。

- [ ] **Step 2: 工作台门槛**

Run: `cd repo/rftrack/frontend/workbench && node --test test/*.test.mjs 2>&1 | tail -8`
Expected: `214` pass / 0 fail。

- [ ] **Step 3: 桥与 web 门槛**

Run: `cd repo/web && npm test 2>&1 | tail -6`
Expected: `56` pass / 0 fail（`web/serve.mjs` 本计划 0 改动，这条是防"顺手改了桥"）。

- [ ] **Step 4: 浏览器冒烟**

Run: `cd repo && node rftrack/frontend/e2e/workbench.mjs 2>&1 | tail -6`
Expected: `65/65`。

- [ ] **Step 5: native 门槛**

Run: `cd repo/firmware && /Users/fcj/.local/bin/pio test -e native 2>&1 | tail -6`
Expected: `34` 条全绿。

- [ ] **Step 6: 四个 env 全编 + 体积账**

Run:
```bash
cd repo/firmware && for e in esp32s3 esp32c5 esp32c5-head esp32s3-relay; do \
  /Users/fcj/.local/bin/pio run -e $e >/tmp/w58-probe/m2-$e.log 2>&1; \
  printf "%-16s %s  %s\n" "$e" "$(grep -c SUCCESS /tmp/w58-probe/m2-$e.log)" \
    "$(ls -l .pio/build/$e/firmware.bin | awk '{print $5}')"; done
```
Expected（基线：`esp32s3` 565,024、`esp32c5` 687,680、relay 695,344）：
- `esp32s3` **565,024 不变**（它只吃 `uart_link.c` 的空桩支，新增的三支里它一支都不进——若有差值必须逐项指出来是什么）。
- `esp32c5` / `esp32c5-head`：差值只应来自 `line_pace.h` 内联 + `uart_link.c` 的 `line_rate/tx_ring` 两个新符号。
- relay：`relay_pipe.c` + `NET UART`/`HEAD` + `sta_reason` 三项，逐项报数。
不设"必须为 0"，但每一字节要有名字。

- [ ] **Step 7: 扫描 env 里没有 net/relay_pipe 对象**

Run: `cd repo/firmware && for d in esp32s3 esp32c5 esp32c5-head; do printf "%-14s %s\n" "$d" "$(find .pio/build/$d -name '*relay_pipe*' -o -name '*ws_link*' | wc -l)"; done`
Expected: 三行都 `0`。

- [ ] **Step 8: L6 真机回退——本计划内标未执行**

`[env:esp32s3]` 重烧 + MONITOR 60 s ×3 + `CONFIG RESET` 后无 Wi-Fi 残留态这条**需要板子**，与用户飞线窗口一起做。**在报告里如实写「真机回归未执行」，此时 M2 只能标到「已通过软件测试」**。

---

### Task 13: 文档回写 + 脱敏扫描 + 提交

**Files:**
- Modify: `docs/superpowers/specs/2026-11-01-s3-wifi-relay-design.md`（§8 状态、§6 门槛表加 L1.5）
- Modify: `docs/rf-track/RF_Track_落地实施方案与计划.md`（§7 的 M2 行实况）
- Create: 本计划的收尾节（勾选项 + 数字）

- [ ] **Step 1: 勾执行痕迹**

本文件每个已做完的 Step 把 `- [ ]` 改成 `- [x]`，并在文末「收尾实况」里记：三门槛/native 数字、四 env 体积账、L1 的 `{sent, frames, crc_errors, resync_bytes}`、D1–D4 的落地位置。真机四道门槛（L2/L3/L4/L5）逐条写「未执行：等 C5↔S3 飞线」。

- [ ] **Step 2: 脱敏扫描（三条执行期规则，M1 已定）**

```bash
cd repo && git add -A && git diff --cached --name-only
python3 /tmp/w58-probe/scrub_scan.py "fork/fw-wifi-relay..HEAD" 2>&1 | tail -12
```
`scrub_scan.py` 的第一个参数是 `git rev-list --objects` 的**区间**（不是单个 rev），它自己扫两处：暂存区每个 blob + 该区间内每个 blob。所以顺序必须是先 `git add -A` 再扫，且区间写 `fork/fw-wifi-relay..HEAD`。
判据（沿用 M1 计划 Step 5 末段的三条更正）：
1. 按**实际待推区间逐 blob**（`git rev-list --objects fork/fw-wifi-relay..HEAD`），不是按提交 grep；
2. 私网字面量的判据是"**任何非 `192.168.99.*` / `203.0.113.x` 占位的私网地址**"，扫描规则本身与文档里都不许出现本机真实网段/SSID；
3. 凭据三源（`ipconfig`、`"ssid":"…"`、`NET SSID|PASS <值>` 命令行）只进内存，回显一律打码；AppleScript 引号串按 WPA 的 8–63 收窄。

Expected: `CLEAN`（0 命中）。有新写的样例地址就当场改字面量——**加补救提交没用**，未推送的 tip 用 `--amend` 重写后复跑门槛再重扫。

- [ ] **Step 3: 提交文档**

```bash
cd repo && git add docs && git commit -m "docs(relay): M2 execution record - gates, size ledger, real-hardware gates deferred"
```

- [ ] **Step 4: 推送只在用户明确要求时做**

形式沿用：`git -c http.proxy=socks5://127.0.0.1:7897 -c http.postBuffer=524288000 -c http.version=HTTP/1.1 push fork fw-wifi-relay`。
**`origin`（CapnBry/widscan58）永不推；`rf-track` 永不合入 `main`。**

---

### Task 14: 真机四道门槛（本计划**不执行**，写清步骤与判据）

前置：C5 与 S3 共地；C5 `GP11(TX) → S3 的 rx`、`C5 GP12(RX) ← S3 的 tx`；C5 烧 `[env:esp32c5-head]`（921600），S3 烧 `[env:esp32s3-relay]`；桥上 `serve.mjs` 与后端在跑；**烧 S3 前确认串口空闲且用户没在网页端占板子**；S3 原固件 4 MB 备份在 `/tmp/w58-probe/s3-full-4MB-backup.bin`；S3 bootloader 偏移 0x0、C5 是 0x2000，别混。

| 层 | 步骤 | 门槛 | 判据在哪读 |
|---|---|---|---|
| L2 飞线静置 | C5→S3 接好后发 `HEAD PROTOCOL MONITOR`，跑满 30 min，期间不碰串口、不发命令 | `crc_errors=0` **且** `resync_bytes=0`、固件 `link_drops()=0`、实测 ≥30 KiB/s | 平台 health（Task 10 的 `capture.endpoint_stats[]` 按 `endpoint=="node:<id>"` 那一条读）+ S3 的 `NET STATUS`（`head_bytes_up`/`out_drops`/`queue_permille`） |
| L3 断网重连 | 路由器断电 60 s 再回 | ≤30 s 自动重连；平台侧 `offline→active` **一次且只一次**；重连期不出现"在列表但 0 帧" | 桥日志 + health；`sta_downs`/`backoff_ms` 给因果 |
| L4 命令回环 | 页面经 WS 发 `HEAD PROTOCOL MONITOR` / `HEAD GAIN MANUAL 30` / `HEAD SCAN START` | 每条 ≤500 ms 收到 ASCII 应答，且落回**同一条** WS；不出现 `ERR head_down` | 回环脚本**执行时现写**（M1 的 `/tmp/w58-probe/hold_node_pipe.py` 已不在，见 §0）：自己就是持有那条会话的客户端，发一行、盖一个本地单调钟、在**同一连接**上等到第一行应答，打印 `RT_MS=`；三条各跑 5 次取最大值 |
| L5 帧对拍 | 同一块 C5 分别走 ①USB 与 ③Wi-Fi 各 60 s，两份字节过同一个 `frame_parser.decode` | 帧类型分布、档位序号、`rssi/snr` 取值集合、`channels` 数逐字段相同；只允许时间戳不同；**RAWIQ 只出现在 USB 那一份** | `rftrack/tools/` 的录制 + Task 8 的同一入口 |
| L1.5（新增） | 拔 AP 电源线重连，或把 SSID 改成一个不存在的 | `NET STATUS` 的 `why` 与 `sta_reason` **必须给得出为什么**，不许再出现 `why:""` 长时间不变 | S3 串口 |

L5 用真录制时注意既有口径：`services/replay.py` 现在按 `SOURCE="mock"` 硬拒真录制——要么走 `frame_parser` 直接吃字节（对拍脚本自己做，别改 replay 的口径），要么显式翻案并记账。

---

## 3. 收尾实况（执行时填）

- 三门槛 / native：待填
- 四 env 体积账：待填
- L1 假中继：待填（`{sent, frames, crc_errors, resync_bytes}`）
- 真机四道门槛：**未执行**（等用户飞线窗口）
- 推送：待用户授权
