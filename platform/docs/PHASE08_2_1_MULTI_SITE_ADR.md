# Phase 08.2.1 — ADR-08.2.1-01：多站点裁定

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.1 — 实际代码审计结果补充约束
> 状态：**PROPOSED（待人工审批）**
> 日期：2026-11-05
> 约束：不修改业务代码、不修改数据库、不修改 API、不修改协议
> 依据：`docs/PHASE08_2_PLATFORM_INFORMATION_ARCHITECTURE.md`、Phase 07 验收文档、`site-server` 实际代码

## 0. 证据等级定义

| 等级 | 含义 |
|---|---|
| **L1** | 运行实测（08.5 / 08.6 的真实进程、真实日志、真实截图） |
| **L2** | 静态代码事实（可直接指认文件/行号，无需运行） |
| **L3** | 既有文档记录（Phase 07 / 08.1 等，本轮未复测） |
| **L4** | 推演/估算（基于 L1–L3，无运行证据） |

## 1. 引用事实与证据等级

> 完整 16 项事实见 `docs/PHASE08_2_1_CROSS_ARCHITECTURE_REVIEW.md` §2（F-01–F-16）；本 ADR 引用与多站点直接相关的条目。

| 事实 | 内容 | 等级 | 证据 |
|---|---|---|---|
| F-01 | 单站点事实，无 `GET /api/v1/sites` | **L1**（并 L2） | 08.5 `curl` 实测 404；`main.py` 仅 `GET /api/v1/site` |
| F-13 | Site/Agent/Device/Measurement 多站点查询能力不完整 | **L2** | `main.py` 各列表走 `site_id or cfg.site.id`，无跨站点聚合 |
| F-14 | 前端只读是功能约束，非后端授权 | **L2** | 平台无写 UI；后端写端点仍开放 |
| F-09 | Site Server REST 无鉴权 | **L1**（并 L2） | 08.5 无凭证 `curl` 成功；`main.py` 无 `Depends` |
| F-15 | Phase 07 四项 PASS，整体 CONDITIONAL PASS | **L3** | `docs/PHASE07_PRODUCTION_READINESS_ACCEPTANCE.md` |

## 2. 三个层次（必须分开）

### 2.1 当前单站点运行事实（L1 / L2）

- 配置：`cfg.site.id` 唯一，启动时 `bootstrap_default_site` 写入/补齐一条 Site。
- API：`GET /api/v1/site`、`GET /api/v1/agents`、`GET /api/v1/devices` 等均接受可选 `site_id` 查询参数，缺省取 `cfg.site.id`。
- **没有** `GET /api/v1/sites`；没有 Site 创建/删除端点。
- 前端没有 Site 切换器；Overview/Sites 视为单实例。
- REST 无鉴权，因此当前即使传入任意 `site_id`，只要库中存在该 Site 就会返回对应数据（**当前库中只有 1 条**）。

### 2.2 未来多站点目标架构（设计，未实现）

```text
Platform Shell（全局）
  ├── Site Switcher（受授权约束，禁止 Mock）
  ├── Overview（按 Site Scope 聚合）
  └── Sites 管理（列表/创建/详情）

Site Server
  ├── Site CRUD（受 System Admin 权限约束）
  ├── Agent 配对 → 绑定 Site
  ├── Device 归属 → 绑定 Site（或 Site 内共享）
  ├── Measurement 写入 → 带 site_id
  └── 所有查询强制 Site Scope 过滤（由后端注入，不信任前端参数）

Gateway Agent
  └── 不感知 Site；Site 归属由配对凭证决定
```

### 2.3 单站点 → 多站点实施路径（PROPOSED，未执行）

| 步骤 | 内容 | 前置 | 产物 |
|---|---|---|---|
| S1 | Security Architecture Review（08.2.2） | 无 | 认证/授权/Site Scope 设计 |
| S2 | Site CRUD + `GET /api/v1/sites`（后端） | S1 | 多 Site 管理 API |
| S3 | REST 查询强制 Site Scope | S1 | 统一 scope 依赖/查询封装 |
| S4 | Agent 配对绑定 Site、Device 归属规则与变更审计 | S2 | 归属可追溯 |
| S5 | 旧数据归属核验（无 NULL；导入库需回填 + 审计） | S2 | 归属报告 |
| S6 | 前端 Site 切换器（仅在后端就绪后） | S2–S5 | `?site=` 驱动 |
| S7 | 前端多站点验收（无 Mock） | S6 | 真实多 Site 截图 |

> 本阶段（08.2.1）**不执行** S1–S7。当前前端不得出现任何 Mock 多站点数据或假切换器。

## 3. 必答问题（Q1–Q10）

### Q1. 当前数据库是否具备多 Site 数据归属基础？

**答：具备基础，但不是完整多租户模型。**（L2）

| 表 | site_id | 约束 | 证据 |
|---|---|---|---|
| `sites` | 主键 `id` | — | `models.py:57-71` |
| `agents` | `NOT NULL`，索引 | `ix_agents_site_status` | `models.py:82,104` |
| `devices` | `NOT NULL`，索引 | `ix_devices_site_status` | `models.py:137,164` |
| `measurements` | `NOT NULL`，索引 | `uq_measurement_event(site_id,event_id)` | `models.py:254,274` |
| `device_profiles` | `NOT NULL` | — | `models.py:226` |
| `audit_logs` | `NOT NULL` | — | `models.py:291` |
| `site_events` | `NOT NULL` | — | `models.py:308` |

结论：

- 归属字段与索引**已经存在**，`event_id` 幂等键**已按 Site 隔离**。
- 缺口不在“字段”，而在：Site 生命周期 API、查询层强制 Scope、授权、Device 跨 Site 规则。

### Q2. 如果没有，最小必要的数据模型改造是什么？

**答：核心表无需新增 `site_id`；最小改造取决于“设备是否可同时属于多个 Site”这一产品规则。**（L2 + L4）

| 产品规则 | 数据模型改造 | 迁移风险 |
|---|---|---|
| A. 一台 Device 同一时刻只属于一个 Site | **无需改表**；新增 `transfer-site` 操作 + `AuditLog`；`devices.id` 保持全局主键 | 低 |
| B. 同一 Device 可在多个 Site 复用 | 需新增 `device_site_memberships(site_id, device_id, ...)` 或改为复合主键；所有 FK、`uq_measurement_event`、查询层需同步改造 | **高**（涉及冻结协议与数据迁移） |

**PROPOSED**：先采用 A，避免冻结协议变更；B 仅在明确产品需求后单独立项。

其他最小补齐（非表结构）：

- Site 生命周期接口（创建/停用）。
- 统一 `site_scope` 依赖（后端强制过滤）。
- Device 归属变更审计（已有 `audit_logs` 表，补写入）。

### Q3. Agent、Device、Measurement 如何关联 Site？

**答：**（L2）

```text
Site 1 ──< Agent (agent.site_id) ──< DeviceEndpoint / ConnectionSession
  │
  ├──< Device (device.site_id, NOT NULL)
  │        └──< Measurement (measurement.site_id, device_id, agent_id, device_boot_id)
  │
  └──< SiteEvent / AuditLog / DeviceProfile
```

- Agent 的 Site 由**配对凭证**决定：`WS auth` 校验凭证后读取 `agent.site_id`（`agents/connection.py:191-201`）。
- Device 的 Site 由接入 Agent 上报时确定，写库时带 `site_id`。
- Measurement 写入时同时记录 `site_id`、`device_id`、`agent_id`、`device_boot_id`（`measurements.persist_measurement`）。
- Measurement 的 Site 是**冗余归属**（denormalized），用于高效 Scope 查询与幂等键。

### Q4. 历史数据如何确定 Site 归属？

**答：**（L1 / L2）

- 现有生产库由单实例写入：每条 Measurement 在写入时就带 `site_id = cfg.site.id`，**不存在 NULL**。
- 因此历史归属**已确定且精确**，无需“事后猜测”。
- 若将旧单站点库导入新多站点实例：import 脚本必须显式指定目标 `site.id`，并在 `audit_logs` 记录 batch 元数据（来源库、目标 Site、行数、时间）。

### Q5. 无法确定历史归属的数据如何处理？

**答：**（L2 + L4）

当前 schema `measurements.site_id NOT NULL`，因此**不会出现无归属行**。若未来导入来源缺少归属：

1. 不静默分配到默认 Site。
2. 进入隔离区：`measurements_quarantine`（或临时表 + `site_id` 可空 + `attribution_status`），只读、不参与统计。
3. 由人工/运维按来源批次确认后回填，并写审计。
4. 未确认前，平台统计必须排除隔离区。

### Q6. 多 Site 数据隔离由哪个后端层保证？

**答：由 Site Server 的查询/授权层保证，前端不承担隔离职责。**（L2 + L4）

- 认证层：确定 principal（用户/服务）。
- 授权层：principal → 可访问 Site 集合（Site Scope）。
- 查询层：所有列表/详情/聚合必须注入 `site_id in allowed_sites`；详情按 `(site_id, id)` 双条件查询。
- 序列化层：返回体包含 `site_id` 供前端展示，但不可作为授权依据。
- 审计层：跨 Site 访问拒绝需记录。

现状：以上四层**均未实现**（REST 无鉴权、无 Scope、查询可接受任意 `site_id`）。

### Q7. 全局查询如何执行权限过滤？

**答：**（L4，PROPOSED）

```text
Request → AuthN → principal
       → ScopeResolver(principal) → allowed_site_ids
       → QueryBuilder(required site_id ∈ allowed_site_ids)
       → 否则 403（不返回“空列表”掩盖越权）
```

- 全局 Overview 只能聚合 `allowed_site_ids`。
- 客户端传入的 `site_id` 必须与 Scope 求交；不在范围内 → `403`（记录审计），不得静默回退到默认 Site。
- 禁止“先查全库再前端过滤”。

### Q8. 多站点是否需要独立迁移阶段？

**答：需要，且必须先于 UI 切换器。**（L4，PROPOSED）

理由：

1. 涉及认证/授权/Site Scope，不能与 UI 设计混做。
2. 若采用规则 B（设备跨 Site 共享），涉及 schema 迁移与冻结协议，需单独评审。
3. 需要旧 Agent 兼容性验证与旧库归属核验。

建议顺序：**08.2.2 安全架构评审 → 多站点后端基础阶段 → UI 切换器（08.3/08.4 之后的新阶段）**。

### Q9. 是否需要兼容旧版 Agent？

**答：需要，且当前协议天然兼容。**（L2）

- Agent 不上报 `site_id`；Site Server 通过配对凭证确定 `agent.site_id`。
- 因此旧 Agent 无需改协议，只需在正确的 Site 下完成配对。
- 风险点：同一 Agent 被错误配对到其他 Site 时，其 Device/Measurement 归属会整体迁移；必须禁止“改绑”或要求显式 `transfer-site` + 审计。

### Q10. 是否影响现有单机 Console？

**答：不影响。**（L1）

- `/app/` Console 与 Site Server 无数据依赖，也不读取 site 配置。
- 多站点改造只发生在平台平面；设备平面保持独立。
- **冻结约束**：不得为多站点在 Console 中注入 Site 选择或平台 token。

## 4. 决策

| 项 | 决策 | 状态 |
|---|---|---|
| 当前是否实现多站点 | 否；保持单站点事实 | **PROPOSED** |
| 是否在前端做 Mock 多站点 | 禁止 | **PROPOSED（硬约束）** |
| 是否现在做 Site 切换器 | 否 | **PROPOSED** |
| 核心表是否新增 site_id | 不需要（已存在） | **PROPOSED** |
| Device 跨 Site 规则 | 先按“单 Site 归属 + 显式迁移” | **PROPOSED** |
| 多站点是否独立阶段 | 是，安全评审之后 | **PROPOSED** |
| 旧 Agent 兼容 | 是，通过凭证绑定 Site | **PROPOSED** |

## 5. 影响范围与风险

**影响范围**

- 后端：`main.py` 的 site 相关路由、查询函数、配对流程、授权中间件（未来）。
- 数据库：短期无变更；规则 B 时才需迁移。
- 前端：Sites 页、Overview 卡、导航（未来增加切换器）。
- Console：无影响（冻结）。

**风险**

| 风险 | 等级 | 说明 |
|---|---|---|
| 无鉴权下先做多站点 = 跨 Site 数据泄漏 | **高** | 必须先完成 08.2.2 |
| `devices.id` 全局主键限制跨 Site 共享 | 中 | 产品规则需先定 |
| 旧 Agent 错误改绑导致归属迁移 | 中 | 需要配对/改绑审计 |
| 前端假切换器误导运维 | 高 | 明确禁止 |
| 统计/幂等键未按 Site 校验 | 中 | 已有 `uq(site_id,event_id)`，查询需同步 |

## 6. 本阶段结论

- 多站点**当前不具备可用能力**，但数据库归属基础**已具备**。
- 最小路径是“先安全、后 API、再 UI”，不涉及本次 UI 设计与代码修改。
- 本 ADR 保持 **PROPOSED**，等待人工审批；未执行任何代码/数据库/API/协议变更。
