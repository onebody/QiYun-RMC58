# Phase 08.2.2 — Threat Model（STRIDE 威胁模型）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**威胁建模文档。未修改任何产品代码、数据库、API、协议或固件。**
> 日期：2026-11-05
> 输入：`docs/PHASE08_2_2_SECURITY_ASSET_AUDIT.md`（G-01–G-20）
> 状态：**PROPOSED（待人工审批）**

---

## 0. 风险等级判定口径

| 等级 | 判定依据 |
|---|---|
| **CRITICAL** | 未认证即可读写/破坏核心数据，或可直接取得平台身份；影响全站；利用门槛低 |
| **HIGH** | 未认证或低权限即可造成实质数据泄露/篡改/服务中断；或安全机制存在明确绕过路径 |
| **MEDIUM** | 需要一定前提（同网段、本机、外部绑定、已认证身份）或影响局部；有部分防护 |
| **LOW** | 需要较高前提、影响有限，或已有有效缓解 |
| **INFO** | 功能缺口而非安全漏洞；或为设计约束提示 |

**原则**：风险等级按**当前实现**判定，并在“多站点/公网/前端 WS 落地后”标注变化。不得用未来设计降低当前评级。

---

## 1. 信任边界

```text
[浏览器 Platform UI] --同源 HTTP(无认证)--> [Site Server REST]
        |                                        |
        |  (无 WS)                               |  SQLite 明文文件
        v                                        v
   /app/ RF Console                    [Measurement / Device / Agent]
                                                 ^
                                                 | Agent 反向 WS（首条 auth）
                                                 |
                                     [Gateway Agent 本地 API 127.0.0.1]
                                                 |  命令 Token
                                                 v
                                     [物理设备 ESP32-C5]
```

边界定义：

| 边界 | 一侧 | 另一侧 | 当前控制 |
|---|---|---|---|
| TB-1 | 任意 HTTP 客户端 | Site Server REST | **无**（仅网络位置） |
| TB-2 | Gateway Agent | Site Server WS | 凭证首消息认证 + Site 绑定 |
| TB-3 | 本机进程/局域网 | Gateway Agent 本地 API | 回环绑定 + 写端点 Token |
| TB-4 | 浏览器 | 设备（Serial/USB） | 浏览器/Electron 权限 |
| TB-5 | 操作系统 | SQLite 文件 | **无应用层控制** |

---

## 2. 威胁清单

### T-01 同一局域网未授权用户访问 Site Server REST API

- **STRIDE**：Spoofing / Information Disclosure / Tampering / Elevation of Privilege
- **资产**：Device、Measurement、Site、Agent 凭证签发能力、诊断信息
- **攻击者能力**：同网段任意主机；无需任何凭证；会使用 curl/browser/脚本
- **攻击路径**：直接请求 `GET /api/v1/devices` → `GET /api/v1/devices/{id}/measurements` → `PATCH/DELETE` 修改设备 → `POST /api/v1/agents/pairing-codes` 取得明文凭证
- **当前防护**：默认 `127.0.0.1`（`config.py` L26），但 `main()` 允许 `--host` 覆盖且日志仅警告（`main.py` L539-540）
- **防护缺口**：G-01、G-02、G-03
- **影响范围**：全站数据与完整性；可进一步伪造 Agent 身份
- **风险等级**：**CRITICAL**
- **判定依据**：未认证 + 可写 + 可签发身份，利用门槛最低
- **建议缓解**：REST 全量认证（SP-02）；Site Scope 强制（SP-03）；RBAC（SP-04）；默认仅回环或强制反向代理
- **对现有架构影响**：需新增认证中间件与用户模型（DB 变更）
- **需人工批准**：**是**

### T-02 未授权客户端直接调用 Agent 本地 API

- **STRIDE**：Spoofing / Tampering / Information Disclosure
- **资产**：设备状态、事件、Connector 配置
- **攻击者能力**：本机任意用户/进程；若外部绑定则同网段
- **攻击路径**：`GET /api/v1/devices`、`GET /api/v1/diagnostics`、`GET /api/v1/site-connector`、`WS /ws/v1/events` 读取；写端点需 Token 但读端点无
- **当前防护**：默认回环（`config.py` L122-144）；写端点 Token（`api/__init__.py` L186-269）；事件 WS 丢弃 RAWIQ
- **防护缺口**：G-15
- **影响范围**：设备与事件侦察；若外绑则数据泄露
- **风险等级**：**MEDIUM**（默认回环）/ **HIGH**（外部绑定）
- **判定依据**：默认部署下需本机权限；外绑场景读端点完全开放
- **建议缓解**：读端点同样要求 Token；`/ws/v1/events` 首消息认证；外绑时强制要求读认证（SP-11）
- **对现有架构影响**：Agent 本地 API 增加认证依赖；需保留回环自用路径
- **需人工批准**：**是**

### T-03 普通用户访问其他 Site 的数据

- **STRIDE**：Information Disclosure / Elevation of Privilege
- **资产**：跨 Site Device、Measurement、Agent
- **攻击者能力**：任何已访问平台者（当前等于“任意 HTTP 客户端”）
- **攻击路径**：`GET /api/v1/devices?site_id=other`；`GET /api/v1/devices/{id}/measurements`（无 site 过滤）；`GET /api/v1/measurements/stats?site_id=other`
- **当前防护**：单站点部署下只有一个 Site，跨站影响尚未显现；`adopt`/`transfer-owner` 有同站校验
- **防护缺口**：G-03
- **影响范围**：多站点落地后为全量数据越权
- **风险等级**：**HIGH**（当前）/ **CRITICAL**（多站点）
- **判定依据**：代码层面无 Scope 强制；当前因单站点降低实际影响
- **建议缓解**：所有查询从**会话上下文**取 Site，忽略客户端 `site_id`；或校验其属于成员集合（SP-03/SP-04）
- **对现有架构影响**：API 入参语义变更；需兼容单站点默认
- **需人工批准**：**是**

### T-04 Observer 执行 Owner 专属操作

- **STRIDE**：Elevation of Privilege
- **资产**：Device 配置/删除/迁移、命令
- **攻击者能力**：已认证 Agent（Observer 角色）；或未来低权限用户
- **攻击路径**：Agent 平面：`command.request` 被 `authorize_device_command` 拒绝（已防护）；平台平面：当前无写 UI，但 REST 无鉴权，任何人可直接 `PATCH/DELETE`
- **当前防护**：`_ALLOWED_ROLES`（`authorization.py` L38-44）；`_handle_inbound_command` 拒绝并记录（`connection.py` L310-345，授权判定 L334）
- **防护缺口**：G-02（用户平面无授权）
- **影响范围**：Agent 平面已缓解；用户平面未建立
- **风险等级**：**MEDIUM**（当前写操作未在 UI 暴露，但 REST 可直调）/ **HIGH**（平台写 UI 上线后）
- **判定依据**：机器身份平面有测试覆盖；人类角色平面不存在
- **建议缓解**：用户 RBAC（SP-04）；写操作后端强制角色校验（SP-02/03）
- **对现有架构影响**：新增用户角色与成员表
- **需人工批准**：**是**

### T-05 浏览器被 XSS 注入后窃取凭证

- **STRIDE**：Information Disclosure / Spoofing
- **资产**：会话凭证、操作权限
- **攻击者能力**：能注入脚本（当前无已知注入点）
- **攻击路径**：注入脚本 → 读取 `localStorage` / JS 变量 → 外带
- **当前防护**：平台 UI 无 `innerHTML`、无 `eval`、无本地凭证存储（VERIFIED，`site-server/web` 检索 0 命中）
- **防护缺口**：未来若引入 JS 可读 Token 则暴露
- **影响范围**：单用户会话
- **风险等级**：**LOW**（当前）/ **HIGH**（若采用 localStorage JWT）
- **判定依据**：当前无凭证可窃取；风险取决于认证方案选型
- **建议缓解**：采用 HttpOnly + SameSite Cookie 会话；CSP；禁止 `innerHTML`（ADR-08.2.2-02）
- **对现有架构影响**：认证载体选择
- **需人工批准**：**是**

### T-06 WebSocket 未认证连接

- **STRIDE**：Spoofing
- **资产**：Agent 事件写入、设备状态
- **攻击者能力**：任意网络可达者
- **攻击路径**：连接 `/ws/v1/agents/connect` 后不发 auth，或发送业务消息
- **当前防护**：10 s auth 超时（`connection.py` L159-164）；首条必须 auth（L170-176）；未认证不处理业务
- **防护缺口**：`accept()` 在认证前（L134）；无 IP 频率限制
- **影响范围**：连接资源消耗；无法写入
- **风险等级**：**LOW**
- **判定依据**：认证强制且无绕过；仅存在资源占用
- **建议缓解**：连接速率限制、认证前连接数上限（SP-09）
- **对现有架构影响**：WS 入口增加限流
- **需人工批准**：**是（轻微）**

### T-07 WebSocket 认证后访问未授权站点

- **STRIDE**：Elevation of Privilege
- **资产**：跨 Site 数据
- **攻击者能力**：持有有效 Agent 凭证
- **攻击路径**：认证为 Agent A，尝试写入 Site B
- **当前防护**：`site_id` 由服务端 `agent.site_id` 绑定（L200-201）；所有写入使用该 `site_id`
- **防护缺口**：Site 缺失回落 `"site-001"`（L201）
- **影响范围**：理论上无跨站写入
- **风险等级**：**LOW**
- **判定依据**：服务端绑定，客户端不可指定
- **建议缓解**：删除 `"site-001"` 兜底，记录缺失即拒绝（SP-03）
- **对现有架构影响**：极小
- **需人工批准**：**否（属缺陷修复，需批准后实施）**

### T-08 Agent 凭证泄漏

- **STRIDE**：Information Disclosure / Spoofing
- **资产**：Agent 身份
- **攻击者能力**：能读取 Agent 主机文件/配置/进程内存
- **攻击路径**：读取 YAML `site.credential`（`config.py` L63、`save_config` L147-163）；或从进程内存导出
- **当前防护**：服务端仅存哈希；不打印日志；30 天有效期
- **防护缺口**：G-14 明文落盘、无轮换、无 env 注入
- **影响范围**：单 Agent 身份被冒用
- **风险等级**：**HIGH**
- **判定依据**：明文落盘 + 长有效期 + 无轮换，泄露后长期有效
- **建议缓解**：`GATEWAY_SITE_CREDENTIAL` 环境变量优先；文件权限 0600；轮换 API 与双凭证宽限期；缩短有效期（SP-10）
- **对现有架构影响**：Agent 配置加载与凭证管理变更
- **需人工批准**：**是**

### T-09 Agent 被复制或冒用身份

- **STRIDE**：Spoofing
- **资产**：Agent 身份、设备所有权
- **攻击者能力**：复制配置到另一主机；或同时运行两份
- **攻击路径**：复制 YAML → 以相同 `agent_id` 连接 → 覆盖 registry 条目（`AgentConnectionRegistry.register` L88-91）→ 接收设备快照/命令
- **当前防护**：凭证绑定 `agent_id`；连接注册表按 `agent_id` 唯一
- **防护缺口**：G-17 无设备指纹/证书；旧连接不被主动关闭
- **影响范围**：设备所有权混淆、数据来源不可信
- **风险等级**：**HIGH**
- **判定依据**：无硬件/主机绑定，复制即冒用
- **建议缓解**：连接时若已有同 `agent_id` 在线，拒绝新连接或踢旧连接并审计；可选主机指纹；跨互联网场景 mTLS（SP-10）
- **对现有架构影响**：WS 连接管理语义变更
- **需人工批准**：**是**

### T-10 Agent 与 Server 之间重放或伪造请求

- **STRIDE**：Spoofing / Tampering
- **资产**：事件流完整性、命令
- **攻击者能力**：网络中间人（同网段/路由可达）
- **攻击路径**：截获 `ws://` 首条 auth 凭证 → 重放认证 → 伪造事件；或篡改帧
- **当前防护**：首消息认证；凭证为 bearer
- **防护缺口**：G-13 无 TLS 强制、无 nonce/challenge
- **影响范围**：数据伪造、设备状态篡改
- **风险等级**：**HIGH**（明文 `ws://`）/ **MEDIUM**（`wss://`）
- **判定依据**：默认示例为 `ws://`；凭证可被截获重放
- **建议缓解**：跨主机部署强制 `wss://`；本地回环可用 `ws://`；可选 nonce 挑战（SP-10/SP-16）
- **对现有架构影响**：部署配置与 TLS 证书管理
- **需人工批准**：**是**

### T-11 伪造 Measurement 数据

- **STRIDE**：Tampering / Repudiation
- **资产**：Measurement、统计、审计
- **攻击者能力**：持有任一有效 Agent 凭证，或未认证 REST 调用者（当前）
- **攻击路径**：Agent 上报任意 `device_id` 事件 → `upsert_device_from_agent` 首次即认领（`devices/__init__.py` L44-71）→ 写入 Measurement；或 REST 未授权删除/篡改
- **当前防护**：event_id 唯一约束（防重复，不防伪造）；`agent_id` 追溯字段
- **防护缺口**：G-16 不校验设备归属；无事件签名
- **影响范围**：数据可信度崩塌
- **风险等级**：**HIGH**
- **判定依据**：任一 Agent 可注入任意设备数据；REST 亦可破坏
- **建议缓解**：设备归属校验（Agent 上报设备须有该 Agent 的 endpoint 记录或首次认领需授权）；REST 认证；审计 `source=agent`
- **对现有架构影响**：摄入逻辑增加归属校验
- **需人工批准**：**是**

### T-12 恶意客户端构造超大请求或高频请求

- **STRIDE**：Denial of Service
- **资产**：Site Server 可用性、SQLite I/O
- **攻击者能力**：任意网络可达者
- **攻击路径**：大量请求；`PATCH /api/v1/site` 或 `transfer-owner` 传超大 JSON；批量分页拉取
- **当前防护**：无
- **防护缺口**：G-06
- **影响范围**：全站不可用
- **风险等级**：**MEDIUM**
- **判定依据**：需网络可达；单机 SQLite 易被拖慢
- **建议缓解**：反向代理限流 + 应用层速率限制 + 请求体上限（SP-09）
- **对现有架构影响**：中间件
- **需人工批准**：**是**

### T-13 数据库文件被直接访问

- **STRIDE**：Information Disclosure / Tampering
- **资产**：全部 Site/Agent/Device/Measurement/Audit
- **攻击者能力**：主机本地文件读取权限（同机用户、备份泄露、路径穿越）
- **攻击路径**：读取 `data/site.db`（`config.py` L31）；`diagnostics` 直接返回 `db_path`（`main.py` L398）辅助定位
- **当前防护**：无应用层控制
- **防护缺口**：G-18、G-08
- **影响范围**：全量数据
- **风险等级**：**HIGH**
- **判定依据**：明文、无加密、无权限保证
- **建议缓解**：文件权限 0600/0700；专用系统用户；诊断不返回绝对路径；备份加密；可选 SQLCipher（需评估）
- **对现有架构影响**：部署与运维；诊断字段变更
- **需人工批准**：**是**

### T-14 错误日志泄漏凭证

- **STRIDE**：Information Disclosure
- **资产**：凭证、Token
- **攻击者能力**：能读日志
- **攻击路径**：异常栈/调试日志包含请求体或 URL
- **当前防护**：WS URL 脱敏（`connection.py` L46-53）；Agent Token 不打印（`api/__init__.py` L40-43/L128-133）；诊断掩码
- **防护缺口**：`site-connector/enable`、`pair` 的请求体未显式脱敏（当前无日志打印，属潜在）
- **影响范围**：凭证泄漏
- **风险等级**：**LOW**
- **判定依据**：现有代码未发现泄漏；风险来自后续改动
- **建议缓解**：统一日志脱敏中间件；禁止打印 body（SP-07）
- **对现有架构影响**：日志规范
- **需人工批准**：**否（需批准后实施）**

### T-15 管理员账户被盗用

- **STRIDE**：Spoofing / Elevation of Privilege
- **资产**：System Admin 权限
- **攻击者能力**：钓鱼、口令猜测、会话窃取
- **攻击路径**：未来登录入口 → 弱口令/会话固定/无 MFA
- **当前防护**：**不存在账户体系**
- **防护缺口**：认证方案尚未实施
- **影响范围**：全平台
- **风险等级**：**INFO（当前）→ HIGH（认证上线后）**
- **判定依据**：当前无账户，故无此风险；设计方案必须内置防护
- **建议缓解**：强口令哈希（Argon2id/bcrypt）、登录限流、会话轮换、可选 TOTP MFA、审计（SP-01/SP-02）
- **对现有架构影响**：认证基础设施
- **需人工批准**：**是**

### T-16 Site Server 被入侵后横向访问 Agent

- **STRIDE**：Elevation of Privilege / Lateral Movement
- **资产**：Agent 本地设备控制
- **攻击者能力**：取得 Site Server 主机权限
- **攻击路径**：读取 Agent 凭证（服务端只存哈希，**不能直接冒用**）→ 但可伪造配对码注册新 Agent；可读取全部数据；可断开/吊销 Agent
- **当前防护**：服务端不存明文凭证；命令远端分发未实现
- **防护缺口**：无服务身份隔离；配对码签发无认证
- **影响范围**：全站数据 + 可中断 Agent
- **风险等级**：**HIGH**
- **判定依据**：横向到设备控制受限于“无远端命令分发”，但数据与可用性已失守
- **建议缓解**：最小权限运行、服务账户隔离、密钥与数据分离、审计外送（SP-07/SP-16）
- **对现有架构影响**：部署与运维
- **需人工批准**：**是**

### T-17 一个 Site 的安全事件影响其他 Site

- **STRIDE**：Elevation of Privilege / Information Disclosure
- **资产**：跨 Site 数据与可用性
- **攻击者能力**：取得单 Site 权限的攻击者
- **攻击路径**：共享 SQLite 文件；`site_id` 可任意指定；单进程共享内存；事件表共享
- **当前防护**：DB 行级 `site_id`；单站点部署
- **防护缺口**：G-03；无租户隔离
- **影响范围**：多站点后为跨租户
- **风险等级**：**MEDIUM（当前单站点）→ CRITICAL（多站点）**
- **判定依据**：数据层有 `site_id`，但应用层不强制
- **建议缓解**：统一 Scope 中间件；可选独立 DB/实例（多站点独立迁移，见 08.2.1 ADR-01）
- **对现有架构影响**：多站点架构决策（仍 PROPOSED）
- **需人工批准**：**是**

### T-18 RF Console 绕过 Platform 的访问限制

- **STRIDE**：Elevation of Privilege
- **资产**：本地设备、Console 能力
- **攻击者能力**：能打开 `/app/` 或直接使用本地构建
- **攻击路径**：直接打开 Console → 使用 Web Serial/WebUSB 访问设备，不经过平台权限
- **当前防护**：Console 不调用 Site Server API；设备访问需浏览器/Electron 权限与用户手势
- **防护缺口**：静态托管无鉴权
- **影响范围**：物理邻近设备的本地控制
- **风险等级**：**MEDIUM**
- **判定依据**：属设备本地物理边界，不是平台数据越权
- **建议缓解**：保持独立入口；对 `/app/` 可加平台登录但不破坏 Electron/本地入口；设备侧配对/白名单（未来）
- **对现有架构影响**：不得破坏 Web Serial/WebUSB
- **需人工批准**：**是（若加登录）**

### T-19 自助注册伪 Agent（配对码滥用）

- **STRIDE**：Spoofing / Elevation of Privilege
- **资产**：Agent 身份、Measurement 写入
- **攻击者能力**：任意 HTTP 客户端（当前）
- **攻击路径**：`POST /api/v1/agents/pairing-codes` 未认证 → 得到 `pairing_code` + `plain_credential` → `POST /api/v1/agents/pair`
- **当前防护**：配对码一次性、300 s、最多 5 个活跃（`auth/__init__.py` L54-104）
- **防护缺口**：**签发接口本身无认证**
- **影响范围**：可无限注册伪 Agent，注入数据
- **风险等级**：**CRITICAL**
- **判定依据**：身份签发完全开放
- **建议缓解**：配对码签发仅限 Site Admin（SP-04）；审计；可选一次性安装令牌 + 人工确认
- **对现有架构影响**：配对流程增加授权
- **需人工批准**：**是**

### T-20 接口契约与内部信息侦察

- **STRIDE**：Information Disclosure
- **资产**：API 结构、DB 路径、在线 Agent
- **攻击者能力**：任意 HTTP 客户端
- **攻击路径**：`/docs`、`/openapi.json`、`GET /api/v1/diagnostics`
- **当前防护**：无
- **防护缺口**：G-07、G-08
- **影响范围**：为 T-01/T-13 提供攻击地图
- **风险等级**：**MEDIUM**
- **判定依据**：单独不造成破坏，但显著降低攻击成本
- **建议缓解**：生产关闭 docs；诊断仅管理员且不含路径（SP-08）
- **对现有架构影响**：配置与权限
- **需人工批准**：**是**

---

## 3. 汇总

| 等级 | 数量 | 编号 |
|---|---|---|
| CRITICAL | 2 | T-01、T-19 |
| HIGH | 8 | T-03（当前）、T-08、T-09、T-10、T-11、T-13、T-16、T-02（外绑） |
| MEDIUM | 7 | T-02（回环）、T-04、T-12、T-17、T-18、T-20、T-06（资源） |
| LOW | 4 | T-05、T-06、T-07、T-14 |
| INFO | 1 | T-15（当前无账户） |

> 注：同一威胁在不同部署形态下跨级，已在各条目注明；汇总按“当前默认部署 + 已声明前提”取主评级。

---

## 4. 关键结论

1. **最高优先级不是“前端补按钮”，而是后端身份与授权缺失**（T-01/T-19）。
2. **Agent 平面已有真实防护**，主要缺口在凭证生命周期（T-08/T-09）与设备归属校验（T-11）。
3. **网络隔离不是安全措施**：T-01/T-03 在内网同样成立；Tailscale 等只降低暴露面，不替代认证授权。
4. **多站点会显著抬升 T-03/T-17**，必须在多站点后端之前完成 Scope 强制。
5. 所有缓解均为 **PROPOSED**，不实施。

---

## 5. 变更声明

- 未修改任何产品代码、数据库、API、协议、固件或测试。
- 文档状态：**PROPOSED**。
