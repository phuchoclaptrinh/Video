#!/usr/bin/env bash
# ==============================================================
# Nạp dữ liệu mẫu cho môi trường dev: user demo, prompt template,
# vài video job mẫu để test giao diện mà không tốn quota gen video.
#
#   bash scripts/seed.sh
#   bash scripts/seed.sh --reset    # xoá sạch dữ liệu rồi seed lại
#
# Logic seed nằm ở backend/app/db/init_db.py
# ==============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml"

# Chặn chạy nhầm trên production
[[ -f .env ]] && set -a && . ./.env && set +a
if [[ "${ENVIRONMENT:-development}" == "production" ]]; then
    echo "XX Không seed trên production." >&2
    exit 1
fi

if [[ "${1:-}" == "--reset" ]]; then
    echo "!! XOÁ TOÀN BỘ dữ liệu trong database rồi seed lại."
    read -r -p "   Gõ 'yes' để xác nhận: " confirm
    [[ "$confirm" == "yes" ]] || { echo "Đã huỷ."; exit 0; }

    echo ">> Downgrade về base"
    $COMPOSE exec backend alembic downgrade base
    echo ">> Upgrade lại head"
    $COMPOSE exec backend alembic upgrade head
fi

echo ">> Nạp dữ liệu mẫu"
$COMPOSE exec backend python -m app.db.init_db

echo ">> Xong. Tài khoản demo: demo@vo3.local / demo1234"
