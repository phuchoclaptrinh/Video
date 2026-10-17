# Tổng thời lượng video và thời lượng từng cảnh

## Vì sao yêu cầu 20 giây lại chỉ có 8 giây?

Trước đây prompt ưu tiên ít cảnh nhất và chọn một cảnh nếu ý tưởng ngắn. Chat gửi `duration: 8`, nhưng đây là thời lượng của một clip trong spec, không phải tổng thời lượng mong muốn. Planner chưa có ràng buộc độc lập để kiểm tra tổng video theo yêu cầu. Vì vậy một bản nháp chỉ có một cảnh 8 giây vẫn vượt qua validation, dù người dùng đã yêu cầu 20 giây trong nội dung chat.

Không phải cứ clip dài 8 giây thì video hoàn chỉnh cũng chỉ được dài 8 giây.

## Luồng đã sửa

1. Lấy tổng thời lượng từ `target_duration_seconds` nếu API gửi rõ. Nếu không, đọc các dạng như `20s`, `20 giây`, `20 giay`, `20 seconds`, `0,5 phút` trong yêu cầu hiện tại. Phân biệt với tuổi, phần trăm và các câu ghi thời lượng riêng cho mỗi cảnh; không chọn bừa khi có nhiều giá trị mâu thuẫn.
2. Khi lập lại một project, giữ tổng thời lượng của bản trước nếu yêu cầu mới không nêu thời lượng. Yêu cầu mới có thời lượng sẽ thay thế giá trị cũ.
3. Tính một lịch gồm tối đa 5 clip từ các duration mà adapter công bố. Ưu tiên tổng đúng yêu cầu; nếu không đạt chính xác, lấy tổng hỗ trợ gần nhất không ngắn hơn yêu cầu, rồi giảm số clip cần tạo.
4. Gửi số cảnh và tổng thời lượng bắt buộc vào system prompt. LLM vẫn viết nội dung, diễn biến và lời thoại liên tục cho từng cảnh, không nhân bản một cảnh ngắn để đủ thời gian.
5. Kiểm tra số cảnh, tổng thời lượng và duration từng clip. Nếu LLM trả thiếu thời lượng, cho sửa toàn bản nháp đúng một lần; không lưu bản 8 giây như thể đã đáp ứng yêu cầu 20 giây. Các kiểm tra partition lời thoại vẫn được giữ.
6. Lưu `requested_duration_seconds` trong story plan; `duration_target_seconds` vẫn là tổng duration các clip đã lập. Chat hiển thị cả thời lượng yêu cầu và thời lượng dự kiến.

## Ví dụ

Các lựa chọn dưới đây dựa trên contract của adapter hiện có trong codebase, không tự đổi model hoặc cấu hình tài khoản.

| Yêu cầu | Duration mỗi clip mà adapter hỗ trợ | Lịch hợp lệ | Tổng dự kiến |
| --- | --- | --- | --- |
| 20 giây | 8 | 8 + 8 + 8 | 24 giây |
| 20 giây | 4, 6, 8 | 8 + 6 + 6 | 20 giây |
| 32 giây | 8 | 8 + 8 + 8 + 8 | 32 giây |
| 60 giây | 8, tối đa 5 cảnh | Không khả thi | Báo lỗi trước khi gọi LLM |

Nếu muốn đúng 20 giây với adapter chỉ hỗ trợ clip 8 giây, cần một quy trình dựng/cắt an toàn theo lời thoại đo được, hoặc đổi sang adapter có duration ngắn hơn. Đợt sửa này không tự cắt mất 4 giây cuối hay tăng tốc giọng để ép đủ 20 giây. Output thực sau kiểm tra và loại khoảng im lặng có thể ngắn hơn timeline dự kiến; chỉ video đã render mới có thời lượng đo được.

API phân biệt rõ hai trường:

```json
{
  "prompt": "Tạo video KOC nữ Việt Nam review sách dài 20 giây",
  "duration": 8,
  "target_duration_seconds": 20,
  "require_planner": true,
  "ratio": "16:9"
}
```

Trong chat chỉ cần nhập yêu cầu có `20 giây`; không phải sửa duration một clip thành 20. Nếu không yêu cầu tổng thời lượng, planner vẫn có thể chọn một cảnh cho ý tưởng ngắn. API có tổng thời lượng rõ nhưng chưa cấu hình planner sẽ báo cần cấu hình, không âm thầm dùng template một cảnh.

## File liên quan

- `ai-service/app/pipelines/story_duration.py`: đọc tổng thời lượng và tính lịch clip.
- `ai-service/app/prompts/video_story_planner.md`: quy tắc viết kịch bản.
- `ai-service/app/pipelines/story_planner.py`: kiểm tra timeline, tự sửa duration và giữ kiểm tra lời thoại.
- `ai-service/app/pipelines/video_api.py`, `backend/app/main.py`: contract `target_duration_seconds`.
- `backend/app/services/platform_planning.py`: forward yêu cầu, lưu bản nháp và giữ thời lượng khi replan.
- `frontend/src/types/video-event.ts`, `frontend/src/components/chat/VideoEventCard.tsx`: metadata thời lượng trên bản nháp.

## Kiểm chứng

AI service: **156 test passed**; backend: **11 test liên quan passed**; TypeScript, Ruff trên các file Python chỉnh sửa, compile và `git diff --check` đều pass. Provider/LLM được mock, không gọi dịch vụ tạo ảnh hoặc video trả phí. Có test cho yêu cầu 20 giây, lịch 4/6/8, giới hạn 5 cảnh, tự sửa một lần, giữ context khi replan, lỗi mạng trong bước sửa và bảo toàn bài nói tổng thể.

AI service và backend cần được khởi động lại để nạp code. Refresh trang chat sau khi frontend nạp thay đổi. Bản nháp/video đã tạo trước đó không bị sửa hoặc tạo lại tự động.
