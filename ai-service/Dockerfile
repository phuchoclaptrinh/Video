# ==============================================================
# AI service (FastAPI) — multi-stage, cùng khuôn với backend
#   target development : uvicorn --reload
#   target production  : user không phải root
# ==============================================================

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ------------------------------------------------------- builder
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# --------------------------------------------------- development
FROM builder AS development

RUN pip install pytest pytest-asyncio pytest-cov respx ruff mypy watchdog

COPY . .

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]

# ---------------------------------------------------- production
FROM base AS production

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

RUN mkdir -p /app/storage && chown -R app:app /app/storage

USER app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8001/health || exit 1

# Service này chủ yếu chờ I/O (gọi API provider) nên ít worker là đủ
# 1 worker: số luồng Seedvis đang chạy được đếm trong bộ nhớ của tiến trình.
ENV WEB_CONCURRENCY=1
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port 8001 --workers ${WEB_CONCURRENCY}"]
