# ==============================================================
# Lệnh tắt cho công việc hằng ngày.
# Windows: cài make bằng `winget install GnuWin32.Make` hoặc chạy trong Git Bash,
#          hoặc gõ thẳng lệnh docker compose ở phần bên phải mỗi target.
# ==============================================================

DC       := docker compose
DEV      := -f docker-compose.yml -f docker-compose.dev.yml
PROD     := -f docker-compose.yml -f docker-compose.prod.yml
BACKEND  := $(DC) $(DEV) exec backend
AI       := $(DC) $(DEV) exec ai-service
WEB      := $(DC) $(DEV) exec frontend

.DEFAULT_GOAL := help
.PHONY: help init up down restart logs ps build rebuild clean \
        migrate migration seed shell-db shell-redis shell-backend \
        test test-backend test-ai lint format typecheck \
        prod-up prod-down prod-logs

help: ## Hiện danh sách lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------- setup
init: ## Tạo .env từ .env.example và build lần đầu
	@test -f .env || cp .env.example .env
	@echo ">> Đã tạo .env — nhớ điền GOOGLE_API_KEY / SEEDANCE_API_KEY trước khi chạy"
	$(DC) $(DEV) build

# --------------------------------------------------------- dev
up: ## Khởi động toàn bộ stack (dev)
	$(DC) $(DEV) up -d
	@echo ">> Frontend : http://localhost:3000"
	@echo ">> API docs : http://localhost:8000/docs"
	@echo ">> AI docs  : http://localhost:8001/docs"

down: ## Dừng stack (giữ nguyên dữ liệu)
	$(DC) $(DEV) down

restart: ## Khởi động lại stack
	$(DC) $(DEV) restart

logs: ## Xem log tất cả service (make logs s=backend để xem 1 service)
	$(DC) $(DEV) logs -f --tail=100 $(s)

ps: ## Trạng thái các container
	$(DC) $(DEV) ps

build: ## Build lại image
	$(DC) $(DEV) build

rebuild: ## Build lại từ đầu, bỏ qua cache
	$(DC) $(DEV) build --no-cache

clean: ## XOÁ container + volume (mất sạch dữ liệu DB)
	$(DC) $(DEV) down -v --remove-orphans

# --------------------------------------------------- database
migrate: ## Áp dụng migration mới nhất
	$(BACKEND) alembic upgrade head

migration: ## Sinh migration mới:  make migration m="them bang video_jobs"
	$(BACKEND) alembic revision --autogenerate -m "$(m)"

downgrade: ## Lùi lại 1 migration
	$(BACKEND) alembic downgrade -1

seed: ## Nạp dữ liệu mẫu
	$(BACKEND) python -m app.db.init_db

shell-db: ## Mở psql
	$(DC) $(DEV) exec postgres psql -U $${POSTGRES_USER:-vo3} -d $${POSTGRES_DB:-vo3}

shell-redis: ## Mở redis-cli
	$(DC) $(DEV) exec redis redis-cli

shell-backend: ## Mở bash trong container backend
	$(BACKEND) bash

# -------------------------------------------------------- test
test: test-backend test-ai ## Chạy toàn bộ test

test-backend: ## Test backend
	$(BACKEND) pytest -m "not slow"

test-ai: ## Test ai-service
	$(AI) pytest -m "not provider"

test-web: ## Typecheck frontend
	$(WEB) npm run typecheck

# ------------------------------------------------ chất lượng mã
lint: ## Lint toàn bộ
	$(BACKEND) ruff check .
	$(AI) ruff check .
	$(WEB) npm run lint

format: ## Tự format toàn bộ
	$(BACKEND) ruff format .
	$(BACKEND) ruff check --fix .
	$(AI) ruff format .
	$(WEB) npm run format

typecheck: ## Kiểm tra kiểu
	$(BACKEND) mypy app
	$(AI) mypy app
	$(WEB) npm run typecheck

# -------------------------------------------------------- prod
prod-up: ## Khởi động stack production
	$(DC) $(PROD) up -d

prod-down: ## Dừng stack production
	$(DC) $(PROD) down

prod-logs: ## Log production
	$(DC) $(PROD) logs -f --tail=100 $(s)
