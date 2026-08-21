#!/usr/bin/env bash
# ==============================================================
# Chạy migration Alembic trong container backend.
#
#   bash scripts/migrate.sh                    # upgrade head
#   bash scripts/migrate.sh new "them bang x"  # sinh migration mới
#   bash scripts/migrate.sh down               # lùi 1 bước
#   bash scripts/migrate.sh history            # xem lịch sử
# ==============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
ACTION="${1:-up}"

case "$ACTION" in
    up|upgrade)
        echo ">> Áp dụng migration tới head"
        $COMPOSE exec backend alembic upgrade head
        ;;
    new|revision)
        MSG="${2:-}"
        [[ -z "$MSG" ]] && { echo "Thiếu mô tả: bash scripts/migrate.sh new \"them bang video_jobs\""; exit 1; }
        echo ">> Sinh migration: $MSG"
        $COMPOSE exec backend alembic revision --autogenerate -m "$MSG"
        echo ">> ĐỌC LẠI file vừa sinh trong backend/alembic/versions/ trước khi commit."
        echo "   Autogenerate hay bỏ sót: đổi tên cột, đổi kiểu dữ liệu, index có điều kiện."
        ;;
    down|downgrade)
        echo ">> Lùi 1 migration"
        $COMPOSE exec backend alembic downgrade -1
        ;;
    history)
        $COMPOSE exec backend alembic history --verbose
        ;;
    current)
        $COMPOSE exec backend alembic current
        ;;
    check)
        echo ">> Kiểm tra model đã khớp migration chưa"
        $COMPOSE exec backend alembic check
        ;;
    *)
        echo "Lệnh không hợp lệ: $ACTION"
        echo "Dùng: up | new <msg> | down | history | current | check"
        exit 1
        ;;
esac
