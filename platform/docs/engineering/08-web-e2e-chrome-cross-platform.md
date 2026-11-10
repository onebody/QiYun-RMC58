# Web E2E Chrome 跨平台路径配置

> 任务编号：WEB-E2E-CHROME-001
> 执行日期：2026-11-04

## 1. 原始问题

`web/e2e/headless.mjs` 第 17 行硬编码了 Linux Chrome 路径：

```javascript
const chrome = arg("--chrome", "/usr/bin/google-chrome-stable");
```

这导致：
- macOS 用户首次运行会失败（需要手动 `--chrome` 指定）
- Windows 用户同样不自动可用
- 不支持 `CHROME_BIN` 环境变量（CI 友好场景缺失）
- arg() 只支持 `--chrome <path>`，不支持 `--chrome=<path>` 等价形式
- 没有 `--help`
- 没有启动前验证（路径存在、可执行、--version 检查）
- 找不到 Chrome 时只抛 `Browser was not found at the configured executablePath`，不告诉用户哪里找、怎么配

## 2. 新代码结构

```
repo/web/e2e/
├── headless.mjs                        # 入口（小改，引入 chrome-resolver）
├── chrome-resolver.mjs                 # ★ 新增：路径解析 + 验证模块
└── test/
    └── chrome-resolver.test.mjs        # ★ 新增：34 项 Node 原生 test runner 单元测试
```

chrome-resolver.mjs 提供：
- `parseCliArgs(argv, { extraKnown })` — 统一命令行解析，支持 `--chrome` / `--chrome=` / `--sim` / `--help` / 额外扩展
- `resolveChromeExecutable({ cliPath })` — **核心**，按优先级返回可用 Chrome 路径
- `autoDetectChrome()` — 根据 process.platform 走 darwin/linux/windows 分支
- `getChromeVersion(path, { timeoutMs })` — spawnSync 安全调用（shell: false，参数数组）
- `validateChromeExecutable(path)` — 路径存在 + 可执行 + --version 三重检查
- `formatChromeNotFound(resolution)` — 找不到时输出详细诊断

## 3. 配置优先级（高到低）

| # | 来源 | 说明 |
|---|---|---|
| 1 | `--chrome <path>` / `--chrome=<path>` | 命令行显式指定 |
| 2 | `CHROME_BIN` 环境变量 | CI 友好 |
| 3 | 自动探测 | 按当前操作系统找候选位置 |
| 4 | 报错退出 | exit code 1，输出诊断 |

**关键规则：** 显式路径（CLI 或 CHROME_BIN）无效时**不会静默回退到自动探测**——要么用指定的，要么报错。这是有意设计，防止 CI 环境"看似成功、实际跑了错浏览器"。

## 4. 自动探测候选位置

### macOS
```
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
/Applications/Chromium.app/Contents/MacOS/Chromium
~/Applications/Chromium.app/Contents/MacOS/Chromium
/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
```
找到第一个就返回；然后 fallback 查 PATH 里的 `google-chrome` / `chromium`。

### Linux
1. 先查 PATH（`google-chrome` / `google-chrome-stable` / `chromium` / `chromium-browser` / `chrome`）
2. 再查绝对候选（`/usr/bin/google-chrome`、`/usr/bin/google-chrome-stable`、`/usr/bin/chromium`、`/snap/bin/chromium`、flatpak 导出路径...）

### Windows
1. 先查 PATH（`chrome.exe` / `chromium.exe` / `msedge.exe`，带 `.COM/.EXE/.BAT/.CMD` 后缀）
2. 再查绝对候选（`%ProgramFiles%\Google\Chrome\Application\chrome.exe`、`%ProgramFiles(x86)%\...`、`%LocalAppData%\...`）

## 5. 启动前安全检查

```javascript
// 安全调用：shell:false + 参数数组
result = spawnSync(executable, ["--version"], {
  shell: false,
  timeout: 5000,
  encoding: "utf-8",
  maxBuffer: 1024 * 1024,
});
```

- `ENOENT` → 报告 "file not found"
- 非零退出码 → 报告退出码 + stderr 末尾
- 超时 → 5s 硬超时防止挂起
- **不用 `exec(\`"${chromePath}" --version\`)`**，防止路径中有空格或特殊字符时命令注入

## 6. 错误输出示例

```
$ node e2e/headless.mjs --chrome /no/such/chrome --sim

[ERROR] Chrome executable not found.

Platform:  darwin (arm64)

Checked:
(CHROME_BIN not set)
- /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
- ~/Applications/...
...

Specify Chrome manually:
  node e2e/headless.mjs --chrome "/path/to/Google Chrome"

Or set environment variable:
  export CHROME_BIN="/path/to/Google Chrome"

Reason: CLI path invalid (path does not exist: /no/such/chrome)
exit code: 1
```

## 7. 跨平台验证

| 层级 | 覆盖 | 测试 | 结果 |
|---|---|---|---|
| 单元测试（macOS 主机上跨平台 mock） | parseCliArgs 8 tests、resolve priority 8 tests、darwin autoDetect 3 tests、linux autoDetect 5 tests、windows autoDetect 3 tests、getChromeVersion 3 tests、validate 4 tests、error msg 2 tests | `node --test e2e/test/chrome-resolver.test.mjs` | **34/34 通过** |
| 本机实际运行（macOS auto-detect） | dev server + `node e2e/headless.mjs --sim` | 自动探测到 Chrome 156.0.8078.80 | **28/28 E2E checks passed** |
| 命令行显式路径 | `--chrome "/Applications/.../Google Chrome"` | 正常 | ✅ 行为与 auto 一致 |
| 环境变量 | `CHROME_BIN="..." node e2e/headless.mjs` | 正常 | ✅ |
| 错误处理 | 不存在路径 | exit 1 + 详细诊断 | ✅ |
| vitest 回归 | 原有 10 个测试文件 | `npm test` | **56/56 通过** |
| tsc --noEmit | TypeScript 类型检查 | 嵌入 `npm run build` | **exit 0** |

**未验证：** Linux 实机启动 Chrome、Windows 实机启动 Chrome。path join、PATH split、os.homedir 全部通过 `_internals` seam 可被 mock，34 项单元测试覆盖了所有平台逻辑分支，但这只是"代码路径正确"的证据，不是"Chrome 实机能启动"的证据。

## 8. CI 配置示例

GitHub Actions：
```yaml
jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 22 }
      - run: sudo apt-get install -y google-chrome-stable
      - run: npm ci && npm run build
      - run: npm run e2e:sim
```

## 9. 新增/修改文件一览

| 文件 | 操作 | 行数 | 作用 |
|---|---|---:|---|
| `repo/web/e2e/chrome-resolver.mjs` | **新增** | ~270 | 路径解析 + 验证模块（纯 JS，零依赖） |
| `repo/web/e2e/test/chrome-resolver.test.mjs` | **新增** | ~400 | 34 项单元测试（Node 原生 test runner） |
| `repo/web/e2e/headless.mjs` | **修改** | 8 行变 | 引入 resolver，替换硬编码默认值，替换简陋 arg()，增加 --help/unknown-flag 错误 |
| `repo/web/package.json` | **修改** | +2 | 新增 `e2e` 和 `e2e:sim` npm scripts |
| `repo/web/vite.config.ts` | **修改** | +8 | 增加 test.exclude，把 vitest 默认 exclude 显式列出并追加 `e2e/**` 和 `**/*.mjs`（因为我们引入了 Node 原生 runner 的 .mjs 测试） |
| `docs/engineering/08-web-e2e-chrome-cross-platform.md` | **新增** | 本文档 | 交付文档 |

## 10. 维护说明

- 新增 Chrome 候选位置时，只需修改 `chrome-resolver.mjs` 的 `findOnDarwin` / `findOnLinux` / `findOnWindows` 三个函数
- 新增 known CLI flag 时，在 `headless.mjs` 的 `extraKnown` 里注册即可
- `_internals` 对象是测试 seam，生产代码永远用它来调用 fs/cp/os/path。**不要**在生产代码里直接 `import fs from "node:fs"` 然后在其他地方调用
- 当 vitest 新增 .mjs 文件时，也要在 `vite.config.ts` 的 exclude 里追加（否则 vitest 会尝试加载）

## 11. 限制

1. **Chrome 启动能力**未验证：Linux 和 Windows 的 path join、PATH 后缀查找、AppData/ProgramFiles 拼接全部有单元测试覆盖，但这些机器上的 Chrome 启动还未实机跑过。建议在 CI 上用 ubuntu-latest + google-chrome-stable 验证一次
2. **非 Linux 发行版特有路径**：arch/flatpak/snap 的路径已列出，但 gentoo/nixos 等可能还有不同位置。PATH + 绝对候选 fallback 是通用策略
3. **headless.mjs 其他 flags**（`--bridge`、`--out`）不在 `chrome-resolver.mjs` 的 parseCliArgs 默认 known 里，headless.mjs 通过 `extraKnown` 注册。将来如果有其他脚本也用 chrome-resolver 的 parseCliArgs，它们也需要显式注册自己的额外 flags
