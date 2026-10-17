import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.credential_vault import CredentialVault
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry
from app.providers.seedvis_keys import SeedvisKeyPool
from tests.test_seedvis import adapter, lifecycle, request_body


def test_pool_keeps_a_legacy_key_and_never_exposes_secrets(tmp_path):
    vault = CredentialVault(str(tmp_path / "vault.enc"), "test-secret")
    vault.set("seedvis", "legacy-seedvis-key-1111")
    pool = SeedvisKeyPool(vault)
    assert pool.public() == [{"id": "primary", "label": "Key chính", "slots": 4, "hint": "…1111"}]
    second = pool.add("second-seedvis-key-2222", "Tài khoản B", 6)
    assert second["slots"] == 6 and pool.key(second["id"]) == "second-seedvis-key-2222"
    assert pool.add("second-seedvis-key-2222", "", 50)["id"] == second["id"]  # same key is not duplicated
    assert [entry["slots"] for entry in pool.public()] == [4, 20]
    assert pool.update(second["id"], slots=3)["slots"] == 3
    assert "api_key" not in str(pool.public())
    assert pool.remove("primary") and pool.primary_key() == "second-seedvis-key-2222"
    assert vault.get("seedvis") == "second-seedvis-key-2222"
    assert pool.remove(second["id"]) and pool.public() == [] and vault.get("seedvis") is None


@pytest.mark.asyncio
async def test_each_task_is_submitted_and_polled_with_its_own_pool_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    seen = []

    def handler(request):
        seen.append((request.method, request.headers.get("Authorization")))
        return httpx.Response(202 if request.method == "POST" else 200, json=lifecycle())

    provider = adapter(handler)
    provider.clear_api_key()
    async with provider.client:
        with TestClient(create_app(ProviderRegistry([provider]), "test-internal-key")) as client:
            client.headers["X-Internal-Key"] = "test-internal-key"
            pool = "/internal/v1/video/seedvis/credentials"
            first = client.post(pool, json={"api_key": "first-seedvis-key-aaaa", "label": "A"}).json()
            second = client.post(pool, json={"api_key": "second-seedvis-key-bbbb", "slots": 2}).json()
            assert client.get(pool).json()["keys"] == [first, second]
            assert client.get("/internal/v1/video/seedvis/credential").json() == {"configured": True}
            canonical = client.post("/internal/v1/video/seedvis/validate", json=request_body()).json()["execution_request"]
            tasks = "/internal/v1/video/seedvis/tasks"
            assert client.post(tasks, params={"credential": second["id"]}, json=canonical).status_code == 202
            assert client.get(tasks + "/task-1", params={"credential": second["id"]}).status_code == 200
            assert client.post(tasks, json=canonical).status_code == 202
            assert seen == [("POST", "Bearer second-seedvis-key-bbbb"), ("GET", "Bearer second-seedvis-key-bbbb"),
                            ("POST", "Bearer first-seedvis-key-aaaa")]
            assert client.patch(f"{pool}/{second['id']}", json={"slots": 5}).json()["slots"] == 5
            assert client.delete(f"{pool}/{second['id']}").json() == {"configured": True}
            missing = client.get(tasks + "/task-1", params={"credential": second["id"]})
            assert missing.status_code == 422 and missing.json()["code"] == "seedvis_credential_missing"
            assert client.get(tasks + "/task-1", params={"credential": "../poyo"}).status_code == 422
            assert client.delete(f"{pool}/{first['id']}").json() == {"configured": False}
