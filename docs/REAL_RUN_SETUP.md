# Cấu hình chạy thật với Seedance

Tài liệu này cấu hình VO3 Studio gọi Seedance 1.5 Pro thật. Mỗi tác vụ được chấp nhận có thể phát sinh phí trong tài khoản BytePlus ModelArk.

## 1. API cần có

Hiện tại chỉ cần một API bên ngoài:

- **BytePlus ModelArk / Seedance API key**: dùng để tạo và kiểm tra trạng thái video.

Không cần API database, Redis hoặc S3 cho bản local cơ bản. Backend dùng SQLite và lưu ảnh/video trên máy. Planner mặc định dùng template; có thể thêm OpenAI API key trong mục API key để lập kịch bản podcast sách theo `video_promt.txt`. Planner không thay thế hệ thống kiểm duyệt production.

## 2. Tạo file môi trường

Từ thư mục root của project:

```powershell
Copy-Item .env.real.example .env
```

Mở `.env` và thay hai giá trị sau:

```dotenv
INTERNAL_API_KEY=<chuỗi bí mật ngẫu nhiên, dài>
SEEDANCE_API_KEY=<API key BytePlus ModelArk thật>
```

Giữ nguyên các giá trị chính:

```dotenv
SEEDANCE_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3
SEEDANCE_MODEL=seedance-1-5-pro-251215
VIDEO_PROVIDER=seedance
ENABLE_PAID_GENERATION=true
VIDEO_ENABLE_FAKE=false
```

Không commit `.env`. API key Seedance chỉ được truyền cho `ai-service`; frontend và trình duyệt không nhận key này.

## 3. Cài dependency

Yêu cầu Python 3.11+ và Node.js 20+.

```powershell
python -m pip install -r backend/requirements.txt
python -m pip install -r ai-service/requirements.txt
Set-Location frontend
npm.cmd ci
Set-Location ..
```

## 4. Chạy local

Tại thư mục root:

```powershell
python scripts/run_local.py
```

Script tự đọc `.env`, chọn các port còn trống và in URL giao diện. Sau khi đăng ký tài khoản:

1. Nhập prompt và tùy chọn video.
2. Có thể tải ảnh JPG, PNG hoặc WebP tối đa 8 MB.
3. Tạo và kiểm tra bản nháp.
4. Đánh dấu xác nhận quyền sử dụng nội dung và chấp nhận chi phí.
5. Bấm **Xác nhận & tạo bằng Seedance**.

Ảnh đã chuẩn hóa được gửi tới Seedance dưới dạng Base64 HTTPS payload. Khi provider hoàn tất, backend tải signed URL về `backend/storage/videos` trước khi cung cấp cho người dùng; lịch sử và metadata nằm trong SQLite tại `backend/storage/mvp.sqlite3`.

## 5. Chạy bằng Docker

Sau khi đã có `.env`:

```powershell
docker compose -f docker-compose.mvp.yml up --build
```

Mở `http://localhost:3000`. Dữ liệu được giữ trong Docker named volumes.

## 6. Kiểm tra cấu hình

Khi hệ thống đang chạy, endpoint sau phải trả `mode: "live"`, `provider: "seedance"` và `paid_generation: true`:

```powershell
Invoke-RestMethod http://127.0.0.1:<backend-port>/api/v1/config
```

`<backend-port>` là port được launcher in ra. `GET http://127.0.0.1:<ai-port>/health` phải liệt kê provider `seedance`; `<ai-port>` cũng được launcher in trong log.

## Giới hạn cần biết

- Chưa có quota theo người dùng, billing nội bộ hoặc moderation semantic riêng. Hướng dẫn planner: [LLM Story Planner](LLM_STORY_PLANNER_SETUP.md).
- Checkbox trên UI là xác nhận thủ công, không thay thế kiểm duyệt nội dung production.
- Chỉ task đang chờ của Seedance mới có thể hủy; task đã chạy có thể tiếp tục phát sinh phí.
- Signed URL từ provider được tải ngay về storage vì URL có thời hạn.
- Nếu submit gặp timeout không rõ kết quả, hệ thống chuyển sang trạng thái cần xử lý thay vì tự submit lại và có nguy cơ tính phí hai lần.

Tham chiếu chính thức: [tạo task](https://docs.byteplus.com/en/docs/ModelArk/1520757), [lấy trạng thái task](https://docs.byteplus.com/en/docs/ModelArk/1521309), [hủy task](https://docs.byteplus.com/en/docs/ModelArk/1521720).
