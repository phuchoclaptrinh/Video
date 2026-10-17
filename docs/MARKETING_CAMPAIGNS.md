# Bộ ảnh sản phẩm và chiến dịch marketing

## Cách dùng

1. Khởi động lại backend và AI service, tải lại frontend tại `http://127.0.0.1:3000`.
2. Chọn tối đa **6 ảnh JPEG/PNG/WebP, mỗi ảnh tối đa 8 MB**. Có thể chọn nhiều file, kéo thả hoặc dán ảnh. Nút ngôi sao đặt một ảnh làm ảnh chính; ảnh này đứng đầu bộ ảnh.
3. Nhập yêu cầu: sản phẩm, người mua mục tiêu, giá/ưu đãi nếu có, phong cách, kênh đăng và CTA. Thông tin không có trong ảnh nên được cung cấp bằng chữ, không để AI tự đoán.
4. Chọn **Một video** để lập một kịch bản quảng cáo, hoặc **Chiến dịch** để lập **3, 5 hoặc 10 kịch bản** cho cùng brief.
5. Với chiến dịch, xác nhận phí LLM rồi nhấn **Lập chiến dịch**. Hệ thống lưu tác vụ ngay và hiển thị tiến độ trong bước Kịch bản.
6. Xem chiến lược, nhóm nội dung, góc bán hàng, hook, CTA, caption và ngày đăng gợi ý. Mở từng kịch bản, chỉnh nếu cần, duyệt rồi mới tạo video.

Ví dụ brief:

> Quảng cáo tai nghe trong bộ ảnh cho sinh viên thích nghe nhạc khi học. Lập 5 video TikTok: mở đầu gây tò mò, khám phá thiết kế, cách dùng, câu hỏi khi chọn mua và lời mời tìm hiểu sản phẩm. Giọng nữ miền Bắc, tự nhiên. Chỉ nói những tính năng được cung cấp hoặc đọc rõ trên ảnh. Giá chưa chốt, không tự thêm ưu đãi.

## AI sử dụng ảnh thế nào

- Backend kiểm tra quyền sở hữu **tất cả** ảnh, không chỉ ảnh chính; ảnh của tài khoản khác hoặc bộ ảnh vượt giới hạn bị chặn trước khi gửi LLM.
- Planner nhận cả bộ ảnh theo thứ tự, mỗi ảnh được chuẩn hóa thành JPEG tối đa 1024px. Ảnh đầu tiên là visual anchor; các ảnh sau cung cấp góc sản phẩm, bao bì và chi tiết hỗ trợ.
- Khi có ảnh, planner mặc định viết theo mục tiêu bán hàng: hook cụ thể → nhu cầu → chi tiết/minh họa → CTA. Có thể gửi `planning_goal: general` qua API cho mục đích khác.
- Prompt không ép số âm tiết. Kịch bản phải có lời thoại hoàn chỉnh và giữ chính sách giọng/thời lượng hiện có.
- AI không được tự bịa giá, ưu đãi, số lượng tồn, chứng nhận, công dụng y tế, testimonial hay trải nghiệm cá nhân. Những thông tin thiếu được ghi trong phần cần xác nhận; không tự lấp bằng lời quảng cáo sai sự thật.
- Tất cả ảnh được **planner** đọc. Đây không phải cam kết Veo/PoYo nhận trực tiếp 6 ảnh: pipeline video hiện dùng **ảnh chính** làm first frame/anchor và dùng mô tả đã lập từ bộ ảnh để phát triển các cảnh. Giới hạn ảnh của từng adapter vẫn được giữ.
- Số ảnh đầu vào tối đa 6 khác với giới hạn tối đa **5 cảnh trong mỗi video**.

Định dạng nhiều ảnh theo [tài liệu OpenAI chính thức](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat). Giữ nguyên model `OPENAI_PLANNER_MODEL`; không tự đổi model hay key.

## Lập kịch bản hàng loạt

Chiến dịch có hai giai đoạn:

1. Một yêu cầu vision lập chiến lược và đúng số creative brief được chọn. Hook và góc bán hàng trùng nhau bị từ chối, không tự nhân bản để đủ số lượng.
2. Mỗi brief được chuyển qua story planner hiện có, cùng bộ ảnh, context hội thoại, ghi nhớ, thời lượng và provider/model đã lưu. Kết quả là các video project và bản nháp độc lập trong cùng lịch sử dự án.

Mỗi ý tưởng có góc nội dung, hook, CTA, caption, định hướng hình ảnh, kênh và giai đoạn phễu: nhận diện, cân nhắc hoặc chuyển đổi. Ngày đăng là **gợi ý tương đối**, chưa phải tác vụ tự đăng lên mạng xã hội.

Tác vụ chạy nền từng bước, lưu tiến độ trong DB. Có thể đóng/tải lại trang rồi mở lịch sử để tiếp tục xem. Nút tải kế hoạch xuất JSON gồm chiến lược và các bản nháp đã tạo.

## Chi phí và thử lại

- Chiến dịch gọi LLM để lập chiến lược và từng kịch bản; bộ ảnh được gửi trong các bước lập kịch bản. Nhiều ảnh/nhiều kịch bản làm tăng usage. Chưa tự động gọi API ảnh hoặc video trong bước lập chiến dịch.
- UI gửi `request_id` ổn định khi thử lại cùng nội dung sau lỗi kết nối. Backend trả về tác vụ đã có; dùng cùng mã cho nội dung khác bị trả 409.
- Khi một kịch bản lỗi, các kịch bản thành công vẫn được giữ. Thử lại cần xác nhận phí LLM và chỉ xử lý phần thiếu.
- Dừng chiến dịch không thu hồi phí của yêu cầu LLM đang chạy; hệ thống giữ kết quả đã xong và không bắt đầu các phần tiếp theo.
- Tác vụ bị gián đoạn không được tự gọi lại LLM. Sau thời hạn nhận diện gián đoạn, người dùng xác nhận thử lại; nếu draft đã được lưu, worker khôi phục draft thay vì lập lại.
- Tạo video vẫn cần duyệt riêng từng bản nháp và xác nhận phí provider. Chưa có chế độ âm thầm tạo hàng loạt video có phí.
- Prompt hướng đến khả năng giữ người xem và bán hàng, **không bảo đảm viral/doanh thu**. Giọng và nhân vật của native video vẫn là best-effort.

## Backend và DB

Thêm bảng `platform_marketing_campaigns` qua metadata `create_all` khi backend khởi động. Không xóa dữ liệu hoặc thay đổi các bảng cũ. Mỗi chiến dịch gắn với tài khoản và cuộc trò chuyện, lưu input asset IDs, render target, chiến lược, các item và tiến độ; không lưu base64 ảnh trong DB/chat.

Worker chiến dịch hiện là thread riêng trong `Platform.start()`, tách khỏi worker video để lời gọi planner dài không chặn tiến độ tạo/ghép video. Luồng local cần backend chạy với `LOCAL_VIDEO_WORKER=true` và bật worker. Chưa có task chiến dịch trong Celery; cấu hình chỉ dùng Celery và tắt local worker sẽ không xử lý hàng đợi này.

| API | Chức năng |
| --- | --- |
| `POST /api/v1/plans` | Một kịch bản với `asset_ids` tối đa 6; `asset_id` cũ vẫn tương thích |
| `POST /api/v1/marketing-campaigns` | Xếp hàng chiến dịch, cần `confirm_planning_costs: true` |
| `GET /api/v1/marketing-campaigns?conversation_id=...` | Chiến dịch thuộc dự án/tài khoản |
| `GET /api/v1/marketing-campaigns/{id}` | Tiến độ và bản nháp |
| `POST /api/v1/marketing-campaigns/{id}/cancel` | Dừng các phần còn lại |
| `POST /api/v1/marketing-campaigns/{id}/retry` | Thử lại phần thiếu sau xác nhận phí |
| `POST /internal/v1/planner/campaign` | AI service lập strategy/creative brief, có internal auth |

## Các file chính

- `frontend/src/components/workspace/WorkspaceImages.tsx`: chọn nhiều ảnh, đổi ảnh chính và bỏ từng ảnh.
- `CampaignPanel.tsx`, `frontend/src/hooks/use-marketing-campaigns.ts`: chiến lược, tiến độ, retry/cancel và xuất JSON.
- `frontend/src/hooks/use-video-workspace.ts`: upload có cache, gửi cả bộ ảnh, xếp hàng chiến dịch và ghép các bản nháp vào lịch sử.
- `backend/app/services/platform_campaigns.py`: lưu/claim tác vụ, xử lý từng bước, idempotency và khôi phục.
- `backend/app/services/platform_planning.py`: gửi bộ ảnh, mục tiêu bán hàng và context cho story planner.
- `ai-service/app/pipelines/marketing_planner.py`: schema, đếm creative brief, kiểm tra góc/hook khác nhau.
- `ai-service/app/pipelines/visual_input.py`: input vision dùng chung và định hướng quảng cáo.
- `ai-service/app/prompts/marketing_campaign_planner.md`: prompt chiến lược marketing.

## Kiểm tra

Test dùng DB tạm và HTTP giả lập, không gọi provider có phí: giới hạn ảnh, quyền sở hữu, giữ thứ tự/ảnh chính, đọc đủ ảnh, đúng số/góc kịch bản, xác nhận phí, dữ liệu sau reload, phần lỗi, idempotency, khôi phục draft và giữ campaign context khi sửa.

Kiểm tra UI ở 1440, 1920, 768, 390 và 320px: sáu ảnh, chặn ảnh thứ bảy, lập chiến dịch, retry phần thiếu, tải JSON, mở/duyệt đúng bản nháp và xem video. Chất lượng marketing/video live chưa được đánh giá bằng các test giả lập này.
