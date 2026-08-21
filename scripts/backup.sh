#!/usr/bin/env bash
# ==============================================================
# Backup PostgreSQL (và tuỳ chọn cả thư mục video).
#
#   bash scripts/backup.sh              # backup DB
#   bash scripts/backup.sh --with-media # backup cả DB lẫn video
#
# Đặt cron chạy hằng ngày lúc 2h sáng:
#   0 2 * * * cd /opt/create-video-vo3 && bash scripts/backup.sh >> logs/backup.log 2>&1
# ==============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"
WITH_MEDIA=false
[[ "${1:-}" == "--with-media" ]] && WITH_MEDIA=true

# Nạp biến DB từ .env
[[ -f .env ]] && set -a && . ./.env && set +a

DB_USER="${POSTGRES_USER:-vo3}"
DB_NAME="${POSTGRES_DB:-vo3}"

mkdir -p "$BACKUP_DIR"

# Chọn compose file theo môi trường
if [[ "${ENVIRONMENT:-development}" == "production" ]]; then
    COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
else
    COMPOSE="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
fi

# ------------------------------------------------------ database
DB_FILE="$BACKUP_DIR/db_${DB_NAME}_${STAMP}.sql.gz"
echo ">> Backup database -> $DB_FILE"

# -T tắt cấp phát TTY, bắt buộc khi chạy trong cron
$COMPOSE exec -T postgres \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner \
    | gzip > "$DB_FILE"

# File rỗng nghĩa là dump lỗi -> phải báo lỗi, không im lặng bỏ qua
if [[ ! -s "$DB_FILE" ]]; then
    echo "XX Backup rỗng — pg_dump thất bại" >&2
    rm -f "$DB_FILE"
    exit 1
fi
echo "   kích thước: $(du -h "$DB_FILE" | cut -f1)"

# --------------------------------------------------------- media
if [[ "$WITH_MEDIA" == true ]]; then
    MEDIA_FILE="$BACKUP_DIR/media_${STAMP}.tar.gz"
    echo ">> Backup video -> $MEDIA_FILE"
    tar -czf "$MEDIA_FILE" -C storage videos thumbnails 2>/dev/null || {
        echo "!! Không backup được media (thư mục trống?)"
    }
fi

# ------------------------------------------------------ dọn dẹp
echo ">> Xoá backup cũ hơn $RETENTION_DAYS ngày"
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -name "media_*.tar.gz" -mtime +"$RETENTION_DAYS" -delete

# ---------------------------------------------- đẩy lên S3 (tuỳ chọn)
if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then
    echo ">> Upload lên s3://$BACKUP_S3_BUCKET"
    aws s3 cp "$DB_FILE" "s3://$BACKUP_S3_BUCKET/db/"
fi

echo ">> Xong"
echo ""
echo "Khôi phục:"
echo "  gunzip -c $DB_FILE | $COMPOSE exec -T postgres psql -U $DB_USER -d $DB_NAME"
