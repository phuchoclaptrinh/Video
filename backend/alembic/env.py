"""Alembic environment.

Đọc URL database từ biến môi trường DATABASE_URL_SYNC (không hardcode vào
alembic.ini) và lấy metadata từ ``app.db.base`` — file đó phải import toàn bộ
model để autogenerate nhìn thấy được bảng.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# app.db.base import tất cả model rồi export Base -> autogenerate mới thấy bảng.
# (File này sẽ tồn tại sau khi bạn code phần models.)
from app.db.base import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Ưu tiên biến môi trường, fallback về giá trị trong alembic.ini
database_url = os.getenv("DATABASE_URL_SYNC") or config.get_main_option("sqlalchemy.url")
if not database_url:
    raise RuntimeError(
        "Chưa có DATABASE_URL_SYNC. Đặt trong .env, ví dụ: "
        "postgresql+psycopg://vo3:vo3@localhost:5432/vo3"
    )
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    """Bỏ qua bảng không do app quản lý (ví dụ bảng của extension)."""
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return True


def run_migrations_offline() -> None:
    """Sinh SQL ra stdout, không cần kết nối DB (alembic upgrade head --sql)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Chạy migration trực tiếp trên database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
