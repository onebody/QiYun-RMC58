# widscan58 工程基线核验

> 执行日期：2026-11-04
> 执行人：主程工程师（本轮实际核验）

## 1. Git 状态

### repo/ 主仓库

| 项目 | 值 |
|---|---|
| 当前分支 | `fw-wifi-relay` |
| HEAD commit | `c547498 feat(rftrack): complete P0 stage 2 console mounting and node identity ingestion` |
| 未提交修改 | 12 个文件（固件 4 个、rftrack 5 个、docs 3 个） |
| 未跟踪文件 | 11 项（docs/ 下的设计文档、firmware/test/test_psd/、rftrack 新测试等） |

**未提交修改清单（保留不动）：**
- `firmware/platformio.ini`、`firmware/src/main.c`、`firmware/src/net/relay_pipe.c`、`firmware/src/net/ws_link.c`、`firmware/src/proto/link.c`
- `rftrack/backend/app/models.py`、`routers/nodes.py`、`schemas.py`、`services/capture.py`、`services/ingest.py`、`tests/test_info_line.py`

### app/ Android 仓库

| 项目 | 值 |
|---|---|
| 当前分支 | `master` |
| 未提交修改 | 无 |
| 未跟踪文件 | `core/bin/`（构建产物） |

## 2. 各模块实际目录与技术栈

### 2.1 Firmware（固件）

| 维度 | 实际值 |
|---|---|
| 路径 | `repo/firmware/` |
| 构建系统 | PlatformIO Core 6.2.0 + pioarduino 55.03.312-1 + ESP-IDF 5.5.5 |
| 语言 | C11 + 少量汇编（S3 SIMD FFT） |
| 入口文件 | `src/main.c` → `app_main()` |
| 环境名称 | `esp32c5`（主）、`esp32s3`、`esp32c5-head`、`esp32s3-relay`、`native` |
| 依赖 | 无外部 pip/npm 包，仅 PlatformIO + ESP-IDF |
| 构建命令 | `pio run -e esp32c5` / `pio run -e esp32s3` |
| 测试命令 | `pio test -e native` |
| 其他静态检查 | PlatformIO 内置 clang-tidy（未配置 pre-build） |

**源码结构：**
```
firmware/src/
├── main.c               # 入口，启动扫描循环
├── dsp/                 # PSD、FFT（radix-4 定点核）、IQ 校正
├── net/                 # WebSocket relay 链（M2）
├── proto/               # 帧协议、命令解析、RotorHazard 协议、UART link
├── radio/c5/, radio/s3/ # 芯片特有射频驱动
├── scan/                # profile、planner、scanner、metrics、lap detector
├── gpio_out.c           # GPIO 输出配置
└── status_led.c         # WS2812 状态灯（C5-Zero）
```

### 2.2 Web（浏览器控制台）

| 维度 | 实际值 |
|---|---|
| 路径 | `repo/web/` |
| 语言 | TypeScript 7.0（strict），纯原生 DOM/Canvas |
| 构建系统 | Vite 8.3 |
| 入口文件 | `src/main.ts` |
| **无 UI 框架** | 仅依赖 `esptool-js`（烧录）+ `spark-md5`（固件校验） |
| 依赖管理 | npm（已安装，node_modules 存在） |
| 构建命令 | `npm run build`（内含 `tsc --noEmit`） |
| 测试命令 | `npm test`（vitest run） |
| 开发服务器 | `npm run dev` → Vite 5173 |
| e2e 命令 | `node e2e/headless.mjs --sim --chrome "..."` |
| PWA | 已配置（manifest + service worker） |

**npm scripts 实际定义：**
```json
"dev": "vite",
"build": "tsc --noEmit && vite build",
"preview": "vite preview",
"serve": "node serve.mjs",
"test": "vitest run"
```

**源码结构：**
```
web/src/
├── serial/parser.ts      # 流式帧+文本解析器（CRC32 校验）
├── serial/device.ts      # Web Serial/WebUSB 设备连接
├── serial/flasher.ts      # esptool 固件烧录
├── proto/frames.ts        # 二进制帧解码（METRICS/SPECTRUM/TELEMETRY/RAWIIQ/LEVELS）
├── model/                 # 应用状态（store.ts/timer.ts/laps.ts/power.ts/...）
├── mock/                  # 模拟设备（用于 ?sim 和测试）
├── views/                 # 10+ 页面视图（频道卡片/全景/瀑布/Zoom/计圈/功率/...）
├── util/                  # PWA、语音合成、屏幕唤醒锁
└── main.ts                # 应用入口
```

### 2.3 Electron（桌面版）

| 维度 | 实际值 |
|---|---|
| 路径 | `repo/web-desktop/` |
| 语言 | CommonJS（无 TS） |
| Electron 版本 | v45.0.0 |
| 入口文件 | `main.mjs`（142 行） |
| 打包工具 | electron-builder v26.18.0 |
| **无 test 脚本** | 测试委托给 `web/` |
| 构建命令 | `npm run dist:mac` / `npm run dist:win` |
| 关键行为 | 自注册 `app://widscan58/` 安全协议，加载 sibling 包 `web/dist/` |

**npm scripts 实际定义：**
```json
"start": "electron .",
"sync-dist": "rm -rf dist && cp -R ../web/dist dist",
"dist:mac": "npm run sync-dist && electron-builder --mac dmg --arm64",
"dist:win": "npm run sync-dist && electron-builder --win zip --x64"
```

### 2.4 Android App

| 维度 | 实际值 |
|---|---|
| 路径 | `app/` |
| 语言 | Kotlin（无 Compose/AndroidX，纯程序化 View/Canvas） |
| 构建系统 | Gradle 8.14.4 + Kotlin 2.0.21 |
| JDK 要求 | 21（**当前 shell 默认指向不可用的 x86 JDK 1.8**，需要 `export JAVA_HOME=...openjdk-21.0.1/Contents/Home`） |
| 模块 | `:core`（纯 JVM，无 android.* 依赖）+ `:mobile`（应用层） |
| **无 test script 脚本** | 测试用 Gradle task 执行 |
| 构建命令 | `./gradlew :core:compileKotlin :mobile:compileDebugKotlin` |
| 测试命令 | `./gradlew :core:test` |
| USB 连接 | USB CDC（UsbSerialLink.kt） |
| 应用内升级 | GitHub releases + PackageInstaller 流式写入 + SHA-256 校验 |

### 2.5 RFTrack（多节点管理后端）

| 维度 | 实际值 |
|---|---|
| 路径 | `repo/rftrack/` |
| 后端语言 | Python 3.14.5 |
| 后端框架 | FastAPI 0.133.1 + SQLAlchemy 2.0.47 + Alembic 1.18.4 |
| 后端测试 | pytest 8.4.2 |
| 数据库 | SQLite（默认），可换 PostgreSQL |
| 前端语言 | 原生 JavaScript ES2022 + Vue global prod |
| 前端构建 | **无 npm/无构建**，直接浏览器加载静态文件 |
| 前端测试 | Node 22 内置 runner `node --test test/*.mjs` |
| 后端启动 | `python3 serve.py`（uvicorn） |
| 后端测试命令 | `python3 -m pytest tests/ -v` |
| 前端测试命令 | `cd frontend/workbench && node --test test/*.mjs` |

**后端源码结构：**
```
rftrack/backend/app/
├── main.py                # FastAPI 应用入口
├── routers/               # 15 个 API 路由（sites/nodes/gateways/samples/events/...）
├── services/              # 21 个服务模块（frame_parser/capture/ingest/ws_bridge/...）
├── plugins/               # 插件系统（interference/anomaly/alerting）
├── models.py / schemas.py # SQLAlchemy ORM + Pydantic schemas
├── config.py / database.py / deps.py
```

### 2.6 Tools（辅助工具）

| 脚本 | 语言 | 作用 |
|---|---|---|
| `widscan58cli.py` | Python | 主机命令行客户端（info/monitor/rawiq/spectrum/cmd/bootloader） |
| `serial_bridge.py` | Python | 串口 ↔ WebSocket 桥 |
| `flash.sh` | Bash | esptool 烧录辅助 |
| `powercal.py` | Python | Betaflight MSP 扫 VTX 做功率校准 |
| `pages_firmware.py` | Python | 固件产物 → Web 烧录器 manifest |
| `check_phy_abi.sh` | Bash | 对比 IDF 私有 PHY 函数指令流 |
| `rh_check.py` | Python | RotorHazard 协议兼容性检查 |

## 3. 未完成的 TODO / FIXME / 临时实现

### 3.1 代码注释扫描结果

**全量 grep TODO\|FIXME\|HACK\|XXX\|TEMP\|WORKAROUND\|BUG\|NOTE（排除文档）：零命中。**

所有源码文件（固件 C、Web TS、Android Kotlin、RFTrack Python、Electron）均不含上述标记的代码注释。

### 3.2 已知未完全解决的问题（来自文档和代码注释）

| 问题 | 位置 | 说明 |
|---|---|---|
| 固件静默启动在 RH 模式 | `firmware/src/main.c` | 设计决策：上电后等待轮询；Web/Electron/Android 三端自动发送 `PROTOCOL MONITOR` 切换 |
| Web e2e headless 默认 Chrome 路径是 Linux 路径 | `web/e2e/headless.mjs:17` | macOS 需 `--chrome "..."` 参数，未实现跨平台回退 |
| Electron 未签名 | `web-desktop/package.json` | 首次打开 macOS 需右键 → 打开 |
| Android shell 默认 JAVA_HOME 指向不可用的 x86 JDK 1.8 | 环境问题 | 项目不自带 gradle JDK 配置，需要用户修正 shell rc |
| C5 内存余量极紧 | `firmware/src/radio/c5/sram_guard.ld` | 静态 RAM 只剩几百字节，加静态变量/IRAM 函数可能链接失败 |

### 3.3 无临时 Mock / 占位实现

- Web 有 `mock/mockDevice.ts`（用于 `?sim` 和测试），它是功能完整的**模拟设备**，不是占位实现
- RFTrack 后端测试 fixture 都是真实数据录制的 golden files（`tests/golden/frames-v1.json`）

## 4. 协议定义副本

| 语言 | 文件 | 角色 |
|---|---|---|
| C（固件） | `firmware/src/proto/frames.h` | **源头** |
| TypeScript（Web） | `web/src/proto/frames.ts` | 镜像（注释标注源路径） |
| Python（RFTrack） | `rftrack/backend/app/services/frame_parser.py` | 镜像（注释标注 TS 镜像源） |
| Kotlin（Android） | `app/core/src/main/kotlin/.../FrameParser.kt` | 镜像（注释标注"跟固件 header 一致"） |

协议定义**有 4 份**，但它们之间有明确的继承关系，且 Python 端有 `test_frame_parity.py` golden file 跨语言一致性测试守护。详见任务四协议一致性核验。

## 5. 构建工具链版本（固件）

| 工具 | 版本 |
|---|---|
| PlatformIO Core | 6.2.0 |
| pioarduino platform | 55.03.312-1 |
| ESP-IDF | 5.5.5（pioarduino 固定） |
| 固件 C 编译器 | xtensa-esp32c5-elf-gcc / xtensa-esp32s3-elf-gcc |

**ESP-IDF 版本变更必须先运行** `tools/check_phy_abi.sh` 对比私有 PHY 函数指令流，因为固件调用了 esp-sdr 发现的私有 PHY 函数。

## 6. 依赖关系摘要（详见任务五）

- Web ↔ Electron：Electron 的 `dist/` 是 `web/dist/` 的物理复制（`npm run sync-dist`）
- Web ↔ Android：**独立实现**，共享协议字段但无代码复用
- RFTrack Python ↔ Web TypeScript：**独立实现**，Python 明确以 TS 为正确标准
- 固件 ↔ 所有上位机：通过二进制帧协议，无代码共享
