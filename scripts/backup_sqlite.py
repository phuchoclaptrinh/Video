"""Back up the SQLite database (and optionally uploaded media) while the app keeps running.

Uses SQLite's online backup API, so the copy is consistent even mid-write. Standard library only.

    python scripts/backup_sqlite.py                                   # backend/storage/mvp.sqlite3 -> ./backups
    python scripts/backup_sqlite.py --db /app/storage/mvp.sqlite3 --out /app/storage/backups
    python scripts/backup_sqlite.py --with-storage --keep-days 14     # also archive images/videos

Cron, daily at 02:00:
    0 2 * * * cd /opt/vo3 && python3 scripts/backup_sqlite.py --with-storage >> logs/backup.log 2>&1

On Railway the volume is not reachable from outside; use the admin console button
"Tải bản sao lưu" (GET /api/v1/admin/backup) to download the database instead.
"""

import argparse
import sqlite3
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def backup_database(db: Path, out: Path, stamp: str) -> Path:
    target = out / f"vo3-db-{stamp}.sqlite3"
    source, copy = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        source.backup(copy)
    finally:
        copy.close()
        source.close()
    with sqlite3.connect(target) as check:
        if check.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"Backup failed integrity check: {target}")
    return target


def backup_storage(storage: Path, out: Path, stamp: str, db: Path) -> Path:
    target = out / f"vo3-storage-{stamp}.tar.gz"
    skip = {db.resolve(), out.resolve()}

    def keep(info: tarfile.TarInfo):
        path = (storage.parent / info.name).resolve()
        # The database is copied consistently above; a raw copy of a live file could be torn.
        if path in skip or any(parent in skip for parent in path.parents) or path.name.endswith(("-wal", "-shm")):
            return None
        return info

    with tarfile.open(target, "w:gz") as archive:
        archive.add(storage, arcname=storage.name, filter=keep)
    return target


def prune(out: Path, keep_days: int) -> int:
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for path in out.glob("vo3-*"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=ROOT / "backend/storage/mvp.sqlite3")
    parser.add_argument("--out", type=Path, default=ROOT / "backups")
    parser.add_argument("--with-storage", action="store_true", help="also archive the storage folder")
    parser.add_argument("--keep-days", type=int, default=14, help="delete backups older than this")
    args = parser.parse_args()
    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    print("database:", backup_database(args.db, args.out, stamp))
    if args.with_storage:
        print("storage :", backup_storage(args.db.parent, args.out, stamp, args.db))
    print("pruned  :", prune(args.out, args.keep_days), "old file(s)")


if __name__ == "__main__":
    main()
