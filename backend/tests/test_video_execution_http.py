"""Cross-service test with real loopback HTTP; never contacts a paid provider."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

from app.services.generation_orchestrator import ExecutionSettings, GenerationOrchestrator
from app.services.video_execution_client import VideoExecutionClient
from app.services.video_execution_store import ExecutionStore


@pytest.fixture
def video_service(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(
        os.environ,
        INTERNAL_API_KEY="local-test-key",
        VIDEO_ENABLE_FAKE="true",
        VIDEO_FAKE_DB=str(tmp_path / "fake.sqlite"),
        SEEDANCE_API_KEY="",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.pipelines.video_api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[2] / "ai-service",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"X-Internal-Key": "local-test-key"},
            timeout=5,
        ) as client:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail(process.stderr.read().decode())
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Video service failed to start")
            yield client
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


def test_real_http_text_image_and_idempotency(video_service, tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "jobs.sqlite"))
    store = ExecutionStore(engine)
    store.initialize()
    runtime = GenerationOrchestrator(
        store, VideoExecutionClient(video_service), ExecutionSettings(poll_seconds=0)
    )
    capabilities = runtime.client.call("fake", "capabilities")
    assert capabilities["provider"] == "fake" and capabilities["model"] == "fake"
    assert capabilities["native_audio"] is False and capabilities["video_continuation"] is False
    try:
        for operation in ["TEXT_GENERATION", "REFERENCE_GENERATION"]:
            body = {
                "spec": {
                    "request": {"request_id": operation, "user_id": "user", "tenant_id": "tenant"},
                    "intent": {"operation": operation, "goal": "A city at sunrise"},
                },
                "preflight_decision": "ALLOW",
                "policy_decision_id": "policy1",
            }
            if operation == "REFERENCE_GENERATION":
                body["spec"]["references"] = [{"asset_id": "a1", "role": "FIRST_FRAME"}]
                body["assets"] = [
                    {
                        "asset_id": "a1",
                        "mime_type": "image/png",
                        "source_url": "https://example.com/image.png",
                        "moderation_status": "APPROVED",
                    }
                ]
            job = runtime.enqueue("fake", body)
            assert runtime.enqueue("fake", body)["generation_id"] == job["generation_id"]
            assert runtime.tick(job["generation_id"])["status"] == "SUBMITTED"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                job = runtime.tick(job["generation_id"])
                if job["status"] == "POST_PROCESSING":
                    break
                time.sleep(0.1)
            assert job["status"] == "POST_PROCESSING"
            assert job["result"]["outputs"][0]["source_url"]
    finally:
        engine.dispose()
