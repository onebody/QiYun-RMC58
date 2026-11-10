# 模块依赖关系（实际代码检查）

> 执行日期：2026-11-04

## 1. 依赖关系图

```
┌─────────────────────┐    二进制帧协议    ┌──────────────────────┐
│   Firmware (ESP32)  │ ◄────────────────► │  Web (TypeScript)     │
│   C / PlatformIO    │  frames.h → frames │  Vite + 原生 DOM      │
└─────────┬───────────┘                   └──────┬───────────┬─────┘
          │                                      │           │
          │ ASCII 命令                           │ 复制 dist/   │ mock/
          ▼                                      ▼           ▼
   ┌───────────────┐                   ┌──────────────┐  ┌──────────────────┐
   │  tools/       │                   │  Electron    │  │  Android :core   │
   │  Python CLI   │                   │  (壳)        │  │  Kotlin 独立实现  │
   └───────────────┘                   └──────────────┘  └────────┬─────────┘
                                                                    │
                                                                    │ USB CDC
                                                                    ▼
                                                              ┌──────────┐
                                                              │ Android  │
                                                              │ :mobile  │
                                                              └──────────┘

   ┌─────────────────────────────────────────────────────────────┐
   │  RFTrack (多节点后端)                                        │
   │                                                             │
   │  Python FastAPI ───── protocol parity test ──────► Web TS   │
   │  frame_parser.py ←── "逐字段镜像" ──── frames.ts             │
   │                                                             │
   │  Node 22 workbench (前端原生 JS + Vue global)               │
   └─────────────────────────────────────────────────────────────┘
```

## 2. 逐模块依赖详解

### 2.1 Firmware ↔ 所有上位机

| 项 | 详情 |
|---|---|
| 协议 | 二进制帧 + ASCII 命令行，**无代码共享** |
| 帧定义 | `firmware/src/proto/frames.h` 是源头，含 `_Static_assert` 锁死尺寸 |
| 命令行 | ASCII 命令 → ASCII 回复，跨语言无共享定义文件 |
| 依赖方向 | 固件被上位机依赖（协议镜像），固件不依赖任何上位机代码 |
| 版本约束 | 帧格式不跨固件版本兼容；录制文件只能在同版本 UI 回放 |

### 2.2 Web ↔ Electron

| 项 | 详情 |
|---|---|
| 关系 | **物理复制**：`web-desktop/dist/` = 复制 `web/dist/` |
| 同步命令 | `npm run sync-dist`（`rm -rf dist && cp -R ../web/dist dist`） |
| 构建依赖 | Electron 不能独立构建——必须先 `npm run build` 在 web/ 下生成 dist |
| 测试委托 | Electron **没有 test 脚本**，测试全部在 web/ 执行 |
| 运行时 | Electron `main.mjs` 注册 `app://widscan58/` 安全协议，加载同目录 `dist/index.html` |

**结论：Electron 是 web 的**薄壳**——没有自己的业务逻辑，没有独立测试。

### 2.3 Web ↔ Android

| 项 | 详情 |
|---|---|
| 协议解析 | **独立实现**：Web 用 `frames.ts`，Android :core 用 `FrameParser.kt` |
| 字段偏移 | **完全对齐**（本轮任务四已验证） |
| 单位换算 | **完全对齐**（levelDb / specDb / centi_db） |
| 共享代码 | **无**。不依赖 Web 的 dist、不 import Web 的 ts 文件 |
| 共同源头 | 都以 `firmware/src/proto/frames.h` 为准 |

**风险：** 两份协议定义独立维护，固件改 struct 时需要**同时**改 TS 和 Kotlin。Kotlin 侧有 216 个测试守护，TS 侧有 56 个测试守护——但没有编译时强制检查。

### 2.4 RFTrack ↔ Web

| 项 | 详情 |
|---|---|
| 协议解析 | **独立实现**：Python `frame_parser.py`，Web TS `frames.ts` |
| 继承关系 | Python 注释明确"**逐字段镜像 TS**"，把 TS 当作正确标准 |
| 一致性守护 | `tests/test_frame_parity.py` 拿 TS 导出的 golden fixtures 逐字段对拍 |
| 共享代码 | **无** |
| 依赖方向 | Python 依赖 TS 的 golden fixtures，但不 import TS 源码 |

### 2.5 Android :core ↔ :mobile

| 项 | 详情 |
|---|---|
| 关系 | :core 是纯 JVM（无 android.* 依赖），:mobile 是应用层 |
| 代码共享 | :mobile 直接依赖 :core（同项目 Gradle 多模块） |
| 测试 | :core 有 216 项 JVM 测试，:mobile 没有单元测试（程序化 View 不适合 JVM） |
| 构建 | 必须先 `:core:compileKotlin` 成功才能 `:mobile:compileDebugKotlin` |

### 2.6 RFTrack 前端 ↔ 后端

| 项 | 详情 |
|---|---|
| 关系 | 原生 JS workbench → FastAPI 后端 REST API |
| 前端构建 | **无 npm、无 Vite**，直接浏览器加载静态文件 + Vue global prod |
| 前端测试 | Node 22 内置 runner `node --test`，214 项 |
| 后端启动 | `python3 serve.py`（uvicorn） |
| 后端测试 | pytest 8.4.2，650 项 |
| 数据库 | SQLite（默认），Alembic 7 个迁移脚本 |

## 3. 固件构建工具链依赖

| 依赖 | 版本约束 | 变更检查 |
|---|---|---|
| PlatformIO Core | 6.2.x（pioarduino 55.03.312-1 固定） | 无显式脚本，但 `pio run` 会提示不兼容 |
| ESP-IDF | 5.5.5（pioarduino platform 固定） | **必须**先运行 `tools/check_phy_abi.sh` 对比私有 PHY 函数指令流 |
| Python | 3.x（host tests + 工具脚本） | 未固定版本 |
| esptool | PlatformIO 内置 | 未固定版本 |

**`check_phy_abi.sh`**：固件调用 esp-sdr 发现的私有 PHY 函数，这些函数在不同 IDF 版本间可能指令级变化。脚本对比新旧 IDF 的反汇编输出。

## 4. 构建顺序要求（全量）

```
Firmware (platformio run -e esp32c5)     ← 独立，第一个
    ↓
Web (npm run build)                       ← 独立（除了 Electron）
    ↓
Electron (npm run dist:mac)               ← 依赖 web/dist
Android (:core:compileKotlin → :mobile:compileDebugKotlin)  ← 独立
RFTrack (pytest 验证后端，无构建)          ← 独立
```

## 5. 无依赖可独立启动的模块

| 模块 | 独立？ | 说明 |
|---|---|---|
| Firmware 构建 | ✅ 完全独立 | PlatformIO + ESP-IDF 自包含 |
| Web dev server | ✅ 完全独立 | `npm run dev` 无需 Electron/Android/RFTrack |
| Android :core test | ✅ 完全独立 | `./gradlew :core:test` 无需其他模块 |
| RFTrack backend serve | ✅ 完全独立 | `python3 serve.py` 可用 SQLite 自包含 |
| RFTrack workbench | ✅ 完全独立 | 静态文件，不依赖后端也能打开（接口调用会 404） |

## 6. 关键发现

1. **协议有 4 份独立镜像**（C 源头、TS、Kotlin、Python）——当前通过测试守护一致性，但无编译时强制
2. **Electron 完全是 web 的薄壳**——零独立业务逻辑、零独立测试
3. **Android :core 是唯一跨平台纯 JVM 模块**——协议/Store/RH 全部可在主机测试（216 项）
4. **Python 有显式 parity 测试**（test_frame_parity.py 拿 TS golden fixtures 对拍），Kotlin 没有——Kotlin 靠 216 个集成测试隐式覆盖
5. **固件与所有上位机之间无代码共享**——协议镜像靠注释、测试和 `_Static_assert` 守护
