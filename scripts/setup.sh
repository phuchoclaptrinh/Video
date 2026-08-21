#!/usr/bin/env bash
# ==============================================================
# Dựng môi trường dev từ đầu.
# Chạy:  bash scripts/setup.sh
# ==============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info()  { printf '\033[36m>> %s\033[0m\n' "$1"; }
warn()  { printf '\033[33m!! %s\033[0m\n' "$1"; }
die()   { printf '\033[31mXX %s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------- kiểm tra công cụ
info "Kiểm tra công cụ cần thiết"
command -v docker >/dev/null || die "Chưa cài Docker: https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "Cần Docker Compose v2 (docker compose, không phải docker-compose)"
docker info >/dev/null 2>&1 || die "Docker chưa chạy — mở Docker Desktop trước"

# ------------------------------------------------------- .env
if [[ -f .env ]]; then
    warn ".env đã tồn tại — giữ nguyên, không ghi đè"
else
    cp .env.example .env
    info "Đã tạo .env từ .env.example"

    # Sinh SECRET_KEY ngẫu nhiên thay cho giá trị mẫu
    if command -v openssl >/dev/null; then
        SECRET="$(openssl rand -hex 32)"
        # macOS dùng sed -i '' còn GNU dùng sed -i
        if sed --version >/dev/null 2>&1; then
            sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" .env
        else
            sed -i '' "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" .env
        fi
        info "Đã sinh SECRET_KEY ngẫu nhiên"
    fi

    warn "MỞ .env VÀ ĐIỀN: GOOGLE_API_KEY, SEEDANCE_API_KEY"
fi

# ------------------------------------------------- thư mục lưu trữ
info "Tạo thư mục storage"
mkdir -p storage/videos storage/thumbnails storage/temp

# -------------------------------------------------------- build
info "Build image (lần đầu mất khoảng 5-10 phút)"
docker compose -f docker-compose.yml -f docker-compose.dev.yml build

info "Khởi động postgres + redis"
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis

info "Chờ database sẵn sàng"
for i in $(seq 1 30); do
    if docker compose -f docker-compose.yml -f docker-compose.dev.yml \
        exec -T postgres pg_isready -U "${POSTGRES_USER:-vo3}" >/dev/null 2>&1; then
        break
    fi
    [[ $i -eq 30 ]] && die "Database không khởi động được sau 60s"
    sleep 2
done

info "Khởi động toàn bộ stack"
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

cat <<'EOF'

==============================================================
  Xong. Các địa chỉ:
    Frontend    http://localhost:3000
    API docs    http://localhost:8000/docs
    AI service  http://localhost:8001/docs

  Lệnh hay dùng:
    make logs          xem log
    make migrate       chạy migration
    make down          dừng stack
==============================================================
EOF
