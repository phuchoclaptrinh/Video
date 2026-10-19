import os
import time
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.platform import Platform


class MemoryObjects:
    def __init__(self):
        self.files, self.types, self.stamps = {}, {}, {}

    def stat(self, relative):
        data = self.files.get(relative)
        return None if data is None else (len(data), self.stamps.get(relative, time.time()))

    def size(self, relative):
        found = self.stat(relative)
        return found[0] if found else None

    def upload(self, path, relative, content_type):
        self.files[relative] = path.read_bytes()
        self.types[relative] = content_type
        self.stamps[relative] = time.time()

    def download(self, relative, target):
        if relative not in self.files:
            raise FileNotFoundError(relative)
        target.write_bytes(self.files[relative])

    def url(self, relative, content_type, filename=None):
        return f"https://r2.example.test/{relative}" + (f"?name={filename}" if filename else "")


@pytest.fixture
def platform(tmp_path):
    current = Platform("sqlite:///" + str(tmp_path / "db.sqlite"), tmp_path / "storage",
                       "http://example.test", "test-key", object_storage=MemoryObjects())
    yield current
    current.close()


def video(platform, name, data=b"0123456789abcdef", age=0.0):
    path = platform.storage / "videos" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def test_sync_uploads_videos_and_drops_only_idle_local_copies(platform, monkeypatch):
    monkeypatch.setenv("R2_KEEP_LOCAL_HOURS", "24")
    recent = video(platform, "recent.mp4")
    idle = video(platform, "idle.mp4", age=25 * 3600)
    partial = video(platform, "merge.part.mp4", age=25 * 3600)
    platform.sync_media()
    assert set(platform.objects.files) == {"videos/recent.mp4", "videos/idle.mp4"}
    assert recent.is_file() and partial.is_file()
    assert not idle.exists()


def test_sync_backs_up_images_and_continuity_files_without_removing_them(platform):
    def write(relative, data, age=120):
        path = platform.storage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    upload = write("abc.jpg", b"uploaded-image")
    anchor = write("continuity/anchor-d1.jpg", b"anchor-v1")
    write("continuity/story-b1.mp3", b"narration")
    write("continuity/keyframe-d1-2-1a2b3c4d.jpg", b"staging", age=5)
    platform.sync_media()
    objects = platform.objects
    assert set(objects.files) == {"abc.jpg", "continuity/anchor-d1.jpg", "continuity/story-b1.mp3"}
    assert objects.types["continuity/story-b1.mp3"] == "audio/mpeg"
    assert upload.is_file() and anchor.is_file()
    objects.stamps["continuity/anchor-d1.jpg"] = time.time() - 600
    write("continuity/anchor-d1.jpg", b"anchor-v2", age=90)
    platform._backed_up_files.clear()
    platform.sync_media()
    assert objects.files["continuity/anchor-d1.jpg"] == b"anchor-v2"


def test_sync_keeps_a_local_copy_whose_upload_did_not_land(platform):
    class Broken(MemoryObjects):
        def upload(self, path, relative, content_type):
            self.files[relative] = b"short"

    platform.objects = Broken()
    path = video(platform, "idle.mp4", age=48 * 3600)
    platform.sync_media()
    assert path.is_file()


def test_evicted_video_is_restored_for_processing(platform):
    path = video(platform, "clip.mp4", data=b"clip-bytes-123", age=48 * 3600)
    platform.sync_media()
    assert not path.exists() and platform._video_available("clip.mp4")
    assert platform._video_file("clip.mp4").read_bytes() == b"clip-bytes-123"
    assert not platform._video_available("missing.mp4")


def test_video_endpoint_redirects_to_object_storage_once_moved(platform, monkeypatch):
    path = platform.storage / "videos" / "final.mp4"
    monkeypatch.setattr(platform, "job", lambda uid, gid: {"status": "COMPLETED"})
    monkeypatch.setattr(platform, "video_path", lambda uid, gid: path)
    with TestClient(create_app(platform, run_worker=False)) as http:
        with platform.engine.begin() as db:
            platform.create_account(db, "one@example.com", "test-password-123")
        login = {"email": "one@example.com", "password": "test-password-123"}
        assert http.post("/api/v1/auth/login", json=login).status_code == 200
        video(platform, "final.mp4", data=b"local-video-bytes")
        local = http.get("/api/v1/generations/g1/video")
        assert local.status_code == 200 and local.content == b"local-video-bytes"
        path.unlink()
        moved = http.get("/api/v1/generations/g1/video?download=true", follow_redirects=False)
        assert moved.status_code == 307
        assert moved.headers["location"] == "https://r2.example.test/videos/final.mp4?name=vo3-video.mp4"


def test_object_storage_signs_r2_requests():
    pytest.importorskip("boto3")
    from botocore.stub import Stubber

    from app.services.object_storage import ObjectStorage

    os.environ.update(R2_ENDPOINT="https://acct.r2.cloudflarestorage.com", R2_BUCKET="vo3-backup",
                      R2_ACCESS_KEY_ID="test-access", R2_SECRET_ACCESS_KEY="test-secret")
    try:
        objects = ObjectStorage.from_env()
    finally:
        for name in ("R2_ENDPOINT", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
            os.environ.pop(name)
    assert ObjectStorage.from_env() is None
    link = urlparse(objects.url("videos/a.mp4", "video/mp4", "vo3-video.mp4"))
    assert link.netloc.endswith("r2.cloudflarestorage.com") and link.path.endswith("/storage/videos/a.mp4")
    assert parse_qs(link.query)["response-content-disposition"] == ['attachment; filename="vo3-video.mp4"']
    with Stubber(objects.client) as stub:
        uploaded = datetime(2026, 10, 1, tzinfo=UTC)
        stub.add_response("head_object", {"ContentLength": 42, "LastModified": uploaded},
                          {"Bucket": "vo3-backup", "Key": "storage/videos/a.mp4"})
        stub.add_client_error("head_object", "404", http_status_code=404)
        assert objects.stat("videos/a.mp4") == (42, uploaded.timestamp())
        assert objects.size("videos/b.mp4") is None
