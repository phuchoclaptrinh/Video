import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.credential_vault import CredentialVault
from app.core.voice_presets import (
    VOICE_PRESETS,
    get_voice_preset,
    tts_voice_instructions,
    voice_preset_for_request,
)
from app.pipelines.video_api import create_app
from app.providers.registry import ProviderRegistry


def test_unset_voice_pace_defaults_to_podcast_and_invalid_config_is_not_silent(monkeypatch):
    monkeypatch.delenv("VOICE_PACE", raising=False)
    assert get_voice_preset().name == "podcast"
    monkeypatch.setenv("VOICE_PACE", "koc")
    assert get_voice_preset().name == "koc"
    monkeypatch.setenv("VOICE_PACE", "typo")
    with pytest.raises(ValueError, match="VOICE_PACE must be podcast or koc"):
        get_voice_preset()


@pytest.mark.parametrize("brief,env,preset_name,pace", [
    ("KOC nữ Việt Nam 18-25 tuổi", "podcast", "podcast", (2.6, 3.0)),
    ("Nhân vật chuyển động chậm và camera chạy nhanh", "podcast", "podcast", (2.6, 3.0)),
    ("Không nói nhanh, chỉ giới thiệu sách", "podcast", "podcast", (2.6, 3.0)),
    ("Nói nhanh hơn, rõ ràng", "podcast", "koc", (3.2, 3.6)),
    ("Đọc chậm, rõ chữ", "koc", "podcast", (2.6, 3.0)),
    ("Giọng podcast sâu lắng", "koc", "podcast", (2.6, 3.0)),
    ("Giọng KOC thân thiện", "podcast", "koc", (3.2, 3.6)),
    ("Tốc độ 1,8-2,2 âm tiết/giây", "koc", "koc", (1.8, 2.2)),
    ("Speak at 1.25 syllables/s", "podcast", "podcast", (1.25, 1.25)),
])
def test_only_explicit_current_speech_preferences_change_pace(monkeypatch, brief, env, preset_name, pace):
    monkeypatch.setenv("VOICE_PACE", env)
    preset = voice_preset_for_request(brief)
    assert preset.name == preset_name and (preset.pace_min, preset.pace_max) == pace
    # The voice profile describes delivery style only; no numeric speaking rate limits the model.
    assert "syllables/s" not in preset.default_voice_profile


def test_backend_mirror_matches_all_preset_data_and_defaults(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "backend/app/core/voice_presets.py"
    spec = importlib.util.spec_from_file_location("backend_voice_presets_parity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert set(module.VOICE_PRESETS) == set(VOICE_PRESETS)
    for name, preset in VOICE_PRESETS.items():
        mirrored = module.get_voice_preset(name)
        assert asdict(preset) == asdict(mirrored)
        assert preset.default_voice_profile == mirrored.default_voice_profile
        assert len(preset.default_voice_profile) <= 600
        monkeypatch.setenv("VOICE_PACE", name)
        assert module.get_voice_preset().default_voice_profile == get_voice_preset().default_voice_profile



@pytest.mark.parametrize("preset_name", ["podcast", "koc"])
@pytest.mark.parametrize("with_profile", [False, True])
def test_tts_endpoint_uses_same_profile_or_preset_with_no_conflicting_delivery(tmp_path, monkeypatch, preset_name, with_profile):
    monkeypatch.setenv("VOICE_PACE", preset_name)
    monkeypatch.setenv("PROVIDER_CREDENTIAL_PATH", str(tmp_path / "credentials.enc"))
    monkeypatch.setattr(CredentialVault, "get", lambda _self, _name: "unit-test-not-real-key")
    profile = "Vietnamese; gentle deep healing podcast tone; slow-moderate pace, calm pauses." if with_profile else ""
    calls = []

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/audio/speech"
        calls.append(json.loads(request.content))
        return httpx.Response(200, content=b"unit-test-audio")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with TestClient(create_app(ProviderRegistry([]), "test-secret")) as client:
        result = client.post("/internal/v1/planner/voice", headers={"X-Internal-Key": "test-secret"},
                             json={"text": "Mình chia sẻ một cuốn sách hôm nay.", "voice_bible": profile, "response_format": "wav"})
    assert result.status_code == 200 and result.content == b"unit-test-audio"
    payload = calls[0]
    assert payload["instructions"] == tts_voice_instructions(profile) and payload["response_format"] == "wav"
    assert len(calls) == 1
    if with_profile:
        assert profile in payload["instructions"] and "lively" not in payload["instructions"]
        assert "3.2" not in payload["instructions"] and "takes priority over" not in payload["instructions"]
    else:
        assert get_voice_preset().default_voice_profile in payload["instructions"]
        assert get_voice_preset().delivery in payload["instructions"]
