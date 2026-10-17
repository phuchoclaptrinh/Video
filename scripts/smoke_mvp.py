"""Exercise a running MVP through its public HTTP API. Creates a test account."""

import argparse
import io
import secrets
import time

import httpx
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        credentials = {
            "email": "smoke-" + secrets.token_hex(6) + "@example.com",
            "password": secrets.token_urlsafe(20),
        }
        client.post("/api/v1/auth/register", json=credentials).raise_for_status()
        image = io.BytesIO()
        Image.new("RGB", (64, 48), "#357866").save(image, format="PNG")
        response = client.post(
            "/api/v1/assets",
            files={"file": ("sample.png", image.getvalue(), "image/png")},
        )
        response.raise_for_status()
        asset = response.json()["id"]
        for aid in (None, asset):
            response = client.post(
                "/api/v1/plans",
                json={
                    "prompt": "Một thành phố lúc bình minh, máy quay chuyển động nhẹ",
                    "asset_id": aid,
                },
            )
            response.raise_for_status()
            path = "/api/v1/plans/" + response.json()["id"] + "/generate"
            response = client.post(path, json={"confirm_paid": False})
            response.raise_for_status()
            gid = response.json()["id"]
            assert client.post(path, json={"confirm_paid": False}).json()["id"] == gid
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                job = client.get("/api/v1/generations/" + gid).json()
                if job["status"] == "COMPLETED":
                    break
                time.sleep(1)
            assert job["status"] == "COMPLETED", job
            video = client.get(job["video_url"])
            video.raise_for_status()
            assert "video/mp4" in video.headers["content-type"]
            assert b"ftyp" in video.content[:32]
            partial = client.get(job["video_url"], headers={"Range": "bytes=0-99"})
            assert partial.status_code == 206
            download = client.get(job["video_url"] + "?download=true")
            assert "attachment" in download.headers["content-disposition"]
        assert len(client.get("/api/v1/generations").json()) == 2
        client.delete("/api/v1/auth/session").raise_for_status()
        assert client.get("/api/v1/me").status_code == 401
    print(
        "PASS: signup, image upload, draft approval, idempotency, text/image jobs, playback, range, download, logout"
    )


if __name__ == "__main__":
    main()
