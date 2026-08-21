#!/usr/bin/env bash
# ==============================================================
# Deploy thủ công lên server (dùng khi không qua GitHub Actions).
# Chạy TRÊN SERVER, trong thư mục deploy.
#
#   bash scripts/deploy.sh              # deploy tag latest
#   bash scripts/deploy.sh v1.2.0       # deploy tag cụ thể
# ==============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TAG="${1:-latest}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

info() { printf '\033[36m>> %s\033[0m\n' "$1"; }
die()  { printf '\033[31mXX %s\033[0m\n' "$1" >&2; exit 1; }

[[ -f .env ]] || die "Thiếu file .env trên server"

export IMAGE_TAG="$TAG"
info "Deploy tag: $TAG"

# ---------------------------------------------- backup trước
info "Backup database trước khi deploy"
bash scripts/backup.sh || die "Backup thất bại — dừng deploy"

# ---------------------------------------------------- pull
info "Kéo image mới"
$COMPOSE pull

# ------------------------------------------------- migration
# Chạy migration TRƯỚC khi đổi image app.
# Migration phải tương thích ngược (thêm cột nullable, không xoá cột đang dùng)
# để phiên bản cũ vẫn chạy được trong lúc rolling update.
info "Chạy migration"
$COMPOSE run --rm backend alembic upgrade head

# ----------------------------------------------------- up
info "Khởi động lại service"
$COMPOSE up -d --remove-orphans

# ------------------------------------------------- kiểm tra
info "Chờ health check"
HEALTH_URL="${PUBLIC_BASE_URL:-http://localhost}/api/v1/health"
for i in $(seq 1 30); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
        info "Deploy thành công"
        $COMPOSE ps
        info "Dọn image cũ"
        docker image prune -f --filter "until=168h"
        exit 0
    fi
    echo "   chờ service... ($i/30)"
    sleep 10
done

die "Health check thất bại sau 5 phút. Xem log: $COMPOSE logs --tail=200 backend"
