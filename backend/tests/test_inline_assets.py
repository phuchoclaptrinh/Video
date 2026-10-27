import base64
import json
import sqlite3
from copy import deepcopy

import pytest
from sqlalchemy import create_engine

from app.services import db_maintenance
from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.inline_assets import (
    PRUNED_REF,
    AssetFileMissing,
    file_ref,
    inline_payload,
    restore_refs,
    strip_inline,
)
from app.services.video_execution_client import ExecutionError
from app.services.video_execution_store import ExecutionStore
from tests.test_video_execution import payload as execution_payload

IMAGE = b"\xff\xd8jpeg-bytes"
DATA_URL = "data:image/jpeg;base64," + base64.b64encode(IMAGE).decode()


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "storage"
    (root / "continuity").mkdir(parents=True)
    (root / "continuity" / "anchor-d1.jpg").write_bytes(IMAGE)
    return root


def with_ref(storage, asset_id="anchor-d1"):
    body = deepcopy(execution_payload())
    body["assets"] = [{"asset_id": asset_id, "mime_type": "image/jpeg", "moderation_status": "APPROVED",
                       "source_url": file_ref(storage, storage / "continuity" / "anchor-d1.jpg")}]
    return body


def test_references_are_inlined_for_sending_and_restored_for_storage(storage):
    stored = with_ref(storage)
    assert stored["assets"][0]["source_url"] == "vo3-file:continuity/anchor-d1.jpg"
    sent = inline_payload(storage, stored)
    assert sent["assets"][0]["source_url"] == DATA_URL
    assert stored["assets"][0]["source_url"].startswith("vo3-file:")
    assert restore_refs(stored, deepcopy(sent))["assets"][0]["source_url"] == "vo3-file:continuity/anchor-d1.jpg"
    plain = deepcopy(execution_payload())
    assert inline_payload(storage, plain) is plain


@pytest.mark.parametrize("ref", [PRUNED_REF, "vo3-file:continuity/missing.jpg", "vo3-file:../outside.jpg"])
def test_missing_pruned_or_outside_files_are_never_read(storage, ref):
    (storage.parent / "outside.jpg").write_bytes(IMAGE)
    body = with_ref(storage)
    body["assets"][0]["source_url"] = ref
    with pytest.raises(AssetFileMissing):
        inline_payload(storage, body)


def test_strip_drops_only_inline_images():
    body = {"assets": [{"source_url": DATA_URL}, {"source_url": "https://example.com/a.jpg"}]}
    assert strip_inline(body) and [item["source_url"] for item in body["assets"]] == [PRUNED_REF, "https://example.com/a.jpg"]
    assert not strip_inline(body)


class RecordingClient:
    def __init__(self):
        self.sent = []

    def call(self, provider, action, **kwargs):
        self.sent.append((action, deepcopy(kwargs.get("payload"))))
        if action == "validate":
            return {"execution_request": deepcopy(kwargs["payload"])}
        return {"provider": provider, "provider_task_id": "task-1", "status": "queued"}


def test_jobs_store_references_but_send_images(storage, tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "jobs.sqlite"))
    store = ExecutionStore(engine)
    store.initialize()
    runtime = GenerationOrchestrator(store, RecordingClient(), ExecutionSettings(poll_seconds=0))
    runtime.asset_resolver = lambda body: inline_payload(storage, body)
    job = runtime.enqueue("fake", with_ref(storage))
    assert store.get(job["generation_id"])["payload"]["assets"][0]["source_url"].startswith("vo3-file:")
    runtime.tick(job["generation_id"])
    actions = dict(runtime.client.sent)
    assert actions["validate"]["assets"][0]["source_url"] == DATA_URL
    assert actions["submit"]["assets"][0]["source_url"] == DATA_URL
    (storage / "continuity" / "anchor-d1.jpg").unlink()
    with pytest.raises(ExecutionError, match="asset_file_missing"):
        runtime._outgoing(with_ref(storage))
    engine.dispose()


def make_database(path, rows=40, image_bytes=40_000):
    big = "data:image/jpeg;base64," + "A" * image_bytes
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE video_execution_jobs (generation_id TEXT PRIMARY KEY, status TEXT, payload TEXT)")
    db.execute("CREATE TABLE platform_drafts (id TEXT PRIMARY KEY, payload TEXT)")
    db.execute("CREATE TABLE platform_users (id TEXT PRIMARY KEY)")
    db.execute("INSERT INTO platform_users VALUES ('u1')")
    for index in range(rows):
        status = "GENERATING" if index == 0 else "COMPLETED"
        db.execute("INSERT INTO video_execution_jobs VALUES (?, ?, ?)",
                   (f"g{index}", status, json.dumps({"assets": [{"source_url": big}]})))
        db.execute("INSERT INTO platform_drafts VALUES (?, ?)", (f"d{index}", json.dumps({"assets": [{"source_url": big}]})))
    db.commit()
    db.close()


class Backups:
    def __init__(self):
        self.uploaded = []

    def upload(self, path, relative, content_type):
        self.uploaded.append((relative, path.stat().st_size))


def test_startup_upkeep_drops_finished_images_and_compacts_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(db_maintenance, "MIN_FREE_BYTES", 100_000)
    path = tmp_path / "mvp.sqlite3"
    make_database(path)
    before = path.stat().st_size
    backups = Backups()
    db_maintenance.run_startup_upkeep("sqlite:///" + str(path), backups)
    assert path.stat().st_size < before / 4
    db = sqlite3.connect(path)
    statuses = dict(db.execute("SELECT generation_id, payload LIKE '%data:%' FROM video_execution_jobs"))
    assert statuses["g0"] == 1 and sum(statuses.values()) == 1  # the running job keeps its image
    assert db.execute("SELECT count(*) FROM platform_drafts WHERE payload LIKE '%data:%'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM platform_users").fetchone()[0] == 1
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()
    assert len(backups.uploaded) == 1 and backups.uploaded[0][1] == before
    # A second start finds nothing to do and keeps the file as it is.
    size = path.stat().st_size
    db_maintenance.run_startup_upkeep("sqlite:///" + str(path), backups)
    assert path.stat().st_size == size and len(backups.uploaded) == 1


def test_upkeep_failure_before_the_swap_keeps_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(db_maintenance, "MIN_FREE_BYTES", 100_000)
    path = tmp_path / "mvp.sqlite3"
    make_database(path)

    class Broken:
        def upload(self, *_args):
            raise OSError("R2 down")

    db_maintenance.run_startup_upkeep("sqlite:///" + str(path), Broken())
    db = sqlite3.connect(path)
    assert db.execute("SELECT count(*) FROM video_execution_jobs").fetchone()[0] == 40
    db.close()
