# Phase 08.2.2 — Security Test Plan（安全测试方案）

> 项目：QiYun / widscan58
> 阶段：Phase 08.2.2
> 性质：**测试方案设计。未新增或修改任何测试代码。**
> 日期：2026-11-05
> 状态：**PROPOSED（待人工审批）**

---

## 0. 分类口径

| 分类 | 含义 |
|---|---|
| **EXISTING** | 已有测试，可直接运行 |
| **NEW** | 需在实现认证/授权后新增 |
| **UNVERIFIABLE** | 当前代码不具备被测能力，无法验证 |
| **REAL-DEPLOY** | 必须在真实多机/网络环境执行，单测无法覆盖 |

执行方式标记：`AUTO`（自动化）/ `MANUAL`（人工）。

---

## 1. 既有测试资产

| 测试文件 | 覆盖 | 与安全的关系 |
|---|---|---|
| `site-server/tests/test_auth_hardening.py` | WS URL 脱敏、URL 凭证默认禁止、认证超时配置、迁移链 | 认证（WS-A） |
| `site-server/tests/test_phase07_authorization.py` | Owner/Observer 授权矩阵 | 授权（Plane M） |
| `site-server/tests/test_device_ownership.py` | 设备归属、迁移 | 授权（Plane M） |
| `site-server/tests/test_phase07_command_gateway.py` | 命令网关拒绝/授权 | 授权 + 命令 |
| `site-server/tests/test_phase08_platform_ui.py` | 静态托管、路由、无敏感文件 | UI 边界 |
| `gateway-agent/tests/test_command_api_security.py` | Token 认证、503/401、常量时间比较、诊断掩码 | 认证（Agent 本地） |
| `gateway-agent/tests/test_command_authorization.py` | 命令白名单与状态 | 输入约束 |
| `gateway-agent/tests/test_site_connector.py` | 连接状态、认证失败、队列 | 认证（WS-A） |

> 数量以 08.1/验收文档记录为准；本阶段未运行测试，不声称通过。

**覆盖缺口**：用户认证、用户授权、REST 认证、Site Scope、限流、CSRF、前端 WS——**全部没有测试**。

---

## 2. 认证测试

| 编号 | 用例 | 前置 | 步骤 | 预期 | 类型 | 方式 |
|---|---|---|---|---|---|---|
| AUTH-01 | 未登录访问受保护 REST | 认证已实现 | 无 Cookie/Token 请求 `/api/v1/devices` | 401 | NEW | AUTO |
| AUTH-02 | 无效 Token | 认证已实现 | `Authorization: Bearer invalid` | 401 | NEW | AUTO |
| AUTH-03 | 过期会话 | 认证已实现 | 构造 `expires_at` 过去的会话 | 401 | NEW | AUTO |
| AUTH-04 | 已撤销会话 | 认证已实现 | 登出后复用旧 Cookie | 401 | NEW | AUTO |
| AUTH-05 | Token 重放（跨 IP/UA） | 认证已实现 | 复制 Cookie 到另一客户端 | 按策略拒绝或告警 | NEW | AUTO |
| AUTH-06 | 用户退出后旧会话失效 | 认证已实现 | 登出 → 重放请求 | 401 | NEW | AUTO |
| AUTH-07 | 登录限流 | 认证已实现 | 连续 6 次错误口令 | 第 6 次 429/锁定 | NEW | AUTO |
| AUTH-08 | 口令哈希不可逆 | 认证已实现 | 检查 DB 字段 | 非明文、强哈希 | NEW | AUTO |
| AUTH-09 | Agent WS 错误凭证 | 现状 | 连接后发错误 credential | 4401 + 断开 | EXISTING | AUTO |
| AUTH-10 | Agent WS 超时未认证 | 现状 | 连接后不发 auth | 10 s 后关闭 | EXISTING | AUTO |
| AUTH-11 | Agent 本地 API 无 Token | 现状 | POST commands 无 Token | 401 或 503 | EXISTING | AUTO |
| AUTH-12 | Agent 读端点无认证（现状确认） | 现状 | GET `/api/v1/devices` 无 Token | **200**（记录为缺口） | EXISTING（负向确认） | AUTO |
| AUTH-13 | REST 无认证（现状确认） | 现状 | GET `/api/v1/devices` 无凭证 | **200**（记录为 CRITICAL） | EXISTING（负向确认） | AUTO |

> AUTH-12/13 是**记录当前风险**的测试，不是期望通过的安全测试；实现认证后应改为 401，并纳入回归。

---

## 3. 授权测试

| 编号 | 用例 | 前置 | 步骤 | 预期 | 类型 | 方式 |
|---|---|---|---|---|---|---|
| AZ-01 | Observer 调用写接口 | RBAC 已实现 | Observer 会话 PATCH device | 403 | NEW | AUTO |
| AZ-02 | Observer 发起命令 | RBAC + 命令链 | Observer POST command | 403 | NEW | AUTO |
| AZ-03 | Operator 删除设备 | RBAC | Operator DELETE device | 403 | NEW | AUTO |
| AZ-04 | Operator 迁移 Owner | RBAC | Operator transfer-owner | 403 | NEW | AUTO |
| AZ-05 | Site Admin 跨站访问 | RBAC | Admin(A) 请求 Site B device | 403 | NEW | AUTO |
| AZ-06 | System Admin 跨站访问 | RBAC | 请求 Site B | 200 + 审计 | NEW | AUTO |
| AZ-07 | 非成员访问任意 Site | RBAC | 用户请求未加入 Site | 403 | NEW | AUTO |
| AZ-08 | 客户端伪造 `site_id` | Scope 已实现 | `?site_id=other` | 403 | NEW | AUTO |
| AZ-09 | 设备端点跨站 ID 猜测 | Scope 已实现 | 读/改其他 Site device_id | 404/403 | NEW | AUTO |
| AZ-10 | 跨 Site Measurement 访问 | Scope 已实现 | 读其他 Site device measurements | 403 | NEW | AUTO |
| AZ-11 | Observer 执行 Owner 专属（Plane M） | 现状 | Observer Agent 请求命令 | 拒绝 + 审计 | EXISTING | AUTO |
| AZ-12 | 低置信度设备非读动作 | 现状 | 对 `identity_confidence=low` 设备命令 | 拒绝 | EXISTING | AUTO |
| AZ-13 | 角色变更后旧会话 | RBAC | 降级角色后复用会话 | 401/403 | NEW | AUTO |
| AZ-14 | 前端隐藏按钮不可绕过 | RBAC | 直接调用隐藏接口 | 403 | NEW | AUTO |
| AZ-15 | 自助配对码（现状确认） | 现状 | 无认证创建配对码 | **200**（记录 CRITICAL） | EXISTING（负向） | AUTO |

---

## 4. API 测试

| 编号 | 用例 | 预期 | 类型 | 方式 |
|---|---|---|---|---|
| API-01 | 未认证请求全部写端点 | 401 | NEW | AUTO |
| API-02 | 越权请求 | 403 | NEW | AUTO |
| API-03 | 参数篡改（`site_id`/`device_id`/`role`） | 拒绝或忽略 | NEW | AUTO |
| API-04 | 超大请求体（如 10 MB JSON） | 413 / 400 | NEW | AUTO |
| API-05 | 高频请求（如 1000 rps 短时） | 429，服务不崩溃 | NEW | AUTO |
| API-06 | 错误信息不泄漏堆栈/路径 | 无 `db_path`、无 traceback | NEW | AUTO |
| API-07 | `/docs`、`/openapi.json` 生产关闭 | 404 | NEW | AUTO |
| API-08 | 未知字段/类型错误 | 422，不泄漏内部字段 | NEW | AUTO |
| API-09 | SQL 注入尝试（参数化） | 不执行、不报错泄漏 | NEW | AUTO |
| API-10 | 分页越界/超大 limit | 被限制 | NEW | AUTO |
| API-11 | CORS 预检（若同源策略依赖） | 不允许任意 Origin | NEW | AUTO |
| API-12 | Host 头伪造 | 拒绝或仅允许白名单 | NEW | AUTO |

---

## 5. WebSocket 测试

### 5.1 WS-A（Agent）

| 编号 | 用例 | 预期 | 类型 | 方式 |
|---|---|---|---|---|
| WSA-T01 | 未认证连接发业务消息 | 4401/忽略 | EXISTING | AUTO |
| WSA-T02 | 认证超时 | 关闭 | EXISTING | AUTO |
| WSA-T03 | 凭证撤销后新连接 | 拒绝 | EXISTING | AUTO |
| WSA-T04 | **凭证撤销后活动连接** | **仍未断开（记录缺口）** | EXISTING（负向） | AUTO |
| WSA-T05 | 同 `agent_id` 双连接 | 记录覆盖行为 | NEW | AUTO |
| WSA-T06 | 超大消息 | 断开/拒绝 | NEW | AUTO |
| WSA-T07 | 高频消息 | 限流/断开 | NEW | AUTO |
| WSA-T08 | Site 缺失 Agent | 拒绝（非回落 site-001） | NEW | AUTO |
| WSA-T09 | 明文 `ws://` 跨主机 | 部署禁止（REAL-DEPLOY 检查） | REAL-DEPLOY | MANUAL |

### 5.2 WS-U（前端，未实现）

| 编号 | 用例 | 预期 | 类型 |
|---|---|---|---|
| WSU-T01 | 未认证连接 | 4401 | UNVERIFIABLE |
| WSU-T02 | 无效/过期票据 | 4401 | UNVERIFIABLE |
| WSU-T03 | 票据重复使用 | 4401 | UNVERIFIABLE |
| WSU-T04 | 跨 Site 订阅 | 4403 + 审计 | UNVERIFIABLE |
| WSU-T05 | 订阅数组含越权 device_id | 整条拒绝 | UNVERIFIABLE |
| WSU-T06 | 会话过期 | 4401 | UNVERIFIABLE |
| WSU-T07 | 权限变更 | 关闭/降级 | UNVERIFIABLE |
| WSU-T08 | 快照补齐越权 | 拒绝 | UNVERIFIABLE |
| WSU-T09 | 断线重连复用旧票据 | 拒绝 | UNVERIFIABLE |
| WSU-T10 | Origin 伪造（CSWSH） | 拒绝 | UNVERIFIABLE |
| WSU-T11 | 慢客户端 | 有界丢弃，不拖垮服务 | UNVERIFIABLE |

---

## 6. Agent 测试

| 编号 | 用例 | 预期 | 类型 | 方式 |
|---|---|---|---|---|
| AG-01 | 无凭证注册 | 拒绝 | NEW | AUTO |
| AG-02 | 错误凭证认证 | 4401 | EXISTING | AUTO |
| AG-03 | 凭证撤销后重连 | AUTH_FAILED，停止重连 | EXISTING | AUTO |
| AG-04 | 凭证过期 | 拒绝 | NEW | AUTO |
| AG-05 | 凭证轮换（双凭证宽限） | 新旧均可用，宽限后旧失效 | NEW | AUTO |
| AG-06 | Agent 身份伪造（复制配置） | **当前可冒用（记录缺口）** | EXISTING（负向） | AUTO |
| AG-07 | Agent 绑定错误 Site | 拒绝/审计 | NEW | AUTO |
| AG-08 | 上报未归属设备 | 按新策略拒绝或告警 | NEW | AUTO |
| AG-09 | 单 Agent 多 Site | 不允许 | NEW | AUTO |
| AG-10 | 配置文件权限 | Agent 侧 0600 | REAL-DEPLOY | MANUAL |
| AG-11 | 凭证出现在日志 | 检索日志无明文 | REAL-DEPLOY | MANUAL |
| AG-12 | 明文凭证落盘 | 记录现状（HIGH） | EXISTING（负向） | MANUAL |

---

## 7. 网络与部署测试（REAL-DEPLOY）

| 编号 | 用例 | 预期 |
|---|---|---|
| NET-01 | 同网段未认证主机访问 REST | 401（认证后） |
| NET-02 | 默认仅回环绑定 | 外部不可达 |
| NET-03 | 反向代理注入身份头 | 仅受信代理可用 |
| NET-04 | 公网误暴露检测 | 默认拒绝/明确告警 |
| NET-05 | Tailscale 场景 | 仍需认证授权 |
| NET-06 | 跨主机 Agent `wss://` | TLS 校验通过 |
| NET-07 | DB 文件权限 | 仅服务账户可读 |
| NET-08 | 备份加密 | 无明文凭证/数据 |

---

## 8. 自动化工具建议

| 工具 | 用途 |
|---|---|
| `pytest` + `httpx.AsyncClient` | Site Server REST 认证/授权回归 |
| `pytest` + `TestClient` WebSocket | WS-A/WS-U 握手与消息 |
| `Playwright` | Cookie 属性、CSRF、Origin、XSS 回归 |
| `OWASP ZAP` baseline | 被动扫描（NEW 阶段） |
| `pip-audit` / `npm audit` | 依赖漏洞 |
| 自写脚本 | 限流、超大请求、并发连接 |

**约束**：不得为了让测试通过而修改产品代码或放宽断言。

---

## 9. 验收标准

1. 所有 CRITICAL/HIGH 威胁至少有一条对应测试（T-01/T-19 → AUTH/AZ/API；T-08/T-09 → AG；T-11 → AG-08；T-13 → NET-07）。
2. 认证类测试 **100% 失败关闭**（未认证不得返回业务数据）。
3. 授权类测试覆盖四个角色的允许与拒绝。
4. Site Scope 测试必须包含“客户端伪造 site_id”和“跨站资源 ID 猜测”。
5. WS 测试必须在认证与授权实现后才可标记通过；当前 WS-U 全部为 UNVERIFIABLE。
6. REAL-DEPLOY 用例必须在真实多机环境执行并留痕。
7. 任何负向确认用例（记录当前缺口）在修复后必须转为正向回归。
8. 测试报告必须区分“通过 / 失败 / 未验证”，不得把未验证计为通过。

---

## 10. 变更声明

- 未新增或修改任何测试代码。
- 文档状态：**PROPOSED**。
