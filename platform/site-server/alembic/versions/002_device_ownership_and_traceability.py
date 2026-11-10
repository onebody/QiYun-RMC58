"""002: Device ownership + Measurement traceability (V1.1.2.1)

Adds:
  devices.owner_agent_id          — FK → agents.id, nullable
  devices.ownership_status        — string: owned | unassigned | observer
  devices.owner_assigned_at       — when owner was set
  devices.owner_updated_at        — last ownership change

  measurements.agent_id           — FK → agents.id, nullable (历史保留 NULL)
  measurements.device_boot_id     — C5 设备启动周期标识, 可空
  measurements.device_id + agent_id partial unique 索引 (幂等扩展)

Revision ID: 002_device_ownership
Revises: 001_initial
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "002_device_ownership"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Device 表加列 ---
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "owner_agent_id",
            sa.String(length=64),
            sa.ForeignKey("agents.id", name="fk_devices_owner_agent_id"),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "ownership_status",
            sa.String(length=32),
            nullable=False,
            server_default="unassigned",
        ))
        batch_op.add_column(sa.Column(
            "owner_assigned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "owner_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ))
        batch_op.create_index(
            "ix_devices_owner",
            ["owner_agent_id"],
            unique=False,
        )

    # --- Measurement 表加列 ---
    with op.batch_alter_table("measurements", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "agent_id",
            sa.String(length=64),
            sa.ForeignKey("agents.id", name="fk_measurements_agent_id"),
            nullable=True,  # 历史记录保持 NULL
        ))
        batch_op.add_column(sa.Column(
            "device_boot_id",
            sa.String(length=64),
            nullable=True,
        ))
        batch_op.create_index(
            "ix_measurements_agent",
            ["agent_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_measurements_device_boot",
            ["device_boot_id"],
            unique=False,
        )


def downgrade() -> None:
    # --- Measurement 删列 ---
    with op.batch_alter_table("measurements", schema=None) as batch_op:
        batch_op.drop_index("ix_measurements_device_boot")
        batch_op.drop_index("ix_measurements_agent")
        batch_op.drop_constraint("fk_measurements_agent_id", type_="foreignkey")
        batch_op.drop_column("device_boot_id")
        batch_op.drop_column("agent_id")

    # --- Device 删列 ---
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index("ix_devices_owner")
        batch_op.drop_constraint("fk_devices_owner_agent_id", type_="foreignkey")
        batch_op.drop_column("owner_updated_at")
        batch_op.drop_column("owner_assigned_at")
        batch_op.drop_column("ownership_status")
        batch_op.drop_column("owner_agent_id")
