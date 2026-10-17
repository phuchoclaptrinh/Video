import pytest
from sqlalchemy import select, update

from app.services.platform import drafts
from app.services.video_execution_store import executions
from tests.test_multi_provider import CATALOG, configure, plan

pytest_plugins = ["tests.test_platform"]


def test_unavailable_video_is_rejected_before_saving(client, monkeypatch):
    platform, _ = configure(client, monkeypatch)
    model = next(
        m
        for p in CATALOG["video_providers"]
        if p["id"] == "seedvis"
        for m in p["models"]
        if m["id"] == "Veo-3.1"
    )
    monkeypatch.setitem(model, "available", False)
    result = plan(client, "seedvis", "Veo-3.1")
    assert result.status_code == 409 and "chưa khả dụng" in result.json()["detail"]
    with platform.engine.connect() as db:
        assert db.execute(select(drafts)).first() is None


def test_unavailable_image_does_not_block_text_only_clip(client, monkeypatch):
    configure(client, monkeypatch)
    model = CATALOG["image_providers"][1]["models"][0]
    monkeypatch.setitem(model, "available", False)
    assert plan(client, "seedvis", "Veo-3.1").status_code == 201
    assert (
        plan(client, "seedvis", "Veo-3.1", use_planner=True, require_planner=True).status_code
        == 409
    )


@pytest.mark.parametrize("voice_mode", [None, "native_veo"])
def test_not_started_retry_uses_new_request_once_and_keeps_model(client, monkeypatch, voice_mode):
    platform, _ = configure(client, monkeypatch)
    draft = plan(client, "seedvis", "Veo-3.1").json()
    path = f"/api/v1/plans/{draft['id']}/generate"
    body = {"confirm_paid": True, **({"voice_mode": voice_mode} if voice_mode else {})}
    first = client.post(path, json=body).json()
    with platform.engine.begin() as db:
        db.execute(
            update(executions)
            .where(executions.c.generation_id == first["id"])
            .values(
                status="FAILED",
                error_code="seedvis_not_started",
                result={"error_code": "seedvis_not_started"},
            )
        )
    assert client.get(f"/api/v1/generations/{first['id']}").json()["retryable"] is True
    assert client.post(path, json={"confirm_paid": False}).status_code == 409
    second = client.post(path, json=body).json()
    assert second["id"] != first["id"]
    old, new = (platform.store.get(item["id"])["payload"] for item in (first, second))
    assert old["model"] == new["model"] == "Veo-3.1"
    assert old["spec"]["intent"] == new["spec"]["intent"]
    assert client.post(path, json=body).json()["id"] == second["id"]


def test_other_failed_jobs_do_not_receive_new_request_id(client, monkeypatch):
    platform, _ = configure(client, monkeypatch)
    draft = plan(client, "seedvis", "Veo-3.1").json()
    path = f"/api/v1/plans/{draft['id']}/generate"
    first = client.post(path, json={"confirm_paid": True}).json()
    with platform.engine.begin() as db:
        db.execute(
            update(executions)
            .where(executions.c.generation_id == first["id"])
            .values(status="FAILED", error_code="seedvis_generation_failed")
        )
    assert client.get(f"/api/v1/generations/{first['id']}").json()["retryable"] is False
    assert client.post(path, json={"confirm_paid": True}).json()["id"] == first["id"]
