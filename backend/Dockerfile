# ==============================================================
# Backend (FastAPI + Celery) — multi-stage
#   target development : có dev deps, chạy uvicorn --reload (compose dev mount code vào)
#   target production  : chỉ runtime deps, chạy bằng user không phải root
# Venv nằm ở /app/.venv để compose dev có thể mount đè source mà không mất deps.
# ==============================================================

# ---------------------------------------------------------- base
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# curl dùng cho HEALTHCHECK, libpq cho psycopg/asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ------------------------------------------------------- builder
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

# Copy riêng requirements trước để tận dụng cache layer khi code đổi
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# --------------------------------------------------- development
FROM builder AS development

COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------- production
FROM base AS production

# Chạy bằng user thường, không dùng root
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --create-home app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . .

# Thư mục lưu video (compose sẽ mount volume đè lên)
RUN mkdir -p /app/storage && chown -R app:app /app/storage

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

# Mặc định 1 worker: tiến trình API còn chạy worker render/chiến dịch trên SQLite, nhiều worker
# sẽ xử lý trùng và khoá database. `::` nhận cả IPv4 lẫn mạng nội bộ IPv6 (Railway).
ENV WEB_CONCURRENCY=1
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port 8000 --workers ${WEB_CONCURRENCY}"]
