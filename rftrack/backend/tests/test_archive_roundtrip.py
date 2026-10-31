"""S9-2 场地归档往返：导出→临时库导入→逐字段比对，坏包拒绝而不是半导入。

判据不是这里发明的：R18（导入只进新库文件、绝不覆盖现网库）与方案 §4.6（快照用
`backup()`、过滤在快照上做、清单行数必须核对）。往返的"逐字段"落死在表内容全列比对：
对 `COUNTED_TABLES + INSTANCE_TABLES` 的每一张表，源快照与导入库 `SELECT * ORDER BY rowid`
必须完全相等——清单对得上只能说明行数一致，字段级一致才说明数据真的搬家搬对了。
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import migrate
from app.config import Settings
from app.main import create_app
from app.models import (
    Alert,
    AlertRule,
    CandidateTarget,
    LayoutWidget,
    Node,
    PluginFinding,
    SampleSummary,
    SignalEvent,
    TrackObservation,
    WorkbenchLayout,
)
from app.services.archive import COUNTED_TABLES, INSTANCE_TABLES

BASE = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
BUCKET = int(BASE.timestamp())


@pytest.fixture
def two_sites(client):
    """site 1 = home（带全量数据），site 2 = away（应当被裁掉的那份）。"""
    assert client.post("/api/sites", json={"name": "主场", "slug": "home"}).json()["site_id"] == 1
    assert client.post("/api/sites", json={"name": "客队", "slug": "away"}).json()["site_id"] == 2
    assert client.post("/api/relays", json={"site_id": 1, "name": "起点塔"}).json()["relay_id"] == 1
    assert client.post("/api/relays", json={"site_id": 2, "name": "客队塔"}).json()["relay_id"] == 2
    gw1 = client.post("/api/gateways", json={"relay_id": 1, "name": "pit-a", "kind": "usb-bridge"}).json()
    gw2 = client.post("/api/gateways", json={"relay_id": 2, "name": "away-gw", "kind": "usb-bridge"}).json()
    version = client.get("/api/settings/global").json()["version"]
    assert client.put(
        "/api/settings/global",
        json={"version": version, "settings": {"theme": "light"}},
    ).status_code == 200

    with client.app.state.db.scope() as session:
        # 先提交身份层（node/layout/rule）：同一批 flush 里没有 relationship 的
        # 依赖排序不可靠，alert 引用 rule_id 必须等 rule 行真的落库。
        session.add_all(
            [
                Node(gateway_id=gw1["gateway_id"], display_name="n1",
                     transport_kind="usb-native", transport_addr="FT:01"),
                Node(gateway_id=gw2["gateway_id"], display_name="away-n",
                     transport_kind="usb-native", transport_addr="FT:02"),
                WorkbenchLayout(site_id=1, name="默认", is_active=True,
                                defaults_json='{"refresh_s": 2}'),
                WorkbenchLayout(site_id=2, name="客队布局", is_active=True),
                AlertRule(name="rise", site_id=1, plugin_id="anomaly-detector",
                          kind="anomaly_rise", scope="site", scope_id=1),
                AlertRule(name="global-rule", site_id=None, plugin_id="anomaly-detector",
                          kind="threshold_crossing", scope="global", scope_id=None),
                AlertRule(name="away-rule", site_id=2, plugin_id="anomaly-detector",
                          kind="anomaly_rise", scope="site", scope_id=2),
            ]
        )
        session.commit()
        session.add_all(
            [
                SignalEvent(ts=BASE, site_id=1, gateway_id=gw1["gateway_id"], node_id=1,
                            slot=0, freq_khz=5800000, kind="sample", metric="rssi",
                            value=-60.0, source="real", payload_json='{"frame": 1}'),
                SignalEvent(ts=BASE, site_id=2, gateway_id=gw2["gateway_id"],
                            kind="sample", source="real"),
                SampleSummary(node_id=1, slot=0, ts_bucket=BUCKET, site_id=1,
                              gateway_id=gw1["gateway_id"], bucket_s=1, sample_count=10,
                              ema_count=8, rssi_mean=-61.0, rssi_min=-70.0, rssi_max=-55.0,
                              rssi_last=-56.0, ema_mean=-60.5, source="real",
                              last_at=BASE),
                PluginFinding(ts=BASE, site_id=1, gateway_id=gw1["gateway_id"], node_id=1,
                              slot=0, plugin_id="anomaly-detector",
                              producer="anomaly-detector@1.0.0", kind="anomaly_rise",
                              metric="rssi", value=-40.0, threshold=-85.0, source="real",
                              evidence_json='{"nodes": [{"node_id": 1}]}'),
                Alert(rule_id=1, site_id=1, gateway_id=gw1["gateway_id"], node_id=1, slot=0,
                      kind="anomaly_rise", metric="rssi", value=-40.0, threshold=-85.0,
                      ts=BASE, last_ts=BASE, count=2, status="open", source="real",
                      dedup_key="r1:g1:n1:s0", evidence_json="{}"),
                CandidateTarget(site_id=1, kind="flier", producer="target-builder@1.0.0",
                                confidence=0.7, confidence_basis="rule:anomaly_rise",
                                first_seen_at=BASE, last_seen_at=BASE,
                                coverage_json='{"unknown": ["altitude"]}'),
                TrackObservation(target_id=1, finding_id=1, site_id=1,
                                 gateway_id=gw1["gateway_id"], node_id=1, slot=0, ts=BASE,
                                 metric="rssi", value=-40.0, source="real",
                                 evidence_json="{}"),
            ]
        )
        session.commit()
        session.add_all(
            [
                LayoutWidget(instance_id="w1", layout_id=1, plugin_id="anomaly-detector",
                             pos_x=1, pos_y=2, pos_w=4, pos_h=2,
                             config_json='{"threshold_db": -50}'),
                LayoutWidget(instance_id="away-w", layout_id=2, plugin_id="anomaly-detector"),
            ]
        )
        session.commit()
    return {"gw1": gw1["gateway_id"], "gw2": gw2["gateway_id"]}


def export_zip(client, site_id=1) -> tuple[bytes, dict]:
    row = client.get(f"/api/export/site/{site_id}")
    assert row.status_code == 200, row.text
    zf = zipfile.ZipFile(io.BytesIO(row.content))
    manifest = json.loads(zf.read("manifest.json"))
    return row.content, manifest


def rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def test_roundtrip_copies_the_site_field_by_field(client, two_sites, tmp_path):
    payload, manifest = export_zip(client)

    assert manifest["site"] == {"site_id": 1, "slug": "home", "name": "主场"}
    assert manifest["format"] == 1
    assert manifest["schema_version"] == client.app.state.db.schema_version()
    assert manifest["counts"]["signal_event"] == 1
    assert manifest["counts"]["node"] == 1
    # 清单行数 = 裁剪后保留的行数：home 规则 1 条 + 实例级 NULL 规则 1 条。
    assert manifest["counts"]["alert_rule"] == 2
    assert set(manifest["instance_tables_carried"]) == set(INSTANCE_TABLES)

    import_db = tmp_path / "imported.sqlite3"
    result = migrate.run_migrations(import_db)
    assert result.ok, result.error
    import_app = create_app(Settings(db_path=import_db, dialer_enabled=False))
    with TestClient(import_app) as import_client:
        row = import_client.post(
            "/api/import/site-package",
            files={"file": ("site.zip", payload, "application/zip")},
        )
        assert row.status_code == 200, row.text
        report = row.json()
        assert report["active_db_untouched"] is True
        assert report["site"] == {"site_id": 1, "slug": "home"}
        assert report["imported_to"] != str(import_db)
        assert report["imported_to"].startswith(str(import_db) + ".imports" + "/")

        snapshot = zipfile.ZipFile(io.BytesIO(payload)).read("db.sqlite")
        dump = import_app.state.db.path.parent / "snapshot-for-check.sqlite"
        dump.write_bytes(snapshot)
        with sqlite3.connect(dump) as src, \
                sqlite3.connect(f"file:{report['imported_to']}?mode=ro", uri=True) as got:
            for table in (*COUNTED_TABLES, *INSTANCE_TABLES):
                assert rows(src, table) == rows(got, table), f"{table} 往返后不一致"
        dump.unlink()

    # 现网库没被碰到：away 场地与它的数据都还在。
    with sqlite3.connect(client.app.state.db.path) as live:
        live_sites = {r[0]: r[1] for r in live.execute("SELECT site_id, slug FROM site")}
    assert live_sites == {1: "home", 2: "away"}


def test_bad_schema_version_is_rejected_without_half_import(client, two_sites, tmp_path):
    payload, manifest = export_zip(client)
    manifest["schema_version"] = "999-not-real"
    tampered = io.BytesIO()
    with zipfile.ZipFile(tampered, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("db.sqlite", zipfile.ZipFile(io.BytesIO(payload)).read("db.sqlite"))

    import_db = tmp_path / "imported.sqlite3"
    assert migrate.run_migrations(import_db).ok
    import_app = create_app(Settings(db_path=import_db, dialer_enabled=False))
    with TestClient(import_app) as import_client:
        row = import_client.post(
            "/api/import/site-package",
            files={"file": ("site.zip", tampered.getvalue(), "application/zip")},
        )
    assert row.status_code == 422, row.text
    assert row.json()["detail"]["reason"] == "schema_version_mismatch"

    imports_dir = import_db.parent / (import_db.name + ".imports")
    assert not imports_dir.exists() or list(imports_dir.iterdir()) == [], \
        "拒绝的包不许留下半个库文件"


def test_garbage_upload_is_rejected_as_not_a_zip(client, two_sites, tmp_path):
    import_db = tmp_path / "imported.sqlite3"
    assert migrate.run_migrations(import_db).ok
    import_app = create_app(Settings(db_path=import_db, dialer_enabled=False))
    with TestClient(import_app) as import_client:
        row = import_client.post(
            "/api/import/site-package",
            files={"file": ("site.zip", b"this is not a zip", "application/zip")},
        )
    assert row.status_code == 422, row.text
    assert row.json()["detail"]["reason"] == "not_a_zip"


def test_import_is_write_side_and_refused_on_aggregator(client, two_sites, tmp_path):
    payload, _ = export_zip(client)
    import_db = tmp_path / "imported.sqlite3"
    assert migrate.run_migrations(import_db).ok
    agg_app = create_app(
        Settings(db_path=import_db, dialer_enabled=False, instance_role="aggregator")
    )
    with TestClient(agg_app) as agg_client:
        row = agg_client.post(
            "/api/import/site-package",
            files={"file": ("site.zip", payload, "application/zip")},
        )
    assert row.status_code == 405, row.text
    assert row.json()["detail"]["reason"] == "aggregator_read_only"
