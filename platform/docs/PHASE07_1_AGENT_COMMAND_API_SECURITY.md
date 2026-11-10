# Phase 07.1 — Gateway Agent 本地命令 REST API 安全加固

> 状态：PASS
> 日期：2026-11-04
> 范围：只加固 Gateway Agent 本地 REST 命令入口，不改 Gateway Agent 架构、不改 ESP32-C5 协议。

## 1. 原始问题（B-1）

`gateway-agent/src/widscan_agent/api/__init__.py` 的 `POST /api/v1/devices/{device_id}/commands`
没有任何身份认证，只依赖监听地址 `127.0.0.1`。任何能访问本机端口的进程都可以直接执行设备命令。

## 2. 根因

- `build_app()` 未接收任何认证配置，路由函数也没有 `Depends` 身份校验。
- `main.run()` 仅做 "非本地地址回退 127.0.0.1" 的硬限制，没有认证概念；一旦用户改监听地址，权限模型仍是"默认放行"。
- 原有命令白名单 `is_command_allowed()` 只校验命令名，不校验调用者身份。

## 3. 修改文件

| 文件 | 作用 |
|---|---|
| `gateway-agent/src/widscan_agent/api/__init__.py` | 新增本地 API Token 认证依赖，保护全部会触发设备操作/Connector 变更的 POST 端点 |
| `gateway-agent/src/widscan_agent/config.py` | `ApiConfig` 新增 `require_auth` / `api_token`；支持 `GATEWAY_API_TOKEN`、`GATEWAY_API_REQUIRE_AUTH` 环境变量；新增 `resolve_bind_host()` |
| `gateway-agent/src/widscan_agent/main.py` | 使用 `resolve_bind_host()` 与认证配置构建 app |
| `gateway-agent/config/agent.example.yaml` | 生产示例明确 `require_auth: true`，Token 由环境变量注入 |
| `gateway-agent/tests/test_command_api_security.py` | 新增 16 个认证/监听/脱敏测试 |
| `gateway-agent/tests/test_config.py` | 新增 5 个 Token/环境变量默认值测试 |
| `gateway-agent/tests/test_api.py` | 测试夹具改为携带合法 Token（保持原有 9 个用例语义） |

## 4. 具体实现

### 4.1 认证依赖

`api/__init__.py::build_app(..., api_token="", require_auth=True)` 内定义：

```python
async def require_command_auth(request: Request) -> None:
    if not auth_required:
        return
    if not token_configured:
        raise HTTPException(503, "command API disabled: no API token configured")
    supplied = extract_api_token(request)
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
```

- 凭据来源：`Authorization: Bearer <token>` 或 `X-API-Token: <token>`。
- 比较使用 `hmac.compare_digest`（恒定时间）。
- Token 未配置 → 503（**拒绝服务，而不是放开**）。
- 认证失败 → 401；日志只记录客户端地址与路径，**不记录 Token**。
- `api/__init__.py::mask_secret()` 与 `/api/v1/diagnostics` 的 `command_api_security` 只暴露 `require_auth` / `token_configured` / `token: "***"`。

### 4.2 受保护端点

以下端点全部使用 `dependencies=[Depends(require_command_auth)]`：

- `POST /api/v1/devices/{device_id}/connect`
- `POST /api/v1/devices/{device_id}/disconnect`
- `POST /api/v1/devices/{device_id}/reconnect`
- `POST /api/v1/devices/{device_id}/commands`
- `POST /api/v1/site-connector/enable`
- `POST /api/v1/site-connector/disable`
- `POST /api/v1/site-connector/pair`

只读端点（health / devices 列表 / 单个设备 / diagnostics / WS 事件流）保持原有行为；它们不触发设备操作，也不暴露 Token。

### 4.3 白名单没有被绕过

认证依赖只做身份边界。路由函数内部仍执行原有：

```python
if not is_command_allowed(req.command):
    raise HTTPException(403, f"command not allowed: {req.command}")
```

`test_auth_does_not_bypass_command_whitelist` 使用合法 Token 请求 `ERASE_FLASH`，得到 403。

### 4.4 监听地址

`config.py::resolve_bind_host(requested, *, auth_required, token_configured)`：

| requested | auth_required | token_configured | 结果 |
|---|---|---|---|
| 127.0.0.1 / localhost / ::1 / "" | 任意 | 任意 | 固定 127.0.0.1 |
| 0.0.0.0 等外部地址 | true | true | 允许外部监听（警告） |
| 0.0.0.0 等外部地址 | false 或未配置 Token | — | 回退 127.0.0.1（警告） |

监听地址变化**不会**自动放开命令权限：认证依赖始终按 `require_auth` 判定。

## 5. API / 配置变化

- 新增配置字段：`api.require_auth`（默认 `true`）、`api.api_token`（默认 `""`，`repr=False`）。
- 新增环境变量：`GATEWAY_API_TOKEN`、`GATEWAY_API_REQUIRE_AUTH`。
- 命令 API 新增 401 / 503 语义；403 语义保持。
- 无数据库变更。

## 6. 测试

### 单元 / API 测试（真实 FastAPI TestClient + Simulator）

命令：`cd gateway-agent && PYTHONPATH=src python3 -m pytest tests/test_command_api_security.py tests/test_config.py -q`

覆盖：

- 无 Token → 401；错误 Token → 401；正确 Token（Header / Bearer）→ 200。
- Token 缺失（生产默认）→ 命令 API 503，且 `connect` 等设备端点同样 503。
- 显式 `require_auth=False` 才允许本地开发免认证；`GatewayConfig()` 默认仍为 `true`。
- 合法 Token + 非法命令 → 403（白名单未被绕过）。
- 合法 Token + 空命令 → 422（请求体 schema 校验仍生效）。
- 7 个设备操作端点未认证全部 401。
- Token 不出现在 DEBUG 级日志；`mask_secret` 永远返回 `***`。
- diagnostics 只暴露布尔状态与掩码。
- 监听地址 4 种组合。

### 回归

- 原有 `test_api.py` 9 个用例在携带合法 Token 后全部通过；原 `test_site_connector.py::test_11_agent_api_regression` 未改代码即通过（无 Token → 503，符合新生产默认）。

## 7. 已知限制

- 本地 API 仍是 HTTP（无 TLS）。认证用于本机进程边界，不替代网络层加密；外部监听仅在显式开启认证后允许，生产不建议跨主机暴露。
- Token 是单一静态凭据，无轮换/审计链；后续可扩展为短期签名 Token。
- `save_config()` 仍会序列化 `api_token` 字段（用于 YAML 配置场景）。生产推荐仅用环境变量注入，避免明文落盘。

## 8. 生产部署建议

1. `export GATEWAY_API_TOKEN="<随机 32+ 字节>"`，不要写入仓库。
2. 保持 `api.require_auth: true`。
3. 校验启动日志包含 `command API security: require_auth=True token_configured=True`。
4. 若确需外部监听，确认上述布尔均为 True，否则服务会回退 127.0.0.1。
5. 控制台/脚本调用命令 API 时必须携带 `X-API-Token` 或 Bearer。

## 9. 未解决问题

- 无 Token 轮换与多调用方分权（不影响本轮阻断解除）。
