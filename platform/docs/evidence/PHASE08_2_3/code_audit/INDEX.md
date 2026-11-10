# Code Audit Index — Phase 08.2.3

> 文档版本：1.0.0
> 创建日期：2026-11-05
> 最后修改：2026-11-05
> 状态：INDEX_ONLY（静态代码事实，不是运行实测）
> Baseline：NO

本索引记录审计时读取到的真实文件与静态事实。**代码存在 ≠ 功能已验收**；本文件不得作为硬件或性能证据。

## 1. 关键代码证据

| 事实 | 文件 | 修改时间（本机） | 证据类型 |
|---|---|---|---|
| 无 `/api/v1/rf/*` 路由 | `site-server/src/widscan_site/main.py`（22,024 B） | 2026-11-05 11:38:48 | 全文检索无匹配（STATIC） |
| `/platform` 静态挂载 | 同上 | 同上 | `StaticFiles` 挂载（STATIC） |
| 可选 `/app/` Console 挂载 | 同上 | 同上 | 条件注册（STATIC） |
| 无 channel 级事实表 | `site-server/src/widscan_site/database/models.py`（14,343 B） | 2026-11-05 11:38:48 | 模型清单（STATIC） |
| Agent 配对/凭证哈希模块（非用户认证） | `site-server/src/widscan_site/auth/__init__.py`（6,288 B） | 2026-11-05 11:38:48 | 源码阅读（STATIC） |
| 平台 UI 实现（index/app/styles） | `site-server/web/`（4,020 / 46,890 / 13,541 B） | 2026-11-05 11:38:48 | 源码（STATIC） |
| 平台 UI 专项测试（5 条） | `site-server/tests/test_phase08_platform_ui.py`（2,829 B） | 2026-11-05 11:38:48 | 测试文件存在（未重跑） |
| 9 状态连接机、双队列、退避 | `gateway-agent/src/widscan_agent/site_connector.py`（50,820 B） | 2026-11-05 11:38:47 | 源码（STATIC） |
| 固件频点上限 8 | `repo/firmware/src/scan/profile.h` | 2026-10-27 23:48:13 | 宏定义（STATIC） |
| S3 relay STA + WS 客户端 | `repo/firmware/src/net/ws_link.c`（22,093 B） | 2026-11-02 22:15:57 | 源码（STATIC） |
| relay UART→WS 转发 | `repo/firmware/src/net/relay_pipe.c` | 2026-11-02 22:28:00 | 源码（STATIC） |

## 2. 证据等级

- 以上全部为 **STATIC**（静态阅读/检索）。
- 没有运行实测、没有真机、没有压测。
- 因此 `docs/PHASE08_2_3_CAPACITY_AND_PERFORMANCE.md` 的性能数字仍为推导值，保持 **NOT VERIFIED**。

## 3. 复核命令（可重放）

```bash
grep -rn "/api/v1/rf" site-server/src
grep -n "StaticFiles\|/platform\|/app" site-server/src/widscan_site/main.py
grep -n "PROFILE_MAX_CHANNELS" repo/firmware/src/scan/profile.h
```
