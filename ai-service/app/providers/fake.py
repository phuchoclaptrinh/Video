"""Persistent fake task metadata. Outputs are fixtures, not generated videos."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from app.providers.seedance import SeedanceProvider
from app.providers.seedance_compiler import SeedanceCompiler
from app.schemas.provider import ProviderError, ProviderResult


class FakeVideoProvider(SeedanceProvider):
    name = "fake"

    def __init__(self, database: str, *, delay: float = 2, fail: bool = False):
        self.compiler = SeedanceCompiler("fake")
        self.database = database
        self.delay = delay
        self.fail = fail
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS fake_tasks "
                "(id TEXT PRIMARY KEY, created REAL, cancelled INTEGER, payload TEXT)"
            )

    async def submit(self, payload: dict) -> ProviderResult:
        task_id = "fake-" + uuid4().hex
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO fake_tasks VALUES (?, ?, 0, ?)",
                (task_id, time.time(), json.dumps(payload)),
            )
        return ProviderResult(provider=self.name, provider_task_id=task_id, status="queued")

    async def get_status(self, task_id: str) -> ProviderResult:
        with closing(sqlite3.connect(self.database)) as db, db:
            row = db.execute(
                "SELECT created, cancelled FROM fake_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise ProviderError("provider_http_404")
        status = "running"
        if row[1]:
            status = "cancelled"
        elif time.time() - row[0] >= self.delay:
            status = "failed" if self.fail else "succeeded"
        return self.normalize_result(
            {
                "id": task_id,
                "status": status,
                "content": {"video_url": "https://example.com/fake-video.mp4"},
                "usage": {"completion_tokens": 0},
            }
        )

    async def cancel(self, task_id: str) -> ProviderResult:
        result = await self.get_status(task_id)
        if result.status not in {"completed", "failed", "cancelled"}:
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("UPDATE fake_tasks SET cancelled = 1 WHERE id = ?", (task_id,))
            return await self.get_status(task_id)
        return result
