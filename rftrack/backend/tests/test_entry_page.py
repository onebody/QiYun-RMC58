"""`GET /`：两个工作区的入口页（`app/routers/entry.py`）。

这一页唯一的价值是"说实话"，所以判据全朝着两件事：

- **给了链接就必须点得通**——只比字符串的判据认不出"写了个 404 的地址"，所以每条链接都真发一次请求。
- **没挂上就必须说清为什么**——"不发这一页"（部署显式关掉了）与"产物还没构建"（配了目录但没
  `index.html`）是两句话，混成一句"不可用"就等于让运营者去查一条本来就不该走的路线。

它还替后端根上补了一个原本的空洞：在挂上这一页之前，`/` 是 FastAPI 那句
`{"detail":"Not Found"}`，既不说是哪一边也不说为什么没有。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routers import entry
from test_console_mount import build_console

#: 名字与前缀的真相在那份 JS 里（工作台顶栏用的就是它），后端只能抄一份，所以拿回来对拍。
WORKSPACES_JS = Path(entry.__file__).parents[3] / "frontend" / "workbench" / "workspaces.js"


def _client(db_path, console_dir, workbench_dir):
    app = create_app(
        Settings(
            db_path=db_path,
            dialer_enabled=False,
            console_dir=console_dir,
            workbench_dir=workbench_dir,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_two_mounted_workspaces_are_two_links_that_actually_work(db_path, tmp_path):
    console = build_console(tmp_path / "dist")
    workbench = build_console(tmp_path / "workbench")
    with _client(db_path, console, workbench) as client:
        page = client.get("/")
        assert page.status_code == 200, "实例根上不再是一句 Not Found"
        assert page.headers["content-type"].startswith("text/html")
        hrefs = re.findall(r'href="([^"]+)"', page.text)
        assert set(hrefs) == {"/app/", "/workbench/"}, f"两个工作区各给一条链接：{hrefs}"
        for href in hrefs:
            assert client.get(href).status_code == 200, f"入口页给了点了不通的地址 {href}"
        for spec in entry.WORKSPACES:
            assert spec["name"] in page.text and spec["zh"] in page.text


def test_a_workbench_that_is_not_served_gets_a_reason_instead_of_a_link(db_path, tmp_path):
    """汇聚实例的常态：`RFTRACK_WORKBENCH_DIR=`，这一页只提供 API。"""
    console = build_console(tmp_path / "dist")
    with _client(db_path, console, None) as client:
        page = client.get("/")
        assert re.findall(r'href="([^"]+)"', page.text) == ["/app/"]
        assert "这台实例不发这一页" in page.text
        assert client.get("/workbench/").status_code == 404, "没挂上就不该有第二处假装它在"


def test_not_built_and_not_configured_are_two_different_sentences(db_path, tmp_path):
    """"产物还没进来"与"部署就没要这一页"必须分得开，否则下一步会查错方向。"""
    empty = tmp_path / "dist"
    empty.mkdir()
    with _client(db_path, empty, build_console(tmp_path / "workbench")) as client:
        unbuilt = client.get("/").text
        health = client.get("/api/health").json()
    assert "/app/" not in re.findall(r'href="([^"]+)"', unbuilt)
    assert "配了目录但里面没有 index.html" in unbuilt
    assert health["console"] == {"mounted": False, "reason": "no_index_html", "dir": str(empty), "index": False}

    with _client(db_path, None, build_console(tmp_path / "workbench")) as client:
        disabled = client.get("/").text
    assert "这台实例不发这一页" in disabled, "显式关掉的那一边不该被说成「还没构建」"
    assert "配了目录但里面没有 index.html" not in disabled


def test_the_page_never_invents_a_reason_the_health_check_did_not_give(db_path, tmp_path):
    """入口页与 `/api/health` 读同一份挂载结果：两处的结论必须一字不差地对上。"""
    console = build_console(tmp_path / "dist")
    workbench = tmp_path / "workbench"
    workbench.mkdir()
    with _client(db_path, console, workbench) as client:
        page = client.get("/").text
        health = client.get("/api/health").json()
    assert (health["console"]["mounted"], health["workbench"]["mounted"]) == (True, False)
    assert "/workbench/" not in re.findall(r'href="([^"]+)"', page)
    assert health["workbench"]["reason"] == "no_index_html"


def test_workspace_names_are_pinned_to_the_workbench_data(db_path):
    """两份名字必须一起改：页面上叫 Single Node Console、顶栏叫别的词，就是同一台机器两套话。"""
    js = WORKSPACES_JS.read_text(encoding="utf-8")
    assert WORKSPACES_JS.is_file(), f"对拍的出处不在了：{WORKSPACES_JS}"
    for spec in entry.WORKSPACES:
        assert f'name: "{spec["name"]}"' in js, f"{spec['name']} 在 workspaces.js 里找不到"
        assert f'zh: "{spec["zh"]}' in js, f"{spec['zh']} 与 workspaces.js 里的中文不是一句"
    # `/app/` 这个前缀在两份数据里都得是同一个字符串；`/workbench/` 那一条由上面那条
    # "点了要 200" 判据兜住，它比抄一遍挂载点更硬。
    assert 'base: "/app/"' in js
    assert entry.WORKSPACES[0]["prefix"] == "/app/"


def test_a_path_with_markup_in_its_name_stays_text(db_path, tmp_path):
    """目录名会原样显示在页面上：它来自部署配置，但配置里有怪字符不该换来一处脚本执行。"""
    console = build_console(tmp_path / "d<script>ist")
    with _client(db_path, console, build_console(tmp_path / "workbench")) as client:
        page = client.get("/")
    assert "<script>ist" not in page.text
    assert "d&lt;script&gt;ist" in page.text, "转义之外还得看得见那个目录到底叫什么"
