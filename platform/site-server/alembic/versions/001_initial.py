"""initial site-server schema — 10 tables."""
from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. sites
    op.create_table(
        "sites",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True, server_default="UTC"),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 2. agents
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True, server_default=""),
        sa.Column("agent_version", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("platform", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("os_info", sa.String(length=255), nullable=True, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="offline"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_id", sa.String(length=64), sa.ForeignKey("agent_credentials.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agents_site_id", "agents", ["site_id"])
    op.create_index("ix_agents_site_status", "agents", ["site_id", "status"])

    # 3. agent_credentials
    op.create_table(
        "agent_credentials",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("agent_id", sa.String(length=64), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("credential_hash", sa.String(length=255), nullable=False),
        sa.Column("pairing_code", sa.String(length=32), nullable=True),
        sa.Column("pairing_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=255), nullable=True, server_default=""),
    )
    op.create_index("ix_agent_credentials_agent_id", "agent_credentials", ["agent_id"])
    op.create_index("ix_agent_credentials_pairing_code", "agent_credentials", ["pairing_code"])

    # 4. devices
    op.create_table(
        "devices",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True, server_default=""),
        sa.Column("hardware_model", sa.String(length=128), nullable=True, server_default="esp32c5"),
        sa.Column("firmware_version", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("protocol_version", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("phy_version", sa.String(length=128), nullable=True, server_default=""),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="pending"),
        sa.Column("identity_confidence", sa.String(length=16), nullable=True, server_default="high"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_devices_site_id", "devices", ["site_id"])
    op.create_index("ix_devices_site_status", "devices", ["site_id", "status"])

    # 5. device_endpoints
    op.create_table(
        "device_endpoints",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("device_id", sa.String(length=64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("agent_id", sa.String(length=64), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("transport_type", sa.String(length=32), nullable=False),
        sa.Column("endpoint_identifier", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="connected"),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("device_id", "agent_id", "transport_type", "endpoint_identifier",
                            name="uq_device_endpoint"),
    )
    op.create_index("ix_device_endpoints_device_id", "device_endpoints", ["device_id"])
    op.create_index("ix_device_endpoints_agent_id", "device_endpoints", ["agent_id"])

    # 6. connection_sessions
    op.create_table(
        "connection_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("device_id", sa.String(length=64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("agent_id", sa.String(length=64), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("boot_id", sa.String(length=64), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnect_reason", sa.String(length=255), nullable=True, server_default=""),
        sa.Column("reconnect_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=True, server_default="connected"),
    )
    op.create_index("ix_connection_sessions_device_id", "connection_sessions", ["device_id"])
    op.create_index("ix_connection_sessions_agent_id", "connection_sessions", ["agent_id"])
    op.create_index("ix_sessions_device_connected", "connection_sessions", ["device_id", "status"])

    # 7. device_profiles
    op.create_table(
        "device_profiles",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("profile_type", sa.String(length=64), nullable=True, server_default="scan"),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_device_profiles_site_id", "device_profiles", ["site_id"])

    # 8. measurements
    op.create_table(
        "measurements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("device_id", sa.String(length=64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("session_id", sa.String(length=64), sa.ForeignKey("connection_sessions.id"), nullable=True),
        sa.Column("frame_type", sa.String(length=32), nullable=False),
        sa.Column("frame_type_code", sa.Integer(), nullable=True),
        sa.Column("device_timestamp_ms", sa.Integer(), nullable=True),
        sa.Column("agent_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=True, server_default="hardware"),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("event_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("site_id", "event_id", name="uq_measurement_event"),
    )
    op.create_index("ix_measurements_site_id", "measurements", ["site_id"])
    op.create_index("ix_measurements_device_id", "measurements", ["device_id"])
    op.create_index("ix_measurements_session_id", "measurements", ["session_id"])
    op.create_index("ix_measurements_frame_type", "measurements", ["frame_type"])
    op.create_index("ix_measurements_event_id", "measurements", ["event_id"])
    op.create_index("ix_measurements_device_time", "measurements", ["device_id", "created_at"])
    op.create_index("ix_measurements_site_time", "measurements", ["site_id", "created_at"])
    op.create_index("ix_measurements_site_frame", "measurements", ["site_id", "frame_type"])

    # 9. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True, server_default="system"),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=True, server_default=""),
        sa.Column("target_id", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_site_id", "audit_logs", ["site_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_site_time", "audit_logs", ["site_id", "created_at"])

    # 10. site_events
    op.create_table(
        "site_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=64), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=True, server_default="agent"),
        sa.Column("source_id", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("device_id", sa.String(length=64), nullable=True, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_site_events_site_id", "site_events", ["site_id"])
    op.create_index("ix_site_events_event_type", "site_events", ["event_type"])
    op.create_index("ix_site_events_device_id", "site_events", ["device_id"])
    op.create_index("ix_site_events_site_time", "site_events", ["site_id", "created_at"])


def downgrade() -> None:
    op.drop_table("site_events")
    op.drop_table("audit_logs")
    op.drop_table("measurements")
    op.drop_table("device_profiles")
    op.drop_table("connection_sessions")
    op.drop_table("device_endpoints")
    op.drop_table("devices")
    op.drop_table("agent_credentials")
    op.drop_table("agents")
    op.drop_table("sites")
