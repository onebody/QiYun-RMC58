# 00 · 全仓库审计报告（P0-1）

> **架构版本**：Architecture V1.0（混合式分布式架构）· **本文状态**：只读审计，实现代码零行。
> **审计时点**：2026-11-04 02:41 · **基线**：`repo/` @ 分支 `fw-wifi-relay` / commit `c547498` + §2.5 列出的未提交在制品；`app/`（独立仓库）@ `b03b8cc`。
> **取证方式**：四个并行子代理分别盘点 `firmware/`、`web/`+`web-desktop/`、`rftrack/`、`app/`；承重结论由主线本人逐条 `sed` 复核（§8 列出复核清单）。
> **标注体系**（沿用本仓库既有口径）：**[已由源码确认]** / **[已由真机实测]** / **[已由软件测试]** / **[根据源码推断]** / **[需要实机验证]** / **[当前无法确认]**。
> 关联文档：本目录 `01`–`08`；历史设计件 `current-state.md`、`feature-inventory.md`、`data-flow.md`、`risk-register.md`、`topology-and-access-methods.md`、`multi-node-spectrum.md`、`device-protocol-v2.md`、`docs/superpowers/plans/2026-11-03-widscan58-p0-stage1-audit.md`。

---

## 0. 十条结论

| # | 结论 | 承重证据 |
|---|---|---|
| C1 | 项目实际存在**四份协议镜像**（C / TS / Python / Kotlin），不是一份。任何新平台若再写一份解析器就是第五份，防漂移成本翻倍 | `firmware/src/proto/frames.h` · `web/src/proto/frames.ts` · `rftrack/backend/app/services/frame_parser.py` · `app/core/src/main/kotlin/com/onebody/widscan58/core/FrameParser.kt` **[已由源码确认]** |
| C2 | **固件侧不存在任何硬件唯一 ID**。`INFO` 六段是 `widscan58\|target\|proto\|fw_ver\|commit\|phy`，全树无 `esp_read_mac`/efuse/boot_id 上报 | `firmware/src/proto/commands.c:421-427`（本人复核）；检索 `esp_wifi_get_mac`/`efuse` 无命中 **[已由源码确认]** |
| C3 | **C5/S3 固件没有以太网栈**（全树无 `esp_eth`），Wi-Fi 只有 relay 镜像的 STA + 客户端 WebSocket，且 **`wss://` 被有意拒绝** | `grep -i ethernet\|esp_eth firmware/src/` 为空（本人复核）；`firmware/src/net/ws_codec.h:42-44`「wss:// is refused on purpose: the relay is a LAN device」 |
| C4 | **单机侧一切功能真实存在且可用**，但它 100% 依赖浏览器/手机的本地链路：Web 无框架纯 TS、页面同时只接一块板、持久化只有 localStorage。多节点在 `web/` 里**零落点** | `web/src/main.ts:840`「one radio per page」（本人复核）；`web/serve.mjs:172-174` 每端点一页锁；`web/src/model/store.ts:100-118` 单设备状态 **[已由源码确认]** |
| C5 | **RF Track 已有 20 张 ORM 表 / 62 个路由端点（16 个 router 模块）/ 30 个测试文件（565 个 `def test_`）**，且它的四层固定业务层级 `site→relay→gateway→node` 就写在外键里。这与 V1.0「禁止固定业务层级」正面冲突，但**它的协议对拍、归档往返、压测三类资产是可迁移的硬通货** | `rftrack/backend/app/models.py:85-814`（`grep -c __tablename__` = **20**，本人复核；旧文档口径的"21 张"把 `Base` 类算进去了，见 §2.3 勘误）；`tests/golden/frames-v1.json`（14 cases + 8 streams）；进度台账 §7 **[已由源码确认]** |
| C6 | **真机验证覆盖度不均衡**：USB 双板、Wi-Fi 中继 M1 已过真机；M2（C5→S3 UART 全量中继）只到软件侧，L2/L3/L6 三道真机门槛**未执行** | `docs/rf-track/RF_Track_落地实施方案与计划.md` 进度头 M1/M2 行 **[已由真机实测 / 明确未执行]** |
| C7 | **Android 端零网络能力**：全仓 `app/` 无任何 Socket/TCP/WS，仅 USB Host + GitHub Releases 两处 HTTP。它在新架构里只能是「单机 Console 的第三个形态」 | `app/mobile/src/main/AndroidManifest.xml:5-6` 注释；`usb/UsbCdc.kt:14` 唯一 VID `0x303A` **[已由源码确认]** |
| C8 | **CI 只做发布不跑测试**：唯一 workflow 是 Pages 构建；`web/` 的 e2e、固件 native、后端 pytest 全靠人工执行 | `.github/workflows/pages.yml:11-15,83-115` **[已由源码确认]** |
| C9 | **配置持久化断在设备侧**：扫描配置纯 RAM，只有 RH 模式的 `CONFIG SAVE` 落 NVS blob；relay 的 `NET *` 落 NVS `"net"`。所以「配置版本 / 期望态」必须由平台侧承担并显式下发 | `firmware/src/scan/scanner.c:140-146`（RAM）；`commands.c:147-218`（NVS blob）；`firmware/src/net/net_prefs.c:23-93` **[已由源码确认]** |
| C10 | **本机没有任何可复现构建保证**：版本串来自 `git describe`，但工作区脏（12 个已改 + 9 个未跟踪），且 `firmware/.pio/` 里存在 `esp32s3-relay`/`esp32c5-head` 两棵构建树 → 同一 commit 在不同工作树状态下产出不同二进制 | `firmware/CMakeLists.txt:10-15`；§2.5 工作区清单 **[已由源码确认]** |

---

## 1. 范围与不范围

**审了**：`firmware/`（含 `tools/`）、`web/`、`web-desktop/`、`rftrack/`、`app/`、`archives/`、`.github/`、既有 `docs/` 决策台账。
**没审**（本轮明确不取证，写文档时按「未知」处理）：`ref/`（45 MB 上游参考件）、`esp-dsp`/`esp-sdr` 私有 PHY ABI 的上游行为、RotorHazard 服务端侧（Pi 上的 RH 本体）、Android `mobile/` 的 UI 层自动化（不存在）、任何需要实机才能定的量（对齐精度、Pi 侧容量、SD 磨损）。

---

## 2. 资产清点

### 2.1 `firmware/` —— ESP-IDF + PlatformIO，C

| 项 | 事实 | 证据 |
|---|---|---|
| 构建环境 | 5 个 env：`esp32s3`、`esp32s3-relay`、`esp32c5`（默认）、`esp32c5-head`、`native` | `platformio.ini:19-29,31-45,47-57,59-68,70-78`，默认值 `:10` |
| 规模 | `src` 下 22 个 `.c` + 24 个 `.h` ≈ **7,513 行**；relay 净增 4 文件 ≈ **941 行** | 本人行数统计 **[已由源码确认]** |
| 帧出口 | 二进制头 `0xA5 'R' type ver len payload crc32`，六种帧 METRICS/SPECTRUM/TELEMETRY/RAWIQ/LOG/LEVELS；**LOG 无发射点** | `frames.h:4-13,16-23`；`link.c:126-175`；发射点 `scanner.c:481,345,257,503,543-550` |
| ASCII 面 | `commands.c handle():399-819` 约 45 条命令（含 `INFO`/`PROFILE?`/`CHANNELS`/`MODE`/`ENABLE`/`GAIN`/`SCAN START|STOP`/`METRICS`/`SPEC`/`RAWIQ`/`SYNC`/`FFTEVAL`/`SPURS`/`GPIO`/`NET *`/`HEAD`/`REBOOT`/`BOOTLOADER`） | `commands.c:399-819` **[已由源码确认]** |
| 通道 | USB CDC（`usb_serial_jtag`，非 GPIO UART）、UART0/UART1、Wi-Fi STA + WS 客户端；**USB+UART 可并发**，relay 镜像**三路并启** | `link.c:108-113,250`；`uart_link.c:11-30,78-137`；`ws_link.c` 全文；并发点 `commands.c:967-971,982` |
| sink 规则 | **RAWIQ 无论 sink 一律走 USB**，注释说明理由（UART TX ring 与 WS 帧尺寸都吃不下） | `link.h:17-22`（本人复核原文注释） |
| DSP | `psd.c:148,247`（Welch+Hann+IQ 校正，esp-dsp `dsps_fft2r`）、`fft_r4_sc16.h`、`fft_eval.c:32,59`、`metrics.c:66`（噪声底分位）`:143`（RSSI/SNR/OBW/质心）`:231`（level） | **[已由源码确认]** |
| RH 兼容 | `rh.c:14-42`（读写寄存器段）`:43`（API level 36）；与 monitor 态互斥切换，白名单两张硬表 `commands.c:304-319` | **[已由源码确认]** |
| 硬限制 | 单板 8 频道 `profile.h:8`；`ENABLE` 是 u32 掩码 ⇒ 隐藏 32 频道上限，第 33 起静默失效 | `profile.h:8`、`commands.c:408` |
| 宿主测试 | `test_dsp`（FFT vs DFT/metrics/lap/planner）、`test_pace`、`test_psd`（金标，仅 S16R）、`test_ws`（RFC6455）；一律 `pio test -e native`，**无 Makefile/README** | `firmware/test/*/test_main.c` 头注释；`CLAUDE.md:486` |
| 构建可复现性 | 版本 = `git describe` 注入 `PROJECT_VER`；relay 的网络代码由 `relay_build.py` 用 ENV 门控；分区 `partitions.csv`（factory@0x10000），bootloader 偏移 C5 `0x2000` / S3 `0x0` 只记在 `tools/pages_firmware.py` 头注释里 | `CMakeLists.txt:10-15`；`src/CMakeLists.txt:22-24,53-56` **[已由源码确认]** |
| RAW IQ 机制 | 直读固定 SRAM 银行 `0x40830000`、16380 字，**非公开接口**（逆向所得），有 FILL/GUARD 字校验 | `radio/c5/radio_c5.c:28-37` |

`tools/`（9 件，均可直接复用）：`widscan58cli.py`（主机客户端）、`serial_bridge.py`（串口↔WS 桥）、`rh_check.py`/`rh_diag.py`（RH 节点验证）、**`powercal.py`（dBFS↔dBm 定标）**、`pages_firmware.py`（Pages 固件工件与清单）、`ttyfix.py`、`gen_twiddle.py`、`check_phy_abi.sh`（私有 PHY ABI 校验）。

### 2.2 `web/` + `web-desktop/` —— 无框架 Vanilla TS + Electron

| 项 | 事实 | 证据 |
|---|---|---|
| 栈 | Vite ^8.3.3、TS ^7.0.2 strict、Vitest ^5.0.3；**零框架**，运行时依赖仅 3 个 | `web/package.json:10,13,16-18`；`tsconfig.json:9-11` |
| 规模 | `src` 54 文件 ≈ **10,738 行**（含测试）；14 个 view、`serial/`、`proto/`、`model/`、`mock/`、`util/` | **[已由源码确认]** |
| Transport 抽象 | `device.ts:7-12`；实现：Web Serial `:32`、WebUSB `webusb.ts:11,98-102`、WS `device.ts:139`、Mock、Replay `recorder.ts:39` | **[已由源码确认]** |
| 解析层 | **独立模块**，未散落 UI：`parser.ts:28`（分帧/分行，text 行须匹配 `^[A-Z][A-Z0-9_?]*`）+ `frames.ts:101,148,191,205,260` | **[已由源码确认]** |
| 频谱引擎覆盖 | RSSI `channelGrid.ts:166-181`、SNR `:188`、噪声底 `store.ts:112`+`panorama.ts:46,138,326`、质心 `channelGrid.ts:191`/`zoom.ts:157`、99% OBW `frames.ts:39-40`+`channelGrid.ts:192`、全景 `panorama.ts:1`、瀑布 `panorama.ts:149,334-352`、RAWIQ `views/rawiq.ts`、LEVELS `store.ts:194`、录制回放 `recorder.ts:5,26,39`+`main.ts:886-911`、控制 `views/controls.ts:86,94,125,131` | **[已由源码确认]** |
| 多节点 | **未实现**。页面单设备硬约束 + Store 无节点维度 | `main.ts:840`（本人复核）、`serve.mjs:172-174`、`store.ts:100-118` |
| 本地服务 | `web/serve.mjs` **537 行零依赖 WS**：`GET /api/endpoints:102`、`GET/POST /api/mode:103-135`、`WS /serial?ep=:380-389`、`WS /node?id=:379`、serialport 桥接 `:441-482`、UART 板先停 RH `:486-504`、45 s 心跳 `:291-300`、udev 别名 `:177-203` | **[已由源码确认]** |
| 安全面 | `serve.mjs` 手写 RFC6455（`:248-359`）、**无 TLS 无鉴权**；`/api/mode` 的 POST 体只接受 `scan\|rh` 二值枚举、命令串来自启动参数（`:85-90` `exec`）⇒ **无注入面，但局域网任意主机可让板子停 RH**（可用性） | 本人复核 `serve.mjs:100-135` |
| 持久化 | 只有 localStorage（7 个键），**无 IndexedDB**；历史 = 内存环 120 s | `store.ts:29-55`；键表见 `current-state.md` §6 |
| i18n | 自研字典，en/zh，zh 约 390 条、168 处 `t()` | `i18n.ts:21-25,87` |
| 桌面壳 | Electron ^45：`app://` 特权 scheme（`main.mjs:32-37`）、读 `web/dist` 拷贝（`:80-96`）、设备选择器（`:103-131`）、`WIDSCAN58_AUTO_SERIAL` 钩子（`:107`）、**`webSecurity:false`（`:23`）**；产物 mac dmg(arm64) / win zip | **[已由源码确认]** |
| 测试 | 单元 10 文件 ≈ 56 例；e2e `web/e2e/headless.mjs`（puppeteer-core，28 项检查）+ `layout-stability.mjs`，**不在 CI** | `package.json:13`；`headless.mjs:16` 依赖本机 Chrome 路径 |

### 2.3 `rftrack/` —— FastAPI + SQLite + 无构建 Vue 工作台（本轮进 legacy）

| 项 | 事实 | 证据 |
|---|---|---|
| 表 | **20 张 ORM 表**：`app_config` `site` `relay` `gateway` `node` `channel_plan` `channel_plan_item` `workbench_layout` `layout_widget` `local_settings_import` `signal_event` `sample_summary` `plugin_registry` `plugin_state` `plugin_log` `plugin_finding` `alert_rule` `alert` `candidate_target` `track_observation`。归档清单口径是 **21**（= 裁剪 13 张 + 实例级 8 张，后者含 `alembic_version`），两个数字说的是同一套表、只是把版本表算进不算进的区别 | `models.py:85,118,149,171,216,265,284,314,354,389,431,482,538,570,603,632,680,721,778,814`（20 条，本人 grep 复核）；`archive.py:49-89`（`SITE_SCOPED_TABLES`/`DERIVED_TABLES`/`INSTANCE_TABLES` 三组，含 `alembic_version`） |
| 固定层级 | `site→relay→gateway→node` 写进外键：`Gateway.relay_id` NOT NULL + RESTRICT（`models.py:175-177`）、`uq_gateway_relay_name`（`:172`）、Node 唯一键 `(gateway_id, transport_addr)`（`:195-205`，明文禁止按显示名全局去重） | **[已由源码确认]** |
| 迁移 | `001_baseline`…`007_node_identity`；**`007` 仍未提交**（工作区未跟踪文件） | `rftrack/backend/alembic/versions/`；§2.5 |
| API | **62 个路由端点**，分布在 **16 个 router 模块**（`alerts 5` `archive 2` `bridge 2` `events 2` `gateways 6` `health 1` `layouts 10` `live 1` `nodes 3` `plugins 6` `relays 5` `samples 1` `settings 9` `sites 5` `targets 3` `ws 1`），`main.py` 本身 0 条 | 本人逐文件 `grep -oE "@(router\|app)\.(get\|post\|put\|patch\|delete\|websocket\|api_route)"` 计数求和 **[已由源码确认]** |
| 测试 | **30 个 `test_*.py`，565 个 `def test_`**（另有 `conftest.py` 与 `tests/golden/`）；门槛台账见 §7；**golden 帧对拍夹具** `tests/golden/frames-v1.json`（14 cases + 8 streams，权威来自 TS 侧导出） | `ls tests/test_*.py` + `grep -c "def test_"` **[已由源码确认]** |
| 部署物 | `deploy/` 5 件 156 行：`Dockerfile`、`docker-compose.yml`、`rftrack.service`、`rftrack.env.example`、`README.md`；Docker 首跑抓到并修掉两条阻断级缺陷（缺 `python-multipart`、宿主端口写死） | 进度头 S9-3a 段 |
| 与 web 的耦合 | **零**：`web/`、`web-desktop/` 全文无 `rftrack` 引用 | 本人 grep **[已由源码确认]** |

### 2.4 `app/` —— Android（Kotlin，独立仓库）

| 项 | 事实 | 证据 |
|---|---|---|
| 双份维护 | `repo/app`（主仓跟踪）与 `app/`（独立 git）源码逐文件 diff 为空，同为 `versionCode 9 / "0.9"`；`app/` 略新（多签名配置与图标）⇒ **内容等价、无一致性校验** | `mobile/build.gradle.kts:24-25` 两侧一致 |
| 模块 | `:core`（纯 JVM，**30 个文件零 `android.*` import**）+ `:mobile`（UI/USB） | `settings.gradle.kts:24-25`；`README.md:111` 自证 |
| 栈 | Kotlin 2.2.20、AGP 8.11.1、Gradle 8.14.4、JDK 17、minSdk 26 / target 36；**View 体系非 Compose，无 DI、无 Room** | `build.gradle.kts:1-4`；`mobile/build.gradle.kts:20-23` |
| 规模 | `src` 95 个 `.kt` ≈ **15,825 行** | **[已由源码确认]** |
| 设备接入 | 原生 USB Host CDC-ACM（无第三方串口库）：`UsbCdc.kt:14`（VID `0x303A` 唯一匹配）、`:62-76`（按 class 2/10 找 bulk 对）；`UsbSerialLink.kt:52-80`、`:107-110`（C5 RTS 复位坑）、`:111-133` | **[已由源码确认]** |
| 网络 | **无**：manifest 注释「Nothing else in the app opens a socket」；仅 GitHub Releases 两处 HTTP（`GitHubReleases.kt:22,27`） | **[已由源码确认]** |
| 功能 | 六项频谱能力全在 `core`：`FrameParser.kt:25-38,43,58-100,138-177`、`Waterfall.kt`、`Panorama.kt`、`RawIq.kt`、`Power.kt:29-41`、`RhLapTracker.kt`；录制/回放与 web 的 `.widscan58` **互通**（`Session.kt:1-15`，32 MB 上限 `:14`）；导出 CSV `Export.kt:11-42` | **[已由源码确认]** |
| 多节点 | **不支持**：单 `link` 字段 `Widscan58Activity.kt:120`、`findBoard` 取 `firstOrNull` `UsbCdc.kt:76` | **[已由源码确认]** |
| 测试 | `:core:test` 28 文件 / **216 例**（README 记数）；`mobile` 零测试源码；无 instrumentation、无 CI | `README.md:174` |
| 发布 | 外置 `keystore.properties`、APK（非 AAB）、tag 规则 `app-v<x>`、手动上传 GitHub Release | `mobile/build.gradle.kts:8-16,31-44`；`AppRelease.kt:16` |

### 2.5 工作区状态（审计时点快照，全部属**并行会话在制品**，本次审计未触碰、未提交）

已修改 12 个：`docs/superpowers/plans/2026-11-02-s3-wifi-relay-m2-head-pipe.md`、`2026-11-03-widscan58-p0-stage1-audit.md`、`firmware/platformio.ini`、`firmware/src/main.c`、`firmware/src/net/relay_pipe.c`、`firmware/src/net/ws_link.c`、`firmware/src/proto/link.c`、`rftrack/backend/app/models.py`、`routers/nodes.py`、`schemas.py`、`services/capture.py`、`services/ingest.py`、`tests/test_info_line.py`。
未跟踪 9 项：4 份 ChatGPT 采集件、`docs/architecture/device-protocol-v2.md`、`docs/architecture/multi-node-spectrum.md`、`docs/superpowers/specs/2026-11-03-widscan58-two-workspace-platform-design.md`、`firmware/test/test_psd/`、`rftrack/backend/alembic/versions/007_node_identity.py`、`rftrack/backend/tests/test_node_identity.py`。

**含义（必须写进任何基线声明）**：`c547498` 这个 commit **不是**本轮实测数字的产地；阶段 3.1 那组数字（后端合计 650）产自「commit + 上述未提交改动」。新平台开工前要先请并行会话把这批落盘或明确放弃，否则 §7 的台账无法复现。**[当前无法确认]** 未提交改动与已提交版本的行为差异。

---

## 3. 协议面现状（新平台必须原样兼容的对象）

### 3.1 二进制帧

`0xA5 'R' | type(u8) | ver(u8) | len(u16) | payload | crc32`（`frames.h:4-13`）。六种类型 `METRICS=1 SPECTRUM=2 TELEMETRY=3 RAWIQ=4 LOG=5 LEVELS=6`（`frames.h:16-23`）。**帧头 version 字段没有任何一端读它**——TS 只靠 CRC 认帧，Python 明文「不校验帧头 version 字节」（`frame_parser.py:8-10`）**[已由源码确认]**。⇒ 「协议 v2」不能靠帧头协商，只能靠能力声明。

尺寸与理论带宽（默认 `FULL`、8 频道、~4.0 ms/hop、SPEC 每 4 hop；`frames.h:118-122`）：METRICS 252 B @~250/s ≈ 63.0 KB/s、SPECTRUM 282 B @~62.5/s ≈ 17.6 KB/s、TELEMETRY 69 B @4/s ⇒ **合计 ≈80.9 KB/s·节点**（理论账 **[根据源码推断]**）。实测参照：双板 USB C5 37 / S3 52 KB/s **[已由真机实测]**、扫描 env 重烧回归 3×60 s = 51,983.6 / 52,173.4 / 52,261.2 B/s **[已由真机实测]**。

### 3.2 ASCII 命令与回复行

`INFO`（身份/握手，六段无唯一 ID）、`PROFILE {json}`、`STATUS/CONFIG {json}`、`GPIO{}`、`NET {json}`、`HEAD {json}`、`OK`/`ERR <cmd>`、`FFTEVAL …`/`FFTEVAL END`、`REGDIFF`、`PROBE`。**没有 `S`/`L`/`F` 这类单字母前缀行**（只有帧头 `0xA5 'R'` 与上述关键字）——早期文档里若出现这种说法属误记 **[已由源码确认]**。

命令面约 45 条（`commands.c:399-819`），RH 态另有两张硬白名单表 `exact[]`/`prefix[]`（`:304-319`）；relay 镜像另有 `NET SSID/PASS/ID/URL/UART/ON/OFF/RESET/SAVE/STATUS` 与 `HEAD`（`:701-807`）。**新平台的每一条写命令都必须同时过 `rh_allowed` 与 relay 两道白名单，漏一道就是静默失败**（风险 G6）。

### 3.3 身份与时间

- 身份：`INFO` 无唯一标识；`NET ID` 是「桥侧显示名」（`net_prefs.h:11` 注释原文 *the name the bridge lists us under*），**且拨号只用 URL 里的 `?id=`，`cfg.id` 不参与握手校验**（`net_prefs.h:8-11`，本人复核结构体）⇒ 双源、可漂移 **[已由源码确认]**。
- 时间：`t_ms = esp_timer_get_time()/1000` 是**各节点自己的开机毫秒**（`scanner.c:332,468,489`），`uint32_t` ⇒ **约 49.7 天回绕**；`seq_metrics` 由 METRICS 与 LEVELS **共用**、`seq_spectrum` 独立（`scanner.c:60,331,467,249`）⇒ **序号只能在同类帧内查缺，不能跨帧类型对齐** **[已由源码确认]**。
- 对齐精度现状：M1 真机 `INFO` 三次往返 **9 / 12 / 43 ms** ⇒ 往返中点法误差下界 ≈ **±20 ms** **[已由真机实测]**。

### 3.4 传输通道矩阵

| 通道 | 实现位置 | 鉴权 | TLS | 并发 | 备注 |
|---|---|---|---|---|---|
| USB CDC（Serial/JTAG） | `link.c:108-113,250` | 无 | n/a | 与 UART/WS 并发 | RAWIQ 强制此路（`link.h:17-22`） |
| UART0/UART1 | `uart_link.c:11-30,78-137`；921600 ≈ 90 KB/s（`:80-82`） | 无 | n/a | 是 | RH 态独占 2 s 持有（`commands.c:64,473`） |
| Wi-Fi WS 客户端 | `ws_link.c`（重连退避 500 ms→30 s `:28-29`；RX 环 1 KB `:24`） | 仅 `?id=` 名字 | **拒绝 wss**（`ws_codec.h:42-44`） | 是 | 凭据强制只存 RAM（`ws_link.c:140-141`） |
| 以太网 | **不存在** | — | — | — | 全树无 `esp_eth`（本人复核） |
| 主机侧桥 | `web/serve.mjs`（537 行）、`tools/serial_bridge.py` | **无鉴权** | 无 | 每端点一页锁 | 二者功能重叠（既有风险 R12） |

---

## 4. 任务书要求 × 现场事实 对照矩阵

| 任务书 | 要求 | 现场事实 | 判定 |
|---|---|---|---|
| §1 多场地 | 架构必须支持 | rftrack 有 `site` 表与按场地隔离测试（`test_site_isolation.py`），但汇聚靠「每场地一套实例 + 只读拉取」 | 🟡 有基础，无 Central |
| §1 多节点 | 第一版重点 | rftrack 后端已有 N 节点接入与 `sample_summary`；`web/`、`app/` 均单设备 | 🟡 后端有、单机侧无 |
| §1 USB | 支持 | 真机已过（双板 USB） | ✅ |
| §1 Ethernet | 支持 | **固件无以太网**；只能靠「串口转以太网」或 Agent 间网络 | ❌ 需重新定义语义（G2） |
| §1 Wi-Fi | 支持 | relay M1 真机已过；M2 只软件侧 | 🟡 |
| §1 ARM Linux | 支持 | rftrack `deploy/` 有 Dockerfile/compose；**systemd 单元从未在 Pi 上跑过** | 🟡 |
| §1 x86 Linux / macOS / Windows | 支持 | macOS 只有 Electron 壳；**Windows 零产物**（web-desktop 只出 win zip 未验）；无 Agent | ❌（G3） |
| §4 稳定设备 ID | 必须 | 固件无硬件 ID（C2） | ❌（G1） |
| §4 每启动新 boot_id | 必须 | rftrack `007_node_identity` 已加 `boot_id` 列（未提交），来源为**会话派生** | 🟡 |
| §5 Agent 原生四平台 | 必须 | 现状是 `serve.mjs`（Node，仅本机）+ Python 后端直连中继 | ❌（G3） |
| §6 六路发现 | 必须 | 现状只有「串口枚举」+「中继 `?id=` 拨入」两路 | ❌（G4） |
| §6 发现≠认领 | 必须 | 后端 `ingest.py:114-134` **当场自动建 Node** | ❌（G5，旧设计 R-3 已识别未落地） |
| §7 节点指向场地接入服务 | 必须 | 固件 `NET URL` 存整条 `ws://…/node?id=`，与「指向场地接入服务」同构 | ✅ 概念一致，落点要从后端移到 Agent（G7） |
| §8 频谱引擎全量恢复 | 必须 | 单机侧 18 项**全部存在**（`web/`）；平台侧缺 SPECTRUM/瀑布/OBW/质心/峰值（`capture.py` 丢弃分支） | 🟡（旧 P0 阶段表已逐项映射） |
| §9 多节点三模式 | 必须 | 旧设计只到 PER_NODE+MAX；`NODE_COMPARE` 语义等价于旧 V4 视图 | 🟡 |
| §10 Central Sync | 必须 | **零实现**，且 2026-10-28 定案明文「不做云端、连上行钩子也不做」 | ❌（G8，本轮已改判） |
| §13 九套测试 | 必须 | 已有：协议 golden 对拍、DSP（native + web + core 216 例）、部分 Transport（`test_ws`/`test_ws_bridge.py`）、浏览器冒烟 65 条 | 🟡 缺 Agent/发现/部署/多节点四套 |
| §13 有界队列 | 必须 | `link.c` 16 KB/32 KiB 有界、rftrack `queue_limit=256`、`live_queue_limit=64` 丢最旧 | ✅ |
| §13 可观测 | 必须 | `endpoint_stats`、`crc_errors`/`resync_bytes`、`dropped_batches`/`frames_offered`/`frames_fed` 双账在案 | ✅ 口径可延用 |

---

## 5. 缺口与冲突清单（G1–G14）

| ID | 条款 | 现场事实（证据） | 影响 | 建议处置 |
|---|---|---|---|---|
| **G1** | §4 设备 ID 必须稳定 / §6 设备身份验证 | 固件无硬件唯一 ID（`commands.c:421-427`），`NET ID` 不参与握手（`net_prefs.h:8-11`） | 认领只能靠操作员声明 + host 侧观测；跨介质会身份分裂 | 已裁定：本期用「host 观测键 + 一次性配对码」，`DEVICE?` 作为 **P1 独立审批项**（见 `03` §5、`08` §4 决策） |
| **G2** | §1/§6 Ethernet | 固件全树无 `esp_eth` | 「以太网设备」不能理解为板子自带网口 | 重新定义：Ethernet Adapter = **串口转以太网端点**（raw TCP / RFC2217）+ **跨机 Agent**；设备本体永远只有 USB/UART/Wi-Fi 三种物理口（`04` §3.3） |
| **G3** | §5 Agent 四平台原生 | 现状 Node 桥（`serve.mjs`）+ Python 直连中继；Windows 零产物 | 「Site Server 不直接管理操作系统串口」无法满足 | 新建 **Go Agent**（已裁定），`serve.mjs` 归 legacy 参考实现（`04`、`06`） |
| **G4** | §6 六路发现 | 只有 USB 枚举与中继拨入两路；mDNS/UDP/QR 全无 | 发现矩阵缺 4 路 | `03` 逐条给协议；并显式声明**设备侧 mDNS 在当前固件不可得**（C3） |
| **G5** | §6 发现≠认领 | `ingest.py:114-134` 自动建 Node | 未授权设备直接成为可信节点（违反 §6 末两条） | 新平台从第一天就没有这条路：入站端点→`pending_device`，只有 `claim` 才产 `Device`（`02` §4、`03` §6） |
| **G6** | §8 设备控制 | 命令需同时过 `rh_allowed`（`commands.c:304-319`）与 relay 白名单 | 平台下发静默失败、排查成本极高 | `01` §6 定义「命令能力矩阵」，每条命令标注在三态（MONITOR/RH/RELAY）下是否可用，平台按矩阵拒绝而不是发了再说 |
| **G7** | §7 上报目标指向场地接入服务 | 旧设计让**后端**直接收 `/node`（内连接入设计 §2） | 与 V1.0 分层冲突 | 改判：入站 WS 归 Agent，Site Server 只与 Agent 对话（`01` §4、`04` §4） |
| **G8** | §2/§10 Central Platform | 2026-10-28 定案「不做云端，连上行钩子也不做」（`RF_Track_落地实施方案与计划.md:374`） | 正面冲突 | **本轮用户改判：自托管单实例 + 场地外拨**；旧定案在 `07` §2 记录为「已覆盖」，不改写历史文本 |
| **G9** | §8 RAWIQ | RAWIQ 强制 USB sink（`link.h:17-22`） | 远程/中继节点**取不到 RAWIQ** | `01` §6.4 明确：RAWIQ 是「本地近端能力」，平台侧只在 Agent 同机场景可用，UI 必须显式标注不可得，禁止伪造 |
| **G10** | §9 校准偏差 | 定标工具存在但离线（`tools/powercal.py`），固件无校准表下发通道 | 跨节点电平不可比（旧裁定 A6 已认定默认不可信） | `02` 建 `device_calibration`（设计预留）+ `08` 排 P2；首版多节点图带「不可比」标签 |
| **G11** | §13 部署测试 | rftrack 的 systemd 单元**从未在真机执行**（Pi 不在线，两地址均不可达） | 四平台部署承诺无实测支撑 | `06` §7 把每条部署判据标成「未执行」，禁止写成已通过 |
| **G12** | 工程质量 | `MOCK` 标记有缺口：`capture.tap()` 对所有字节硬编码 `source="real"` | 模拟数据可冒充真数据（R4 级信誉风险） | 新平台 ingress 必须显式声明 source 且**默认 unknown**（`04` §6），并把这条做成测试 |
| **G13** | 工作区卫生 | 12 个已改 + 9 个未跟踪文件属并行会话（§2.5） | 数字台账不可复现、提交混杂 | 开工前与用户逐条确认归属；本轮**零代码改动**（`07` §3） |
| **G14** | 双份 `app/` | `repo/app` 与 `app/` 内容等价但无一致性校验 | 漂移（已有 mipmap/keystore 差异） | 列入不动清单，另开一轮决定单一真源（`07` §5） |

---

## 6. 既有裁定 × V1.0 的改判映射（D1–D10）

> 原则：**旧文本不删改，只在此登记谁被覆盖**。逐条落到 `07-migration-plan.md`。

| ID | 既有裁定（出处） | V1.0 处置 |
|---|---|---|
| D1 | 不做云端 / 每场地一套实例 / 连上行钩子也不做（`RF_Track_落地实施方案与计划.md:374`） | **覆盖** → 自托管 Central + Outbox 外拨（G8） |
| D2 | 四级固定层级 site→relay→gateway→node（`topology-and-access-methods.md`；外键在 `models.py:172,175-177`） | **覆盖** → Device/DeviceEndpoint/HostAgent 扁平 + 关系表（§2 禁止固定层级） |
| D3 | 内连接入：后端自己收 `/node`（`2026-11-03-rftrack-inline-relay-ingress-design`） | **覆盖** → 入站归 Agent（G7）；其白名单闸 + 拒绝留痕 + 一闸三断**语义保留**并搬进 Agent |
| D4 | R-5「Ethernet Discovery = 经 HTTP 的接入机发现，不做 mDNS/扫段」 | **覆盖** → UDP + mDNS 成为必需项（G4），但「广播不得作准入凭据」**保留** |
| D5 | R-2「中继 = 一行 Gateway，准入载体是 Gateway 行本身」 | **变形保留** → 准入载体改为 HostAgent + DeviceEndpoint；「不另建第二张准入表」的精神保留 |
| D6 | R-3 发现≠认领（旧设计已裁定但**未落地**） | **保留并升为硬约束**（G5） |
| D7 | A1–A6 多节点裁定（`multi-node-spectrum.md` §0） | **全部保留**，其中 A4 双时间戳、A6 默认不可比直接进 §9 |
| D8 | 协议 v1 不动、三份镜像同改纪律、新命令一律可选（`device-protocol-v2.md` P1/P3/P5） | **保留**，且镜像数量从 3 修正为 **4**（C1）；Go Agent 明令禁止解析 |
| D9 | ⛔ 平台侧不做 RAWIQ / 烧录 / 功率计（旧 §12 表 #10/#15/#16） | **部分覆盖**：§8 要求恢复 RAWIQ/设备控制/设置 → RAWIQ 按 G9 降级为「本地近端可得」；**浏览器内烧录仍只在 Console**（esptool-js 需 Web Serial，`views/flasher.ts:93`），平台侧不代理串口刷写 |
| D10 | 分支隔离：rf-track 永不合入 main、只许 main→rf-track 单向 | **保留**，新增 `platform/*` 分支线；legacy 基线用注释 tag 钉死（`07` §3） |

---

## 7. 实测门槛台账（新平台的「只增不减」起点）

| 门槛 | 命令 | 最新在案值 | 产地 |
|---|---|---|---|
| 后端 pytest | `python3 -m pytest tests`（`rftrack/backend/`） | **649 passed / 1 skipped（合计 650）** | 阶段 3.1（2026-11-03，含未提交改动） |
| 工作台单测 | `node --test test/*.test.mjs` | **214 / 0 fail** | 阶段 2/3.1 |
| 真浏览器冒烟 | `node rftrack/frontend/e2e/workbench.mjs` | **65 / 65**（51.1 s，未捕获异常 0） | 阶段 2 |
| `web/` 单测 | `npx vitest run` | **10 文件 / 56 passed** | 阶段 2（`web/` 零改动） |
| 固件 native | `pio test -e native` | **34**（M2 那轮记录；阶段 1–3.1 **未复跑**） | M2 记录 |
| Android core | `:core:test` | **216 例**（README 记数，本轮未复跑） | `app/README.md:174` |
| 协议对拍 | `tests/test_frame_parity.py` + golden | 真机录制 **17,437 项 / 17,436 帧、565,620 字段 0 差**（连 0.01 dB 容差都没用上） | S7-T1 **[已由软件测试]** |
| 归档往返 | `tests/test_archive_roundtrip.py` | 三层 21 表逐字段全过（21 = 20 张 ORM 表 + `alembic_version`，见 `archive.py:77-89`）+ 跨实例只读 API 30/30 | S9 **[已由软件测试]** |
| 容器内测试 | Docker 里跑 pytest | **541 passed / 3 skipped**（少 46 条 = `test_replay`28 + `test_live`18，镜像不含 `rftrack/tools/`） | S9-3a，**如实与本机分开** |
| 4 节点压测 | 本地 300 s | **1,134 帧/s、RSS 70–80 MB、health p50 14.3 ms、落库 9.17 MB** | S9-4（Mac，非 Pi） |

**三条未执行、不得当作已通过**：`deploy/rftrack.service` 的 systemd 真机行为、Pi 上的 CPU/内存/延迟/CRC 定值与 SD 磨损、M2 的 L2（≥1800 s）/L3（断网 60 s）/L6（真机回退）。

---

## 8. 主线本人复核清单

| # | 复核项 | 方法 | 结果 |
|---|---|---|---|
| 1 | `INFO` 六段无唯一 ID | `sed -n '415,432p' commands.c` | 与子代理一致 |
| 2 | RAWIQ 强制 USB sink | `sed -n '10,25p' link.h` | 注释原文「RAWIQ stays on USB whatever the sink says」 |
| 3 | 拒绝 `wss://` 是有意设计 | `sed -n '35,50p' ws_codec.h` | 「wss:// is refused on purpose: the relay is a LAN device」 |
| 4 | `NET ID` 只是桥侧显示名 | `sed -n '5,20p' net_prefs.h` | `id[33]` 注释 + `url[129]` 携带 `?id=`，双源确认 |
| 5 | 固件无以太网 | `grep -rli "ethernet\|esp_eth" firmware/src/` | 空输出 |
| 6 | 表数量 | `grep -c __tablename__ models.py` | **20 条**（不是文档旧口径的 21：`Base` 类无 `__tablename__`，而归档清单的 21 含 `alembic_version`）。此处先前记为 21，属本人首次复核时的计数错误，已按 20 改正 §2.3 与 C5 |
| 7 | `serve.mjs` 是否有命令注入面 | `sed -n '100,140p'` + `:85-90` | 闭集枚举 + 启动参数命令 ⇒ 无注入、有无鉴权可用性风险 |
| 8 | 「一页一板」 | `sed -n '835,845p' main.ts` | `:840` 注释原文 |
| 9 | 四份协议镜像 | `ls` 四文件 + `find app/core` | Kotlin 包名 `com.onebody.widscan58.core` |
| 10 | golden 夹具形状 | `python3` 读 `frames-v1.json` | 顶层 6 键、`cases` 14、`streams` 8 |
| 11 | 17,437 帧出处 | 全库 grep | 命中在 `RF_Track_落地实施方案与计划.md:7` 进度头与 ChatGPT 采集件；**夹具文件本身不含该计数** |

---

## 9. 本审计无法确认的事项（诚实清单）

1. 未提交那 12 个文件与 `c547498` 的行为差异 **[当前无法确认]**——需要并行会话先落盘。
2. 各 USB 板在 host 侧是否暴露可用的 `iSerialNumber`（决定 host 观测键强度）**[需要实机验证]**。
3. ESP32-C5 的 `usb_serial_jtag` 在 Windows/macOS 上的驱动形态与 VID/PID 全集 **[当前无法确认]**（Android 侧只匹配 `0x303A`）。
4. 「串口转以太网」设备的行为（RFC2217 流控、掉线语义、UDP 广播可达性）**[当前无法确认]**，无任何实物在案。
5. mDNS 在目标场地的 VLAN/组播策略下是否可用 **[需要实机验证]**。
6. `MAX_GATEWAYS`/`DIAL_CONCURRENCY` 终值：旧文档维持 8/4，依据只有 Mac 一轮观测 **[需要实机验证]**。
7. 多节点时间对齐在**跨场地**下的可用精度：±20 ms 是单场地 USB/relay 实测外推，跨场地 NTP 偏差未量 **[推算，非实测]**。
