"""`007_node_identity`：七列的形状、两条建库路径、以及撤得回去。

三条口径：

- **旧行不许被补造**。迁移前就存在的节点行，INFO 那四列与 `boot_id`/`boot_changed_at` 一律
  回 `NULL`。只有 `time_sync_quality` 拿到 `'unsynced'`——"这台没做过时间同步"是一句事实
  陈述，不是"缺数据"，所以它配得上一个默认值，别的都不配。
- **两条建库路径落到同一个结构**。`create_all + stamp` 与 `alembic upgrade head` 的列序必须
  一致（`test_migrations._facts` 比的就是 `PRAGMA table_info` 的顺序），所以新列在
  `models.Node` 里追加在末尾、迁移里也按同一顺序 `add_column`。这条测试就是那处顺序的回归线。
- **撤得回去**。`down()` 只动这七列，`node` 上原有的列与那一行数据一行不许少。

`identity_source` 的**行为**（谁写的、设备赢还是人工赢）在 `test_info_line.py`，
那里已经有真 `client` + 真端点表 + 真采集管线；这个文件只管结构，不开应用。

Revision 号位：本迁移占 `007`（号位 = 执行顺序），原计划的 relay ingress 顺延为
`008_relay_ingress`。
"""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

import migrate
from app.models import Base

#: 追加顺序 = `models.Node` 里的声明顺序 = 迁移里 `add_column` 的顺序。三处必须一字不差，
#: 差一个位置就是"我这台机器当初走的是哪条建库路"这种查不出来的差异。
NEW_COLUMNS = [
    "identity_source",
    "identity_target",
    "commit_time",
    "phy_version",
    "boot_id",
    "boot_changed_at",
    "time_sync_quality",
]


def _columns(path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute("PRAGMA table_info(node)")]


def _one(path, sql: str) -> tuple:
    """裸 sqlite3 游标没有 `.one()`，取一行要 `fetchone()` 并自己确认它存在——
    否则"行没了"会以 `TypeError: 'NoneType' object is not subscriptable` 的形式出现，
    看不出是数据被冲掉了还是列名写错了。"""
    with sqlite3.connect(path) as conn:
        row = conn.execute(sql).fetchone()
    assert row is not None, "node_id=1 那行必须还在"
    return row


@pytest.fixture
def legacy_db(tmp_path):
    """一个 006 时代的库：一台接入机、一块板、一行身份还没人管过的节点。"""
    db = tmp_path / "s6_era.sqlite3"
    command.upgrade(migrate._alembic_config(migrate.url_for(db)), "006_targets")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO site (site_id, name, slug, timezone, status, origin, created_at, "
            "updated_at) VALUES (1, '主场', 'main', 'UTC', 'active', 'local', "
            "'2026-10-29 00:00:00+00:00', '2026-10-29 00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO relay (relay_id, site_id, name, status, created_at, updated_at) "
            "VALUES (1, 1, '主场塔', 'active', '2026-10-29 00:00:00+00:00', "
            "'2026-10-29 00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO gateway (gateway_id, relay_id, name, kind, status, dial_failures) "
            "VALUES (1, 1, 'pi-main', 'usb-bridge', 'active', 0)"
        )
        conn.execute(
            "INSERT INTO node (node_id, gateway_id, display_name, transport_kind, "
            "transport_addr, fw_version, protocol_version, status, stops_rh) "
            "VALUES (1, 1, 'widscan58-A', 'uart-pl011', '/dev/widscan58-A', "
            "'0.5.2', 1, 'active', 0)"
        )
        conn.commit()
    return db


def test_the_head_is_the_identity_migration_stacked_on_targets():
    assert migrate.head_revision() == "007_node_identity"


def test_upgrade_adds_the_seven_columns_after_the_existing_ones(legacy_db):
    before = _columns(legacy_db)
    result = migrate.run_migrations(legacy_db)
    assert result.ok, result.error
    after = _columns(legacy_db)
    assert after[: len(before)] == before, "新列必须追加在末尾：两条建库路径的列序要一致"
    assert after[len(before) :] == NEW_COLUMNS


def test_an_old_row_keeps_nulls_and_only_gets_the_semantic_default(legacy_db):
    """旧行 `fw_version='0.5.2'` 是当年人工 PUT 进去的，迁移不许把它冲成 NULL。"""
    assert migrate.run_migrations(legacy_db).ok
    row = _one(
        legacy_db,
        "SELECT identity_source, identity_target, commit_time, phy_version, boot_id, "
        "boot_changed_at, time_sync_quality, fw_version FROM node WHERE node_id=1",
    )
    assert row[:6] == (None,) * 6
    assert row[6] == "unsynced", "没做过时间同步是一句事实，不是缺数据"
    assert row[7] == "0.5.2"


def test_downgrade_drops_exactly_the_seven_columns_and_keeps_the_row(legacy_db):
    assert migrate.run_migrations(legacy_db).ok
    command.downgrade(migrate._alembic_config(migrate.url_for(legacy_db)), "006_targets")
    assert set(_columns(legacy_db)) & set(NEW_COLUMNS) == set()
    kept = _one(
        legacy_db,
        "SELECT display_name, transport_addr, fw_version, protocol_version "
        "FROM node WHERE node_id=1",
    )
    assert kept == ("widscan58-A", "/dev/widscan58-A", "0.5.2", 1)


def test_both_bootstrap_paths_land_on_the_same_node_shape(tmp_path):
    """`create_all + stamp` 与 `upgrade head` 差一个列序，就会在运行时以 500 的形式还回来。"""
    stamped = tmp_path / "via_create_all.sqlite3"
    Base.metadata.create_all(create_engine(migrate.url_for(stamped)))
    assert migrate.run_migrations(stamped).ok

    upgraded = tmp_path / "via_alembic.sqlite3"
    command.upgrade(migrate._alembic_config(migrate.url_for(upgraded)), "head")

    assert _columns(upgraded) == _columns(stamped)
