"""P0 阶段 3.1：`node` 的设备身份四列 + 启动会话三列。

`007` 这个号位按"号位 = 执行顺序"排：本迁移在阶段 3 落地，原计划的 relay ingress
顺延为 `008_relay_ingress`。

七列全部**可空或带语义默认**，所以旧库、旧行零影响：

- `identity_source` / `identity_target` / `commit_time` / `phy_version`——板子在 `INFO` 行里
  自己报的（`firmware/src/proto/commands.c:426`）。迁移前存在的行拿到 `NULL`，那是真相：
  **没人说过这块板的固件是哪版**，比补一个 `'unknown'` 或空串好查得多。
- `boot_id` / `boot_changed_at`——一次上电会话的身份。协议 v1 没有 `CLOCK?`，会话由软件
  重启判定派生（C-4），派生逻辑还没写，所以这里先给列、值留 `NULL`。
- `time_sync_quality`——七列里唯一 `NOT NULL` 的，默认 `'unsynced'`。"这台没做过时间同步"
  是一句**事实陈述**而不是缺数据，所以它配得上默认值；其余六列都不配。

`commit_time` 存文本不存 `DateTime`：固件那一格给的是构建时间戳**字符串**，可能为空，
把它解析成时刻等于替设备编造一个它没说过的时间。原样存，读侧要显示就直接显示。

列序与 `app/models.py` 的 `Node` 声明顺序一字不差——`create_all + stamp` 与 `upgrade head`
两条建库路径必须落到同一个结构上（`tests/test_migrations.py:193` 比 `PRAGMA table_info`
的顺序，`tests/test_node_identity.py` 再钉一次）。

Revision ID: 007_node_identity
Revises: 006_targets
Create Date: 2026-11-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "007_node_identity"
down_revision = "006_targets"
branch_labels = None
depends_on = None

#: `(列名, 类型, 是否可空, server_default)`。顺序 = `models.Node` 里的声明顺序。
COLUMNS: list[tuple[str, sa.types.TypeEngine, bool, str | None]] = [
    ("identity_source", sa.String(length=16), True, None),
    ("identity_target", sa.String(length=48), True, None),
    ("commit_time", sa.String(length=32), True, None),
    ("phy_version", sa.String(length=64), True, None),
    ("boot_id", sa.String(length=36), True, None),
    ("boot_changed_at", sa.DateTime(timezone=True), True, None),
    ("time_sync_quality", sa.String(length=16), False, "'unsynced'"),
]


def _node_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("node")}


def upgrade() -> None:
    """逐列判存在再加：场景 2（有数据无版本）走的是 `create_all` 先建全表、再从头 stamp
    增量 upgrade 那条路，这时七列已经在了——不判就会撞 "duplicate column name"，
    而那恰好是"你这台机器当初走的是哪条建库路"变成 500 的地方。"""
    have = _node_columns()
    for name, type_, nullable, server_default in COLUMNS:
        if name in have:
            continue
        op.add_column(
            "node",
            sa.Column(
                name, type_, nullable=nullable, server_default=sa.text(server_default)
                if server_default
                else None
            ),
        )


def downgrade() -> None:
    """倒着删，只删这七列。SQLite 3.35+ 支持 `DROP COLUMN`；这七列不在任何索引或约束里，
    所以不需要重建表——重建表会把 `uq_node_gateway_addr` 与外键的 ON DELETE 全抄一遍，
    抄漏一项就是一个静默的行为变化。"""
    have = _node_columns()
    for name, _type, _nullable, _default in reversed(COLUMNS):
        if name in have:
            op.drop_column("node", name)
