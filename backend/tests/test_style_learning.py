# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import copy
import subprocess
from itertools import pairwise

import httpx
import imageio_ffmpeg

from app.services.platform_style_learning import speech_metrics, style_frame_times
from tests.test_platform import client, register  # noqa: F401 - shared fixture

BLUEPRINT = {
    "title": "Mở bằng tay đang làm", "summary": "Hook cận tay, cắt nhanh, kết bằng câu hỏi.",
    "language": "Tiếng Việt", "format": "POV", "narration_mode": "voice_over",
    "hook": {"technique": "Cận tay giữa hành động", "opening_visual": "Bàn tay mở hộp",
             "opening_line": "Câu mở đầu của video gốc", "why_it_works": "Gây tò mò"},
    "beats": [{"start_seconds": 0, "end_seconds": 1.5, "beat": "Hook", "shot": "Cận", "camera": "Cầm tay",
               "on_screen_text": "", "audio": "Giọng dẫn", "purpose": "Gây tò mò"}],
    "pacing": "Cắt mỗi giây ở phần mở đầu", "voice_style": "Nhanh, gọn", "visual_style": "Sáng, ấm",
    "music": "", "editing": "Cắt nhảy", "rules": [
        "Open on a close-up of hands mid-action.", "Cut every second during the hook.",
        "End on a question to the viewer.",
    ], "avoid": [],
}


def reference_video(path):
    # Two colour shots give one detectable cut; a tone gives the clip an audio track.
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y",
        "-f", "lavfi", "-i", "color=red:size=320x240:rate=24:duration=1.5",
        "-f", "lavfi", "-i", "color=blue:size=320x240:rate=24:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True, capture_output=True)
    return path.read_bytes()


class InternalClient:
    def __init__(self):
        self.calls = []

    def post(self, path, **kwargs):
        self.calls.append((path, copy.deepcopy(kwargs.get("json"))))
        request = httpx.Request("POST", "http://ai.test" + path)
        if path.endswith("transcribe-clip"):
            return httpx.Response(200, request=request, json={"text": "Lời thoại video gốc"})
        if path.endswith("learn-style"):
            return httpx.Response(200, request=request, json={"blueprint": BLUEPRINT})
        output = {"core_value": "A coherent product story", "clips": [
            {"index": 1, "beat": "Hook", "duration_seconds": 8,
             "video_prompt": "A Vietnamese woman opens a box in a bright room.",
             "voice_script": "Mở hộp ra là thấy ngay chi tiết mình thích nhất nè."},
        ]}
        return httpx.Response(200, request=request, json={"plan": output})

    def close(self):
        return None


def test_frame_times_sample_hook_and_cuts_without_duplicates():
    times = style_frame_times(10.0, [2.0, 2.1, 6.0])
    assert times[0] == 0.2 and 2.2 in times and 6.2 in times
    assert all(later - earlier >= 0.5 for earlier, later in pairwise(times))
    assert len(style_frame_times(120.0, [index * 0.7 for index in range(150)])) == 14


def test_reference_video_is_analysed_stored_and_steers_planning(client, tmp_path, monkeypatch):
    platform = client.platform
    register(client)
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    platform.http = InternalClient()
    video = reference_video(tmp_path / "reference.mp4")
    learned = client.post("/api/v1/style-references", files={"file": ("viral.mp4", video, "video/mp4")})
    assert learned.status_code == 201, learned.text
    style = learned.json()
    assert style["title"] == BLUEPRINT["title"] and style["blueprint"]["rules"] == BLUEPRINT["rules"]
    analysis = next(body for path, body in platform.http.calls if path.endswith("learn-style"))
    assert analysis["frames"] and all(frame["image_data_url"].startswith("data:image/jpeg;base64,")
                                      for frame in analysis["frames"])
    assert analysis["transcript"] == "Lời thoại video gốc" and analysis["audio_present"] is True
    assert analysis["speech_metrics"]["words"] == 4
    # Only the wording is analysed; no audio of the reference speaker leaves the server.
    assert "audio_base64" not in analysis
    assert any(1.2 <= cut <= 1.8 for cut in analysis["cut_times"])
    # The reference video itself is never kept on disk.
    assert not any((platform.storage / "style-learning").iterdir())
    assert [item["id"] for item in client.get("/api/v1/style-references").json()] == [style["id"]]

    plan = client.post("/api/v1/plans", json={"prompt": "Quảng cáo hộp quà", "duration": 8,
                                              "style_reference_id": style["id"]})
    assert plan.status_code == 201, plan.text
    story_call = next(body for path, body in platform.http.calls if path.endswith("planner/story"))
    assert story_call["project_context"]["style_reference"]["rules"] == BLUEPRINT["rules"]

    assert client.delete(f"/api/v1/style-references/{style['id']}").json() == {"ok": True}
    assert client.get("/api/v1/style-references").json() == []


def test_reference_links_are_limited_to_video_platforms(client, monkeypatch):
    platform = client.platform
    register(client)
    monkeypatch.setattr(platform, "planner_credential_status", lambda: {"configured": True})
    for url in ("http://127.0.0.1:8100/secret", "https://evil.example/video.mp4", "http://www.tiktok.com/@a/video/1"):
        response = client.post("/api/v1/style-references", data={"url": url})
        assert response.status_code == 422 and "TikTok" in response.json()["detail"]
    other = client.post("/api/v1/style-references", data={})
    assert other.status_code == 422


def test_writing_metrics_measure_sentences_particles_and_addressing_only():
    transcript = "Mấy mẹ ơi, bộ này xinh lắm nè! Mặc đi chơi luôn nha. Mình thấy hợp không?"
    metrics = speech_metrics(transcript)
    assert metrics["words"] == 17 and metrics["sentences"] == 3 and metrics["avg_words_per_sentence"] == 5.7
    assert metrics["questions"] == 1 and metrics["exclamations"] == 1
    assert metrics["particles"] == {"nè": 1, "luôn": 1, "nha": 1}
    assert metrics["addressing_terms"]["mấy mẹ"] == 1 and metrics["addressing_terms"]["mình"] == 1
    assert not {"words_per_second", "inner_pauses", "speech_seconds"} & set(metrics)
    assert speech_metrics("") == {}
