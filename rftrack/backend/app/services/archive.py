"""场地归档（S9）：把一个场地连同它的全部下级与观测数据装进一个 ZIP，跨机器唯一的数据搬家通道。

四条口径在本文件落死：

1. **快照用 `sqlite3.Connection.backup()`**，不是热库直接 copy（WAL 下 copy 出来的是
   半个事务），也不是 `VACUUM INTO` 后手工裁剪（申报过的禁手）。backup 出的文件是
   完整、一致的一份，**过滤发生在快照副本上**，现网库一个字节都不动。
2. **过滤按场地归属裁剪**：带 `site_id` 的表按列删别的场地；不带的两张经父键推导
   （node→gateway→relay，layout_widget→layout）。`app_config`/`plugin_*`/
   `channel_plan*`/`local_settings_import` 是实例级的东西，不属任何场地——原样带上
   并在清单里申报（换库重启后由 `registry.sync()` 对齐注册表，导入接口不碰任何表）。裁完必须过 `foreign_key_check`，
   有孤儿行直接失败，不许带着坏结构出门。
3. **导入只写新库文件**（R18 的原文）：校验 ZIP→清单→`schema_version` 必须与当前
   库版本相等（老包先在源头升级再导，拒绝而不是半导入）→ 快照落到 `<库>.imports/`
   下的新文件 → 完整性与清单行数逐一核对 → 返回路径与"下一步人工切换"的说明。
   **绝不覆盖 `RFTRACK_DB_PATH` 指着的现网库**，切换是运营者自己改配置重启的事。
4. **导入是写侧**（`require_writable_session`，aggregator 405），导出是读侧（汇聚
   实例也允许——它读的就是别人场地的东西）。
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from sqlalchemy.orm import Session

from .. import APP_VERSION
from ..database import Database
from ..models import Site, to_utc, utcnow
from .hierarchy import not_found, unprocessable

LOG = logging.getLogger("rftrack.archive")

#: ZIP 内两个成员的固定名：清单在前，快照在后。
MANIFEST_NAME = "manifest.json"
DB_NAME = "db.sqlite"

#: 归档格式版本：清单结构变了就 +1，导入侧只认自己认识的。
ARCHIVE_FORMAT = 1

#: 带 `site_id` 列、按列裁剪的表（→ 导出后在清单里报行数）。
SITE_SCOPED_TABLES = (
    "site",
    "relay",
    "workbench_layout",
    "signal_event",
    "sample_summary",
    "plugin_finding",
    "alert",
    "candidate_target",
    "track_observation",
)

#: 不带 `site_id`、经父键推导裁剪的表：谓词引用**还没裁**的父表，顺序固定
#: （layout_widget 先于 workbench_layout，node/gateway 先于 relay）。
DERIVED_TABLES = (
    ("layout_widget",
     "layout_id IN (SELECT layout_id FROM workbench_layout WHERE site_id != :sid)"),
    ("node",
     "gateway_id IN (SELECT gateway_id FROM gateway WHERE relay_id IN "
     "(SELECT relay_id FROM relay WHERE site_id != :sid))"),
    ("gateway",
     "relay_id IN (SELECT relay_id FROM relay WHERE site_id != :sid)"),
)

#: `alert_rule` 单独处理：`site_id` 可空（NULL = 实例级规则），只裁"明确属于别的场地"的行。
ALERT_RULE_PREDICATE = "site_id IS NOT NULL AND site_id != :sid"

#: 实例级、不属任何场地：原样带进归档并在清单申报。
INSTANCE_TABLES = (
    "app_config",
    "channel_plan",
    "channel_plan_item",
    "plugin_registry",
    "plugin_state",
    "plugin_log",
    "local_settings_import",
    "alembic_version",
)

#: 清单里报行数的全部表（裁剪过的 + 推导裁剪的）。
COUNTED_TABLES = (*SITE_SCOPED_TABLES, "alert_rule", *dict(DERIVED_TABLES))


def site_row(session: Session, site_id: int) -> Site:
    site = session.get(Site, site_id)
    if site is None:
        raise not_found("site", site_id)
    return site


def _prune(conn: sqlite3.Connection, site_id: int) -> dict[str, int]:
    """在快照副本上删掉别的场地。裁剪期间关 FK（顺序已声明），裁完必须查干净并恢复。"""
    counts: dict[str, int] = {}
    for table, predicate in DERIVED_TABLES:
        counts[table] = conn.execute(
            f"DELETE FROM {table} WHERE {predicate}", {"sid": site_id}
        ).rowcount
    counts["alert_rule"] = conn.execute(
        f"DELETE FROM alert_rule WHERE {ALERT_RULE_PREDICATE}", {"sid": site_id}
    ).rowcount
    for table in SITE_SCOPED_TABLES:
        if table == "site":
            continue
        counts[table] = conn.execute(
            f"DELETE FROM {table} WHERE site_id != :sid", {"sid": site_id}
        ).rowcount
    # site 行本身最后删：上面循环清掉的是引用它的下级行，现在才没有孩子指着它。
    counts["site"] = conn.execute(
        "DELETE FROM site WHERE site_id != :sid", {"sid": site_id}
    ).rowcount
    conn.commit()
    orphans = conn.execute("PRAGMA foreign_key_check").fetchall()
    if orphans:
        raise RuntimeError(f"归档裁剪后出现孤儿行，拒绝打包：{orphans[:5]}")
    return counts


def export_site_archive(
    session: Session, db: Database, site_id: int
) -> tuple[Path, dict]:
    """出一份场地 ZIP。返回 (临时文件路径, 清单)；调用方发完负责删临时文件。"""
    site = site_row(session, site_id)

    tmp = Path(tempfile.mkstemp(prefix="rftrack-export-", suffix=".zip")[1])
    snapshot_path = tmp.with_suffix(".snapshot.sqlite")
    try:
        live = db.engine.raw_connection()
        try:
            with closing(sqlite3.connect(snapshot_path)) as target:
                live.backup(target)
        finally:
            live.close()

        # 快照继承现网库的 WAL 模式：裁剪必须真正落进主文件才能进 ZIP，
        # 所以这里转回 DELETE 日志模式（顺带 checkpoint），且连接必须显式关——
        # `with sqlite3.connect()` 只管事务、不关连接，会把裁剪留在 -wal 里丢掉。
        with closing(sqlite3.connect(snapshot_path)) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA foreign_keys=OFF")
            deleted = _prune(conn, site_id)
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in COUNTED_TABLES
            }
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        if row is None:
            raise RuntimeError("快照里读不到 alembic_version，拒绝打包")

        manifest = {
            "kind": "rftrack-site-archive",
            "format": ARCHIVE_FORMAT,
            "schema_version": row[0],
            "app_version": APP_VERSION,
            "exported_at": to_utc(utcnow()).isoformat(),
            "site": {"site_id": site.site_id, "slug": site.slug, "name": site.name},
            "counts": counts,
            "pruned_other_sites_rows": deleted,
            "instance_tables_carried": list(INSTANCE_TABLES),
            "notes": [
                "数据只含上述场地；实例级表（instance_tables_carried）原样携带，"
                "插件注册表由换库后那台实例启动时的 registry.sync() 对齐（导入接口本身不改任何表）",
                "导入必须走 POST /api/import/site-package，只写新库文件，不自动切换现网库",
            ],
        }
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=1))
            zf.write(snapshot_path, DB_NAME)
        return tmp, manifest
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        snapshot_path.unlink(missing_ok=True)


def _read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        return zf.read(name)
    except KeyError as exc:
        raise unprocessable("missing_member", member=name) from exc


def _import_path(db: Database, slug: str) -> Path:
    """`<库>.imports/` 下的唯一新文件名：绝不指向现网库本身，撞名追加序号而不是覆盖。"""
    base = db.path.parent / f"{db.path.name}.imports"
    base.mkdir(parents=True, exist_ok=True)
    stamp = to_utc(utcnow()).strftime("%Y%m%dT%H%M%SZ")
    candidate = base / f"site-{slug}-{stamp}.sqlite"
    n = 2
    while candidate.exists():
        candidate = base / f"site-{slug}-{stamp}-{n}.sqlite"
        n += 1
    return candidate


def import_site_package(db: Database, payload: bytes) -> dict:
    """把一份场地 ZIP 落成**新库文件**并核对。返回导入报告；现网库不被触碰。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise unprocessable("not_a_zip", error=str(exc)) from exc
    with zf:
        try:
            manifest = json.loads(_read_member(zf, MANIFEST_NAME))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise unprocessable("bad_manifest", error=str(exc)) from exc
        if not isinstance(manifest, dict) or manifest.get("kind") != "rftrack-site-archive":
            raise unprocessable("bad_manifest", expected_kind="rftrack-site-archive")
        if manifest.get("format") != ARCHIVE_FORMAT:
            raise unprocessable(
                "format_unsupported", expected=ARCHIVE_FORMAT, actual=manifest.get("format")
            )
        live_version = db.schema_version()
        if manifest.get("schema_version") != live_version:
            # 老包先在源头升级再导：给一个"半导入"的库比拒绝它危险得多。
            raise unprocessable(
                "schema_version_mismatch", expected=live_version, actual=manifest.get("schema_version")
            )
        snapshot = _read_member(zf, DB_NAME)

    target = _import_path(db, str(manifest.get("site", {}).get("slug", "unknown")))
    target.write_bytes(snapshot)
    try:
        with closing(sqlite3.connect(target)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise unprocessable("integrity_failed", detail=str(integrity)[:200])
            orphans = conn.execute("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise unprocessable("foreign_keys_broken", detail=str(orphans[:5]))
            actual = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in COUNTED_TABLES
            }
            site = conn.execute(
                "SELECT site_id, slug FROM site ORDER BY site_id"
            ).fetchall()
        declared = manifest.get("counts")
        if not isinstance(declared, dict) or declared != actual:
            diff = {
                t: {"manifest": (declared or {}).get(t), "actual": actual.get(t)}
                for t in set(actual) | set(declared or {})
                if (declared or {}).get(t) != actual.get(t)
            }
            raise unprocessable("manifest_counts_mismatch", diff=diff)
        if len(site) != 1 or site[0][0] != manifest.get("site", {}).get("site_id"):
            raise unprocessable(
                "site_mismatch",
                manifest=manifest.get("site"),
                rows=site,
            )
    except BaseException:
        # 校验失败（422）还是别的什么错，落了一半的目标文件都必须清掉。
        target.unlink(missing_ok=True)
        raise

    LOG.warning("场地归档导入成新库（未切换现网）：%s", target)
    return {
        "imported_to": str(target),
        "active_db_untouched": True,
        "manifest": manifest,
        "counts": actual,
        "site": {"site_id": site[0][0], "slug": site[0][1]},
        "next_step": (
            "人工切换：把 RFTRACK_DB_PATH 指向 imported_to 的路径并重启实例；"
            "本接口按 R18 不自动覆盖现网库"
        ),
    }
