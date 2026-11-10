# 35 — Site Connector 安全与认证

> **版本**：V1.1  
> **日期**：2026-11-04  
> **代码基准**：`gateway-agent/src/widscan_agent/site_connector.py` · `site-server/src/widscan_site/agents/connection.py`  
> **交叉引用**：[33-site-connector-runtime-audit.md](./33-site-connector-runtime-audit.md) — A2（Credential URL 泄露）+ A10（TLS）+ AUTH_FAILED 停止重连  
> **区分说明**：V1.1 首条消息 auth 取代 V1.0 URL credential；A10 TLS 加固为生产就绪。

---

## 一、威胁模型与风险

### 1.1 V1.0 暴露的安全问题（审计 A2）

**Credential 在 WebSocket URL Query 中**（`site_connector.py` V1.0 L504）：

```
ws://site.example.com:8766/ws/v1/agents/connect?agent_id=abc123&credential=SUPERSECRET
```

泄露路径：
1. **Nginx / Proxy access log** — 每次连接 URL 被完整记录
2. **`websockets` 客户端连接日志** — debug 模式打印完整 URL
3. **异常堆栈** — `InvalidStatusCode` 异常携带请求 URL
4. **网络抓包** — 非 TLS 时明文可见
5. **浏览器历史** — 开发调试时误粘贴

### 1.2 Credential 泄露影响

Credential 是 Agent 身份凭证（类似 API token），一旦泄露：
- 攻击者可冒充 Agent 连入 Site Server
- 伪造设备上线 / 伪造频谱数据
- 可能越权访问 Site Server REST API（取决于 credential 作用域）

---

## 二、首条消息认证协议（V1.1 A2 根治）

### 2.1 认证时序

```
Agent (site_connector.py)          Site Server (connection.py)
     │                                    │
     │ ── ws.connect(URL 仅带 agent_id) ─▶│   L758: URL = ".../ws/v1/agents/connect?agent_id=X"
     │                                    │   L97:  await ws.accept()
     │                                    │   L99:  读 URL credential → 空！
     │                                    │   L104: if not credential → 等首条消息
     │                                    │
     │ ◀────── ws 握手完成 ──────────────│
     │                                    │
     │ ── 首条消息: auth ──────────────▶│   L809: _send_json_raw(ws, auth_msg)
     │    {type:"auth", agent_id,        │   L106: first_raw = ws.receive_json() timeout=5s
     │     credential, agent_version}     │   L116: ftype=="auth" → 提取 credential
     │                                    │   L136-141: verify_credential(sess, agent_id, credential)
     │                                    │   L142: 关闭 (4401) 若无效
     │                                    │
     │ ◀───── {type:"connected", site_id}│   L156-161: 认证通过 → 发 connected
     │                                    │
     │ ── _lifecycle 标记 CONNECTED ───▶ │   L690: _set_state(CONNECTED)
     │                                    │
     │ ── devices_snapshot ────────────▶ │   L695: 重连补偿
     │                                    │
     │ ◀──┬── heartbeat.ack ────────────│ 每 30s
     │ ──┴── heartbeat ────────────────▶│ 每 15s
```

### 2.2 Agent 端实现

**URL 构建**：`_try_connect` L756-758

```python
async def _try_connect(self):
    url = f"{self._endpoint.rstrip('/')}/ws/v1/agents/connect?agent_id={self._agent_id}"
    # ↑ 注意：只有 agent_id，没有 credential
    ssl_ctx = self._build_ssl_context()
    ws = await asyncio.wait_for(
        websockets.connect(url, ssl=ssl_ctx,
                          additional_headers={"User-Agent": "WIDSCAN58-Agent/1.1.0"}),
        timeout=10.0,
    )
    return ws
```

**首条认证消息**：`_do_auth` L792-836

```python
async def _do_auth(self, ws):
    auth_msg = {
        "type": "auth",
        "agent_id": self._agent_id,
        "credential": self._credential,       # ← 走消息体，不走 URL
        "agent_version": "1.1.0",
        "ts": time.monotonic(),
    }
    await self._send_json_raw(ws, auth_msg)    # L809
    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))  # L817
    return resp.get("type") == "connected"    # L819
```

**关键**：`_do_auth` 用 `_send_json_raw(ws, ...)` 而不是 `_send_json` — 因为此时 `self._ws` 还未赋值（要等认证通过后 L687 才 `self._ws = ws`），但 `_send_lock` 已在 `_lifecycle` L656 创建。锁保护依然生效。

### 2.3 Site Server 端实现

**代码**：`agent_connect_ws` L88-161

```python
async def agent_connect_ws(ws: WebSocket):
    await ws.accept()

    agent_id = ws.query_params.get("agent_id", "").strip()
    credential_from_url = ws.query_params.get("credential", "").strip()
    credential = credential_from_url

    # V1.1: URL 无 credential → 等首条消息
    if not credential:                                  # L104
        try:
            first_raw = await asyncio.wait_for(ws.receive_json(), timeout=5.0)  # L106
        except asyncio.TimeoutError:
            await ws.close(code=4401)                   # L109 — 认证超时
            return

        ftype = first_raw.get("type", "")
        if ftype == "auth":                             # L116
            agent_id = first_raw.get("agent_id", agent_id).strip()   # L118
            credential = first_raw.get("credential", "").strip()     # L119
        else:
            await ws.close(code=4401)                   # L126 — 首条非 auth → 拒绝
            return

    # V1.0 兼容：URL 有 credential → 直接用（跳过首条消息等待）
    # ... 认证校验（L136-148）
    cred = verify_credential(sess, agent_id, credential)
    if cred is None or cred.agent_id != agent_id:
        await ws.close(code=4401)                        # L141
        return

    # 认证通过 → 发 connected
    await ws.send_json({"type": "connected", ...})      # L156-161
```

### 2.4 两种认证路径对比

| 维度 | V1.1 首条消息 auth | V1.0 URL credential（兼容） |
|------|---------------------|----------------------------|
| URL 参数 | 仅 `agent_id` | `agent_id` + `credential` |
| credential 位置 | WS binary/text frame 内 | HTTP Upgrade URL |
| 暴露到 access log | ✗ 不暴露 | ✓ 完全暴露 |
| 暴露到 ws 异常 | ✗ 不暴露 | ✓ InvalidStatusCode 带 URL |
| Site Server 等待 | 5s 内必须收到首条 auth | URL 即到，无需等待 |
| 认证前业务消息 | 不可能（auth 消息先被消费） | 不可能（主循环在认证后才开始） |
| 代码路径 | `connection.py` L104-128 | `connection.py` L101-103 直接跳过等待 |

### 2.5 认证前禁止业务消息

V1.1 Site Server 自然保证：
- V1.1 Agent：首条消息必须是 `auth`，否则 Site Server L126 直接 close(4401)
- V1.0 Agent：URL credential 已校验通过，直接进入主循环 — 但 V1.0 Agent 也不会先发业务消息

`connection.py` L117 注释：「auth 消息已在上方被消费 — 主循环无需跳过」

主循环从 L166 开始，此时 credential 已经校验通过 — 业务消息不可能在认证前到达。

### 2.6 认证超时处理

| 超时类型 | Agent 端 | Site Server 端 |
|----------|----------|----------------|
| Site Server 等 auth 超时 | — | 5s → `close(4401)` L107-109 |
| Agent 等 connected 超时 | 5s → `_last_error = "auth handshake timeout"` L830-832 | — |
| HTTP 4401 立即拒绝 | `_try_connect` L772-776 → `AUTH_FAILED` + 清空 credential | — |
| 连接 10s 超时 | `_try_connect` L762-765 timeout=10.0 | — |

---

## 三、Credential 生命周期保护

### 3.1 AUTH_FAILED 停止重连

**代码**：`_lifecycle` L728-734

```python
if reason == DisconnectReason.AUTH_FAILED:
    self._set_state(ConnectorState.AUTH_FAILED, reason)
    log.error("AUTH_FAILED — credential rejected; stopping reconnect")
    self._credential = ""      # ← 清空 credential
    self._enabled = False      # ← 禁用 Connector
    break                      # ← 跳出主循环，不再重连
```

**为什么必须清空 credential**：
1. Credential 撤销是永久状态 — 重连无意义
2. 不清空的话 Connector 会一直用无效 credential 重连（退避等待 → 每次 4401 → 再退避）
3. 清空后 `enabled=False` + `credential=""` → 下次 `start()` L320-323 会进入 STOPPED 等待状态

**另一个 AUTH_FAILED 入口**：`_try_connect` L769-776

```python
except InvalidStatusCode as exc:
    if exc.status_code in (4401, 401):
        self._set_state(ConnectorState.AUTH_FAILED, DisconnectReason.AUTH_FAILED)
        self._credential = ""
        self._enabled = False
        return None
```

HTTP 层收到 4401/401 也立即触发相同的 credential 清空逻辑。

### 3.2 Credential 在 diagnostics 中不暴露

**`diagnostics` property** L254-301：
```python
"has_credential": bool(self._credential),   # ← 只暴露 bool，不暴露值
```

没有任何字段返回 credential 明文。这解决了 [33 §4](./33-site-connector-runtime-audit.md) 验收项：「Credential 不出现在 diagnostics API」。

### 3.3 Credential 在日志中不暴露

代码中所有日志点都不打印 credential：
- `_do_auth` L820-827：只打印 `site_id`、`message`，不打印 credential
- `_try_connect` L770：只打印 HTTP status code + reason
- `start()` L343-344：只打印 `agent_id[:12]`（截断）和 endpoint

---

## 四、TLS 安全配置（V1.1 A10 根治）

### 4.1 SSL Context 构建

**代码**：`_build_ssl_context` L746-754

```python
def _build_ssl_context(self):
    if not self._endpoint.startswith("wss://"):
        return None                             # ws:// 不需要 ssl_ctx
    if not self._tls_verify:
        ctx = ssl._create_unverified_context()  # 开发环境跳过验证
    else:
        ctx = ssl.create_default_context(cafile=self._tls_ca_file)  # 生产默认
    return ctx
```

### 4.2 三种安全等级

| 配置组合 | CA File | verify | 用途 | 安全性 |
|----------|---------|--------|------|--------|
| 生产默认 | None（系统 CA） | True | 公网 / 自签名 CA 已导入 | ★★★★★ |
| 自定义 CA | `/path/to/ca.pem` | True | 自建 PKI | ★★★★★ |
| 开发调试 | — | False | localhost / 自签名 | ★★☆☆☆ |

### 4.3 安全默认

```python
def __init__(self, ..., tls_ca_file=None, tls_verify=True):  # L157-158
```

`tls_verify=True` 是默认 — 生产环境无法"意外"关闭证书验证。这比 V1.0（隐式系统默认，可能被忽略）更安全。

### 4.4 Diagnostics 中的 TLS 状态

L264-265：
```python
"tls_ca_file": self._tls_ca_file,     # 配置了哪个 CA 文件
"tls_verify": self._tls_verify,       # 是否验证证书
```

运维可以从 diagnostics API 确认当前 TLS 配置 — 避免生产错误配置。

---

## 五、认证失败矩阵

| 失败场景 | Agent 端感知 | Site Server 端感知 | ConnectorState 最终态 | 是否重连 |
|----------|-------------|-------------------|----------------------|----------|
| Credential 无效 | `_do_auth` 收到 `{type:"error"}` → `AUTH_FAILED` | `verify_credential` 返回 None → `close(4401)` | AUTH_FAILED | ✗（清空 credential） |
| Credential 撤销 | 同上 | 同上（DB 中 credential 已 revoked） | AUTH_FAILED | ✗ |
| 首条消息非 auth | `_try_connect` 收到 `close(4401)` | L124-127 → `close(4401)` | AUTH_FAILED | ✗ |
| 认证超时（Site Server 等） | `_do_auth` timeout=5s | `close(4401)` timeout 后 | RECONNECTING | ✓（credential 未清空） |
| 认证超时（Agent 等） | `_do_auth` timeout=5s | Site Server 等 5s 后 close(4401) | RECONNECTING | ✓ |
| WS 连接 10s 超时 | `_try_connect` timeout=10.0 | — | RECONNECTING | ✓ |
| HTTP 4401 拒绝 | `_try_connect` InvalidStatusCode | — | AUTH_FAILED | ✗ |
| HTTP 401 拒绝 | 同上 | — | AUTH_FAILED | ✗ |
| WS 传输错误 | `ConnectionClosedError` | — | RECONNECTING | ✓ |

**关键区分**：只有明确 HTTP 4401/401 或 auth 消息 error 响应才清空 credential（永久失效）。传输层错误（超时/WS 断连）保留 credential 并重试。

---

## 六、生产安全 Checklist

- [x] Credential 不出现在 WS URL（Agent L758 只有 agent_id）
- [x] Credential 不出现在 Nginx access log（Site Server URL 无 credential）
- [x] Credential 不出现在 diagnostics API（只有 `has_credential: bool`）
- [x] Credential 不出现在任何日志语句（L343-344, L820-827, L770 均不打印）
- [x] wss:// 默认启用证书验证（`tls_verify=True` 默认）
- [x] 自签名 CA file 可配置（`tls_ca_file`）
- [x] AUTH_FAILED 后 credential 清空 + 停止重连（永久失效处理）
- [x] 认证通过前禁止业务消息（Site Server 首条消息必须是 auth）
- [x] Site Server 认证 5s 超时防止半开连接
- [x] Agent 认证响应 5s 超时防止半开连接
- [x] HTTP 层 4401 快速拒绝路径（`_try_connect` L772-776）
- [ ] 待补充：生产环境 wss:// 强制 + 禁止 ws://（可在 `start()` 中加 endpoint 校验）
