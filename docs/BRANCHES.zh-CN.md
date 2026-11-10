# 分支角色表（QiYun-RMC58）

这页解决两件事：**每条分支是干什么的、谁能合进谁**；以及「GitHub 上的版本看着比本地少功能」时**该查哪一层**。

下面的数字是 2026-11-11 在公开仓 `onebody/QiYun-RMC58` 上实测的（`git rev-list` / `git cherry` / `git diff --name-status`，复现命令在文末）。分支动了以后请重跑并更新本页。

## 一张表

| 分支 | 号位 | 角色 | 相对 `main`（实测） | 合并裁定 |
|---|---|---|---|---|
| `main` | tag `Web-v0.11` = `8fa8592` | **线上唯一入口**：Pages 站点、Releases 产物、烧写清单都从它出 | — | 只接跑过门槛的集成结果 |
| `relay/dev` | `c7b8743` | **集成线**：中继（Wi-Fi / W5500）、平台阶段、方案 C 设计先落这里 | 领先 0 / 落后 4 | 2026-11-10 `--no-ff` 合进 main，合并结果树与 `relay/dev` 逐字节相同 |
| `rf-track` | `9de3319` | RF Track 平台线（`rftrack/`：FastAPI 后端 + 工作台 + deploy） | 领先 31 / 落后 81；**独有内容 0**（见下节） | **永不合入 main**；只允许 main → rf-track 单向 |
| `platform/phase08` | `7d1cbbc` | **第三轨快照**：`gateway-agent/` + `site-server/` + 阶段治理文档，全部在 `platform/` 前缀下 | 领先 2（1 笔入库 + 1 笔脱敏）；191 个文件是 main 没有的，唯一的 M 是 `web/package.json`（main 后来 bump 到 0.11.0，它停在旧串） | 快照线，不合 main。**2026-11-10 裁定：只补文档指路，不动代码** |
| `develop/rf-monitor-platform` | `03eb498` | 平台阶段 1–3.1 的开发线 | 领先 29 / 落后 81；`git cherry` 非等价只有 2 笔，就是 rf-track 那两笔 | 内容已经 relay/dev → main |
| `ci/release-workflow` | `3ecdb15`（**只在本地**） | `.github/workflows/release.yml`（145 行，2 笔） | 领先 2 | 卡住：推送用的 token 没有 `workflow` scope，GitHub 扫整个待推区间会直接拒。拿到 scope 后单独推 |
| `fork/phone-app` | `21d4f28` | 手机端在本仓库里的历史线 | 领先 0 / 落后 135；独有文件 0 | 已废弃：Android 侧挪到独立的私有仓库，不在本仓库分支里 |
| `rh-uart-bridge` | `6fe37a0` | F1（UART 出频谱帧）当年的桥接分支 | 领先 0 | 历史保留，无待推工作 |
| `backup/pre-scrub-58d673c` | `58d673c` | 脱敏重写前的备份 | — | 别从它开新工作 |

> 本地 `repo/` 有两个 remote：`fork` = `onebody/QiYun-RMC58`（推这个），`origin` = 上游 `CapnBry/widscan58`（**永不推**）。CI 里的 `origin` 指的是 fork，因为 Actions 跑在 fork 上。

## 为什么 rf-track 领先 31 个提交，却算「零独有内容」

判据一律用**补丁 / 文件内容**对拍，不用提交号对拍（同一份改动被后续演进改写后 hash 一定变，按 hash 判会得出「线上缺功能」的错误结论）：

1. `git cherry main rf-track` → 只有 2 笔非补丁等价：`015018b`（audit docs + M0 baseline）与 `7bec675`（F1）。
2. `015018b` 的 6 份 `docs/rf-track/*.md` 在 main 上 **blob 哈希逐字节相同**。
3. `7bec675`（F1）引入的符号 `uart_pace` / `uart_frame_buf` / `FRAME_UART_MAX` / `link_set_frame_sink` 在 main 的 `firmware/` 里出现次数与 rf-track 完全一致 —— **F1 在线上**，只是它落在了另一串 hash 上。
4. `git diff --name-status main rf-track` 里 `A`（rf-track 有、main 没有）计数 **0**。

所以 rf-track 的独立价值是**部署形态与裁定**（它永不合 main），不是 main 缺的代码。

## 「GitHub 上少功能」按三层分开查

| 层 | 怎么判 | 已知边界 |
|---|---|---|
| ① Pages 站点 | 线上 `index-*.js` 指纹对本地 `web/dist`；`firmware/index.json` 的版本串 | 每次 push main 都重建（`pages.yml` 无 paths 过滤，纯文档提交也跑），约 4–5 分钟 |
| ② 源码（main） | `git log`、`git diff` | 只有 relay/dev 及被裁定合入的线会进来；rf-track、第三轨**故意**不在 main |
| ③ Releases 产物 | `gh release list`，核对 `assets[].digest` | **最容易滞后的一层**：`Web-v0.10` 落后 80 提交 / 261 文件才补发成 `Web-v0.11` |

产物层还有几条硬边界，都不是 bug：

- 线上清单里出现 `Web-v0.11-<n>-g<sha>` **不代表少功能**：那个串是 Pages CI 对 main 当时那一格做 `git describe` 得到的，发布之后 main 每走一格（**连只改文档的提交也算**）它就 +1 并带上 sha，而 `tag Web-v0.11` 钉在发布提交上不动。要让它归零只有一条干净路——发下一版；把 tag 往前挪属于 force push，本项目的发布规则里禁止。

- 网页烧写清单只出 `esp32c5`（`pages.yml` 的 `FW_ENVS: esp32c5`）。`esp32s3-relay`、`esp32c5-head`、`esp32c5-eth` 三张中继/网口镜像页面上刷不到，要本地 `pio run -e …` 或 `./build_release.sh` 一次出五张。
- `net/` 那五个源文件（`ws_codec` / `ws_link` / `net_prefs` / `relay_pipe` / `softap`）只在带 `WIDSCAN_RELAY` 的构建里编译（`firmware/src/CMakeLists.txt:22`），所以站点刷出来的 C5 镜像**听不懂 `NET` 命令**，即便网页上有配网对话框。
- 托管在 github.io 的页面结构上连不上局域网中继（中继是 WS 客户端，要拨 `ws://<host>:8080/node?id=…`，而 Pages 域名不可能有 `/node` 监听）。局域网入口是 Release 里的 `qiyun-rmc58-platform-<ver>.zip`（`serve.mjs` + `dist/`，解压 `node serve.mjs --port 8080`）。

## 推送前的固定动作

```bash
git fetch fork
git rev-list --count main..<b> && git rev-list --count <b>..main     # 领先 / 落后
git cherry -v main <b> | grep '^+'                                   # 补丁不等价的提交
git diff --name-status main <b> | grep -c '^A'                       # <b> 独有文件数
```

然后做**对象级脱敏扫描**：待推区间里的家庭/办公内网网段一律换成样例 `192.168.99.*`，家目录绝对路径、SSID 与设备凭据要清掉；`.github/workflows/*` 若落在待推区间会连带整条分支被拒（见 `ci/release-workflow` 那行）。
