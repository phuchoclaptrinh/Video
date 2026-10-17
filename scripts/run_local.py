"""Launch all MVP services; Ctrl+C stops only children started by this script."""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def load_env(path):
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def free_port(start):
    for port in range(start, start + 100):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No available port")


def main():
    root = Path(__file__).resolve().parents[1]
    load_env(root / ".env")
    ai_port, backend_port, web_port = free_port(8001), free_port(8100), free_port(3000)
    provider = os.getenv("VIDEO_PROVIDER", "fake").lower()
    provider_keys = {"seedance": "SEEDANCE_API_KEY"}
    required_key = provider_keys.get(provider)
    if required_key:
        value = os.getenv(required_key, "")
        if not value or value.startswith("replace-with-"):
            raise SystemExit(
                f"Set a real {required_key} in .env when VIDEO_PROVIDER={provider}"
            )
    env = dict(
        os.environ,
        INTERNAL_API_KEY=os.getenv("INTERNAL_API_KEY", secrets.token_urlsafe(32)),
        VIDEO_ENABLE_FAKE="false" if provider != "fake" else "true",
        VIDEO_ENABLE_POYO="true" if provider == "poyo" else "false",
        VIDEO_ENABLE_SEEDVIS="true" if provider == "seedvis" or os.getenv("IMAGE_PROVIDER") == "seedvis" else os.getenv("VIDEO_ENABLE_SEEDVIS", "false"),
        AI_SERVICE_URL=f"http://127.0.0.1:{ai_port}",
        NEXT_PUBLIC_API_URL=f"http://127.0.0.1:{backend_port}",
        APP_ORIGINS=f"http://127.0.0.1:{web_port},http://localhost:{web_port}",
        LOCAL_VIDEO_WORKER="true",
    )
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("Install Node.js first")
    processes = []
    try:
        for directory, command in [
            (
                "ai-service",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(ai_port),
                ],
            ),
            (
                "backend",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                ],
            ),
            (
                "frontend",
                [
                    npm,
                    "run",
                    "dev",
                    "--",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
            ),
        ]:
            processes.append(subprocess.Popen(command, cwd=root / directory, env=env))
        print(f"VO3 Studio: http://127.0.0.1:{web_port}", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False
                    )
                else:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
