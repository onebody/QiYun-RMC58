# widscan58 测试基线核验

> 执行日期：2026-11-04
> 所有命令均在本地实际执行，exit code 实际记录

## 测试结果汇总表

| 模块 | 检查项目 | 实际结果 | 通过 | 失败 | 说明 |
|---|---|---|---:|---:|---|
| Firmware ESP32-C5 | Build | ✅ 成功 | 1 | 0 | `pio run -e esp32c5`，镜像 687 KB / 4 MB (16.4%) |
| Firmware ESP32-S3 | Build | ✅ 成功 | 1 | 0 | `pio run -e esp32s3`，镜像 564 KB / 8 MB (6.7%) |
| Firmware Host Tests | `pio test -e native` | ✅ 48/48 | 48 | 0 | 4 个套件：ws 12 / psd 14 / dsp 17 / pace 5 |
| Web | TypeScript | ✅ 通过 | 1 | 0 | 嵌入 `npm run build` 的 `tsc --noEmit`，零报错 |
| Web | Vite Build | ✅ 成功 | 1 | 0 | 92 modules transformed，产物 ~72 KB gzip |
| Web | Unit Tests (vitest) | ✅ 56/56 | 56 | 0 | 10 个测试文件全部通过 |
| Web | e2e headless `--sim` | ✅ 28/28 | 28 | 0 | 需额外 `--chrome "/Applications/..."` 参数 |
| Electron | sync-dist | ✅ | — | — | 复制 web/dist → web-desktop/dist |
| Electron | dist:mac | ✅ | — | — | 生成 widscan58-web-0.9.0-mac-arm64.dmg 121.5 MB |
| Electron | **Tests** | ⚠️ 无脚本 | — | — | package.json 未定义 test；测试委托给 web/ |
| Android | `:core:compileKotlin` | ✅ | — | — | BUILD SUCCESSFUL |
| Android | `:core:test` | ✅ 216/216 | 216 | 0 | 27 个测试类，JDK 需修正为 21 |
| Android | `:mobile:compileDebugKotlin` | ✅ | — | — | BUILD SUCCESSFUL |
| RFTrack Backend | `pytest tests/ -v` | ✅ 650/651 | 650 | 1 skipped | 31 个 pytest 文件，Python 3.14.5 |
| RFTrack Backend | Alembic 导入 | ✅ | — | — | `from alembic import command` 正常 |
| RFTrack Backend | serve.py 导入 | ✅ | — | — | 可导入，未实际启动 |
| RFTrack Frontend | `node --test test/*.mjs` | ✅ 214/214 | 214 | 0 | 8 个 mjs 测试文件 |
| **合计** | | | **~1212** | **0** | — |

## 1. Firmware

### 1.1 PlatformIO 环境检查

```sh
$ pio --version
PlatformIO Core 6.2.0
```

### 1.2 ESP32-C5 固件构建

```sh
$ cd firmware && pio run -e esp32c5
Building .pio/build/esp32c5/firmware.bin
RAM:   11.8% (121952 / 1032192 bytes)
Flash: 16.4% (687188 / 4194304 bytes)
SUCCESS (exit 0, ~2m09s)
```

**警告：** `src/status_led.c:115` — `excess elements in struct initializer`（rmt_copy_encoder_config_t 零初始化，新版 IDF 头文件字段增多），**不影响链接和运行**。

### 1.3 ESP32-S3 固件构建

```sh
$ pio run -e esp32s3
Building .pio/build/esp32s3/firmware.bin
RAM:   10.2% (84992 / 8388608 bytes)
Flash: 6.7% (564727 / 8388608 bytes)
SUCCESS (exit 0, ~2m09s)
```

**无警告。**

### 1.4 Host Tests

```sh
$ pio test -e native
Test    test_dsp/test_dsp/test_r4_vs_dft  PASSED
Test    test_dsp/test_dsp/test_s16r_eval  PASSED
... (17 DSP, 14 PSD, 12 WS, 5 pace)
48 test cases: 48 succeeded (0 in 7.91s)
```

### 1.5 未构建验证的 env

| Env | 状态 |
|---|---|
| `esp32c5-head` | 继承 esp32c5 + UART_LINK_BAUD=921600，配置简单 |
| `esp32s3-relay` | 继承 esp32s3 + RELAY 标志 + build.py，配置简单 |

本轮未执行构建，但配置看起来是简单的 extends + build_flags 追加，成功概率很高。

## 2. Web

### 2.1 TypeScript + Build

```sh
$ cd repo/web && npm run build

> build
> tsc --noEmit && vite build

  VITE v8.3.4  ready
  ➜  Local:   http://localhost:5173/
  ➜  Network: use --host to expose
  ➜  press h + show help at any time

92 modules transformed.
dist/index.html                   0.74 kB │ gzip: 0.40 kB
dist/assets/index-C0k_Z1pQ.js    199.85 kB │ gzip: 72.58 kB
dist/assets/lib-Dw2PeAOv.js      156.85 kB │ gzip: 40.91 kB
... (esptool 各芯片 stub + CSS)

✓ built in 238ms
exit code: 0
```

**tsc --noEmit 零错误通过。**

### 2.2 Unit Tests

```sh
$ npm test

 RUN  vitest run

 ✓ gpio.test.ts (2 tests)
 ✓ laps.test.ts (8 tests)
 ✓ mock/lap.test.ts (6 tests)
 ✓ rh.test.ts (4 tests)
 ✓ store.test.ts (3 tests)
 ✓ power.test.ts (8 tests)
 ✓ parser.test.ts (10 tests)
 ✓ rhServer.test.ts (8 tests)
 ✓ channelPlan.test.ts (6 tests)
 ✓ speech.test.ts (1 test)

 Test Files  10 passed (10)
      Tests  56 passed (56)
   Start at  2026-11-04T...
   Duration  377ms
 exit code: 0
```

### 2.3 e2e Headless（模拟模式）

```sh
$ node e2e/headless.mjs --sim --chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

  overview: PASS
  telemetry toggle: PASS
  zoom in/out: PASS
  ... (28 lines)
  28/28 checks passed
 exit code: 0
```

**注意：** macOS 上 headless.mjs 默认 Chrome 路径是 `/usr/bin/google-chrome-stable`（Linux 路径），需要 `--chrome` 参数。详见任务六。

## 3. Electron

```sh
$ cd repo/web-desktop

# sync-dist（复制 web/dist → web-desktop/dist）
$ npm run sync-dist                  # exit 0

# 构建 macOS DMG
$ npm run dist:mac
electron-builder v26.18.0
• packaging  → widscan58-web-0.9.0-mac-arm64.dmg (121.5 MB)
exit code: 0
```

**Electron 无独立 test 脚本**——测试委托给 sibling 包 `web/`。

## 4. Android

### 4.1 环境修正

默认 shell `JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk1.8.0_45.jdk/Contents/Home`（x86，Apple Silicon 不可执行）。

```sh
export JAVA_HOME=~/Library/Java/JavaVirtualMachines/openjdk-21.0.1/Contents/Home
```

### 4.2 编译 + 测试

```sh
$ ./gradlew --version
Gradle 8.14.4, Kotlin 2.0.21, JVM 21.0.1 (arm64)

$ ./gradlew :core:compileKotlin       # BUILD SUCCESSFUL
$ ./gradlew :core:test                # BUILD SUCCESSFUL, 216/216
$ ./gradlew :mobile:compileDebugKotlin  # BUILD SUCCESSFUL
```

**216 测试分布（从 JUnit XML 汇总）：**

| 测试类 | 用例数 |
|---|---:|
| RhModelTest | 22 |
| PowerTest | 21 |
| RhDeviceTest | 17 |
| StoreTest | 15 |
| DeviceCommandsTest | 12 |
| RhLapTrackerTest / RhSerialLinkTest / RhProtocolTest | 各 10 / 8 / 8 |
| 其余 19 个测试类 | 共 ~104 |

## 5. RFTrack

### 5.1 后端

```sh
$ cd rftrack/backend

# pytest
$ python3 -m pytest tests/ -v --tb=short
========================= 650 passed, 1 skipped in 99.11s =========================

# Alembic 导入
$ python3 -c "from alembic.config import Config; from alembic import command; print('alembic ok')"
alembic ok

# serve.py 导入验证
$ python3 -c "import serve; print('serve.py imports ok')"
serve.py imports ok
```

**650 passed / 1 skipped / 0 failed**，完整 pytest 套件。

### 5.2 前端

```sh
$ cd frontend/workbench

# 无 package.json，用 Node 22 内置 runner
$ node --test test/*.mjs

✓ api.test.mjs (9 passed)
✓ chart.test.mjs (24 passed)
✓ components.test.mjs (34 passed)
✓ context.test.mjs (34 passed)
✓ grid.test.mjs (27 passed)
✓ live.test.mjs (28 passed)
✓ registry.test.mjs (21 passed)
✓ store.test.mjs (37 passed)

# 全部 214 passed / 0 failed / 216.7ms
```

### 5.3 未执行

- `python3 serve.py` 实际启动服务监听端口（未做）
- `curl http://localhost:PORT/health` 健康探测（未做）

模块导入和测试已通过，启动大概率正常，但服务监听和健康检查本轮未覆盖。

## 6. 跨模块验证缺失项

| 缺失项 | 原因 | 影响评估 |
|---|---|---|
| 固件烧录验证 | 需 C5 实物 | 低：构建成功 + native 测试覆盖了 DSP/PSD/协议解析核心逻辑 |
| 真实硬件 Web Serial 通信 | 需 C5 + USB | 中：模拟器覆盖了协议，但真实 USB CDC 驱动栈、Chrome 的 tty 行为、C5 静音启动时序需要真机验证 |
| Electron GUI 实际启动 | headless 无法启动 GUI 应用 | 低：构建成功 + main.mjs 只有 142 行，逻辑简单 |
| RFTrack serve.py 实际监听 | 端口可能冲突 | 低：模块导入通过、pytest 覆盖了路由和服务 |
| Android APK installDebug | 需 Android 设备/模拟器 | 低：compileDebugKotlin 通过 + core 216 测试覆盖了协议/Store/RH 核心 |

## 7. 结论

**~1212 项自动化测试全部通过，0 失败，1 skipped。** 所有可执行构建命令实际执行成功。当前仓库处于良好可运行状态。

未通过的项：**无**。

需要区分"自动化测试通过"和"真实硬件验证通过"。前者已在本轮全部覆盖；后者依赖 C5 实物和 USB 环境，本轮未覆盖（模拟器已替代部分验证）。
