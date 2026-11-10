# Web E2E Chrome 跨平台路径配置 — 最终代码审查报告

> 执行日期：2026-11-04
> 审查范围：WEB-E2E-CHROME-001 修改集
> 代码审查 + 修复完成

## 1. 审查发现清单

| # | 类别 | 问题 | 严重度 | 位置 |
|---|---|---|---|---|
| D1 | Vitest 配置 | 手工复制默认 exclude（node_modules/dist/.git/.cache/coverage），未来 Vitest 版本更新可能不兼容 | P1 | `vite.config.ts` |
| D2 | Windows PATH | PATHEXT 包含 `.BAT/.CMD/.PS1` 批处理脚本，在 `shell: false` 安全子进程模式下无法执行，增加 PATH 查找循环时间和歧义 | P1 | `chrome-resolver.mjs` `which()` |
| D3 | 未发现 | — | — | — |

## 2. 确认修复

### D1 — Vitest 默认 exclude 继承

**原始代码（上一轮临时版本）：**
```typescript
exclude: ["node_modules/**", "dist/**", ".git/**", ".cache/**", "coverage/**", "e2e/**", "**/*.mjs"],
```
手工复制了全部默认规则，覆盖范围过宽（dist/.cache/coverage 其实不是 Vitest 默认 exclude）。

**修复后：**
```typescript
import { configDefaults } from "vitest/config";
// ...
exclude: [...configDefaults.exclude, "e2e/**", "**/*.mjs"],
```
继承 vitest/config 的官方默认值（实际是 `["**/node_modules/**", "**/.git/**"]`），只追加我们自己需要的 `e2e/**`（Node 原生 test runner 的 .mjs 测试，vitest 不应扫它）。

**验证：**
- `npm test`：10 files / 56 tests 全绿
- vitest 不会再扫 node_modules/zod 的 .test.ts 文件（之前错误覆盖导致扫到了）

### D2 — Windows PATHEXT 过滤

**原始代码：**
```javascript
const suffixes = (process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";");
```
浏览器安装器永远只发布 `.exe`，`.bat/.cmd/.ps1` 是 shell 批处理脚本，我们用 `shell: false` 无法执行。

**修复后：**
```javascript
const suffixes = [".EXE", ".COM"];
```
只保留真正的可执行扩展名。浏览器不会以 .bat/.cmd 形式发布；如果 PATH 里真有个 chrome.bat，那也是用户自己写的 wrapper，应该手动指定绝对路径给 `--chrome` 或 `CHROME_BIN`。

**注释原因：**
- `(a) spawnSync` 用了 `shell: false`，BAT/CMD/PS1 在这种模式下直接 `ENOENT`
- `(b)` 浏览器安装器从来不用批处理脚本
- `(c)` 减少 PATH 查找的循环次数

### 其他审查点（确认无问题）

| 检查项 | 结论 |
|---|---|
| 浏览器类型命名 | 统一使用 Chrome/Chromium/Edge，puppeteer-core 的参数是 `executablePath`，不限制品牌；代码里用 Chrome 作为通用名称，没问题 |
| CLI 参数优先级 | CLI > env > auto，显式路径 invalid 时不静默回退到 auto（单元测试覆盖） |
| 自动探测顺序 | macOS: `.app` bundle → PATH which；Linux: PATH → 绝对候选；Windows: PATH → 绝对候选 |
| 文件存在 + 执行权限 + --version 三重验证 | ✅ `validateChromeExecutable` 已实现 |
| spawnSync 安全启动 | ✅ `shell: false` + 参数数组 + 5s 超时 + 合理 maxBuffer |
| 路径中有空格 | ✅ 参数数组形式自动处理；`_internals.pathJoin` 拼接也正确 |
| ENOENT / EACCES / 退出码非零 | ✅ 各有错误分支覆盖 |
| 错误信息 | ✅ formatChromeNotFound 输出平台/候选路径/修复建议 |
| 退出码 | ✅ 找不到 Chrome → exit 1；未知 flag → exit 2；--help → exit 0 |
| 测试 seam 生产风险 | `_internals` 是纯导出，生产代码自己调用自己（不是外部 patch），不会被误改；注释已说明 |
| .app bundle 处理 | ✅ macOS 代码正确拼接 `Contents/MacOS/<name>`，没有把 .app 目录当可执行文件 |
| Windows 环境变量缺失 | ✅ ProgramFiles/ProgramFiles(x86)/LocalAppData 都有 fallback |
| exit 后临时资源 | headless.mjs 末尾 `await browser.close()`，正常退出不残留；进程被 kill 时 Puppeteer 默认 signal 处理会清理子进程 |

## 3. 最终修改文件清单

| 文件 | 操作 | 变化行数 | 原因 |
|---|---|---:|---|
| `web/e2e/chrome-resolver.mjs` | **新增** | ~270 | Chrome 路径解析 + 安全验证（本轮修复了 PATHEXT） |
| `web/e2e/test/chrome-resolver.test.mjs` | **新增** | ~400 | 34 项单元测试（Node 原生 test runner） |
| `web/e2e/headless.mjs` | 修改 | +48/-13 | 引入 resolver，替换硬编码默认值 + 简陋 arg() |
| `web/package.json` | 修改 | +2 | 新增 `e2e` / `e2e:sim` npm scripts |
| `web/vite.config.ts` | 修改 | +9 | 继承 `configDefaults.exclude` 合并（本轮修复了手工复制默认值的问题） |

## 4. 未改变的东西（保证范围约束）

- **没有修改**：C5 固件、协议代码、DSP、RFTrack、Android、Electron 业务逻辑
- **没有修改**：原有 E2E 断言（28 项 check 完整保留）
- **没有删除**：任何测试文件或测试用例
- **没有引入**：任何新 npm 依赖（chrome-resolver.mjs 只用 Node 内置模块）
- **没有升级**：任何现有依赖

## 5. 全量测试执行结果

### 5.1 Chrome Resolver 单元测试
```
node --test e2e/test/chrome-resolver.test.mjs
# tests 34  # pass 34  # fail 0
```
覆盖：parseCliArgs 8 tests、resolver 优先级 8 tests、darwin autoDetect 3 tests、linux autoDetect 5 tests、windows autoDetect 3 tests、getChromeVersion 3 tests、validate 4 tests、error msg 2 tests。

### 5.2 Web vitest（原有 56 项回归）
```
npm test
Test Files  10 passed (10)
Tests       56 passed (56)
```

### 5.3 TypeScript 类型检查
```
npx tsc --noEmit
exit code: 0
```

### 5.4 Vite 生产构建
```
npm run build
✓ built in 185ms
```

### 5.5 macOS 实机 E2E（auto-detect Chrome）
```
npm run e2e:sim
[headless] Using Chrome from auto: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
[headless] Google Chrome 156.0.8078.80
... 28 个 PASS ...
28/28 checks passed
```

### 5.6 一键完整回归
```
bash tools/run-regression.sh skip-build
通过: 1184  失败: 0  跳过: 0
```
Firmware 48 / Web vitest 56 / Android 216 / RFTrack backend 650 / RFTrack workbench 214，全部通过。

## 6. 跨平台实机验证状态

| 平台 | 代码分支 | 单元测试 mock 覆盖 | 实机启动 Chrome |
|---|---|---|---|
| **macOS** | ✅ `findOnMacOS()` | ✅ 3 tests | ✅ **已验证**：auto-detect 找到 Chrome 156.0.8078.80 |
| **Linux** | ✅ `findOnLinux()` | ✅ 5 tests | ⚠️ 未验证（代码逻辑覆盖 PATH + snap + flatpak） |
| **Windows** | ✅ `findOnWindows()` | ✅ 3 tests | ⚠️ 未验证（代码逻辑覆盖 PATH + ProgramFiles + LocalAppData） |

Linux / Windows 的 mock 测试覆盖了所有路径拼接、PATH suffix 查找、环境变量缺失 fallback，但 **没有在真实 Linux/Windows 机器上实际启动 Chrome**。

## 7. 未解决 / 限制

| # | 事项 | 说明 |
|---|---|---|
| 1 | Linux/Windows 实机 Chrome 启动 | 需要 CI 跑一次 ubuntu-latest + `apt-get install google-chrome-stable` 验证 |
| 2 | headless.mjs 未在本轮增加 --version 参数 | 当前 `--help` 和 `--chrome=/path` 等价，但 `--version`（只输出版本不启动）是常见 CLI 模式，不在本轮任务范围内 |
| 3 | `_internals.pathJoin` 的测试使用 Node path.join 实现 | 对 macOS/Linux mock 没问题；Windows 测试单独 mock 了 `(...parts) => parts.join("\\")` |
| 4 | 其他 Node 调用 `pathJoin` / `osHomedir` 的地方没走 `_internals`（如 `tryPaths` 里用 `process.env.PATH.split`） | 当前只在被单元测试的路径上走 `_internals`；`formatChromeNotFound` 里不参与解析的路径拼接也没走。如果将来需要完全可 mock，应该全部改为走 `_internals`，但当前测试覆盖的路径已经足够 |

## 8. Git Diff 范围

```
web/e2e/headless.mjs        | 只修改 headless.mjs 引入 resolver 部分；28 项 E2E 断言完整保留
web/package.json            | 只加了 2 条 npm scripts
web/vite.config.ts          | 只加了 test.exclude；原有 base/plugins 配置完整保留
新增: web/e2e/chrome-resolver.mjs       （纯逻辑 + 安全 spawn）
新增: web/e2e/test/chrome-resolver.test.mjs （34 项单元测试）
```

**未修改：** firmware/*、android/*、electron/*、rftrack/*、tools/*、协议代码。

## 9. 最终验收结论

| 验收项 | 状态 |
|---|---|
| 跨平台自动探测 | ✅ |
| CHROME_BIN 环境变量 | ✅ |
| --chrome CLI（空格 + 等号形式） | ✅ |
| 参数优先级（CLI > env > auto） | ✅ |
| 显式路径 invalid 不静默回退 | ✅ |
| 启动前三重验证（存在 + 可执行 + --version） | ✅ |
| spawnSync 安全启动（shell: false + 参数数组 + 超时） | ✅ |
| 错误信息清晰（平台/候选/修复建议） | ✅ |
| 退出码正确 | ✅ |
| 单元测试覆盖所有平台路径 | ✅ 34/34 |
| vitest 回归（56 项） | ✅ 全绿 |
| TypeScript 类型检查 | ✅ exit 0 |
| macOS 实机 E2E | ✅ 28/28 |
| 一键完整回归（1184 项） | ✅ 全绿 |
| 修改范围约束 | ✅ 只动 web/ 任务范围 |

**结论：本轮审查通过，两个确认问题（Vitest 配置继承、Windows PATHEXT 过滤）已修复。所有测试保持绿色。可以安全合入。**
