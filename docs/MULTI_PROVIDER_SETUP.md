# Sử dụng nhiều provider trên VO3 Studio

## Cách dùng trên giao diện

1. Khởi động lại backend và AI service sau khi cập nhật code. Frontend phát triển tự tải lại thay đổi.
2. Mở biểu tượng **Cài đặt** trên workspace. Có ba ô key riêng: **PoYo API key**, **Seedvis API key**, **OpenAI API key**. Có thể lưu key PoYo và Seedvis cùng lúc.
3. Mở **Model & thiết lập** trong vùng yêu cầu để chọn **Provider video** và **Model video**.
4. Trong Cài đặt, chọn **Provider ảnh** và **Model ảnh** cho ảnh tham chiếu của bản nháp mới.
5. Nhập yêu cầu, xem bản nháp, duyệt và xác nhận phí trước khi tạo video.

Ví dụ: dùng OpenAI để lập kịch bản, Seedvis Nano Banana Pro để tạo ảnh tham chiếu, rồi PoYo Veo để tạo video. Cũng có thể dùng Seedvis cho cả ảnh và video.

**Đã lưu key** chỉ xác nhận máy chủ đang có key, không xác nhận key còn quota. Danh sách model lấy từ các adapter đã tích hợp; riêng Seedvis còn đối chiếu API `/models` bằng key đã lưu. Model không xuất hiện trong danh sách live được đánh dấu **chưa khả dụng**. Nút **Cập nhật model** tải lại trạng thái; không tự đổi model của bản nháp đã duyệt.

## Provider và model hiện có

| Vai trò | Provider | Model/lựa chọn |
| --- | --- | --- |
| Lập kịch bản và chat | OpenAI | Model `OPENAI_PLANNER_MODEL` đã cấu hình trên máy chủ |
| Video | PoYo | Các model Veo trong adapter PoYo, gồm fast và quality |
| Video | Seedvis | `Veo-3.1`, `seedance_2.5`, `seedance_2.0_fast`, `Omni-Flash` |
| Ảnh tham chiếu | OpenAI | Model `OPENAI_IMAGE_MODEL` đã cấu hình trên máy chủ |
| Ảnh tham chiếu | Seedvis | `gpt-image-2`, `GEM_PIX_2`, `NARWHAL`, `HARBOR_SEAL` |
| Thử nghiệm | Demo | Hiện khi bật `VIDEO_ENABLE_FAKE=true` |
| Video | Seedance trực tiếp | Hiện khi máy chủ có `SEEDANCE_API_KEY`; giữ cấu hình tích hợp cũ |

Kịch bản nhiều cảnh có thoại chỉ bật khi adapter xác nhận hỗ trợ native audio và thời lượng cố định. Các model Seedvis còn lại dùng **Clip trực tiếp**; Omni Flash hiển thị thời lượng **Tự động**. Tỷ lệ và thời lượng cập nhật theo model đang chọn.

## Cấu hình máy chủ

```dotenv
ENABLE_PAID_GENERATION=true
INTERNAL_API_KEY=<giu-gia-tri-hien-co>
```

`ENABLE_PAID_GENERATION` vẫn là điều kiện để dùng provider video có phí. Giữ `INTERNAL_API_KEY` ổn định để giải mã các key đã lưu. Không cần đổi `VIDEO_PROVIDER` hoặc bật/tắt `VIDEO_ENABLE_POYO` / `VIDEO_ENABLE_SEEDVIS` mỗi lần chuyển provider trên UI: hai adapter này được đăng ký khi AI service khởi động.

`VIDEO_PROVIDER`, `POYO_MODEL`, `SEEDVIS_VIDEO_MODEL`, `IMAGE_PROVIDER`, `OPENAI_IMAGE_MODEL` và `SEEDVIS_IMAGE_MODEL` tiếp tục đặt lựa chọn mặc định. Lựa chọn UI được lưu theo tài khoản trong trình duyệt; localStorage chỉ chứa tên provider/model, không chứa API key.

PoYo cần `PUBLIC_BASE_URL` là HTTPS công khai trỏ tới backend khi dùng ảnh đầu vào hoặc nối cảnh bằng frame. Xem [SCENE_CONTINUITY_SETUP.md](SCENE_CONTINUITY_SETUP.md). Seedvis dùng ảnh được truyền từ backend nên luồng này không cần tunnel HTTPS để tải ảnh đầu vào.

Key được mã hóa trong vault của AI service và hiện dùng chung cho bản cài đặt. Đây chưa phải cơ chế key riêng cho từng người dùng.

## Nhiều key Seedvis

Trong **trang admin (`/admin`) → API key** có thể thêm nhiều key, mỗi key kèm số luồng tối đa (mặc định 4, tối đa 20). Key cũ đã lưu tự trở thành key đầu tiên, không cần nhập lại; key đầu tiên còn dùng để đọc danh sách model và tạo ảnh tham chiếu.

- Trước khi gửi một cảnh, worker chọn key còn luồng trống (ít tải nhất theo tỉ lệ). Hết luồng ở mọi key thì cảnh ở trạng thái **Đang xếp hàng** và tự gửi khi có luồng; thời gian chờ không tính vào giới hạn 15 phút của tác vụ.
- Key được lưu vào tác vụ (`result.credential`) ngay trước khi gửi. Kiểm tra trạng thái, hủy và gửi lại sau lỗi mạng luôn dùng đúng key đó, vì tác vụ và Idempotency-Key thuộc tài khoản đã tạo ra chúng.
- Các cảnh trong một video có thể chạy trên key khác nhau vì cảnh Seedvis chỉ nhận ảnh tham chiếu, không tham chiếu mã tác vụ của cảnh trước.
- Không xóa được key đang chạy video. Tác vụ gửi trước khi có tính năng này được tính vào key đầu tiên.
- Kiểm tra điều khoản của Seedvis trước khi dùng nhiều tài khoản để tăng số luồng.

## Giữ đúng provider khi chạy tác vụ

Mỗi bản nháp mới lưu `render_target` gồm `provider`, `video_model`, `image_provider`, `image_model` trong database. Tạo ảnh, tạo cảnh, thử lại và worker chạy theo lựa chọn đã lưu. Chỉnh sửa kịch bản trên UI giữ lựa chọn của bản nháp đó. Đổi bộ chọn trong ô nhập áp dụng cho bản nháp mới.

Bản nháp cũ chưa có `render_target` tiếp tục dùng cấu hình mặc định trước đây. Không cần đổi schema database hoặc xóa dữ liệu hiện có.

## Thêm provider mới trong code

- Thêm adapter theo `VideoProviderAdapter`, đăng ký trong `ProviderRegistry` và khai báo model/capabilities trong catalog.
- Nếu provider mới hỗ trợ nhiều model, thêm cách tạo adapter theo model trong `ProviderRegistry.get`, giữ compiler riêng cho từng request.
- Bổ sung provider vào allowlist backend, kiểu dữ liệu frontend và luồng quản lý credential khi cần.
- Kiểm tra payload, duration, reference và audio theo hợp đồng API của provider; chỉ bật các khả năng đã xác minh.

Không tự động chuyển sang provider khác khi lỗi, vì việc đó có thể đổi chất lượng, giọng hoặc phát sinh phí ngoài lựa chọn đã duyệt.

## Kiểm tra khi không thấy lựa chọn

- Đảm bảo backend và AI service đang chạy code mới; backend cần gọi được `/internal/v1/providers` với đúng internal key.
- Provider hiện **chưa bật**: kiểm tra `ENABLE_PAID_GENERATION` trên backend.
- Provider hiện **chưa có key**: mở Cài đặt, lưu đúng key của provider đó.
- Không tải được danh sách: dùng nút tải lại cạnh thông báo lỗi và kiểm tra kết nối backend/AI service.

Các test tự động dùng database tạm và HTTP giả lập, không tiêu credit tạo ảnh/video.

## Kết quả kiểm tra tích hợp

- Backend: 234 test qua; chưa chạy `test_video_worker.py` vì môi trường hiện tại chưa cài Celery.
- AI service: 289 test qua, gồm key, catalog, compiler riêng cho tác vụ đồng thời, model planner và model ảnh.
- UI: TypeScript và lint không có lỗi; kiểm tra desktop 1440px/1366px và mobile 390px/320px bằng Playwright đều qua. Lint còn bốn cảnh báo tối ưu thẻ ảnh của giao diện hiện có.
- Build trong bản sao tạm đã biên dịch và tạo trang thành công. Đóng gói standalone của bản sao có cảnh báo quyền symlink trên Windows do dùng junction `node_modules`; không dùng bản sao tạm này làm artifact deploy.

Kiểm tra UI không khởi động worker tạo video thật và không thay key/database của bản cài đặt đang dùng.
