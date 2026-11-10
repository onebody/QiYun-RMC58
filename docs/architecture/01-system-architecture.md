# 01 · 系统架构（P0-3）

> **架构版本**：Architecture V1.0 · **状态**：设计提案，本文不含任何已实现功能。
> **上游件**：`00-repo-audit.md`（事实与缺口 G1–G14、改判 D1–D10）。**下游件**：`02` 数据模型、`03` 发现与认领、`04` Gateway Agent、`05` Central Sync、`06` 部署、`07` 封存与迁移、`08` 路线图。
> **基线**：`repo/` @ `fw-wifi-relay` / `c547498`（含审计 §2.5 的未提交在制品）。
> 标注体系同 `00`。凡本文出现的**接口、字段、时限、容量**未标 [已由真机实测] / [已由软件测试] 者，一律是**设计值**，不得被引用为既成事实。

---

## 0. 一页结论

| # | 裁定 | 依据 | 代价 |
|---|---|---|---|
| S1 | **三层：Central Platform / Site Server / Device Access**，且只有中间一层持有业务权威 | 任务书 §2 | 三层意味着三份进程与两套接口契约；契约必须版本化（`04` §7） |
| S2 | **Device Access 是唯一能碰物理链路的地方；Site Server 一个 `open()` 都不做** | §5「Site Server 不直接管理操作系统串口」；现状反面样本 = `serve.mjs` + Python 后端直连中继（审计 G3/G7） | 每次接入都要多一个常驻 Agent 进程；同机部署时用 Docker `--network host` + Agent 裸跑化解（`06` §3） |
| S3 | **帧解析在 Site Server 只有一份**，Agent 永不解析 | 已有四份镜像（C/TS/Py/Kotlin，审计 C1），第五份的防漂移成本无人付 | Agent 无法在边缘做「按帧过滤」，只能按字节率/采样率粗降频（`04` §5.4） |
| S4 | **设备入站一律先到 Agent，再到 Site Server**；Central 永不直连设备 | §7「禁止节点默认直连 Central」+ S2 | 覆盖 D3：旧内连接入设计里「后端自己收 `/node`」的形态作废 |
| S5 | **身份 = host 观测键 + 配对凭据**，硬件签名留待 `DEVICE?`（单独审批） | §4/§6 要求 vs 审计 C2（固件无硬件 ID）；用户已裁定 | 换接入机/换介质会产生端点分裂，需人工合并（`02` §5.3 给出合并流程） |
| S6 | **Central 自托管单实例、场地主动外拨（Outbox）**；覆盖 2026-10-28「不做云端」定案 | 用户 2026-11-04 裁定（覆盖 D1） | 新增一份需备份、需升级的长期存储；跨场地一致性只有最终一致（滞后必须显式，`05` §6） |
| S7 | **平台代码落在 `repo/platform/`，`rftrack/` 整体转 legacy 只读**；Agent 用 **Go**，Site Server 与 Central 用 **Python/FastAPI**，站点 UI 用 **无构建 Vue3 CDN** | 用户 2026-11-04 裁定（四平台原生 + 复用可移植资产） | 一个产品两种语言；用契约测试 + 单一协议镜像把成本压住（§10） |
| S8 | **单机 Console（`web/`、`web-desktop/`、`app/`）本期零改动**，作为「近端单设备形态」共存 | §8「完整恢复原有功能」的最低成本路径就是别动它；审计 C4 证明功能都在 | 站点侧的「恢复」是**平台新建等价能力**，不是复用 Console 代码；两套实现的口径要靠对拍钉住（`08` §3） |

```
        ┌────────────────────────────  A · Central Platform  ────────────────────────────┐
        │  Site Registry · 全局身份与权限 · 场地状态 · 汇总/告警 · 归档索引 · SyncEvent    │
        └───────────────▲───────────────────────────────────────────────▲────────────────┘
             HTTPS outbox（唯一上行通道；批量、幂等、可重试、断点续传）    │ 管理面（人）
        ┌───────────────┴───────────────────────────────────────────────┴────────────────┐
        │                 B · Site Server（每场地一套；本地数据的唯一权威）                │
        │  Ingress（唯一解析器）· Spectrum Engine · Multi-Node Engine · 库 · REST/WS · UI  │
        └───────▲──────────────────────────────▲──────────────────────────▲──────────────┘
   WS 会话 ×N   │                              │ WS /api                  │ 浏览器/手机
        ┌───────┴──────────┐          ┌────────┴─────────┐        ┌───────┴────────┐
        │ C · HostAgent    │          │ C · HostAgent    │        │ 单机 Console    │
        │  (Go, 原生常驻)   │          │  (ARM Pi / x86)  │        │ web/ · app/     │
        │  USB Adapter     │          │  ETH Adapter     │        │ （本期不动）     │
        │  WIFI Adapter    │          │  USB Adapter     │        └────────────────┘
        └───▲──────▲───▲───┘          └───▲──────────────┘
            │      │   │                  │
     USB CDC│      │UART│ Wi-Fi relay 拨入 │ USB/TCP 串口
          C5 ······┘    └·················┘   （串口转以太网 / 远程 Agent）
        ESP32-C5 扫描头 / ESP32-S3 中继
```

---

## 1. 三层职责边界

### 1.1 A · Central Platform

**做**：场地注册与发现清单、全局身份（组织/用户/设备台账）、跨场地权限、场地在线状态、告警汇总、汇总数据（摘要与栅格统计）、归档索引（不存原件，存「哪里有原件 + 指纹」）、配置基线下发（可选，见 `05` §7）。
**不做**：不接设备、不解析帧、不参与扫描调度、不存 RAWIQ 原件（§10 默认）、不提供现场实时视图（滞后必须显式标注）。
**理由**：一旦 Central 能直连设备，「场地是本地权威」当场失效；审计 G8 记录旧定案与新架构的正面冲突，处置方式是让 Central 变成一个**只能被外拨写入的副本**。

### 1.2 B · Site Server

**做**：§2 列出的十项（场地/设备/扫描计划/实时接入/频谱分析/多节点融合/历史/本地 UI）。它是**唯一**被允许写场地库的进程，也是**唯一**持有协议解析器的地方。
**不做**：不 `open()` 串口（§5）、不直接 listen 设备端口（S4）、不在自身不可达时让设备失联（设备只认 Agent，`04` §4.4）。

### 1.3 C · Device Access（HostAgent + 三类 Adapter）

**做**：物理与网络接入、热插拔、多设备管理、字节收发、命令转发、重连、状态与错误计数、有界缓冲。
**不做**：解析（S3）、持久化业务数据（无库）、判断设备归属/权限（它只认「我这一条会话有没有被授权」）、跨设备聚合。
**允许缓存的最小状态**：Agent 身份与令牌、Site Server 地址、每端点的凭据与最后已知配置版本、一个**有界**内存环形缓冲（断网重连补传，默认 60 s/端点，`04` §5.5）。掉电即失——这条要写进 UI 的帮助文本，不要让运维以为它是黑匣子。

---

## 2. 运行时对象与生命周期

| 对象 | 由谁创建 | 出生判据 | 死亡判据 | 权威记录者 |
|---|---|---|---|---|
| HostAgent | 运维安装；Site Server 首次见其注册 | 注册成功（令牌 + 指纹） | 永久下线需人工撤销 | Site Server（`host_agent`） |
| DeviceEndpoint | Agent 观测（USB 插入 / 中继拨入 / TCP 连上） | 一次成功的 `INFO` 应答或等价握手 | 物理消失 → `gone`，记录不删 | Site Server（`device_endpoint`） |
| ConnectionSession | Agent 在端点建立数据链路时 | 链路 OPEN | 链路 CLOSE（原因闭集） | Site Server（`connection_session`） |
| PendingDevice | 任一发现通道报来且无法匹配已认领设备 | 见 `03` §3 | 认领或显式拒绝 | Site Server |
| Device | **只在认领动作中产生** | `claim` 事务 | 解绑不删（`unbound`） | Site Server |
| ScanTask | 调度器或人工 | 下发成功且被设备接受 | 完成/取消/失败 | Site Server |

**生命周期的一条硬规则**：`DeviceEndpoint` 与 `ConnectionSession` 是**易失**对象，`Device` 是**稳定**对象。任何把「链路 id」当「设备 id」使用的代码都属缺陷（审计 D2/C2 的直接后果，`02` §5 处理它）。

---

## 3. 数据面（下行：设备 → 界面 → 归档）

```
device bytes ─▶ Adapter(usb/eth/wifi) ─▶ Agent session
   · 每个 chunk 附 (endpoint_id, seq, agent_rx_unix_ns)      ← Agent 不打时间戳之外的标签，不解析
   · 有界环：默认 60 s/端点，溢出丢最老并计数（显式可观测）
─▶ Site Server Ingress ─▶ FrameStream（唯一解析器：0xA5'R' + crc32 + 文本行）
   · crc_errors / resync_bytes / dropped / text_lines 逐会话计数（沿用旧口径）
─▶ Normalizer：频率栅格 + 每节点时间映射（node_clock_map）
─▶ 实时环（每端点每槽位有界）─▶ WS 扇出（有界队列，丢最旧，满则标 lagging 并踢订阅者）
─▶ 落库批写（Measurement / SpectrumBin / Event；桶粒度按 SD 磨损纪律）
─▶ Outbox（同事务写）─▶ Central
```

三条不可让的口径：
1. **接收时间 ≠ 采集时间**（§9 明文禁令）。`agent_rx_ns` 与帧内 `t_ms` 各自存到底，任何视图/融合只用 `capture_wall_ms`（由 `node_clock_map` 换算），换算不了就带 `sync_quality='unsynced'`。
2. **解析错误不得静默**：`crc_errors`/`resync_bytes` 增量非零时，`/api/health` 与实时视图都要显式报，不得只写日志。
3. **数据真实性分级**：`source ∈ {real, mock, replay, unknown}`，**默认 `unknown`**，由 ingress 显式声明。这是对审计 G12（`capture.tap()` 硬编码 `real`）的结构性纠正。

---

## 4. 控制面（上行：界面 → 设备）

```
UI/计划器 ─▶ Site Server
   ├─ 授权检查（Device 归属 + 角色）
   ├─ 能力矩阵检查（§6：这条命令在这个 protocol_state / 这个介质上可用吗）
   ├─ 写权仲裁（每端点单写者租约；读者不限）
   └─ 命令 + 超时 + 期望回执 → 下发到持有该端点的 Agent
        └─ Agent: 逐端点写队列（有界）→ 物理链路 → 回执/超时/错误分类回传
             Site Server: 落 AuditLog + 更新期望态（desired vs applied 对账）
```

**为什么单写者**：设备只有一条 ASCII 文本流，两个写者会交错成坏行（现状样本：`serve.mjs:172-174` 每端点一页锁 **[已由源码确认]**）。**为什么读者不限**：多面板观察同一条流是真实需求，旧设计已有「N 读者 + 1 写者」裁定，本架构原样继承。

**期望态与配置版本**：设备的扫描配置在断电后回到 profile 默认（审计 C9：纯 RAM，`scanner.c:140-146`）。因此平台必须：① 存 `desired_revision`；② 每次新 `ConnectionSession` 建立后自动重放期望态（旧 Console 已有同形机制：`web/src/model/app.ts:117-128` 连接时还原 **[已由源码确认]**）；③ 重放结果逐条落 `AuditLog`，**半落地状态必须逐条点名**，不允许「看起来成功了」。

---

## 5. 多场地 / 多节点的规模承诺

| 维度 | 承诺 | 依据 |
|---|---|---|
| 每场地节点数 | **按 8 设计**，超出即不支持，不承诺性能 | 旧裁定 D5（`multi-node-spectrum.md` §5）与 `MAX_GATEWAYS=8` 同量级；**注意 8 是继承值，`06` §6 要求重测** |
| 每场地 Agent 数 | 按 4 设计（USB 机 / 网口机 / 中继机 / 备用机） | 设计值 **[需要实机验证]** |
| 每场地带宽 | 8 × ~80.9 KB/s ≈ **0.65 MB/s** 理论上限；实测参照 37–52 KB/s·板 | 理论账 **[根据源码推断]**；实测 **[已由真机实测]** |
| 每场地落库 | 栅格路线上界 0.35 KB/s·节点 ⇒ 8 节点 ≈ **0.24 GB/天** | 旧文档体积账（推算，非实测），P3 一轮必须换成实测值 |
| 场地数 | 架构不设上限；Central 侧按 32 场地做容量设计 | 设计值 |

---

## 6. 引擎切分与命令能力矩阵

### 6.1 Spectrum Engine（§8）= 原 Console 能力的平台等价物

| 能力 | 单机侧证据（存在） | 平台侧数据来源 | 关键约束 |
|---|---|---|---|
| RSSI / SNR / 噪声底 | `web/src/views/channelGrid.ts:166,188`；`model/store.ts:112` | `Measurement`（METRICS/LEVELS 帧） | LEVELS 与 METRICS **共用 seq**（`scanner.c:60,331`）⇒ 缺口检测只在同类帧内 |
| 中心频率 / 99% OBW / 峰值 | `frames.ts:39-40`、`zoom.ts:157` | `Measurement` 派生列 | 帧里有、旧平台落库时丢了（旧差异表 #8/#9/#10）⇒ 新库必须成列 |
| Spectrum 全景 | `panorama.ts:1` | `SpectrumBin`（350-bin 栅格 `v1`） | 一帧只覆盖一个 LO 段（256 bin × 100 kHz ≈ 25.6 MHz）⇒ 缺段必须数得出来，**禁止平滑补齐** |
| 瀑布图 | `panorama.ts:334-352` + 256 项色表 `util/canvas.ts:174` | `SpectrumBin` × 时间栅格 | 色表跨构建体系只能靠 JSON 基线双向对拍（旧阶段 3 裁定，沿用） |
| RAWIQ | `views/rawiq.ts`（16380 点，`radio_c5.c:28-37`） | **仅本地近端可得** | 审计 G9：`link.h:17-22` 把 RAWIQ 钉在 USB sink ⇒ 远程/中继节点**取不到**；UI 必须显式标不可得，不得伪造 |
| LEVELS | `store.ts:194` | 同 METRICS 通路 | — |
| 录制 / 回放 | `serial/recorder.ts:5,26,39`，`.widscan58` 格式（web ↔ app 互通，`Session.kt`） | `SpectrumArchive`（平台侧）+ 原样 `.widscan58` 文件下载（沿用） | 平台侧录制**必须可原样回放**：录制件 = 字节流 + 清单，不是解析后的表 |
| 设备控制 | `views/controls.ts` | 控制面（§4） | 过 §6.2 能力矩阵 |
| 原有设置 | `model/app.ts:13-54`（localStorage 7 键） | `ScanProfile`（平台）+ 浏览器 localStorage（Console 侧不动） | **两套设置不合并**（旧裁定 #14：合并是假需求） |

### 6.2 命令能力矩阵（审计 G6 的处置形态）

固件用两张白名单把「什么态能收什么命令」钉死 **[已由源码确认]**：

| 设备态 | 判据 | 可用命令面 | 证据 |
|---|---|---|---|
| **MONITOR**（扫描头 / USB 直连） | `PROTOCOL MONITOR` | `commands.c handle():399-819` 全部约 45 条 | — |
| **RH**（RotorHazard 兼容） | `PROTOCOL RH` | 仅 `exact[]` 21 条 + `prefix[]` 7 条（`PROTOCOL?`/`INFO`/`CONFIG?`/`METRICS RSSI|SNR`/`GAIN?`/`GAIN HARDWARE`/`FILTER AUTO`/`MODE GROUP|SINGLE`/`CONFIG SAVE|RESET`/`RECAL`/`LEVELFILTER MEDIAN|EMA`/`PHYIDLE?`/`GPIO?` + 前缀 `GAIN MANUAL `/`FFT `/`FILTER `/`IQCORR `/`PHYIDLE `/`THROTTLE `/`GPIO `） | `commands.c:307-318` |
| **RELAY**（S3 中继本体，无扫描器） | 编译期 `RX_WIFI_RELAY` | 只有 `INFO`、`PROTOCOL?`、`REBOOT`、`NET *`、`HEAD *`。**`PROTOCOL MONITOR` 被有意拒绝**（注释：`set_proto()` 会读写 Wi-Fi 独占的那份 config） | `commands.c:262-276` |
| **HEAD**（经中继的 C5 扫描头） | 走 `HEAD <line>` | 转发是 **opt-in**；扫描头本体仍是 MONITOR/RH 二态 | `commands.c:267-268` |

**矩阵的用法**：Site Server 为每个 `(命令, protocol_state, medium)` 三元组建一张**能力表**（`02` 的 `device_capability` + 常量矩阵），下发前先查表；查不到或矩阵说不可用 → **显式拒绝并给原因**，绝不「发了再说」。理由正是审计 G6：静默失败排查成本极高，而旧真机轮次里「能力位 `undeclared`」已经是申报过的缺口。

### 6.3 Multi-Node Engine（§9）

| 模式 | 语义 | 首版 | 前置 |
|---|---|---|---|
| `PER_NODE` | 每节点一条迹线，不融合 | ✅ | 无 |
| `NODE_COMPARE` | 同频点跨节点并排 + 差值，带可比性标签 | ✅ | 可比性标签（D7/A6） |
| `SITE_MAX` | 逐 (频点, 时间桶) 跨节点取最大 | ✅ | 频率栅格对齐 |
| `MEDIAN` / `AVG_POWER` | 中值 / 功率平均 | ❌ 后续 | 校准链路（审计 G10） |

§9 列的八项「必须处理」全部落到 `02`/`03`：频率坐标对齐（栅格 `v1`）、时间窗口（`time_window` + 桶）、节点 `boot_id`（会话派生，`02` §5.4）、单调时钟（`t_ms` 49.7 天回绕检测）、时间映射（`node_clock_map`，往返中点法 ±20 ms **[已由真机实测]**）、数据过期（`ttl` + `stale` 状态）、数据缺失/丢帧（`missing_prev`/`gap` 记账，缺口只在同类帧内查）、节点校准偏差（`device_calibration` 预留，未标定则标「不可比」）。

---

## 7. 不变式（评审检查项，每条都可写成测试）

| ID | 不变式 | 可测判据（`08` §3 引用） |
|---|---|---|
| I1 | 设备字节只能经 HostAgent 会话进入 Site Server | 静态检查：Site Server 无 `serial`/`socket accept` 设备监听依赖 |
| I2 | 帧解析全平台只有一份 | 仓库内 `0xA5` 魔数实现计数：平台侧仅 1 处，Go 侧 0 处 |
| I3 | 只有 Site Server 写场地库 | Agent 二进制不含 SQLite 驱动；集成测试断言 |
| I4 | Central 是副本，场地是权威 | Central 收到的每个记录带 `(site_id, revision)`；冲突时场地版本胜出 |
| I5 | 采集时间与接收时间各自存到底 | 任一行可断言 `capture_t_ms != NULL` 且 `receive_ns != NULL`；融合只用换算时间 |
| I6 | 一切队列有界 | 每个队列都有配置上限与溢出计数；`/api/health` 暴露 |
| I7 | Central 不可达不影响现场 | 断链测试：outbox 有界增长、溢出显式点名、扫描与本地 UI 正常 |
| I8 | 未认领设备不产生可信 Measurement | `pending` 观察不写 `measurement` 表（用例断言行数为 0） |
| I9 | 命令下发前先过能力矩阵 | 对 `RELAY` 态发 `SCAN START` 必须 4xx + 原因，而非超时 |
| I10 | 每个身份字段可指认来源 | `identity_source ∈ {info_line, host_observed, manual, pairing}`，无 `NULL` 路径 |
| I11 | 跨节点电平默认不可比 | 未标定节点的对比图必须带 `comparable=false` 标签 |
| I12 | `source` 默认 `unknown` | ingress 未显式声明 → 落库为 `unknown`（纠正审计 G12） |

---

## 8. 与旧系统的关系

- `rftrack/` → **legacy 只读**（`07`）。允许复用其**协议对拍夹具、帧解析实现、归档/保留/有界队列口径、压测与真机台账**；不允许在其表结构上继续加盖。
- `web/`、`web-desktop/`、`app/`、`firmware/` → **本期零改动**（§14 禁止项 + S8）。新平台与它们的关系是「同一设备、不同消费方」。
- 冲突处置已在 `00` §5/§6 登记为 D1–D10、G1–G14；本文不重述理由，只引用编号。

---

## 9. 非目标（本期明确不做）

节点间直接通信/自组网；IQ 级多节点融合（相控阵/干涉测向）；辅助定位与轨迹推测（±20 ms 不够，旧裁定 D7/A6）；C5 侧新增网络管理或任何 PHY/DSP 改动（§14）；`DEVICE?`/`CLOCK?`/`CAPS?` 等协议扩展的实现（**设计已备，需单独审批**，`08` §4）；浏览器内烧录的平台代理（`esptool-js` 依赖 Web Serial，`views/flasher.ts:93` **[已由源码确认]**，平台不代理串口刷写）；第三方插件动态代码加载；跨场地写穿（绝不把命令发到别的场地的板子）。

---

## 10. 技术栈选型（含 trade-off）

| 组件 | 选型 | 理由 | 代价与被否决项 |
|---|---|---|---|
| **HostAgent** | **Go**（用户裁定） | 单静态二进制、`go.bug.st/serial` 纯 Go 无 cgo、一条命令交叉出 5 target、内置 net/http+ws 无需框架、launchd/systemd/Windows 服务三种常驻形态都有成熟做法 | 与 `serve.mjs` 的 JS 逻辑零复用（约 537 行端点枚举/心跳/RH 让位要重写）。**否决 Rust**（交叉编译矩阵与串口原生依赖）、**否决 Node SEA**（Windows 无原生串口服务，产物 ~80 MB）、**否决 Python**（四平台冻结打包最痛、GIL 下 8 节点吞吐未验） |
| **Site Server** | **Python 3.11 + FastAPI + SQLAlchemy + Alembic + SQLite(WAL)** | `frame_parser.py` 与 golden 对拍、归档往返、保留策略、有界队列口径**可整体移植**，这是唯一能让 §13「Protocol Golden Tests」第一天就存在的选项；旧线已跑到 641/650 条测试 | 语言与 Agent 不同 ⇒ 契约要版本化 + 契约测试。**否决 Python Agent**（同上）、**否决把 Server 也写成 Go**（丢掉整套可移植测试资产，等于 §13 从零） |
| **Central Platform** | 同上（独立 schema，不复用 Site 代码路径） | 同团队栈、同部署件；共享的只有同步协议 | 两套库要同步演进 ⇒ `05` §5 用 `schema_version` + `app_version` 显式比对（旧 R20 风险） |
| **站点 Web UI** | **无构建 Vue3（CDN）+ 原生 ESM 组件** | 与已验证的工作台形态一致（`rftrack/frontend/workbench/` 214 条 `node --test` + 65 条真浏览器冒烟 **[已由软件测试]**），零打包链 | 无组件库 ⇒ 约定靠测试钉（旧 R24「跨语言 JSON 形状漂移」对策：契约快照） |
| **单机 Console** | 现状不动（Vanilla TS + Vite / Electron / Kotlin） | S8；审计 C4 证明功能完整 | 站点侧等价能力要新建 ⇒ 用对拍而非共享代码 |
| **传输** | 全部 HTTPS/WSS 可配置，**LAN 内默认明文但必须显式声明** | 设备侧 `wss://` 被固件有意拒绝（`ws_codec.h:42-44` **[已由源码确认]**），不能假装端到端加密 | 安全边界退化为「受控局域网 + 短 TTL 凭据 + 令牌轮换」（`03` §7 诚实申报） |

---

## 11. 工程质量体系的落点（§13 → 谁负责）

| §13 要求的测试 | 落点 | 现状 |
|---|---|---|
| Protocol Golden Tests | Site Server 解析器 ↔ `tests/golden/frames-v1.json`；固件 `pio test -e native` | 已有，可直接移植（17,436 帧 0 差在案） |
| DSP Tests | 固件 `test_dsp`/`test_psd` + web vitest + app `:core:test` | 已有（34 / 56 / 216 例） |
| Firmware Reproducible Build | `platform/` 构建脚本 + 清单（commit + env + sha256） | **无**，`08` P1 建 |
| Transport Tests | Agent 字节管道 + 会话帧（含分块不变性） | 部分（`test_ws`、`ws_bridge`），Agent 侧全新 |
| Agent Integration Tests | 假设备（回放录制件）+ 真串口两档 | **无** |
| Device Discovery Tests | 六路各一条契约测试 | **无** |
| Multi-Node Tests | 栅格对齐 / 时间映射 / 缺帧 / 融合语义 | **无** |
| Browser E2E | 6 个主导航 × 关键流 | 旧 65 条冒烟 + `web/e2e` 可作模板 |
| Deployment Tests | 四平台 × 同机/分机 | 旧 Docker 首跑抓到 2 条阻断缺陷 **[已由软件测试]**；systemd/Pi 从未执行 |

细节见 `08-implementation-roadmap.md` §3。
