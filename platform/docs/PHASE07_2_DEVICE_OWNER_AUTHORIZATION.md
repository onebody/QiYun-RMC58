# Phase 07.2 — Device Owner 命令授权与 Observer 只读隔离

> 状态：PASS（授权实现完成并验证；远程命令分发仍不存在，属于已知能力缺口）
> 日期：2026-11-04

## 1. 原始问题（B-2）

`gateway-agent/src/widscan_agent/site_connector.py::_handle_command_request()`
只检查 `is_command_allowed()` 白名单和本地 DeviceManager 是否存在该 Device，
没有校验命令请求者与 Device Owner 的关系。Site Server 也没有完整的主动命令下发端点，
但缺少可复用的授权服务，未来一旦增加命令路由就可能留下越权路径。

## 2. 根因

- 身份（已认证 Agent）没有参与命令授权判定。
- 设备归属（`Device.owner_agent_id`）只在注册/迁移流程使用，命令链路未使用。
- 没有统一的动作级权限矩阵，也没有"Observer 只读"的服务端拦截点。
- Agent 侧信任了命令消息内可能携带的 Agent ID 字段（未校验）。

## 3. 修改文件

| 文件 | 作用 |
|---|---|
| `site-server/src/widscan_site/devices/authorization.py` | 新增授权服务（角色解析 + 动作权限矩阵 + 命令/读取便捷函数） |
| `site-server/src/widscan_site/agents/connection.py` | WS 收到 `command.request` 时先授权；使用已认证 `agent_id`，忽略消息自报身份 |
| `gateway-agent/src/widscan_agent/site_connector.py` | Agent 执行命令前校验 `target_agent_id`/`agent_id` 断言与自身身份一致，并保留 DeviceManager 归属边界；新增 `commands_unauthorized` 计数 |
| `site-server/tests/test_phase07_authorization.py` | 新增 17 个权限矩阵测试 |
| `site-server/tests/test_phase07_command_gateway.py` | 新增 6 个命令入口拦截测试 |
| `site-server/tests/test_phase07_integration.py` | 真实 WebSocket 下的 Owner / Observer 命令路径测试 |
| `gateway-agent/tests/test_command_authorization.py` | 新增 6 个 Agent 侧归属断言测试 |

## 4. 权限模型

`devices/authorization.py`：

```python
ROLE_OWNER / ROLE_OBSERVER / ROLE_SITE_ADMIN / ROLE_UNAUTHORIZED

_ALLOWED_ROLES = {
  "read":           {owner, observer, site_admin},
  "command":        {owner, site_admin},
  "configure":      {owner, site_admin},
  "transfer_owner": {site_admin},
  "delete":         {site_admin},
}
```

角色解析规则（**只读服务端记录**）：

1. Device 不存在或 `device.site_id != site_id` → `unauthorized`（`device_not_found`）。
2. Agent 不存在或 `Agent.status == "disabled"` → `unauthorized`。
3. `Device.owner_agent_id == 已认证 agent_id` → `owner`。
4. 无 Owner（`NULL` / unassigned）或其他已认证 Agent → `observer`。
5. Device 状态 ∈ `pending/offline/error/disabled/identity_conflict` → 命令/配置拒绝，原因 `device_unavailable:<status>`（读取仍允许）。
6. `identity_confidence == "low"` → 只允许读取，管理动作拒绝（`low_confidence_device`）。

`ACTOR_SITE_ADMIN` 只能由服务端可信调用方（管理 REST / 内部服务）传入；
WS 消息中的 `owner_agent_id` / `requester_agent_id` **不参与**角色解析。

## 5. 授权检查位置

| 位置 | 实现 |
|---|---|
| Site Server WS handler | `connection.py::_handle_inbound_command()` — 收到 `command.request`/`command` 先调用 `authorize_device_command`，拒绝时返回 `command.response {ok:false, reply:"denied: <reason>"}` |
| Site Server Command dispatcher | 不存在（当前无远程命令分发）。授权服务已就绪，未来入口必须调用 |
| Gateway Agent Site Connector | `_handle_command_request()` 校验消息内 Agent 断言 + DeviceManager 归属，再走白名单与执行 |
| DeviceManager 最终执行入口 | Agent 侧只有 DeviceManager 内的 Device 可执行（`manager.get(device_id)`），越界 device 直接 `REJECTED` |

Site Server 当前**没有**主动命令下发 REST 端点（`main.py` 17 条路由中无命令路由），
本轮未新增完整远程命令系统，只实现拦截与可复用授权服务。

## 6. 关键行为

- Owner Agent 命令：授权通过 → 返回 `remote command dispatch not implemented`（明确未实现，不执行设备操作）。
- Observer Agent 命令：`denied: observer_read_only`。
- 未注册/禁用 Agent：`denied: agent_unknown_or_disabled`。
- 跨 Site / 不存在 Device：`denied: device_not_found`。
- 消息伪造 `owner_agent_id`：服务端忽略，仍按真实 Device 记录判定。
- Owner 迁移后：旧 Owner 立即降级为 Observer（角色实时从数据库计算），新 Owner 可授权。
- Agent 侧伪造 `target_agent_id`/`agent_id`：`CommandState.REJECTED`，`commands_unauthorized += 1`。

## 7. 测试

命令：

```
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_authorization.py tests/test_phase07_command_gateway.py -q
cd site-server && PYTHONPATH=src python3 -m pytest tests/test_phase07_integration.py -q
cd gateway-agent && PYTHONPATH=src python3 -m pytest tests/test_command_authorization.py -q
```

覆盖：

- Owner 执行允许的命令；Observer 执行设备命令被拒绝。
- 未授权 Agent 被拒绝；Disabled Agent 被拒绝。
- Agent A 不能操作 Agent B 的 Owner Device。
- 消息伪造 `owner_agent_id` / `requester_agent_id` 被忽略。
- 不存在 / 跨 Site device_id 被拒绝。
- Device 离线时 Owner 命令被拒绝（原因明确），读取仍允许。
- Owner 迁移后旧 Owner 权限失效。
- Owner 不能自行迁移/删除设备（仅 Site Admin）。
- 低置信度设备不能被管理。
- 命令白名单不能被绕过（Agent 侧 `ERASE_FLASH` 拒绝）。
- 真实 WebSocket：Owner 得到 "not implemented"，Observer 得到 `denied: observer_read_only`，Observer 抢不走 Owner。

## 8. 已知限制

- **没有完整的远程命令下发能力**：Site Server 授权通过后不会转发到 Agent；这是本轮明确不扩展的范围。
- Site Server 侧没有命令白名单（白名单只在 Gateway Agent 内）；未来实现远程下发时必须在 Agent 侧复核 `is_command_allowed`。
- `ROLE_SITE_ADMIN` 依赖服务端调用方自证身份；当前只有管理 REST 场景，后续需接入管理员认证。
- 设备离线状态由 "Agent 断线 → 设备 offline" 驱动，命令策略为拒绝；如需离线排队命令需另行设计。

## 9. 未解决问题

- 远程命令下发 + 审计链路（下一阶段）。
- Site Server 命令入口的速率限制 / 审计落库。
