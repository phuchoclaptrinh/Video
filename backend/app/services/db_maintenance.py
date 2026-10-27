"""Startup upkeep for the SQLite file: drop inline images from finished rows, then compact the file.

It runs before the platform opens its engine, so no other connection is writing while the file is swapped.
"""

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from app.services.inline_assets import strip_inline

logger = logging.getLogger(__name__)
# Jobs in these states are never submitted again, so their payload images are not needed.
FINISHED_JOBS = ("COMPLETED", "FAILED", "CANCELLED", "NEEDS_USER_ACTION")
BATCH = 25
# Compact only when it pays off: enough free pages, and most of the file is free.
MIN_FREE_BYTES = 32 * 1024 * 1024
MIN_FREE_SHARE = 0.3
CHECKED_TABLES = ("platform_users", "platform_drafts", "video_execution_jobs", "platform_credit_ledger")


def _strip(db: sqlite3.Connection, table: str, key: str, where: str) -> int:
    ids = [row[0] for row in db.execute(f"SELECT {key} FROM {table} WHERE payload LIKE '%\"data:%' AND {where}")]  # noqa: S608
    changed = 0
    for start in range(0, len(ids), BATCH):
        with db:
            for identifier in ids[start:start + BATCH]:
                raw = db.execute(f"SELECT payload FROM {table} WHERE {key} = ?", (identifier,)).fetchone()[0]  # noqa: S608
                payload = json.loads(raw)
                if strip_inline(payload):
                    db.execute(f"UPDATE {table} SET payload = ? WHERE {key} = ?", (json.dumps(payload), identifier))  # noqa: S608
                    changed += 1
    return changed


def _counts(db: sqlite3.Connection) -> dict[str, int]:
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in CHECKED_TABLES if table in names}


def slim_sqlite_database(database_url: str, objects=None) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    path = Path(url.database)
    if not path.is_file():
        return
    finished = "status IN ({})".format(", ".join(f"'{s}'" for s in FINISHED_JOBS))
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        pending = any(
            table in tables and db.execute(
                f"SELECT 1 FROM {table} WHERE payload LIKE '%\"data:%' AND {where} LIMIT 1"  # noqa: S608
            ).fetchone()
            for table, where in (("video_execution_jobs", finished), ("platform_drafts", "1 = 1"))
        )
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0] * page_size
        total = db.execute("PRAGMA page_count").fetchone()[0] * page_size
        before = _counts(db)
    if not pending and (free < MIN_FREE_BYTES or free < total * MIN_FREE_SHARE):
        return
    # All rewriting happens on a copy in the container's temporary disk: the volume may be too full for
    # even the rollback journal, and is only written once, when the much smaller file replaces the original.
    work_dir = Path(tempfile.mkdtemp(prefix="vo3-compact-"))
    work, compact = work_dir / "work.sqlite3", work_dir / path.name
    keep_compact = False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as source,                 closing(sqlite3.connect(work)) as copy:
            source.backup(copy)
        db = sqlite3.connect(work)
        try:
            stripped = 0
            if "video_execution_jobs" in tables:
                stripped += _strip(db, "video_execution_jobs", "generation_id", finished)
            if "platform_drafts" in tables:
                # Drafts are only shown, never sent: their stored payload images are not used again.
                stripped += _strip(db, "platform_drafts", "id", "1 = 1")
            if stripped:
                logger.warning("Dropped inline images from %s finished rows", stripped)
            db.execute("VACUUM INTO ?", (str(compact),))
        finally:
            db.close()
        check = sqlite3.connect(compact)
        try:
            healthy = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and _counts(check) == before
        finally:
            check.close()
        if not healthy:
            logger.error("Compacted database failed verification; keeping the original")
            return
        if objects is not None:
            # A copy of the file being replaced, for restoring by hand if anything looks wrong later.
            objects.upload(path, f"db-backups/{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3",
                           "application/vnd.sqlite3")
        # The volume may be too full for a second copy next to the original, so the original goes first.
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        path.unlink()
        try:
            shutil.copyfile(compact, path)
        except BaseException:
            # The verified copy is now the only one; leave it in place for a manual restore.
            keep_compact = True
            logger.critical("Could not move the compacted database into place; it is kept at %s", compact)
            raise
        logger.warning("Compacted %s from %.1f MB to %.1f MB", path.name, total / 1e6, os.path.getsize(path) / 1e6)
    finally:
        if not keep_compact:
            shutil.rmtree(work_dir, ignore_errors=True)


def run_startup_upkeep(database_url: str, objects=None) -> None:
    """Upkeep must never stop the app from starting, except when the database file itself went missing."""
    try:
        slim_sqlite_database(database_url, objects)
    except Exception:
        url = make_url(database_url)
        if url.get_backend_name() == "sqlite" and url.database and not Path(url.database).is_file():
            raise  # starting now would create an empty database over the lost one
        logger.exception("Database upkeep failed; starting with the file as it is")
