import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.credential_vault import CredentialVault
from app.pipelines.marketing_planner import create_marketing_campaign
from app.pipelines.story_planner import create_book_story_plan
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.schemas.provider import VideoProviderCapabilities
from tests.test_story_dialogue import Vault, mock_planner, story
from tests.test_visual_planning import picture


def campaign(count=3):
    return {
        "strategy": {
            "product_summary": "Tai nghe màu đen trong ảnh",
            "audience": "Người yêu âm nhạc",
            "objective": "Tăng cân nhắc mua",
            "positioning": "Khám phá sản phẩm từ hình ảnh thật",
            "observed_details": ["Màu đen"],
            "needs_confirmation": ["Giá bán chưa cung cấp"],
            "content_pillars": ["Khám phá chi tiết", "Cách sử dụng"],
        },
        "variants": {
            f"video_{i}": {
                "title": f"Video số {i}",
                "angle": f"Góc khám phá chi tiết số {i}",
                "hook": f"Bạn có nhận ra chi tiết số {i} không?",
                "cta": "Khám phá sản phẩm nhé",
                "caption": "Tìm hiểu sản phẩm cùng mình",
                "visual_direction": "Cận cảnh sản phẩm trong cùng bối cảnh ổn định",
                "channel": "tiktok",
                "funnel_stage": "consideration",
                "publish_day": i,
            }
            for i in range(1, count + 1)
        },
    }


@pytest.mark.asyncio
async def test_campaign_reads_six_images_and_returns_exact_creative_brief_count(monkeypatch):
    calls = mock_planner(monkeypatch, campaign(5), {})
    images = [picture() for _ in range(6)]
    result = await create_marketing_campaign(
        "Quảng cáo sản phẩm trong bộ ảnh",
        Vault(),
        "gpt-4o-mini",
        5,
        images,
        {"memories": ["Giọng Bắc"]},
    )
    assert len(result["variants"]) == 5 and len(calls) == 1
    sent = calls[0]
    assert sent["model"] == "gpt-4o-mini"
    parts = sent["messages"][1]["content"]
    assert [part["image_url"]["url"] for part in parts if part["type"] == "image_url"] == images
    assert json.loads(parts[0]["text"])["project_context"]["memories"] == ["Giọng Bắc"]
    assert (
        len(sent["response_format"]["json_schema"]["schema"]["properties"]["variants"]["required"])
        == 5
    )
    assert "never claim guaranteed virality" in sent["messages"][0]["content"]


@pytest.mark.asyncio
async def test_sales_story_reads_all_images_and_has_one_nonconflicting_sales_policy(monkeypatch):
    calls = mock_planner(monkeypatch, story(), {})
    images = [picture() for _ in range(6)]
    await create_book_story_plan(
        "Quảng cáo sản phẩm, giọng chậm 1.0-2.0 âm tiết/giây",
        Vault(),
        "gpt-4o-mini",
        video_capabilities=VideoProviderCapabilities(
            provider="poyo", model="veo3.1-fast", generation_durations=[4, 6, 8]
        ),
        reference_image_data_urls=images,
        planning_goal="sales",
    )
    sent = calls[0]
    assert len(calls) == 1
    assert sent["messages"][0]["content"].count("SALES STORY DIRECTION") == 1
    assert "Do not invent prices" in sent["messages"][0]["content"]
    assert (
        len([part for part in sent["messages"][1]["content"] if part["type"] == "image_url"]) == 6
    )


@pytest.mark.parametrize("failure", ["missing", "duplicate_hook", "duplicate_angle", "invalid_day"])
@pytest.mark.asyncio
async def test_bad_campaign_does_not_silently_duplicate_or_fill_creative_briefs(
    monkeypatch, failure
):
    output = copy.deepcopy(campaign())
    if failure == "missing":
        del output["variants"]["video_3"]
    elif failure == "invalid_day":
        output["variants"]["video_2"]["publish_day"] = 0
    else:
        field = failure.removeprefix("duplicate_")
        output["variants"]["video_2"][field] = output["variants"]["video_1"][field]
    calls = mock_planner(monkeypatch, output, {})
    with pytest.raises(RuntimeError, match="campaign_schema_mismatch"):
        await create_marketing_campaign("Lập chiến dịch bán hàng", Vault(), "gpt-4o-mini", 3, [])
    assert len(calls) == 1


def test_internal_limits_and_authorization_before_campaign_llm(tmp_path, monkeypatch):
    calls = []

    async def planned(*args, **kwargs):
        calls.append(args)
        return {"strategy": {}, "variants": []}

    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "vault.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda *_args: "not-a-real-key")
    monkeypatch.setattr("app.pipelines.video_api.create_marketing_campaign", planned)
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        path = "/internal/v1/planner/campaign"
        assert client.post(path, json={"prompt": "Product campaign"}).status_code == 401
        headers = {"X-Internal-Key": "test-secret"}
        assert (
            client.post(
                path,
                headers=headers,
                json={"prompt": "Product campaign", "image_data_urls": [picture()] * 7},
            ).status_code
            == 422
        )
        assert (
            client.post(
                path,
                headers=headers,
                json={
                    "prompt": "Product campaign",
                    "image_data_urls": [picture()] * 6,
                    "image_data_url": picture(),
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, headers=headers, json={"prompt": "Product campaign", "script_count": 11}
            ).status_code
            == 422
        )
        assert not calls
        result = client.post(
            path,
            headers=headers,
            json={"prompt": "Product campaign", "image_data_urls": [picture()] * 6},
        )
        assert result.status_code == 200 and len(calls[0][4]) == 6


@pytest.mark.asyncio
async def test_campaign_provider_failure_is_actionable_and_does_not_loop(monkeypatch):
    calls = mock_planner(monkeypatch, httpx.Response(401, json={"error": "bad key"}), {})
    with pytest.raises(RuntimeError, match="planner_invalid_key"):
        await create_marketing_campaign("Product campaign", Vault(), "gpt-4o-mini", 3, [])
    assert len(calls) == 1
