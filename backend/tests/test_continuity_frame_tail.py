# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import subprocess
import time

import imageio_ffmpeg
from sqlalchemy import insert

from app.services.platform import videos
from tests.test_platform import client  # noqa: F401 - shared fixture


def test_final_frame_falls_back_when_speech_outlasts_the_video_stream(client):
    platform = client.platform
    directory = platform.storage / "videos"
    directory.mkdir(parents=True, exist_ok=True)
    generation_id = "omni-tail"
    source = directory / f"{generation_id}.mp4"
    # One second of picture with audio running past it, as Omni Flash outputs do.
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    with platform.engine.begin() as db:
        db.execute(insert(videos).values(generation_id=generation_id, user_id="u", filename=source.name,
                                         size_bytes=source.stat().st_size, created=time.time()))
    # Speech reaches the container end, so the trimmed tail seek lands after the last video frame.
    platform._scene_native_trim = lambda _gid: {"trim_end_seconds": 1.3, "trailing_silence_seconds": 0.0}
    frame = platform._extract_continuity_frame(generation_id)
    assert frame.is_file() and frame.stat().st_size >= 1000
