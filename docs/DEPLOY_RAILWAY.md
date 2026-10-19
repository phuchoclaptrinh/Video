# Deploy lên Railway

Ba service chạy từ cùng repo GitHub này:

| Service | Thư mục | Truy cập | Volume |
|---|---|---|---|
| `frontend` | `/frontend` | **Public** (tên miền của web) | Không |
| `backend` | `/backend` | Chỉ nội bộ, frontend gọi qua `/api/*` | `/app/storage`: database SQLite, ảnh, video |
| `ai-service` | `/ai-service` | Chỉ nội bộ, backend gọi | `/app/storage`: key provider đã mã hoá |

Người dùng chỉ vào tên miền của `frontend`. Next.js chuyển tiếp `/api/*` sang `backend` qua mạng nội bộ của Railway, nên cookie đăng nhập dùng chung một tên miền và các request lâu (lập kịch bản, tạo ảnh, 1–3 phút) không bị cắt.

Mỗi service có file `railway.toml` trong thư mục của nó. File này cấu hình build bằng Dockerfile, lệnh chạy và health check.

> **Bắt buộc 1 replica và 1 tiến trình cho `backend`.** Tiến trình API cũng chạy worker render/chiến dịch trên một file SQLite. Hai bản chạy song song sẽ gây lỗi "database is locked" và xử lý trùng. `railway.toml` đã đặt `--workers 1`; đừng tăng replica trong Railway.

## 1. Tạo project và 3 service

1. Railway → **New Project** → **Deploy from GitHub repo** → chọn `phuchoclaptrinh/Video`.
2. Tạo đủ 3 service từ cùng repo (**+ New → GitHub Repo**). Đặt tên **đúng**: `frontend`, `backend`, `ai-service`. Các biến ở bước 4 tham chiếu theo tên này.
3. Ở **Settings** của từng service:
   - **Root Directory**: `/frontend`, `/backend` hoặc `/ai-service`.
   - **Config file path**: `/frontend/railway.toml`, `/backend/railway.toml`, `/ai-service/railway.toml`. Nên đặt rõ, vì Railway có thể chỉ tìm `railway.toml` ở gốc repo. Kiểm tra: tab Deployments → bản deploy → **Details** phải hiện lệnh chạy `uvicorn ... --workers 1` cho backend/ai-service.
   - Dockerfile cũng mặc định 1 tiến trình (`WEB_CONCURRENCY=1`), nên **đừng đặt `WEB_CONCURRENCY` lớn hơn 1**: backend sẽ xử lý trùng trên SQLite, ai-service đếm sai số luồng Seedvis.

## 2. Volume

- `backend` → **+ New Volume**, mount path `/app/storage`.
- `ai-service` → **+ New Volume**, mount path `/app/storage`.

Không có volume thì mỗi lần deploy lại sẽ **mất hết** tài khoản, credit, video và key đã nhập.

## 3. Biến dùng chung

**Project Settings → Shared Variables**, thêm:

```
INTERNAL_API_KEY=<chuỗi ngẫu nhiên dài 48+ ký tự>
```

Tạo chuỗi: `python -c "import secrets; print(secrets.token_urlsafe(36))"`.

> **Không đổi `INTERNAL_API_KEY` sau khi đã nhập key trên web.** Key PoYo/Seedvis/OpenAI lưu trong `ai-service` được mã hoá bằng khoá suy ra từ biến này; đổi giá trị thì không đọc lại được, phải nhập lại toàn bộ key.

## 4. Biến của từng service

Mở **Variables → Raw Editor** của từng service và dán khối tương ứng. Giá trị trong `${{...}}` là biến tham chiếu, Railway tự điền.

### ai-service

```
INTERNAL_API_KEY=${{shared.INTERNAL_API_KEY}}
RAILWAY_RUN_UID=0
VIDEO_ENABLE_FAKE=false
VIDEO_PROVIDER=poyo
IMAGE_PROVIDER=openai
POYO_BASE_URL=https://api.poyo.ai
POYO_MODEL=veo3.1-fast
SEEDVIS_BASE_URL=https://seedvis.com/api/v1
SEEDVIS_VIDEO_MODEL=Veo-3.1
SEEDVIS_IMAGE_MODEL=gpt-image-2
OPENAI_PLANNER_MODEL=gpt-6-luna
OPENAI_IMAGE_MODEL=gpt-image-2.5-flare
VOICE_PACE=koc
VIDEO_FAKE_DB=/app/storage/fake.sqlite3
PROVIDER_CREDENTIAL_PATH=/app/storage/provider-credentials.json.enc
```

### backend

```
INTERNAL_API_KEY=${{shared.INTERNAL_API_KEY}}
RAILWAY_RUN_UID=0
AI_SERVICE_URL=http://${{ai-service.RAILWAY_PRIVATE_DOMAIN}}:8001
VIDEO_EXECUTION_DATABASE_URL=sqlite:////app/storage/mvp.sqlite3
STORAGE_LOCAL_PATH=/app/storage
LOCAL_VIDEO_WORKER=true
VIDEO_PROVIDER=poyo
IMAGE_PROVIDER=openai
POYO_MODEL=veo3.1-fast
SEEDVIS_VIDEO_MODEL=Veo-3.1
SEEDVIS_IMAGE_MODEL=gpt-image-2
OPENAI_PLANNER_MODEL=gpt-6-luna
OPENAI_IMAGE_MODEL=gpt-image-2.5-flare
VOICE_PACE=koc
ENABLE_PAID_GENERATION=true
VIDEO_OUTPUT_ALLOWED_HOST_SUFFIXES=.poyo.ai,.doculator.org,.seedvis.com
VIDEO_OUTPUT_MAX_BYTES=524288000
ADMIN_EMAILS=<email admin, vd admin@tenmien.vn>
PUBLIC_BASE_URL=https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}
APP_ORIGINS=https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}
COOKIE_SECURE=true
```

- `PUBLIC_BASE_URL`: PoYo tải ảnh tham chiếu qua link HTTPS công khai. Link đi qua tên miền frontend rồi được chuyển tiếp vào backend, nên backend không cần tên miền public.
- `RAILWAY_RUN_UID=0`: image chạy bằng user thường, còn volume Railway thuộc root. Nếu log báo `Permission denied` khi ghi `/app/storage`, kiểm tra biến này.

### frontend

```
NEXT_PUBLIC_API_URL=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:8000
PORT=3000
```

`NEXT_PUBLIC_API_URL` được nhúng **lúc build**. Đổi tên service `backend` thì phải deploy lại `frontend`.

## 5. Tên miền

- `frontend` → **Settings → Networking → Generate Domain** (hoặc gắn tên miền riêng).
- **Không** tạo domain public cho `backend` và `ai-service`.
- Dùng tên miền riêng (vd `app.tenmien.vn`) thì đổi `PUBLIC_BASE_URL` và `APP_ORIGINS` của backend thành `https://app.tenmien.vn`. `RAILWAY_PUBLIC_DOMAIN` chỉ là tên miền `*.up.railway.app`.

## 6. Deploy và cài đặt lần đầu

1. Deploy theo thứ tự `ai-service` → `backend` → `frontend`. Chờ từng service báo healthy.
2. Mở `https://<tên miền frontend>/admin`, bấm **Lần đầu cài đặt? Tạo tài khoản admin** và đăng ký bằng email trong `ADMIN_EMAILS` (email bạn đặt ở bước 4). Người dùng thường không tự đăng ký được; chỉ email admin mới tạo được tài khoản ở bước này.
3. `/admin` → **API key**: nhập key PoYo, các key Seedvis và key OpenAI. Key được lưu mã hoá trong volume của `ai-service`.
4. `/admin` → **Credit**: đặt giá từng model, giá kịch bản/ảnh, mức tặng tài khoản mới, ẩn các model không bán.
5. `/admin` → **Người dùng** → **Tạo tài khoản** để cấp tài khoản cho khách (có nút tạo mật khẩu ngẫu nhiên và sao chép thông tin đăng nhập). Biểu tượng chìa khoá ở mỗi dòng dùng để đặt lại mật khẩu. Tạo thử một tài khoản thành viên để kiểm tra số dư, giá model và việc tạo video.

Database trên Railway bắt đầu **trống**. Dữ liệu ở máy local (`backend/storage/mvp.sqlite3`) không tự chuyển lên.

## 7. Sao lưu

- **Database:** `/admin` → **Tổng quan** → **Tải bản sao lưu**. File `.sqlite3` tải về là bản chụp nhất quán (tài khoản, credit, lịch sử giao dịch, dự án, chiến dịch), lấy được cả khi app đang chạy. Nên tải định kỳ, ví dụ mỗi ngày.
- **Ảnh/video:** nằm trong volume `backend`. Nếu gói Railway có tính năng backup volume thì bật lịch backup cho volume này.
- **Video trên Cloudflare R2 (tuỳ chọn):** đặt thêm cho `backend`:

  ```
  R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
  R2_BUCKET=vo3-backup
  R2_ACCESS_KEY_ID=<access key>
  R2_SECRET_ACCESS_KEY=<secret key>
  R2_PREFIX=storage
  ```

  Mỗi 5 phút backend đẩy video trong `storage/videos` lên `<bucket>/<R2_PREFIX>/videos/`. Bản trên volume được xoá sau `R2_KEEP_LOCAL_HOURS` giờ không dùng (mặc định 24), hoặc sớm hơn khi volume còn dưới 20% trống. Web vẫn xem và tải được: file không còn trên volume thì trình duyệt được chuyển sang link R2 có hạn 1 giờ. Khi cần ghép hay làm lại cảnh, backend tự tải clip về. Ảnh người dùng tải lên (`storage/*.jpg`) và file `continuity/` cũng được sao lưu lên R2 theo cùng chu kỳ nhưng vẫn giữ trên volume; database vẫn chỉ ở volume.
- **Server riêng (VPS):** dùng `scripts/backup_sqlite.py`, có thể chạy theo cron:

  ```
  python3 scripts/backup_sqlite.py --db /app/storage/mvp.sqlite3 --out /backups --with-storage --keep-days 14
  ```

Khôi phục: dừng `backend`, thay `/app/storage/mvp.sqlite3` bằng file sao lưu, rồi chạy lại.

## Khi nào chuyển sang Postgres

SQLite trên một volume phù hợp khi chỉ chạy 1 backend. Muốn chạy nhiều backend cùng lúc thì cần chuyển sang Postgres (`VIDEO_EXECUTION_DATABASE_URL=postgresql+psycopg://...`), chuyển dữ liệu từ SQLite và chỉ cho một tiến trình chạy worker (`LOCAL_VIDEO_WORKER=false` ở các bản còn lại). Code chưa được kiểm thử trên Postgres.
