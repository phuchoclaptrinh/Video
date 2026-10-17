# VO3 Studio MVP

> Chạy với Seedance thật: xem [REAL_RUN_SETUP.md](REAL_RUN_SETUP.md).
>
> Chạy Veo 3.1 qua PoYo: xem [POYO_VEO_SETUP.md](POYO_VEO_SETUP.md).

Bản MVP chạy được luồng: đăng ký → nhập mô tả/upload ảnh → bản nháp có cấu trúc → duyệt → queue/worker → xem/tải video mẫu → thư viện. Giao diện tiếng Việt, có trạng thái tiến trình, hủy và lỗi.

## Chế độ hiện tại

Mặc định là **demo**, không phát sinh phí provider. Planner dùng template deterministic cho đến khi OpenAI API key được cấu hình; khi bật, planner lập năm clip podcast sách theo `video_promt.txt`. Output demo vẫn là clip mẫu CC0, không được tạo từ prompt và không có thời lượng/tỷ lệ tương ứng request. UI luôn ghi rõ chế độ này. Các thông số trên thẻ video là thông số yêu cầu.

Chế độ Seedance thật được bật ở cấp máy chủ bằng `VIDEO_PROVIDER=seedance` và `ENABLE_PAID_GENERATION=true`; mỗi lần tạo còn yêu cầu người dùng xác nhận chi phí trên UI. Kiểm duyệt semantic thực tế, output moderation và quota/billing nội bộ chưa được triển khai. Không coi đây là bản production hoàn chỉnh.

## Chạy local

Sau khi cài dependencies, có thể chạy cả ba service bằng `python scripts/run_local.py` từ root. Script chọn port trống và in URL; Ctrl+C dừng các tiến trình do script tạo. Nếu dùng nhiều virtualenv, chạy từng terminal theo hướng dẫn bên dưới.

Python 3.11+ và Node 20+. Cài dependencies từng service trong virtualenv riêng:

```powershell
# backend
python -m pip install -r requirements.txt
# ai-service
python -m pip install -r requirements.txt
# frontend
npm.cmd ci
```

Terminal AI service, tại `ai-service`:

```powershell
$env:INTERNAL_API_KEY = 'local-mvp-development-key'
$env:VIDEO_ENABLE_FAKE = 'true'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Terminal backend, tại `backend`:

```powershell
$env:INTERNAL_API_KEY = 'local-mvp-development-key'
$env:AI_SERVICE_URL = 'http://127.0.0.1:8001'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal frontend, tại `frontend`:

```powershell
npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
```

Mở http://127.0.0.1:3000 và đăng ký tài khoản. Không có tài khoản/mật khẩu cài sẵn. Database SQLite và ảnh nằm trong `backend/storage`; task giả nằm trong `ai-service/storage`. Restart vẫn giữ lịch sử. Không chạy nhiều process backend ở local demo; worker nền tự quét journal, không cần Redis. Celery của Người 3 vẫn dùng được cho cấu hình riêng; đặt `LOCAL_VIDEO_WORKER=false` nếu chuyển sang Celery/Beat cùng database.

## Docker demo

Đặt `INTERNAL_API_KEY` ngẫu nhiên trong `.env` tại root. Khởi động Docker Desktop rồi chạy:

```powershell
docker compose -f docker-compose.mvp.yml up --build
```

Mở http://localhost:3000. Compose này độc lập với các compose khung trước đó, dùng named volume để giữ dữ liệu. Chưa xác minh build Docker trên máy hiện tại nếu Docker daemon không chạy.

## API

- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `DELETE /api/v1/auth/session`
- `GET /api/v1/config`, `GET /api/v1/health`
- `GET /api/v1/me`
- `GET`, `PUT`, `DELETE /api/v1/provider-credential`
- `POST /api/v1/assets`, `GET /api/v1/assets/{id}/file`
- `POST /api/v1/plans`, `POST /api/v1/plans/{id}/generate`
- `GET /api/v1/generations`, `GET /api/v1/generations/{id}`
- `POST /api/v1/generations/{id}/cancel`
- `GET /api/v1/generations/{id}/video?download=true`

Session lưu server, cookie HttpOnly/SameSite; password PBKDF2 có salt riêng. Kiểm tra ownership cho tài nguyên, kiểm tra Origin đối với browser write. Rate limit đăng nhập cục bộ theo IP. Trước khi public cần HTTPS (`COOKIE_SECURE=true`), cấu hình `APP_ORIGINS`, rate limit dùng chung, quota, migration và monitoring production.

Upload giới hạn 8 MB, 20 megapixel, chỉ ảnh JPEG/PNG/WebP giải mã được; chuẩn hóa JPEG và bỏ metadata. Demo không gửi ảnh riêng tư sang provider ngoài.

## Kiểm thử

Trong backend chạy `python -m pytest tests/test_platform.py tests/test_video_execution.py tests/test_video_execution_http.py -o addopts= -q`.

Trong AI service chạy `python -m pytest tests/test_video_execution.py -o addopts= -q`.

Trong frontend chạy `npm.cmd run typecheck` và `npm.cmd run build`.

Kiểm thử toàn luồng khi các service đang chạy: `python scripts/smoke_mvp.py --url http://127.0.0.1:3000`. Lệnh tạo tài khoản kiểm thử và hai video demo, xác minh upload, idempotency, playback/range, tải về và logout.
