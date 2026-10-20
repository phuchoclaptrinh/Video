import json

import httpx
import pytest

from app.pipelines.dialogue_boundaries import SPOKEN_CODE_RULE
from app.pipelines.scene_line_rewrite import rewrite_scene_line

ORIGINAL = ("Mẹ ơi, trước khi chọn size thì cân nặng của con mới là thứ cần đối chiếu trước. "
            "Bảng size của bộ Takit tê ca không một ghi rõ từng mốc cân nặng nên mẹ chỉ cần nhìn là chọn được ngay.")
NEW = ("Mẹ ơi, chọn size cho con thì xem cân nặng trước tiên. Bộ Takit có bảng size ghi rõ từng mốc cân nặng, "
       "mẹ chỉ cần nhìn một lần là biết ngay con mặc size nào cho vừa vặn.")


class Vault:
    def get(self, _name):
        return "unit-test-not-real-key"


def mock_openai(monkeypatch, line):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"voice_script": line},
                                                                                        ensure_ascii=False)}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls


SCENES = [{"index": 1, "duration_seconds": 8, "voice_script": ORIGINAL, "beat": "Chọn size", "purpose": ""},
          {"index": 2, "duration_seconds": 8, "voice_script": "Chất vải mềm mát nên bé mặc cả ngày vẫn thoải mái.",
           "beat": "Chất vải", "purpose": ""}]


@pytest.mark.asyncio
async def test_rewrite_sends_what_the_model_said_and_returns_a_new_line(monkeypatch):
    calls = mock_openai(monkeypatch, NEW)
    line = await rewrite_scene_line(SCENES, 1, ["native_speech_repetition"], "Bảng của bộ Takit TK0 sửa lại",
                                    Vault(), "gpt-4o-mini")
    assert line == NEW
    sent = json.loads(calls[0]["messages"][1]["content"])
    assert sent["model_said"] == "Bảng của bộ Takit TK0 sửa lại" and sent["line_to_rewrite"] == ORIGINAL
    assert sent["max_words"] == 40 and "repeated words" in sent["problems"][0]
    assert SPOKEN_CODE_RULE in calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("line", [ORIGINAL, "Bộ TK01 rất đẹp.", " ".join(["rất"] * 45), "Ngắn quá."])
@pytest.mark.asyncio
async def test_rewrite_rejects_the_same_line_digits_or_an_off_pace_length(monkeypatch, line):
    mock_openai(monkeypatch, line)
    with pytest.raises(RuntimeError, match="planner_line_rewrite_invalid"):
        await rewrite_scene_line(SCENES, 1, ["native_speech_incomplete"], "", Vault(), "gpt-4o-mini")
