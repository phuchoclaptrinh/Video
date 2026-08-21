# Create Video VO3

Hệ thống tạo video bằng AI (Google **Veo 3** / **Seedance 1.5**).
Luồng chính: người dùng chat mô tả video → LLM sinh prompt video chuẩn hoá → dùng prompt đó gọi model gen video → trả video về cho người dùng.

## Luồng nghiệp vụ

```
User (chat UI)
   │  "tạo video con mèo bay trên thành phố lúc hoàng hôn"
   ▼
Frontend (Next.js)  ──SSE/WebSocket cho tiến trình──┐
   │ POST /api/v1/chat                              │
   ▼                                                │
Backend (FastAPI)                                   │
   │ 1. lưu conversation/message                    │
   │ 2. gọi ai-service: /prompt/generate            │
   ▼                                                │
AI Service ── prompt_engine ── chat_llm (Gemini/Claude)
   │ trả về video prompt đã tối ưu (scene, camera, style, duration...)
   ▼
Backend tạo VideoJob (status=PENDING) → đẩy vào Celery queue
   │
   ▼
Worker ── ai-service /video/generate ── provider (veo3 | seedance)
   │ polling / webhook đến khi hoàn tất
   ▼
Storage (local hoặc S3/GCS) → cập nhật VideoJob (status=DONE, url) ──────┘
```

## Kiến trúc thư mục

| Thư mục | Vai trò |
|---|---|
| `backend/` | API chính (FastAPI): auth, chat, quản lý job, DB, Celery worker |
| `ai-service/` | Lớp model: sinh prompt + adapter tới Veo 3 / Seedance, pipeline gen video |
| `frontend/` | Giao diện chat + xem/tải video (Next.js + TypeScript + Tailwind) |
| `infra/` | Nginx, k8s manifest, terraform, config Postgres/Redis |
| `scripts/` | Script setup / migrate / seed / deploy / backup |
| `docs/` | Tài liệu kiến trúc, API, hướng dẫn viết prompt, ADR |
| `storage/` | Nơi lưu video/thumbnail khi chạy local (đã gitignore) |
| `.github/workflows/` | CI (lint, test) và CD (build image, deploy) |

### Điểm mở rộng chính

- **Thêm model mới**: tạo file trong `ai-service/app/providers/`, kế thừa `base.py`, đăng ký trong `registry.py`.
- **Đổi cách sinh prompt**: `ai-service/app/prompt_engine/` + template ở `ai-service/app/prompts/`.
- **Thêm endpoint**: `backend/app/api/v1/` rồi gắn vào `backend/app/api/router.py`.

## Bắt đầu

```bash
cp .env.example .env            # điền GOOGLE_API_KEY / SEEDANCE_API_KEY, DB, Redis
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs
- AI service: http://localhost:8001/docs

> Hiện tất cả file source đang **rỗng** — đây mới là khung dự án.
> Thứ tự nên code: `backend/app/core/config.py` → `db` → `models` → `schemas` → `ai-service/providers` → `services` → `api` → `frontend`.

## Stack đề xuất

- Backend: Python 3.11, FastAPI, SQLAlchemy 2, Alembic, Celery, Redis, PostgreSQL
- AI service: FastAPI, google-genai (Veo 3), Volcengine/BytePlus SDK (Seedance 1.5)
- Frontend: Next.js (App Router), TypeScript, TailwindCSS, Zustand
- Hạ tầng: Docker Compose (dev) / Kubernetes (prod), Nginx reverse proxy, GitHub Actions
