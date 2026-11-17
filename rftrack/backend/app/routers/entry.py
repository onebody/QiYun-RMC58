"""`GET /`：两个工作区的入口页。

为什么后端根上要有这一页：`/app/` 与 `/workbench/` 是同一实例发出去的**两套产物**，
而两者的"挂没挂"各自独立（`RFTRACK_WEB_DIR=` / `RFTRACK_WORKBENCH_DIR=` 都是合法部署形态，
`web/dist/` 还没构建也是）。今天直接打开 `http://host:9000/` 得到的是 FastAPI 的一句
`{"detail":"Not Found"}`——它既不说是哪一边，也不说为什么没有，运营者只能自己去翻
`/api/health`。这一页把健康检查已经知道的那件事用链接的形状说出来：

- 挂上了 → 给可点的链接（链接就是挂载前缀本身，不另造路径）；
- 没挂上 → 不给链接，把 `/api/health` 里那个 `reason` 翻成人话摆在同一行。

**这一页不新增任何判据**：它读的就是 `app.state.console` / `app.state.workbench`——
与健康检查同一个字典、同一次挂载决定。两处说法不一致的可能因此被排除在结构之外，
而不是靠"记得同步改两个地方"。

名字与前缀的真相在 `rftrack/frontend/workbench/workspaces.js`（页面顶栏那两条入口用的就是它）。
后端 import 不了 JS 模块，所以 `tests/test_entry_page.py` 拿那份数据回来对拍，并且真发一次
请求确认链接取得到 200：只比字符串的判据认不出"写了个能看不能点的地址"。
"""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["entry"])

#: 两个工作区。`health` 是 `app.state` 上那份挂载结果的名字（见 `main.mount_console` /
#: `main.mount_workbench`），`prefix` 是后端实际挂载点——两者都由那两个函数说了算。
WORKSPACES = (
    {
        "id": "single-node",
        "name": "Single Node Console",
        "zh": "单机控制台",
        "prefix": "/app/",
        "health": "console",
        "note": "浏览器直连那块板：Web Serial 烧录、VTX 功率计、计圈与 RotorHazard 节点",
    },
    {
        "id": "site",
        "name": "Site Monitoring",
        "zh": "站点监测",
        "prefix": "/workbench/",
        "health": "workbench",
        "note": "场地/位置/接入机/节点四级层级，多块板同一屏，数据走 /ws/live 与 REST",
    },
)

#: 挂载结果里的 `reason` → 页面上那句人话。未列出的 reason 原样带出，不翻译成猜测
#: （与 `workspaces.js` 的 `DISABLED_TEXT` 同一条纪律：没证据的结论不许替健康检查说话）。
DISABLED_TEXT = {
    "disabled": "这台实例不发这一页（部署时把目录显式配成空）",
    "no_index_html": "配了目录但里面没有 index.html（前端产物还没构建进来）",
}


def line(spec: dict, mount: dict) -> str:
    """一条入口：挂上了是可点的链接，没挂上是同一行的一句理由。"""
    title = f"{spec['name']}（{spec['zh']}）"
    if mount.get("mounted"):
        return (
            f'<li><a href="{escape(spec["prefix"], quote=True)}">{escape(title)}</a>'
            f'<span class="dir">{escape(str(mount.get("dir") or spec["prefix"]))}</span>'
            f'<p class="note">{escape(spec["note"])}</p></li>'
        )
    reason = mount.get("reason")
    text = DISABLED_TEXT.get(reason) or f"未挂载（{reason or '健康检查没给理由'}）"
    return (
        f'<li><span class="off">{escape(title)}</span>'
        f'<span class="why">{escape(text)}</span>'
        f'<p class="note">{escape(spec["note"])}</p></li>'
    )


@router.get("/", include_in_schema=False, response_class=HTMLResponse)
def entry_page(request: Request) -> HTMLResponse:
    state = request.app.state
    rows = "".join(line(spec, getattr(state, spec["health"], {})) for spec in WORKSPACES)
    return HTMLResponse(
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>RF Track</title><style>"
        "body{margin:0;padding:28px 20px;background:#0d0d0d;color:#f2f2f2;"
        "font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}"
        "h1{font-size:15px;margin:0 0 4px}p.lead{margin:0 0 18px;color:#9a9a9a;font-size:12px}"
        "ul{list-style:none;margin:0;padding:0;max-width:620px}"
        "li{padding:12px 0;border-bottom:1px solid #232323}"
        "a{color:#2a78d6;text-decoration:none;font-weight:600}a:hover{text-decoration:underline}"
        ".off{color:#9a9a9a;font-weight:600}"
        ".dir,.why{display:block;margin-top:2px;font-size:11px}"
        ".dir{color:#6f6f6f}.why{color:#c98500}"
        ".note{margin:6px 0 0;color:#9a9a9a;font-size:12px}"
        "</style></head><body>"
        "<h1>RF Track</h1>"
        "<p class=\"lead\">同一实例发出的两套产物。哪一边没在，下面那行就写它为什么没在——"
        "这条结论与 <code>/api/health</code> 里的 <code>console</code> / <code>workbench</code> 两节同源。</p>"
        f"<ul>{rows}</ul></body></html>"
    )
