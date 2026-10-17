import copy
import io
import json
import time

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, update

from app.services.platform_shared import chat_messages, drafts, marketing_campaigns
from tests.test_platform import register

pytest_plugins = ["tests.test_platform"]


def story():
    return {
        "core_value": "Giới thiệu sản phẩm từ hình ảnh thật",
        "clips": [
            {
                "index": 1,
                "beat": "Mở đầu",
                "duration_seconds": 8,
                "video_prompt": "The same product is shown clearly in the original reference setting.",
                "voice_script": "Hôm nay mình cùng nhìn kỹ sản phẩm này và tìm hiểu những chi tiết đáng chú ý nhé.",
            }
        ],
    }


def strategy(count):
    return {
        "strategy": {
            "product_summary": "Tai nghe trong ảnh",
            "audience": "Người yêu âm nhạc",
            "objective": "Tăng cân nhắc mua hàng",
            "positioning": "Khám phá chi tiết nhìn thấy của sản phẩm",
            "observed_details": ["Vỏ màu đen"],
            "needs_confirmation": ["Giá bán"],
            "content_pillars": ["Tính năng", "Trải nghiệm"],
        },
        "variants": [
            {
                "title": f"Video {i}",
                "angle": f"Khám phá góc nhìn số {i}",
                "hook": f"Bạn đã nhìn kỹ chi tiết số {i} chưa?",
                "cta": "Khám phá sản phẩm nhé",
                "caption": "Tìm hiểu sản phẩm từ hình ảnh thật",
                "visual_direction": "Giữ sản phẩm trong khung hình ổn định",
                "channel": "tiktok",
                "funnel_stage": "consideration",
                "publish_day": i,
            }
            for i in range(1, count + 1)
        ],
    }


def upload(client, count=6):
    ids = []
    for i in range(count):
        image = io.BytesIO()
        Image.new("RGB", (48, 32), (i * 30, 100, 200)).save(image, "PNG")
        result = client.post(
            "/api/v1/assets", files={"file": (f"product-{i}.png", image.getvalue(), "image/png")}
        )
        assert result.status_code == 201
        ids.append(result.json()["id"])
    return ids


def configured(client, monkeypatch):
    register(client)
    platform = client.platform
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "test-image")
    monkeypatch.setenv("IMAGE_PROVIDER", "openai")
    monkeypatch.setattr(
        platform,
        "provider_catalog",
        lambda: {
            "video_providers": [{"id": "fake", "models": [{"id": "fake"}]}],
            "image_providers": [{"id": "openai", "models": [{"id": "test-image"}]}],
        },
    )
    calls = []
    failures = set()

    def handler(request):
        data = json.loads(request.content)
        calls.append((request.url.path, data))
        if request.url.path.endswith("/campaign"):
            return httpx.Response(200, json=strategy(data["script_count"]))
        if request.url.path.endswith("/story"):
            index = data["project_context"].get("marketing_campaign", {}).get("variant_index")
            if index in failures:
                return httpx.Response(502, json={"detail": "planner_timeout"})
            return httpx.Response(200, json={"plan": story()})
        raise AssertionError(f"Unexpected internal request: {request.url.path}")

    platform.http.close()
    platform.http = httpx.Client(base_url="http://ai.test", transport=httpx.MockTransport(handler))
    return platform, calls, failures


def create(client, assets=None, **extra):
    return client.post(
        "/api/v1/marketing-campaigns",
        json={
            "prompt": "Quảng cáo sản phẩm trong ảnh để tăng bán hàng",
            "duration": 8,
            "asset_ids": assets or [],
            "script_count": 3,
            "confirm_planning_costs": True,
            **extra,
        },
    )


def test_six_images_are_owned_and_sent_to_story_planner_in_order(client, monkeypatch):
    platform, calls, _ = configured(client, monkeypatch)
    ids = upload(client)
    result = client.post(
        "/api/v1/plans",
        json={
            "prompt": "Giới thiệu sản phẩm trong bộ ảnh",
            "duration": 8,
            "asset_ids": ids,
            "asset_id": ids[3],
        },
    )
    assert result.status_code == 201, result.text
    data = calls[0][1]
    ordered = [ids[3], *[aid for aid in ids if aid != ids[3]]]
    assert result.json()["input_asset_ids"] == ordered
    assert len(data["image_data_urls"]) == 6 and data["planning_goal"] == "sales"
    assert [item["asset_id"] for item in data["project_context"]["input_references"]] == ordered
    assert result.json()["spec"]["references"] == [{"asset_id": ids[3], "role": "FIRST_FRAME"}]
    with platform.engine.connect() as db:
        payload = db.execute(select(drafts.c.payload)).scalar_one()
    assert payload["input_asset_ids"] == ordered and "base64" not in json.dumps(payload)


@pytest.mark.parametrize(
    "extra",
    [
        {"asset_ids": [str(i) for i in range(7)]},
        {"asset_ids": ["same", "same"]},
        {"asset_id": "extra", "asset_ids": [str(i) for i in range(6)]},
    ],
)
def test_asset_limits_checked_before_planner(client, monkeypatch, extra):
    _, calls, _ = configured(client, monkeypatch)
    result = client.post("/api/v1/plans", json={"prompt": "A product review", **extra})
    assert result.status_code == 422 and not calls


def test_foreign_supporting_image_blocks_the_whole_plan(client, monkeypatch):
    _, calls, _ = configured(client, monkeypatch)
    foreign = upload(client, 1)[0]
    register(client, "another@example.com")
    own = upload(client, 1)[0]
    result = client.post(
        "/api/v1/plans",
        json={"prompt": "A product review", "duration": 8, "asset_ids": [own, foreign]},
    )
    assert result.status_code == 404 and not calls


def test_campaign_queues_without_llm_calls_then_builds_distinct_reviewable_drafts(
    client, monkeypatch
):
    platform, calls, _ = configured(client, monkeypatch)
    ids = upload(client)
    result = create(client, ids)
    assert result.status_code == 202 and not calls
    identifier = result.json()["id"]
    for _ in range(4):
        platform.process_campaigns()
    final = client.get(f"/api/v1/marketing-campaigns/{identifier}").json()
    assert final["status"] == "COMPLETED" and final["completed"] == 3
    assert len(calls) == 4 and len(calls[0][1]["image_data_urls"]) == 6
    assert all(len(body["image_data_urls"]) == 6 for _, body in calls[1:])
    assert [
        body["project_context"]["marketing_campaign"]["variant_index"] for _, body in calls[1:]
    ] == [1, 2, 3]
    assert len({item["plan"]["id"] for item in final["items"]}) == 3
    with platform.engine.connect() as db:
        rows = db.execute(select(drafts)).mappings().all()
        assert len(rows) == 3 and all(row["generation_id"] is None for row in rows)
        assert "base64" not in json.dumps(
            [dict(row) for row in db.execute(select(marketing_campaigns)).mappings()]
        )
    assert (
        len(client.get(f"/api/v1/chat/conversations/{final['conversation_id']}/messages").json())
        == 6
    )
    assert (
        client.post(
            f"/api/v1/video-projects/{final['items'][0]['plan']['project_id']}/renders",
            json={"confirm_paid": True},
        ).status_code
        == 409
    )


def test_campaign_consent_limits_and_ownership(client, monkeypatch):
    platform, calls, _ = configured(client, monkeypatch)
    assert create(client, confirm_planning_costs=False).status_code == 409
    assert create(client, script_count=11).status_code == 422
    result = create(client).json()
    identifier = result["id"]
    register(client, "other@example.com")
    for method, path, body in [
        ("GET", "", None),
        ("POST", "/cancel", None),
        ("POST", "/retry", {"confirm_planning_costs": True}),
    ]:
        response = client.request(
            method,
            f"/api/v1/marketing-campaigns/{identifier}{path}",
            **({"json": body} if body else {}),
        )
        assert response.status_code == 404
    assert not calls and client.get("/api/v1/marketing-campaigns").json() == []
    with platform.engine.connect() as db:
        assert len(db.execute(select(marketing_campaigns)).all()) == 1


def test_failed_script_does_not_discard_good_drafts_and_retry_only_replans_failed_item(
    client, monkeypatch
):
    platform, calls, failures = configured(client, monkeypatch)
    failures.add(2)
    identifier = create(client).json()["id"]
    for _ in range(4):
        platform.process_campaigns()
    result = client.get(f"/api/v1/marketing-campaigns/{identifier}").json()
    assert result["status"] == "PARTIAL" and result["completed"] == 2
    kept = [result["items"][i]["plan"]["id"] for i in (0, 2)]
    path = f"/api/v1/marketing-campaigns/{identifier}/retry"
    assert client.post(path, json={"confirm_planning_costs": False}).status_code == 409
    failures.clear()
    assert client.post(path, json={"confirm_planning_costs": True}).status_code == 202
    platform.process_campaigns()
    result = client.get(f"/api/v1/marketing-campaigns/{identifier}").json()
    assert result["status"] == "COMPLETED" and result["completed"] == 3
    assert [result["items"][i]["plan"]["id"] for i in (0, 2)] == kept
    assert len(calls) == 5


def test_cancel_before_start_does_not_call_llm(client, monkeypatch):
    platform, calls, _ = configured(client, monkeypatch)
    identifier = create(client).json()["id"]
    assert (
        client.post(f"/api/v1/marketing-campaigns/{identifier}/cancel").json()["status"]
        == "CANCELLED"
    )
    platform.process_campaigns()
    assert not calls


def test_campaign_request_id_prevents_duplicate_llm_jobs_after_lost_reply(client, monkeypatch):
    platform, calls, _ = configured(client, monkeypatch)
    request_id = "campaign-request-000000001"
    first = create(client, request_id=request_id)
    duplicate = create(client, request_id=request_id)
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"] == request_id
    assert create(client, request_id=request_id, script_count=5).status_code == 409
    for _ in range(4):
        platform.process_campaigns()
    assert len(calls) == 4
    with platform.engine.connect() as db:
        assert len(db.execute(select(marketing_campaigns)).all()) == 1
    register(client, "other@example.com")
    assert create(client, request_id=request_id).status_code == 404


def test_invalid_internal_error_detail_does_not_turn_into_a_server_500(client, monkeypatch):
    platform, _, _ = configured(client, monkeypatch)
    platform.http.close()
    platform.http = httpx.Client(base_url="http://ai.test", transport=httpx.MockTransport(
        lambda _request: httpx.Response(422, json={"detail": [{"type": "invalid_input"}]})))
    result = client.post("/api/v1/plans", json={"prompt": "Giới thiệu sản phẩm", "duration": 8})
    assert result.status_code == 502
    identifier = create(client).json()["id"]
    platform.process_campaigns()
    assert client.get(f"/api/v1/marketing-campaigns/{identifier}").json()["status"] == "FAILED"


def test_campaign_revision_keeps_all_images_and_the_original_sales_angle(client, monkeypatch):
    platform, calls, _ = configured(client, monkeypatch)
    ids = upload(client)
    identifier = create(client, ids).json()["id"]
    platform.process_campaigns()
    platform.process_campaigns()
    campaign = client.get(f"/api/v1/marketing-campaigns/{identifier}").json()
    plan = campaign["items"][0]["plan"]
    revised = client.post(f"/api/v1/video-projects/{plan['project_id']}/plans", json={
        "prompt": "Giữ sản phẩm và góc bán hàng, viết lời thoại tự nhiên hơn", "duration": 8, "asset_ids": ids})
    assert revised.status_code == 201, revised.text
    data = calls[-1][1]
    assert len(data["image_data_urls"]) == 6
    assert data["project_context"]["marketing_campaign"]["campaign_id"] == identifier
    assert data["project_context"]["marketing_campaign"]["variant_index"] == 1
    assert revised.json()["input_asset_ids"] == ids


def test_interrupted_job_stops_without_rebilling_and_recovers_saved_draft_on_manual_retry(
    client, monkeypatch
):
    platform, calls, _ = configured(client, monkeypatch)
    identifier = create(client).json()["id"]
    platform.process_campaigns()
    platform.process_campaigns()
    with platform.engine.begin() as db:
        row = (
            db.execute(select(marketing_campaigns).where(marketing_campaigns.c.id == identifier))
            .mappings()
            .one()
        )
        items = copy.deepcopy(row["items"])
        saved_id = items[0]["plan"]["id"]
        items[0].update(status="RUNNING", plan=None, message_id=None)
        db.execute(
            update(marketing_campaigns)
            .where(marketing_campaigns.c.id == identifier)
            .values(items=items, status="WORKING", updated=time.time() - 400)
        )
    platform.process_campaigns()
    assert len(calls) == 2
    assert (
        client.post(
            f"/api/v1/marketing-campaigns/{identifier}/retry", json={"confirm_planning_costs": True}
        ).status_code
        == 202
    )
    platform.process_campaigns()
    result = client.get(f"/api/v1/marketing-campaigns/{identifier}").json()
    assert result["items"][0]["plan"]["id"] == saved_id and len(calls) == 2
    with platform.engine.connect() as db:
        assert len(db.execute(select(chat_messages)).all()) == 2
