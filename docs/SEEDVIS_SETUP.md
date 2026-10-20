# Tích hợp Seedvis

Tài liệu API v2.0 đối chiếu ngày 13/10/2026: [Seedvis API](https://seedvis.com/api-docs),
[bản Markdown chính thức](https://seedvis.com/api/v1/developer-api/docs.md?early_access=1).
Adapter mới độc lập với PoYo và Seedance/BytePlus cũ. Chat, bộ nhớ và planner vẫn dùng OpenAI.

## Model đã tích hợp

| Loại | ID cấu hình | Phạm vi trong project |
| --- | --- | --- |
| Video | `Veo-3.1` | Text/image-to-video; clip 8 giây (API v2.0 chỉ nhận `8s`); kịch bản nhiều cảnh với thoại native |
| Video | `seedance_2.5` | Clip đơn từ text/ảnh; 5, 10, 20, 25, 30 giây |
| Video | `seedance_2.0_fast` | Clip đơn từ text/ảnh; 5, 10, 15 giây |
| Video | `Omni-Flash` | Clip đơn từ text/ảnh; thời lượng do nhà cung cấp quyết định |
| Ảnh | `gpt-image-2` | GPT Image 2; tạo ảnh tham chiếu |
| Ảnh | `GEM_PIX_2` | Nano Banana Pro; tạo ảnh tham chiếu |
| Ảnh | `NARWHAL` | Nano Banana 2.1; tạo ảnh tham chiếu |
| Ảnh | `HARBOR_SEAL` | Nano Banana 2 lite; tạo ảnh tham chiếu |

Đây là catalog model được adapter hỗ trợ, **không phải cam kết model đang online**.
Bản tài liệu hiện có ghi chú bảo trì cho một số model. Trạng thái thực tế lấy từ `GET /api/v1/models`
của Seedvis với key đã lưu. Bộ chọn UI đánh dấu **chưa khả dụng** khi model không xuất hiện trong danh sách live.
Không tự động đổi model của bản nháp đã tạo. Endpoint `/api/v1/seedvis/models` của project cũng trả trường
`available`; giá trị `null` nghĩa là chưa kiểm tra được, không phải đã xác nhận model online.

Veo dùng thoại tạo cùng hình như luồng hiện có, nhưng Seedvis không công bố tham số bật/tắt audio
hoặc khóa giọng. Không đảm bảo một chất giọng hay khuôn mặt tuyệt đối giữa các lần sinh.
Không suy diễn khả năng thoại native của Seedance/Omni từ tên model: hiện chúng chỉ dùng ở màn hình
Tạo video để tạo clip trực tiếp. Chat vẫn yêu cầu model hỗ trợ kịch bản có thoại và thời lượng xác định.

## Cấu hình

Giữ nguyên `.env` và `INTERNAL_API_KEY` đang dùng. Tham khảo `.env.seedvis.example`, cập nhật các biến cần thiết:

```dotenv
VIDEO_PROVIDER=seedvis
VIDEO_ENABLE_SEEDVIS=true
ENABLE_PAID_GENERATION=true
SEEDVIS_VIDEO_MODEL=Veo-3.1
IMAGE_PROVIDER=seedvis
SEEDVIS_IMAGE_MODEL=gpt-image-2
```

Nếu `.env` đã có `VIDEO_OUTPUT_ALLOWED_HOST_SUFFIXES`, thêm `,.seedvis.com` vào danh sách,
không xóa các host cũ. Nếu không đặt biến này, code đã có mặc định Seedvis.
Không cần `PUBLIC_BASE_URL` cho ảnh đầu vào của Seedvis: backend đọc ảnh được cấp quyền và gửi JPEG base64.

Khởi động lại bằng môi trường Python đã cài requirements của cả backend và ai-service:

```powershell
python scripts/run_local.py
```

Mở địa chỉ script in ra, đăng nhập, mở **Cài đặt** và nhập **Seedvis API key**.
Key được lưu qua credential vault, không gửi lại cho trình duyệt. Giữ key OpenAI để chat/lập kịch bản.
Thiết lập key hiện có phạm vi toàn server, chưa phải key riêng từng người dùng; chỉ triển khai kiểu workspace tin cậy.

Chọn **Provider video: Seedvis** và model ngay dưới ô nhập. Model ảnh chọn trong Cài đặt.
Các biến `.env` chỉ đặt mặc định; backend lưu lựa chọn riêng theo từng bản nháp.
Nút **Cập nhật model** gọi lại danh sách khả dụng. Không cần khởi động lại mỗi lần chọn model trên UI.
Docker MVP cũng truyền các biến này: `docker compose -f docker-compose.mvp.yml up -d --build`.

### Giữ PoYo video, chỉ đổi ảnh

```dotenv
VIDEO_PROVIDER=poyo
VIDEO_ENABLE_POYO=true
VIDEO_ENABLE_SEEDVIS=true
IMAGE_PROVIDER=seedvis
SEEDVIS_IMAGE_MODEL=gpt-image-2
```

UI có thêm ô key Seedvis riêng cho ảnh, không ghi đè key PoYo. Luồng video PoYo vẫn cần URL ảnh HTTPS
công khai như trước; dùng ảnh Seedvis không tự loại bỏ yêu cầu này của PoYo.

## Giới hạn có chủ ý

- Mỗi yêu cầu sinh một video hoặc một ảnh, không tự tăng số lượng hay tự thử model khác.
- Seedvis cho tối đa 10 ảnh ở một số mode; hợp đồng hiện tại của project giới hạn 3 ảnh.
- Veo hỗ trợ ảnh đầu/cuối và bộ ảnh tham chiếu. Bộ 3 ảnh chỉ hướng dẫn ngoại hình/bối cảnh,
  không đảm bảo frame cuối của cảnh trước trở thành frame đầu chính xác của cảnh sau.
- Ảnh đưa Seedance là ảnh tham chiếu, không phải lệnh cố định frame đầu. Seedvis cảnh báo hạn chế
  ảnh người thật trên Seedance; không tự lách bộ lọc hoặc đổi model khi bị từ chối.
- Chưa tích hợp image editing và video-to-video của Omni vào UI; không có video continuation được xác minh.
- Omni không gửi trường duration và UI không hứa một số giây cụ thể.
- Không thay thế thoại native bằng TTS khi nhà cung cấp thiếu khả năng audio.

## Xử lý tác vụ và lỗi

- Gửi `Idempotency-Key` ổn định theo yêu cầu; `queued`/`processing` là đã nhận tác vụ, chỉ polling tiếp.
- Lưu `next.url` và dùng đúng URL/delay do Seedvis trả về, kể cả khi queued không có `wait=0`.
  URL được kiểm tra cùng origin và đúng ID tác vụ trước khi gửi key; thông tin polling lưu trong database.
- Lỗi mạng hoặc 429/5xx trên Seedvis được thử lại có giới hạn bằng cùng `Idempotency-Key` và payload,
  với backoff và `Retry-After`. Worker khôi phục submit Seedvis sau restart cũng giữ nguyên yêu cầu.
  Phản hồi sai ảnh/mode hoặc tình huống hủy khi chưa biết task ID vẫn cần kiểm tra thủ công.
- Lỗi `seedvis_not_started` có nút thử lại với xác nhận phí. Vì Seedvis xác nhận tác vụ chưa chạy,
  lần thử lại dùng request ID mới nhưng giữ provider/model và nội dung; không tự tạo tác vụ thay thế.
- Ảnh chờ quá thời gian HTTP trả `seedvis_image_pending_retry_same_draft`; thử lại cùng bản nháp dùng lại
  idempotency key. Không bấm tạo bản nháp/tạo ảnh mới liên tục, đó là yêu cầu tính phí mới.
- Chỉ hủy được tác vụ còn queued; đang processing sẽ tiếp tục polling, có thể vẫn tính phí.
- URL output giữ nguyên query chữ ký và được tải về storage local. Không chuyển API key sang CDN.
- `seedvis_invalid_api_key`/HTTP 401: kiểm tra key; HTTP 402: kiểm tra credit; bảo trì/429: chờ rồi thử lại.

## Kiểm thử

Unit/integration test dùng `httpx.MockTransport`, database tạm và key giả; không gọi API trả phí.
Chỉ sau khi kiểm tra model đang khả dụng mới duyệt một clip ngắn trong UI để kiểm chứng tài khoản thực tế.
Chất lượng ảnh/video/giọng và billing chỉ có thể xác nhận bằng lần chạy thật có sự đồng ý của người dùng.

Kết quả kiểm tra tích hợp ban đầu ngày 12/10/2026:

- AI service: 283 test đạt, gồm 34 test Seedvis mới.
- Backend: 224 test đạt (không chạy `test_video_worker.py` vì môi trường thiếu Celery);
  12 test trọng điểm chạy lại đạt, gồm chuỗi 3 cảnh Seedvis được thêm sau lượt full test.
- Frontend: TypeScript, production build và kiểm tra Playwright với API giả lập đạt.
  Đã kiểm tra desktop, mobile 390px/320px, key riêng cho ảnh và Omni thời lượng tự động.
- Chưa kiểm thử generation trả phí hay triển khai Docker thực tế. `.env` thật không bị thay đổi.

Các điểm mở rộng chính:

- `ai-service/app/providers/seedvis_models.py`: catalog và khả năng model.
- `ai-service/app/providers/seedvis_compiler.py`: hợp đồng request theo từng model/mode.
- `ai-service/app/providers/seedvis.py`: HTTP, idempotency, trạng thái, hủy tác vụ.
- `ai-service/app/pipelines/seedvis_images.py`: sinh/poll/tải ảnh tham chiếu.
- `backend/app/services/video_provider_capabilities.py`: fallback offline, phải đồng bộ catalog AI service.

## Cập nhật API v2.0 ngày 13/10/2026

- Tải danh sách model live khi đã có key; cache 60 giây theo credential, có nút cập nhật cưỡng bức.
  Lỗi tải danh sách được báo riêng, không giả định mọi model đã offline.
- Đối chiếu `mode` và số lượng `references` cho video và ảnh có lifecycle envelope.
  Phản hồi Images đồng bộ chuẩn OpenAI vẫn được hỗ trợ.
- Kiểm tra task ID cả ở lượt polling ảnh hoàn thành; không tải nhầm ảnh của tác vụ khác.
- Phân biệt `not_started`, lỗi generation thông thường và hủy. Lỗi chưa rõ đã tính phí không mở retry với key mới.
- Giữ key trong credential vault, chỉ backend/AI service gọi Seedvis; không đưa key vào URL hay trình duyệt.

Phạm vi UI vẫn là tạo ảnh tham chiếu và text/image-to-video. Image editing và Omni video-to-video
chưa có thao tác tương ứng trong workspace; không giả định đây là khả năng nối cảnh hoặc khóa giọng.

Kiểm tra cập nhật: 297 test AI service và 243 test backend qua; thêm 36 test trọng điểm chạy lại qua
sau điều chỉnh lịch polling và retry với chế độ giọng nói. Không chạy test Celery vì môi trường chưa cài Celery.
Playwright qua 5 cấu hình desktop/mobile và 2 trường hợp retry clip; TypeScript/lint không có lỗi.
Build trong bản sao tạm biên dịch và tạo trang thành công; đóng gói standalone của bản sao có cảnh báo
quyền symlink trên Windows do dùng junction `node_modules`. Chưa kiểm thử generation có phí.
