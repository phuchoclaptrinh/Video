-- ==============================================================
-- Chạy MỘT LẦN DUY NHẤT khi volume postgres còn trống.
-- Đã có dữ liệu rồi thì file này bị bỏ qua -> muốn chạy lại phải:
--   docker compose down -v
--
-- Ở đây chỉ bật extension và thiết lập mặc định.
-- Schema/bảng do Alembic quản lý, KHÔNG tạo bảng trong file này.
-- ==============================================================

-- gen_random_uuid() cho khoá chính dạng UUID
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Tìm kiếm gần đúng trên prompt / tiêu đề video
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Bỏ dấu tiếng Việt khi tìm kiếm ("hoàng hôn" khớp "hoang hon")
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- Timezone mặc định cho chính database vừa được tạo.
-- Dùng format() vì tên DB đến từ biến môi trường POSTGRES_DB.
DO $$
BEGIN
    EXECUTE format(
        'ALTER DATABASE %I SET timezone TO %L',
        current_database(),
        'Asia/Ho_Chi_Minh'
    );
END
$$;

-- Text search config tiếng Việt đơn giản: bỏ dấu rồi mới index
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'vi') THEN
        CREATE TEXT SEARCH CONFIGURATION vi (COPY = simple);
        ALTER TEXT SEARCH CONFIGURATION vi
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, simple;
    END IF;
END
$$;

-- ==============================================================
-- pg_stat_statements (thống kê truy vấn chậm) cần được nạp trước qua
-- shared_preload_libraries nên KHÔNG bật được ở đây. Muốn dùng thì thêm
-- vào service postgres trong docker-compose.yml:
--
--   command: ["postgres", "-c", "shared_preload_libraries=pg_stat_statements"]
--
-- rồi chạy: CREATE EXTENSION pg_stat_statements;
-- ==============================================================
