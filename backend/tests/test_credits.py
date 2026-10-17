# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from app.services.platform_shared import credit_ledger, platform_settings, users
from app.services.video_execution_store import executions
from tests.test_admin import ADMIN, MEMBER, user_id
from tests.test_platform import client, register  # noqa: F401 - shared fixture


@pytest.fixture
def member(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, MEMBER)
    return user_id(client, MEMBER)


def job(platform, generation_id, status):
    now = time.time()
    with platform.engine.begin() as db:
        db.execute(insert(executions).values(
            generation_id=generation_id, fingerprint=generation_id, provider="seedvis", status=status,
            payload={}, provider_task_id="task", result={}, attempts=0, cancel_requested=False,
            created_at=now, run_started_at=now, next_run_at=now, lease_until=0,
        ))


def test_new_accounts_receive_the_welcome_credits(client, member):
    summary = client.get("/api/v1/credits").json()
    assert summary["balance"] == 100 and summary["exempt"] is False
    assert [(item["kind"], item["delta"], item["balance"]) for item in summary["history"]] == [("signup", 100, 100)]


def test_charges_are_applied_once_per_ref_and_blocked_when_short(client, member):
    platform = client.platform
    platform.charge_credits(member, 30, "plan", "plan:one")
    platform.charge_credits(member, 30, "plan", "plan:one")
    assert platform.credit_balance(member) == 70
    platform.require_credits(member, 70)
    with pytest.raises(HTTPException) as error:
        platform.require_credits(member, 71)
    assert error.value.status_code == 402 and "còn 70 credit" in error.value.detail


def test_admins_are_never_charged(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, ADMIN)
    boss = user_id(client, ADMIN)
    client.platform.charge_credits(boss, 1000, "render", "render:admin-scene", settled=False)
    client.platform.require_credits(boss, 10**6)
    assert client.platform.credit_balance(boss) == 100
    assert client.get("/api/v1/credits").json()["exempt"] is True


def test_scenes_without_a_video_are_refunded_and_finished_scenes_are_kept(client, member):
    platform = client.platform
    for gid, status in (("ok-scene", "COMPLETED"), ("bad-scene", "FAILED"), ("busy-scene", "GENERATING")):
        job(platform, gid, status)
        platform.charge_credits(member, 20, "render", "render:" + gid, settled=False)
    assert platform.credit_balance(member) == 40
    platform.settle_render_credits()
    platform.settle_render_credits()  # settling twice never refunds twice
    assert platform.credit_balance(member) == 60
    with platform.engine.connect() as db:
        rows = {row.ref: row.settled for row in db.execute(
            select(credit_ledger.c.ref, credit_ledger.c.settled).where(credit_ledger.c.user_id == member))}
    assert rows["render:ok-scene"] == 1 and rows["render:bad-scene"] == 1 and rows["refund:render:bad-scene"] == 1
    assert rows["render:busy-scene"] == 0


def test_clip_price_covers_each_block_of_seconds(client, member):
    platform = client.platform
    platform.set_credit_pricing({"video_clip": {
        "default": {"credits": 40, "seconds": 8},
        "seedvis:Omni-Flash": {"credits": 10, "seconds": 10},
        "seedvis:seedance_2.0_fast": {"credits": 20, "seconds": 5},
    }})
    assert platform.clip_cost("seedvis", "Omni-Flash", 10) == 10
    assert platform.clip_cost("poyo", "veo3.1-fast", 8) == 40
    assert platform.clip_cost("poyo", "veo3.1-fast", 4) == 40  # a short clip is still one clip
    assert [platform.clip_cost("seedvis", "seedance_2.0_fast", s) for s in (5, 10, 15)] == [20, 40, 60]
    assert platform.clip_cost("fake", "Omni-Flash", 10) == 0
    draft = {"user_id": member, "payload": {
        "render_target": {"provider": "seedvis", "video_model": "Omni-Flash"},
        "spec": {"output": {"duration_seconds": 10}},
        "story_plan": {"clips": [{"index": 1, "duration_seconds": 10}, {"index": 2}]},
    }}
    # Two Omni Flash clips plus one image per scene.
    assert platform.story_render_estimate(draft) == 2 * 10 + 2 * 1


def test_per_second_prices_saved_earlier_become_clip_prices(client, member):
    platform = client.platform
    with platform.engine.begin() as db:
        db.execute(insert(platform_settings).values(key="credit_pricing", updated=time.time(), value={
            "video_second": {"default": 40, "poyo:veo3.1-fast": 40, "seedvis:seedance_2.5": 30}}))
    clips = platform.credit_pricing()["video_clip"]
    assert clips["poyo:veo3.1-fast"] == {"credits": 40, "seconds": 8}
    assert clips["seedvis:seedance_2.5"] == {"credits": 30, "seconds": 5}
    assert platform.clip_cost("seedvis", "seedance_2.5", 15) == 90


def test_only_admins_set_prices_and_adjust_balances(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, MEMBER)
    member = user_id(client, MEMBER)
    assert client.get("/api/v1/admin/credit-pricing").status_code == 403
    assert client.post(f"/api/v1/admin/users/{member}/credits", json={"delta": 50}).status_code == 403
    register(client, ADMIN)
    pricing = client.put("/api/v1/admin/credit-pricing", json={
        "signup_bonus": 20, "video_clip": {"default": {"credits": 2, "seconds": 8}}, "plan": 1, "campaign": 3,
        "image": 1})
    assert pricing.status_code == 200 and pricing.json()["video_clip"]["default"] == {"credits": 2, "seconds": 8}
    adjusted = client.post(f"/api/v1/admin/users/{member}/credits", json={"delta": 50, "note": "Nạp gói"})
    assert adjusted.json()["balance"] == 150 and adjusted.json()["history"][0]["note"] == "Nạp gói"
    too_much = client.post(f"/api/v1/admin/users/{member}/credits", json={"delta": -151})
    assert too_much.status_code == 409
    listed = next(item for item in client.get("/api/v1/admin/users").json() if item["id"] == member)
    assert listed["credits"] == 150
    register(client, "late@example.com")
    with client.platform.engine.connect() as db:
        assert db.execute(select(users.c.credits).where(users.c.email == "late@example.com")).scalar_one() == 20


def test_hidden_models_are_removed_for_members_but_kept_for_admins(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, MEMBER)
    register(client, ADMIN)
    member, boss = user_id(client, MEMBER), user_id(client, ADMIN)
    platform = client.platform
    platform.set_credit_pricing({"hidden_models": ["poyo:veo3.1-quality", "poyo:veo3.1-quality"]})
    assert platform.credit_pricing()["hidden_models"] == ["poyo:veo3.1-quality"]
    catalog = {"image_providers": [], "video_providers": [
        {"id": "poyo", "models": [{"id": "veo3.1-fast"}, {"id": "veo3.1-quality"}]},
        {"id": "other", "models": [{"id": "only"}]},
    ]}
    platform.set_credit_pricing({"hidden_models": ["poyo:veo3.1-quality", "other:only"]})
    visible = platform.visible_catalog(catalog, member)
    assert [(p["id"], [m["id"] for m in p["models"]]) for p in visible["video_providers"]] == [
        ("poyo", ["veo3.1-fast"])]
    assert platform.visible_catalog(catalog, boss) == catalog
    platform.require_visible_model(member, "poyo", "veo3.1-fast")
    platform.require_visible_model(boss, "poyo", "veo3.1-quality")
    with pytest.raises(HTTPException) as error:
        platform.require_visible_model(member, "poyo", "veo3.1-quality")
    assert error.value.status_code == 409
