"""Phase 08.4 — Platform Shell 静态托管与只读入口测试。

覆盖：
- /platform/ 返回真实平台页面
- /platform/app.js 可访问
- /platform 与 / 重定向
- /health 与现有 REST 不受影响
- 可选 /app/ Console 挂载（SITE_CONSOLE_DIR）
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from widscan_site.config import DatabaseConfig, ServerConfig
from widscan_site.main import create_app


def _cfg(tmp_path) -> ServerConfig:
    return ServerConfig(database=DatabaseConfig(url=f"sqlite:///{tmp_path / 'platform.db'}"))


def test_platform_index_is_served(tmp_path):
    app = create_app(_cfg(tmp_path))
    with TestClient(app) as client:
        res = client.get("/platform/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    assert "WIDSCAN58" in body
    assert 'data-route="overview"' in body
    assert 'data-route="system"' in body


def test_platform_assets_are_served(tmp_path):
    app = create_app(_cfg(tmp_path))
    with TestClient(app) as client:
        js = client.get("/platform/app.js")
        css = client.get("/platform/styles.css")
    assert js.status_code == 200
    assert css.status_code == 200
    assert "pageOverview" in js.text
    assert "--accent" in css.text


def test_platform_redirects(tmp_path):
    app = create_app(_cfg(tmp_path))
    with TestClient(app) as client:
        platform = client.get("/platform", follow_redirects=False)
        root = client.get("/", follow_redirects=False)
    assert platform.status_code in (301, 302, 307, 308)
    assert platform.headers["location"].endswith("/platform/")
    assert root.status_code in (301, 302, 307, 308)
    assert root.headers["location"].endswith("/platform/")


def test_existing_api_and_health_unaffected(tmp_path):
    app = create_app(_cfg(tmp_path))
    with TestClient(app) as client:
        health = client.get("/health")
        site = client.get("/api/v1/site")
        agents = client.get("/api/v1/agents")
        diag = client.get("/api/v1/diagnostics")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert site.status_code == 200
    assert agents.status_code == 200
    assert diag.status_code == 200
    assert "ingestion" in diag.json()


def test_console_mount_is_optional(tmp_path, monkeypatch):
    console_dir = tmp_path / "console-dist"
    console_dir.mkdir()
    (console_dir / "index.html").write_text(
        "<!doctype html><title>RF Console Test</title>", encoding="utf-8")

    monkeypatch.setenv("SITE_CONSOLE_DIR", str(console_dir))
    app = create_app(_cfg(tmp_path))
    with TestClient(app) as client:
        res = client.get("/app/", follow_redirects=False)
    assert res.status_code == 200
    assert "RF Console Test" in res.text
