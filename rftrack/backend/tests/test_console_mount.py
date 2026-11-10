"""`/app`：原 Console 由后端同源发出来，但**它是可选件**。

三条口径：

- **单机不依赖网络**：这一节只是"平台多一个入口"。Web Serial 直连与 `serve.mjs` 那条路一行
  没碰，也不因为后端挂了就用不了（约束 10、§七）。
- **没构建就不是故障**：`web/dist/` 在 `.gitignore` 里，一个只跑 API 或还没出包的实例挂不上
  页面是**合法形态**。把它报成 `degraded`，运营者就会开始忽略 `degraded`——`workbench` 那一节
  已经付过这个学费（`tests/test_workbench_static.py`）。
- **两个入口各发各的页**：`/app` 与 `/workbench` 互不遮蔽，且都靠"相对 base 构建"这一条
  （`web/vite.config.ts:60`）才能在任意挂载点下活。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import DEFAULT_CONSOLE_DIR, Settings
from app.main import create_app


def build_console(root: Path) -> Path:
    """一份最小但形状正确的构建产物：`base: "./"` 出的页面，引用全是相对路径。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        '<!doctype html><html><head>'
        '<script type="module" crossorigin src="./assets/index-abc123.js"></script>'
        '<link rel="stylesheet" crossorigin href="./assets/index-abc123.css">'
        "</head><body><div id=\"app\"></div></body></html>",
        encoding="utf-8",
    )
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "index-abc123.js").write_text("console.log('widscan58')\n", encoding="utf-8")
    (assets / "index-abc123.css").write_text(":root{--page:#0d0d0d}\n", encoding="utf-8")
    return root


def _client(db_path, console_dir, workbench_dir=None):
    app = create_app(
        Settings(
            db_path=db_path,
            dialer_enabled=False,
            console_dir=console_dir,
            workbench_dir=workbench_dir,
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_the_console_is_served_from_the_same_origin(db_path, tmp_path):
    with _client(db_path, build_console(tmp_path / "dist")) as client:
        page = client.get("/app/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert 'id="app"' in page.text
        # 页面 200 而资源 404 只是"半个能用的页"，所以入口模块与样式表各自也要取到。
        for path in ("/app/assets/index-abc123.js", "/app/assets/index-abc123.css"):
            assert client.get(path).status_code == 200, path


def test_health_names_the_console_mount_and_stays_ok(db_path, tmp_path):
    directory = build_console(tmp_path / "dist")
    with _client(db_path, directory) as client:
        health = client.get("/api/health").json()
        assert health["console"] == {
            "mounted": True,
            "reason": None,
            "dir": str(directory),
            "index": True,
        }
        assert health["status"] == "ok", health["problems"]


def test_an_instance_without_a_console_build_is_not_a_problem(db_path):
    with _client(db_path, None) as client:
        health = client.get("/api/health").json()
        assert health["console"] == {
            "mounted": False,
            "reason": "disabled",
            "dir": None,
            "index": False,
        }
        assert health["status"] == "ok", "不发页面是合法部署形态，不许报 degraded"
        assert client.get("/app/").status_code == 404


def test_a_console_directory_without_index_html_names_the_missing_piece(db_path, tmp_path):
    """半路拷过来的发布包最像这个样子：目录有了，`index.html` 还在路上。"""
    empty = tmp_path / "dist"
    empty.mkdir()
    with _client(db_path, empty) as client:
        health = client.get("/api/health").json()
        assert health["console"] == {
            "mounted": False,
            "reason": "no_index_html",
            "dir": str(empty),
            "index": False,
        }
        assert health["status"] == "ok"


def test_the_console_does_not_shadow_the_workbench(db_path, tmp_path):
    workbench = build_console(tmp_path / "workbench")
    console = build_console(tmp_path / "dist")
    with _client(db_path, console, workbench_dir=workbench) as client:
        assert client.get("/app/").status_code == 200
        assert client.get("/workbench/").status_code == 200
        health = client.get("/api/health").json()
        assert (health["console"]["mounted"], health["workbench"]["mounted"]) == (True, True)


def test_the_environment_variable_distinguishes_unset_from_empty():
    """`RFTRACK_WEB_DIR=`（空串）与"未设置"是两种意图，不能都折叠成默认值。"""
    assert Settings.from_env({}).console_dir == DEFAULT_CONSOLE_DIR
    assert Settings.from_env({"RFTRACK_WEB_DIR": ""}).console_dir is None
    assert Settings.from_env({"RFTRACK_WEB_DIR": "/srv/console"}).console_dir == Path(
        "/srv/console"
    )
