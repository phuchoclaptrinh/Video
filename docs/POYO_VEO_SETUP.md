# Chạy Google Veo 3.1 qua PoYo

Project đã tích hợp API từ trang [PoYo Veo 3.1](https://poyo.ai/models/veo-3-1). Provider mặc định là `poyo`, model mặc định là `veo3.1-fast`.

## Lấy API key

1. Đăng nhập PoYo và mở [trang quản lý API key](https://poyo.ai/dashboard/api-key).
2. Tạo API key và nạp đủ credits cho tài khoản.
3. Khởi động VO3 Studio, đăng nhập và mở mục **API key** trên thanh điều hướng.
4. Nhập key rồi bấm **Lưu API key**.

Key được gửi qua backend đã xác thực và không bao giờ được trả lại cho trình duyệt. AI service mã hóa key trước khi lưu; không đưa key vào source code hoặc Git.

## Cấu hình

File `.env` local đã được đặt sẵn như sau:

```dotenv
POYO_BASE_URL=https://api.poyo.ai
POYO_MODEL=veo3.1-fast
POYO_CONTINUITY_MODE=adaptive
VIDEO_PROVIDER=poyo
ENABLE_PAID_GENERATION=true
VIDEO_ENABLE_FAKE=false
VIDEO_ENABLE_POYO=true
VIDEO_OUTPUT_ALLOWED_HOST_SUFFIXES=.poyo.ai,.doculator.org
```

Có thể chọn một trong ba model:

- `veo3.1-lite`: chi phí thấp nhất, chỉ text-to-video.
- `veo3.1-fast`: mặc định, cân bằng tốc độ và chất lượng.
- `veo3.1-quality`: chất lượng cao hơn, chi phí cao hơn.

Giá có thể thay đổi; kiểm tra trực tiếp trên [trang model PoYo](https://poyo.ai/models/veo-3-1) trước khi tạo.

Khi làm video nhiều cảnh trên `veo3.1-fast`, `adaptive` dùng ảnh gốc làm frame mở đầu cảnh 1; từ cảnh 2 trở đi, gửi ba ảnh: nhân vật cắt từ ảnh gốc, bối cảnh cắt từ ảnh gốc và frame cuối cảnh trước. Cách này giữ lại tín hiệu khuôn mặt ban đầu khi clip trước có dấu hiệu lệch. PoYo xem ba ảnh như ảnh tham chiếu, nên không bảo đảm frame cuối cảnh trước trở thành frame mở đầu chính xác.

Đặt `POYO_CONTINUITY_MODE=first_frame` nếu ưu tiên đường cắt liền mạch hơn khuôn mặt; mỗi cảnh sau chỉ dùng frame cuối cảnh trước. Đặt `reference` nếu muốn cả cảnh 1 cũng dùng ba ảnh. Chế độ ba ảnh hiện chỉ áp dụng cho `veo3.1-fast`; model khác tiếp tục dùng một ảnh mở đầu. Dù chọn chế độ nào, model vẫn có thể thay đổi khuôn mặt; bộ kiểm tra sau mỗi cảnh sẽ chặn khi phát hiện lệch lớn, không thể bảo đảm nhận diện tuyệt đối. Video đã tạo sẽ không thay đổi khi đổi cấu hình.

## Khởi động

Tại thư mục root:

```powershell
python scripts/run_local.py
```

Launcher tự đọc `.env` và cho phép ứng dụng khởi động khi chưa có PoYo key. URL frontend được in ra terminal sau khi ba service khởi động. Sau khi đăng nhập, giao diện sẽ báo trạng thái key và khóa nút tạo video cho đến khi key được cấu hình.

Key đã mã hóa nằm tại `ai-service/storage/provider-credentials.json.enc`; Docker lưu file này trong volume `ai_data`. Khóa mã hóa được suy ra từ `INTERNAL_API_KEY`, vì vậy phải giữ ổn định biến này giữa các lần restart. Đổi `INTERNAL_API_KEY` sẽ khiến credential cũ không giải mã được và cần nhập lại trên UI.

Biến `POYO_API_KEY` vẫn được hỗ trợ như phương án cấu hình bằng môi trường, nhưng không còn bắt buộc.

## Luồng hoạt động

1. Backend tạo bản nháp 8 giây ở 720p.
2. Người dùng xác nhận quyền sử dụng nội dung và chấp nhận chi phí.
3. AI service gửi `POST /api/generate/submit` tới PoYo.
4. Worker poll `GET /api/generate/status/{task_id}`.
5. Khi trạng thái là `finished`, backend tải video từ CDN do PoYo trả về (`*.poyo.ai` hoặc `*.doculator.org`) về storage local.
6. UI phát video từ backend, không phụ thuộc URL PoYo có thời hạn.

Veo 3.1 ở trang này chỉ tạo video 8 giây. UI tự giới hạn tỷ lệ `16:9` và `9:16`.

### Kiểm tra lời nói theo cảnh

Khi đã nhập OpenAI API key cho planner qua giao diện, backend gửi mỗi clip PoYo sang `gpt-4o-mini-transcribe` để so lời nói thực tế với kịch bản. Bước này phát sinh thêm phí OpenAI và có thể tăng thời gian xử lý một chút. Có thể đổi model bằng `OPENAI_TRANSCRIPTION_MODEL`. Nếu lời nói bị thiếu, lệch nhiều hoặc lặp, batch dừng trước cảnh kế tiếp; các cảnh tốt đã tạo vẫn được giữ. Sửa lời thoại ở cảnh lỗi rồi xác nhận thử lại riêng cảnh đó. Lần tạo lại trên PoYo có thể bị tính phí; hệ thống không tự gửi lại một clip đã tính phí. Nếu OpenAI không kiểm tra được, app hiển thị cảnh báo và không tự khẳng định tiếng nói đã đạt.

## Giới hạn ảnh đầu vào

API công khai của PoYo yêu cầu `image_urls` là URL HTTPS và không tài liệu hóa việc nhận Base64 hoặc endpoint upload media. Vì project hiện lưu ảnh local, chế độ Poyo tạm thời chỉ bật text-to-video và ẩn upload ảnh để tránh một tác vụ trả phí bị lỗi.

Để bật image-to-video an toàn, cần thêm object storage hoặc media server HTTPS công khai rồi truyền signed URL cho PoYo. `veo3.1-lite` không hỗ trợ image-to-video; dùng `veo3.1-fast` hoặc `veo3.1-quality`.

## Hủy và lỗi mạng

PoYo chưa tài liệu hóa endpoint hủy task. Nút hủy sẽ ngừng ý định phía ứng dụng nhưng worker vẫn theo dõi task đã gửi để không bỏ mất video có thể đã bị tính phí.

Nếu kết nối đứt trong lúc submit và không rõ PoYo đã nhận task hay chưa, hệ thống chuyển job sang `NEEDS_USER_ACTION` thay vì tự gửi lại. Điều này tránh nguy cơ tạo hai video và trừ credits hai lần.

Tài liệu chính thức: [Veo 3.1 API](https://docs.poyo.ai/api-manual/video-series/veo-3-1), [Task status](https://docs.poyo.ai/api-manual/task-management/status).
