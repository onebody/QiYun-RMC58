# 一键回归测试入口

> 执行日期：2026-11-04
> 脚本：`tools/run-regression.sh`

## 用法

```bash
# 跑全部（含构建）
bash tools/run-regression.sh

# 跳过构建，只跑测试（快速）
bash tools/run-regression.sh skip-build

# 只跑某模块
bash tools/run-regression.sh firmware
bash tools/run-regression.sh web
bash tools/run-regression.sh android
bash tools/run-regression.sh rftrack
bash tools/run-regression.sh electron

# 组合
bash tools/run-regression.sh web skip-build
```

## 检查的环境依赖

| 模块 | 依赖命令 | 安装方式 |
|---|---|---|
| Firmware | `pio` | PlatformIO Core 6.2.x |
| Web / Electron | `node`, `npm` | Node 22+ |
| Android | `java` (arm64) | JDK 21（脚本自动探测并 export JAVA_HOME） |
| RFTrack 后端 | `python3` | Python 3.14+ + FastAPI/SQLAlchemy/pytest |
| RFTrack 前端 | `node` | Node 22+（原生 `node --test` runner） |

脚本会自动探测 JAVA_HOME（依次试 openjdk-21 和 corretto-11 的 arm64 版）。如果你的 Java 安装在别的路径，在 shell rc 里手动设置 `export JAVA_HOME=...`。

## 覆盖的模块和命令

| 模块 | 执行命令 | 报告 |
|---|---|---|
| Firmware | `pio run -e esp32c5` + `pio run -e esp32s3` + `pio test -e native` | 构建 + 48 项 host tests |
| Web | `npm run build`（含 tsc --noEmit）+ `npm test` | 构建 + 56 项 vitest |
| Electron | `npm run sync-dist` + `npm run dist:mac` | 构建（依赖 web/dist） |
| Android | `./gradlew :core:compileKotlin` + `./gradlew :core:test` | 构建 + 216 项 JVM tests |
| RFTrack 后端 | `python3 -m pytest tests/ -v` | 650 项 pytest |
| RFTrack 前端 | `node --test test/*.mjs` | 214 项 node tests |

## 验证（2026-11-04 skip-build 模式实际输出）

```
通过: 1184   失败: 0   跳过: 0

PASS  Firmware native tests               48 passed
PASS  Web vitest                          56 passed
SKIP  Electron (skip-build)
PASS  Android :core:test                  216 passed
PASS  RFTrack backend                     650 passed
PASS  RFTrack workbench                   214 passed
```

## 输出行为

- 每个模块独立计时、独立记录
- 某模块失败后**继续执行其他模块**（用 `|| true` 或显式 `py_ok` flag 防止 exit）
- 汇总在末尾用不同颜色显示 PASS/FAIL/SKIP
- 退出码：全部 PASS → 0；任何 FAIL → 1

## 不做的事

- 不 `npm install`（除非 node_modules 缺失且需要）
- 不自动升级依赖
- 不清理 `.pio/` / `node_modules/` / `build/` 等缓存
- 不自动 commit / push
- 不修改用户代码和数据

## 注意事项

1. **Electron 在 skip-build 模式下跳过**（它的构建需要 web/dist，而 web 是在 skip-build 模式下只跑测试不跑构建）
2. **macOS 要求**：脚本用了 BSD 兼容的 `grep -oE`，不用 `-P`（Perl regex）。在 Linux 上同样工作
3. **Electron 构建**：`dist:mac` 要求 macOS arm64 机器。`dist:win` 需要 Windows 机器单独执行
4. **固件构建**：需要 PlatformIO + ESP-IDF 工具链，首次构建较慢（~2 min）
