# Phase 08.2.1 — ADR-08.2.1-03：权限与安全裁定

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.1 — 实际代码审计结果补充约束
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> 约束：不修改业务代码、不修改数据库、不修改 API、不修改协议
> 依据：`site-server` 路由与中间件、`gateway-agent` 本地 API、Phase 07.1/07.2 文档、08.5 实测

## 0. 证据等级定义

| 等级 | 含义 |
|---|---|
| **L1** | 运行实测（08.5 / 08.6） |
| **L2** | 静态代码事实（可指认文件/行号） |
| **L3** | 既有文档记录（本轮未复测） |
| **L4** | 推演/估算 |

## 1. 引用事实与证据等级

> 完整 16 项事实见 `docs/PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md` §2（F-01–F-16）；本 ADR 引用与权限/安全直接相关的条目。

| 事实 | 内容 | 等级 | 证据 |
|---|---|---|---|
| F-08 | 平台没有完整后端 RBAC | **L2** | `main.py` 无认证/授权依赖，无角色模型 |
| F-09 | Site Server REST 无鉴权 | **L1**（并 L2） | 08.5 无凭证 `curl` 可读写；`main.py` 路由无 `Depends` |
| F-10 | 无 CORS 配置 | **L2** | 全仓无 `CORSMiddleware` |
| F-11 | Agent 本地命令 API 不允许映射到平台 | **L3**（并 L2） | Phase 07.1 结论；Agent 本地 API 独立鉴权 |
| F-14 | 前端只读是功能约束，非后端授权 | **L2** | 平台无写 UI；后端写端点仍开放 |
| F-15 | Phase 07 四项 PASS，整体 CONDITIONAL | **L3** | Phase 07 验收文档 |
| F-01 | 单站点、无 `/api/v1/sites` | **L1** | 08.5 404 |
| F-06 | 无前端 `/ws/v1/site` | **L1** | 08.5 404 |
| F-13 | 多站点查询能力不完整 | **L2** | 列表仅按单 Site 查询 |

## 2. 现状：能力矩阵（不做美化）

| 安全层次 | 现状 | 等级 | 说明 |
|---|---|---|---|
| 内网部署限制 | 唯一现有缓解 | L1 | 服务默认无认证，只能靠网络隔离 |
| 身份认证（AuthN） | **无** | L1/L2 | 无登录、无 token、无 session |
| API 授权（AuthZ） | **无** | L2 | 读写端点均开放 |
| CORS | **未配置** | L2 | 不是安全能力（见 §4） |
| CSRF | **不适用**（当前无 Cookie Session） | L2 | 未来若引入 Cookie 必须补 |
| WebSocket 授权（Agent 上行） | 有：首条 `auth` + credential + 可吊销 | L2 | `agents/connection.py:151-199` |
| WebSocket 授权（前端订阅） | **无**（端点不存在） | L1 | `/ws/v1/site` 未实现 |
| Site Scope | **无** | L2 | 查询接受任意 `site_id` |
| 前端页面权限 | **无**（只读是 UI 约束） | L2 | 无角色、无隐藏策略（也不应靠隐藏） |

> **红线**：不得把“前端只读”写成“后端已实现只读授权”；不得把“无 CORS”当成安全措施。

## 3. 必答问题（Q1–Q10）

### Q1. 当前系统是否适合生产部署？

**不适合直接暴露到任何非可信网络。**（L1 + L2）

- 任何能访问端口的人都可以读全部 Site/Agent/Device/Measurement 数据。
- 写端点（配对、吊销、adopt、patch、delete、transfer-owner）同样无鉴权。
- WebSocket Agent 接入虽有凭证，但 REST 侧无认证，攻击者可读取结果。

**可接受的生产形态（临时）**：隔离内网 + 网络 ACL + 仅运维终端可达 + 不映射公网 + 关闭不必要的写端点访问。

### Q2. 可信内网是否足以作为临时风险缓解措施？

**只能作为临时缓解，不能作为安全控制。**（L1 + L4）

成立条件（必须全部满足并由运维书面确认）：

1. 服务端口不暴露到公网/办公网。
2. 仅受控运维网段可访问。
3. 无不可信终端接入。
4. 有访问日志/网络审计。
5. 明确“当前无身份认证”的风险接受人。

任一条件不满足，即视为高风险部署。

### Q3. 是否需要在 Phase 08.3 前先完成认证设计？

**需要“设计”，不需要“实现”。**（L4，PROPOSED）

- 08.3 是 UI 设计系统阶段，可以进行视觉与交互设计。
- 但认证/授权方案必须在 08.3 之前定稿，否则：
  - 无法设计登录态、角色显示、无权限态；
  - 无法确定写操作 UI 是否可能；
  - 08.4/后续集成可能返工。
- **禁止**在 08.3 中设计任何“假登录/假角色切换”。

### Q4. 是否需要独立的 Security Hardening 阶段？

**需要。**（L4，PROPOSED）

建议立项 **Phase 08.2.2 Security Architecture Review / Hardening**，范围：

1. AuthN 方案（Token / mTLS / SSO）。
2. AuthZ + RBAC + Site Scope 统一模型。
3. CORS/CSRF 策略。
4. WebSocket 授权（Agent 与未来前端）。
5. 审计日志与最小权限。
6. 部署边界（内网/反代/TLS）。
7. 旧数据与凭证迁移。

该阶段必须先于任何写 UI、任何多站点 UI、任何公网暴露。

### Q5. RBAC 与 Site Scope 是否必须同步设计？

**必须同步。**（L4，PROPOSED）

- 只有“角色”没有“范围”，Site Admin 可跨 Site 越权。
- 只有“范围”没有“角色”，无法区分只读/运维/管理。
- 统一模型：`(role, allowed_site_ids)`，所有查询/命令按二者求交。
- 建议角色四类：Read Only / Operator / Site Admin / System Admin；范围分别为：
  - Read Only：指定 Site 集（只读）
  - Operator：指定 Site 集（设备运维）
  - Site Admin：单 Site 管理
  - System Admin：全局

### Q6. Owner / Observer 与平台角色如何隔离？

**两个平面，禁止混用。**（L2 + L4）

| 概念 | 所在平面 | 作用对象 | 执行点 |
|---|---|---|---|
| Owner / Observer | **Agent 本地** | 单台 Device 的命令授权 | Gateway Agent 本地 API |
| 平台角色（RBAC） | **平台** | Site/资源/页面 | Site Server（未来） |

- 平台角色**不得**直接推导为 Device owner。
- Device owner 是设备归属事实，不是平台登录身份。
- 未来若平台需要“以某用户身份下发命令”，必须由 Site Server 签发、Agent 校验，且属于独立协议设计（当前禁止）。

### Q7. 未来命令授权在哪里执行？

**在 Gateway Agent 本地 API 执行，平台只做“是否允许发起”的上层授权。**（L2 + L4，PROPOSED）

```text
浏览器/平台 → Site Server（检查平台角色 + Site Scope）
            → 签发短期、最小权限命令票据（未来设计）
            → Agent 本地 API（校验票据 + Device owner/observer）
            → 设备
```

- 当前：**平台完全不调用 Agent 命令 API**（F-11）。
- 禁止：把 `X-Agent-Token` 下发浏览器、把 Agent 命令端点代理成公网接口。
- 若未来代理：必须服务端持有凭据、双向认证、审计、限流、最小权限。

### Q8. 浏览器是否可能直接访问 Agent 本地 API？

**技术上可能（同机/可达网络），当前必须视为风险并加固。**（L2）

- Agent 默认绑定 `127.0.0.1`；仅当 `require_auth=True` 且配置 token 时才允许外部绑定（`main.py:50-56`）。
- 本地 API 要求 `X-Agent-Token`/Bearer；但：
  - 若 token 泄露或页面被注入，同机浏览器可直接调用。
  - 若误配为 `0.0.0.0` 且 token 弱，风险扩大（含 DNS rebinding）。
- **约束**：
  1. 平台代码不得包含/传输 Agent token。
  2. 不得在平台页面发起对 Agent 端口的请求。
  3. 部署检查要求 Agent 保持回环绑定；外部绑定需安全评审。
  4. 未来建议 Agent 增加 Origin 校验与本地 UI 授权提示。

### Q9. 如何防止跨 Site 数据泄漏？

**仅靠后端强制 Scope 过滤。**（L4，PROPOSED）

1. principal → `allowed_site_ids`。
2. 所有查询注入 `site_id ∈ allowed_site_ids`；详情按 `(site_id,id)`。
3. 客户端 `site_id` 与 Scope 求交；越权返回 **403**（不得静默改为默认 Site）。
4. 聚合统计同样受限。
5. WebSocket 订阅按 Scope 过滤，越权关闭连接。
6. 审计拒绝事件。
7. 禁止前端过滤作为隔离手段。

### Q10. 未授权 WebSocket 订阅如何拒绝？

**认证前不订阅；认证后按 Scope 校验。**（L4，PROPOSED）

- 连接后必须在超时内发送 `auth`；否则 `4401` 关闭。
- `subscribe` 消息中的 `site_id` 必须 ∈ Scope；否则 `4403` 关闭并审计。
- 不接受匿名订阅、不接受 URL 传 token、不接受客户端自定义 SQL/过滤表达式。
- 连接数与订阅数限流；慢客户端不缓存无限增量。

## 4. 明确的安全反模式（禁止）

| 反模式 | 原因 |
|---|---|
| 用前端隐藏按钮当权限 | 可篡改，必须后端校验 |
| 用“无 CORS”当安全措施 | 非浏览器客户端不受 CORS 约束 |
| 把 Agent token 放前端配置 | 凭证泄露 |
| 把 Agent 命令 API 映射为公网前端 API | 形成未授权控制面 |
| 用前端参数 `role=`/`site=` 提权 | 必须服务端解析身份 |
| 多站点先于认证上线 | 直接造成跨 Site 泄漏 |
| 用假登录 UI 表示权限 | 误导使用者 |

## 5. 决策汇总

| 项 | 决策 | 状态 |
|---|---|---|
| 当前是否可生产暴露 | 否 | **PROPOSED** |
| 内网隔离作为临时缓解 | 有条件接受 | **PROPOSED** |
| 08.3 前完成认证设计 | 是（设计，不实现） | **PROPOSED** |
| 独立 Security Hardening 阶段 | 是（08.2.2） | **PROPOSED** |
| RBAC + Site Scope 同步设计 | 是 | **PROPOSED** |
| Owner/Observer 与平台角色隔离 | 是（两平面） | **PROPOSED** |
| 平台调用 Agent 命令 | 禁止（现状保持） | **PROPOSED（硬约束）** |
| 前端只在后端就绪后加写 UI | 是 | **PROPOSED** |

## 6. 影响与风险

| 风险 | 等级 | 说明 |
|---|---|---|
| REST 无鉴权导致全量数据暴露 | **高** | 任何可达客户端 |
| 写端点无鉴权导致数据被篡改/删除 | **高** | adopt/patch/delete/transfer-owner |
| 无 CORS 被误解为安全 | 中 | 文档与培训需纠正 |
| Agent token 泄露/误配外部绑定 | 高 | 命令面暴露 |
| 多站点先于授权上线 | **高** | 跨 Site 泄漏 |
| 前端假权限 | 中 | 误导与绕过 |

## 7. 本阶段结论

- 当前安全状态：**仅适用于隔离内网、只读运维、短期过渡**。
- 前端只读是**产品约束**，不是后端授权能力。
- 必须先完成 **08.2.2 安全架构评审**，再进行多站点/写操作/公网部署。
- 本 ADR 保持 **PROPOSED**，本轮未修改任何代码、API、数据库或协议。
