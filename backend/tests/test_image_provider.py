# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import pytest
from sqlalchemy import select

from app.main import PlanInput
from app.services.platform_shared import users
from tests.test_platform import client, register  # noqa: F401 - shared fixture

ADMIN, MEMBER = "boss@example.com", "member@example.com"
CATALOG = {
    "video_providers": [{"id": "fake", "label": "Demo", "enabled": True, "models": [{"id": "fake", "label": "fake"}]}],
    "image_providers": [
        {"id": "openai", "label": "OpenAI", "configured": True,
         "models": [{"id": "gpt-image-2.5-flare", "label": "gpt-image-2.5-flare"}]},
        {"id": "seedvis", "label": "Seedvis", "configured": True, "models": [
            {"id": "gpt-image-2", "label": "GPT Image 2", "available": True},
            {"id": "GEM_PIX_2", "label": "Nano Banana Pro", "available": True},
            {"id": "NARWHAL", "label": "Nano Banana 2.1", "available": False},
        ]},
    ],
}


@pytest.fixture
def admin_client(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.delenv("IMAGE_PROVIDER", raising=False)
    monkeypatch.setattr(client.platform, "provider_catalog", lambda **_: CATALOG)
    register(client, MEMBER)
    register(client, ADMIN)
    return client


def uid(client, email):
    with client.platform.engine.connect() as db:
        return db.execute(select(users.c.id).where(users.c.email == email)).scalar_one()


def test_admin_switches_member_images_to_a_seedvis_model(admin_client):
    client = admin_client
    assert client.get("/api/v1/admin/image-provider").json() == {"provider": "openai", "model": "gpt-image-2.5-flare"}
    assert client.put("/api/v1/admin/image-provider", json={"provider": "seedvis", "model": "nope"}).status_code == 422
    assert client.put("/api/v1/admin/image-provider", json={"provider": "seedvis", "model": "NARWHAL"}).status_code == 409
    saved = client.put("/api/v1/admin/image-provider", json={"provider": "seedvis", "model": "GEM_PIX_2"})
    assert saved.json() == {"provider": "seedvis", "model": "GEM_PIX_2"}
    config = client.get("/api/v1/config").json()
    assert (config["image_provider"], config["image_model"]) == ("seedvis", "GEM_PIX_2")

    platform = client.platform
    asked = PlanInput(prompt="Một video thử", image_provider="openai", image_model="gpt-image-2.5-flare")
    member = platform._plan_target(uid(client, MEMBER), asked)
    assert (member["image_provider"], member["image_model"]) == ("seedvis", "GEM_PIX_2")
    admin = platform._plan_target(uid(client, ADMIN), asked)
    assert (admin["image_provider"], admin["image_model"]) == ("openai", "gpt-image-2.5-flare")
    default = platform._plan_target(uid(client, ADMIN), PlanInput(prompt="Một video thử"))
    assert (default["image_provider"], default["image_model"]) == ("seedvis", "GEM_PIX_2")

    assert len(client.get("/api/v1/providers").json()["image_providers"]) == 2
    client.post("/api/v1/auth/login", json={"email": MEMBER, "password": "test-password-123"})
    images = client.get("/api/v1/providers").json()["image_providers"]
    assert [(item["id"], [model["id"] for model in item["models"]]) for item in images] == [("seedvis", ["GEM_PIX_2"])]
    assert client.get("/api/v1/admin/image-provider").status_code == 403
    assert client.put("/api/v1/admin/image-provider", json={"provider": "openai", "model": "x"}).status_code == 403


def test_campaign_items_planned_as_namespaces_also_use_the_admin_image_model(admin_client):
    from types import SimpleNamespace

    client = admin_client
    client.put("/api/v1/admin/image-provider", json={"provider": "seedvis", "model": "GEM_PIX_2"})
    body = SimpleNamespace(**PlanInput(prompt="Một video thử").model_dump() | {"image_provider": "openai"})
    target = client.platform._plan_target(uid(client, MEMBER), body)
    assert (target["image_provider"], target["image_model"]) == ("seedvis", "GEM_PIX_2")
    assert body.image_provider == "openai"
