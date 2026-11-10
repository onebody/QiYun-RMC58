# 36 — Site Connector Command 生命周期

> **版本**：V1.1  
> **日期**：2026-11-04  
> **代码基准**：`gateway-agent/src/widscan_agent/site_connector.py` L973-1115  
> **交叉引用**：[33-site-connector-runtime-audit.md](./33-site-connector-runtime-audit.md) — A5（Command 无状态机/幂等）根治  
> **区分说明**：V1.0 审计中的"Command 无状态机、无幂等性、无断线保护"问题在 V1.1 已根治。

---

## 一、V1.0 审计发现的问题（A5）

来自 [33 §2 A5](./33-site-connector-runtime-audit.md)：

| 问题 | 影响 | 代码位置（V1.0） |
|------|------|------------------|
| 无状态机 | 跳过 ACCEPTED/EXECUTING → 直接 EXECUTING | V1.0 `_handle_command_request` 一函数到底 |
| 无幂等性 | 同一 request_id 重到 → **重复执行命令** | V1.0 无 registry |
| 无断线保护 | 执行到一半 WS 断开 → Site 重发 → 再执行一次 | V1.0 无去重 |
| 无命令级超时 | 依赖 `device.send_command` 内部 ~1s timeout | V1.0 无全局 30s timeout |
| 无 REJECTED | 非法参数 → FAILED，应先 REJECTED | V1.0 无 REJECTED 分支 |

---

## 二、7 态状态机

### 2.1 State 定义

**代码**：`CommandState` enum L90-97

```python
class CommandState(str, enum.Enum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
```

| State | 含义 | 代码设置位置 | 终态 |
|-------|------|-------------|------|
| RECEIVED | Site Server 消息到达 + 基本 request_id 校验通过 | `_handle_command_request` 入口后隐含 | ✗ |
| ACCEPTED | Device 存在 + 在线 + Command 白名单通过 | L1037-1040 `CommandRecord(state=ACCEPTED)` | ✗ |
| EXECUTING | 交给 `run_in_executor(device.send_command)` | L1044 `rec.state = CommandState.EXECUTING` | ✗ |
| SUCCEEDED | `send_command` 返回 `(True, reply)` | L1052 | ✓ |
| FAILED | `send_command` 返回 `(False, reply)` 或抛异常 | L1052 / L1071 | ✓ |
| TIMEOUT | 30s 超时 | L1063 | ✓ |
| REJECTED | Device 离线 / 不存在 / Command 不在白名单 | L1007-1034 多个提前退出分支 | ✓ |

### 2.2 状态流转图

```
Site Server
   │ command.request
   ▼
┌───────────┐   request_id 存在？  ┌──────────────────────┐
│ RECEIVED  │─────────────────────▶│ 查 registry → 已终态？│
└─────┬─────┘                      │ 返回缓存结果（不执行）│
      │ request_id 不存在            └──────────────────────┘
      ▼
┌───────────┐   manager is None?  ┌─────────┐
│ ACCEPTED  │────────────────────▶│REJECTED │→ registry + response
└─────┬─────┘                      └─────────┘
      │ device 不存在？                ↑
      ▼                               │
   REJECTED ──────────────────────────┤
      ▼                               │
   Device 离线/ERROR？ ──────────────▶│
      ▼                               │
   Command 不在白名单？ ────────────▶│
      │ 通过所有校验
      ▼
┌────────────┐ run_in_executor(device.send_command)
│ EXECUTING  │ timeout=30s
└─────┬──────┘
      │
      ├── 返回 (True, reply) ──────────▶ SUCCEEDED → registry + response
      ├── 返回 (False, reply) ─────────▶ FAILED → registry + response
      ├── 30s timeout ─────────────────▶ TIMEOUT → registry + response
      └── 执行中抛 Exception ──────────▶ FAILED → registry + response
```

---

## 三、CommandRecord 与 Registry

### 3.1 CommandRecord dataclass

**代码**：L100-110

```python
@dataclass
class CommandRecord:
    """已处理的命令（用于幂等性）。"""
    request_id: str
    device_id: str
    command: str
    state: CommandState
    ok: bool = False
    reply: str = ""
    received_at: float = field(default_factory=time.monotonic)
    completed_at: Optional[float] = None
```

字段说明：
- `request_id`：Site Server 分配的唯一 ID（幂等 key）
- `received_at`：入 registry 的时间戳（用于超时排序）
- `completed_at`：终态时间（registry 清理时用最老优先）
- `reply[:500]`：结果截断防内存膨胀（L1054, L1112）

### 3.2 Registry 定义

**代码**：L222, L119

```python
MAX_COMMAND_REGISTRY = 1024
self._command_registry: Dict[str, CommandRecord] = {}
```

### 3.3 Registry 访问约束

所有 registry 读写都在 Connector event loop 内：
- 写入：`_handle_command_request` → `_register_and_respond` / 内联 registry 更新
- 读取：幂等性检查（L996-1003）
- 清理：L1088-1092

**单线程访问 → 不需要锁保护**。

---

## 四、幂等性实现

### 4.1 重复 request_id 检测

**代码**：L995-1003

```python
# --- 1. 幂等性检查 ---
existing = self._command_registry.get(request_id)
if existing is not None:
    log.info("command.request duplicate request_id=%s — returning cached result (state=%s)",
             request_id, existing.state.value)
    if existing.state in (CommandState.SUCCEEDED, CommandState.FAILED,
                          CommandState.TIMEOUT, CommandState.REJECTED):
        await self._send_command_response(request_id, ok=existing.ok, reply=existing.reply)
    return   # ← 直接返回，不重新执行
```

**设计要点**：
1. 幂等性检查在所有校验之前 — 最快路径
2. 如果 existing.state 还在 EXECUTING（理论上罕见但可能）→ 不发送中间态响应，直接 return（Site Server 重发的请求会等下次重试）
3. 只有终态才返回缓存结果

### 4.2 幂等性覆盖的重试场景

| 重试场景 | 发生条件 | Registry 状态 | 行为 |
|----------|----------|--------------|------|
| 执行中 WS 断开 | `_handle_command_request` → EXECUTING → WS send 命令结果时断连 | EXECUTING（未终态） | Site 重发 → Connector 查 registry 发现 EXECUTING → return 不重复执行，但也不返回结果。Site 需等下次重试 |
| 命令结果 send 失败 | `_send_command_response` 异常（L1079-1085） | 已是 SUCCEEDED/FAILED/TIMEOUT | Site 重发 → 查 registry 终态 → 返回缓存结果 ✓ |
| Site Server 超时重发 | Site 没收到 response，认为超时 | 终态 → 返回缓存结果 ✓ |
| 断线恢复后重发 | WS 重连，Site 重发未确认的命令 | 终态 → 返回缓存结果 ✓ |

### 4.3 幂等性不是 100% 的边界

如果 Connector 在命令 EXECUTING 中被重启（进程 crash），内存 registry 丢失 → Site Server 重发时会重新执行。这是所有内存 registry 方案的固有限制。

V1.1 用内存 registry 足够（进程 crash 极罕见）；如需持久化可在后续版本加 SQLite 轻量存储。

---

## 五、命令执行（非阻塞 Event Loop）

### 5.1 run_in_executor 设计

**代码**：L1046-1051

```python
try:
    loop = asyncio.get_event_loop()
    ok, reply = await asyncio.wait_for(
        loop.run_in_executor(None, device.send_command, command),
        timeout=COMMAND_EXEC_TIMEOUT,
    )
```

**为什么用 `run_in_executor`**：
- `device.send_command` 可能含同步阻塞调用（串口写入 / USB 协议交互）
- 默认 ThreadPoolExecutor → Device 命令执行在线程池，**不阻塞 Connector event loop**
- Connector 心跳 / 队列消费 / 其他命令响应全部正常运行

**`asyncio.wait_for` 双层保护**：
1. `run_in_executor` timeout=COMMAND_EXEC_TIMEOUT (30s)
2. Device 内部 `send_command` 可能有自己的 ~1s timeout
3. 两层超时任一先到 → 强制退出

### 5.2 超时处理

**代码**：L1062-1068

```python
except asyncio.TimeoutError:
    rec.state = CommandState.TIMEOUT
    rec.ok = False
    rec.reply = f"execution timeout ({COMMAND_EXEC_TIMEOUT}s)"
    rec.completed_at = time.monotonic()
    self._commands_timeout += 1
    log.warning("command timeout: request_id=%s device=%s", request_id, device_id)
```

**注意**：`run_in_executor` 的 Future 被取消后，线程中的 `device.send_command` 可能继续执行（ThreadPoolExecutor 无法强杀线程）。但 Connector 侧已标记 TIMEOUT + 回传 Site Server + 记录 registry → 后续重发同一 request_id 会返回 TIMEOUT 缓存结果（不会重复执行）。

### 5.3 执行异常处理

**代码**：L1070-1076

```python
except Exception as exc:
    rec.state = CommandState.FAILED
    rec.ok = False
    rec.reply = f"execution error: {exc}"
    rec.completed_at = time.monotonic()
    self._commands_failed += 1
```

设备驱动层抛的异常（USB 断开 / 协议错误 / 回复格式不对等）统一走 FAILED 状态。

---

## 六、REJECTED 路径

### 6.1 四个提前退出分支

| 分支 | 条件 | 代码位置 | 错误消息 |
|------|------|----------|----------|
| 0. request_id 缺失 | `not request_id` | L990-993 | ignored（不进 registry） |
| 2a. 无 DeviceManager | `self._manager is None` | L1006-1011 | "connector has no device manager" |
| 2b. Device 不存在 | `device is None` | L1013-1019 | "device {id} not found" |
| 2c. Device 离线/Error | `device.state in (DISCONNECTED, RECONNECTING, ERROR)` | L1021-1027 | "device offline/error ({state})" |
| 2d. Command 不在白名单 | `not is_command_allowed(command)` | L1029-1034 | "command not allowed: {cmd}" |

### 6.2 `_register_and_respond` 统一入口

**代码**：L1094-1102

```python
async def _register_and_respond(self, request_id, device_id, command,
                                 state: CommandState, *, ok: bool, reply: str):
    self._command_registry[request_id] = CommandRecord(
        request_id=request_id, device_id=device_id, command=command,
        state=state, ok=ok, reply=reply,
        completed_at=time.monotonic(),     # ← REJECTED 是瞬时终态
    )
    await self._send_command_response(request_id, ok=ok, reply=reply)
```

**为什么 REJECTED 也要进 registry**：
1. Site Server 可能重发同一 request_id → 需要返回缓存的 REJECTED
2. 防止 Site Server 以为命令丢失而重复请求

注意 `completed_at` 在 `_register_and_respond` 中被设为 `time.monotonic()`（瞬时终态），而在 `CommandRecord` 默认是 `None`。

---

## 七、Registry 内存管理

### 7.1 膨胀风险

Registry 理论上会无限增长 — 每个收到的 request_id 都占一条记录。如果 Site Server 发大量不同 request_id（包括非法的），内存会逐渐耗尽。

### 7.2 清理策略

**代码**：L1087-1092

```python
# --- 5. 清理旧 registry 条目（防内存泄漏）---
if len(self._command_registry) > MAX_COMMAND_REGISTRY:   # 1024
    old_keys = list(self._command_registry.keys())[:MAX_COMMAND_REGISTRY // 2]  # 删最老 512 条
    for k in old_keys:
        self._command_registry.pop(k, None)
```

**设计要点**：
- **触发条件**：超过 MAX_COMMAND_REGISTRY (1024) 时触发
- **清理粒度**：删最老的一半 (512)
- **"最老"定义**：dict.keys() 插入顺序（Python 3.7+ dict 保持插入序）— 最早进入 registry 的在前
- **副作用**：被清理的 request_id 如果后续重发，会被当作新命令处理 — 但这些是 512 条最旧的，Site Server 重发的概率极低

### 7.3 大小估算

每条 CommandRecord 约：
- 对象开销 ~100 bytes（dict + 内部表）
- `request_id` (UUID string) ~36 bytes
- `device_id` ~20 bytes
- `command` ~10 bytes
- `reply[:500]`（已截断）~500 bytes max
- 其他字段 ~80 bytes

总计约 750 bytes/条。MAX_COMMAND_REGISTRY=1024 → 约 750KB 上限。完全可接受。

---

## 八、Command 响应序列化

### 8.1 `_send_command_response`

**代码**：L1104-1115

```python
async def _send_command_response(self, request_id: str, *, ok: bool, reply: str):
    if self._ws is None:
        return   # ← 无 WS 连接 → 静默放弃（registry 已有终态，重连后 Site 重发返回缓存）
    try:
        await self._send_json({     # ← 走 asyncio.Lock 串行化
            "type": "command.response",
            "request_id": request_id,
            "ok": ok,
            "reply": reply[:500],   # ← 截断
        })
    except Exception:
        pass   # ← send 失败不影响 registry（registry 已写入终态）
```

### 8.2 Serialize 保证

| 保证 | 机制 | 代码 |
|------|------|------|
| 单写者串行化 | `asyncio.Lock _send_lock` | `_send_json_raw` L1159 |
| reply 长度不超过 | `reply[:500]` | L1054, L1112 |
| WS 断开时静默放弃 | `if self._ws is None: return` | L1105-1106 |
| send 异常不影响 registry | `try...except Exception: pass` | L1114-1115 |

---

## 九、断线恢复策略

### 9.1 场景矩阵

| 断线时刻 | Connector 状态 | Site Server 状态 | 恢复后行为 |
|----------|---------------|-----------------|-----------|
| 命令收到但未入 registry | EXECUTING 中丢失 | 不知道命令到达 | Site 重发 → 当作新命令 |
| 命令入 registry 但未执行完成 | EXECUTING 中丢失 | 等待 response | Site 重发 → registry 有 EXECUTING 状态 → return（不重复执行，但也无 response） |
| 命令执行完成但 response send 失败 | registry 已终态 | 未收到 response | Site 重发 → 返回缓存终态 ✓ |
| Connector 进程 crash | 内存丢失 | 未收到 response | Site 重发 → 新进程无 registry → **重新执行**（固有限制） |

### 9.2 为什么内存 Registry 足够

1. Site Server 的 `command.request` 重试有间隔（不会紧锣密鼓）
2. Connector daemon 线程不与其他进程共享内存 — 进程 crash 是极低概率事件
3. 如果 Connector crash → Agent 主进程也可能 crash → Device 状态不可知 → 重发命令反而更安全
4. 后续可升级为 SQLite WAL 模式持久化（进程 crash 后 registry 可恢复）

---

## 十、Diagnostics 中的 Command 统计

**`diagnostics` property** L295-300：

```python
"pending_commands": len(self._command_registry),   # 包括 EXECUTING 和终态
"commands_received": self._commands_received,
"commands_succeeded": self._commands_succeeded,
"commands_failed": self._commands_failed,
"commands_timeout": self._commands_timeout,
"commands_rejected": self._commands_rejected,
```

注意 `pending_commands` 实际上是 registry 总大小（包括终态条目）。这是设计意图 — 运维通过它观察 registry 是否膨胀。

---

## 十一、验收标准

| 验证项 | 预期 | 代码支撑 |
|--------|------|----------|
| 同一 request_id 发 3 次 | 只执行 1 次，后 2 次返回缓存 | L996-1003 幂等检查 |
| Device 离线时发命令 | REJECTED + 明确错误消息 | L1021-1027 |
| 非法 command 发命令 | REJECTED + "not allowed" | L1029-1034 |
| 命令执行 30s 超时 | TIMEOUT + reply "execution timeout (30.0s)" | L1062-1068 |
| 命令执行异常 | FAILED + reply "execution error: {exc}" | L1070-1076 |
| Registry 超 1024 | 自动删最老 512 条 | L1088-1092 |
| 命令 send 失败 + Site 重发 | 返回缓存终态（不重复执行） | L1082-1084 注释 |
| run_in_executor 不阻塞 event loop | 心跳 / 队列消费 / 其他命令正常 | L1047-1050 |
