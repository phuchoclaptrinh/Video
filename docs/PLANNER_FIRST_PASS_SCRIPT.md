# Lập kịch bản tự nhiên và prompt Veo rõ ràng

Cập nhật ngày 09/10/2026. Tài liệu này mô tả luồng **native Veo audio** hiện hành; thay thế các công thức budget, ép nối câu và tự sửa timing trong tài liệu cũ.

## Nguyên tắc hiện tại

- **Không ép 19–23 âm tiết**, không có quota từ/âm tiết theo thời lượng. Schema chỉ kiểm tra định dạng lời nói và giới hạn kích thước dữ liệu.
- Lập nội dung từ nhu cầu người xem: mở đầu có điểm thu hút, các cảnh phát triển ý, kết thúc giải quyết câu hỏi hoặc đưa ra lời mời phù hợp.
- Mỗi cảnh thêm một chi tiết có ích. Không thêm lời rỗng để lấp giây, không bịa thông số sản phẩm, lời trích dẫn hoặc trải nghiệm cá nhân.
- Viết câu/vế trọn ý, giữ nguyên cụm từ, tên riêng và số đi cùng đơn vị. Không cắt “chơi game” thành hai cảnh hoặc ép mọi cảnh kết thúc bằng dấu phẩy.
- Thời lượng là bối cảnh biên tập: thoại cần có thể nói tự nhiên trong cảnh, nhưng không bị ép số từ, kéo âm hoặc chạm một mốc giây cứng.
- Chỉ một mô tả giọng chung cho toàn bộ kịch bản. Nhịp nói trong mô tả không phải công thức tính lượng thoại.
- Hình ảnh và lời nói nằm ở các trường riêng. Không nhúng giọng, thoại hoặc timestamp nói vào `video_prompt`, `start_state`, `end_state`.

## Luồng hiện tại

1. Đọc yêu cầu, context, lựa chọn tốc độ nói và khả năng thời lượng của provider.
2. Nếu có tổng thời lượng, phân bổ trước khi gọi LLM. Yêu cầu 20 giây với provider chỉ hỗ trợ 8 giây cần 8+8+8; provider hỗ trợ 4/6/8 giây có thể dùng 8+6+6.
3. Tạo strict JSON schema với các khóa `scene_1`, `scene_2`... và thời lượng từng cảnh. Khi không yêu cầu tổng thời lượng, planner chọn một đến năm cảnh.
4. Gọi LLM để viết kịch bản trong một lượt sinh nội dung. Không có lượt tự sửa lượng thoại sau khi nhận phản hồi.
5. Chuyển keyed scenes thành `clips`, tính tổng thời lượng và nối các đoạn thành `full_voice_script`. Không yêu cầu model viết thêm một bản thoại tổng dễ lệch với các đoạn.
6. Kiểm tra cấu trúc và văn bản nói, chuẩn hóa dấu câu nhẹ. Không cắt từ, thêm nội dung hoặc ghi lại bản nháp đã duyệt.
7. Khi cần đọc ảnh tham chiếu, bước grounding riêng chỉ sửa nội dung có căn cứ từ ảnh; giữ tiến trình câu chuyện, thời lượng và giọng chung. Bước này cũng không có quota hay lượt sửa timing tiếp theo.
8. Backend dựng prompt của cảnh hiện tại, kiểm tra thoại rỗng/placeholder và độ dài prompt trước khi enqueue. Compiler ánh xạ các trường hợp lệ sang payload PoYo.

Structured Outputs hỗ trợ ràng buộc cấu trúc; đúng schema không đồng nghĩa với nội dung hấp dẫn, đúng nghĩa hoặc đọc vừa thời lượng thực tế. Xem [tài liệu OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs).

## Chi tiết kịch bản

| Trường | Trách nhiệm |
| --- | --- |
| `audience`, `core_value` | Người xem, nhu cầu và giá trị video thực sự mang lại |
| `beat`, `purpose` | Ý cụ thể của cảnh và vai trò trong câu chuyện |
| `video_prompt` | Bố cục, nhân vật, hành động quan sát được, môi trường, ánh sáng |
| `start_state`, `end_state` | Tư thế, vị trí đạo cụ và bố cục đầu/cuối |
| `voice_script` | Chỉ lời nói của cảnh này, tiếng Việt có dấu, dấu câu bình thường |
| `voice_bible` | Một mô tả giọng chung, không khóa giọng hoặc giả định có audio cảnh trước |
| `transition_intent` | Cảnh liên tục, chuyển camera hoặc chuyển shot có chủ ý |

Ví dụ ý tưởng tai nghe khi chưa có thông số sản phẩm, không phải mẫu cứng hay cam kết thời gian đọc:

1. Mở: “Đừng chọn tai nghe chỉ vì nhìn đẹp. Điều đáng thử trước tiên là cảm giác khi đeo.”
2. Phát triển: “Hãy để ý phần đệm và lực ôm quanh tai. Bạn cần thoải mái khi sử dụng, không chỉ lúc thử nhanh.”
3. Kết: “Tiếp theo, thử với nhạc hoặc trò chơi bạn hay dùng. Lựa chọn phù hợp bắt đầu từ nhu cầu của chính bạn.”

Nội dung thật phải bám thông tin người dùng cung cấp. Không tự suy ra tính năng chống ồn, độ trễ hoặc thời lượng pin.

## Prompt gửi Veo

Thứ tự duy nhất trong `build_scene_prompt`:

1. Cảnh và thời lượng.
2. Vai trò ảnh tham chiếu/bối cảnh chung.
3. Chỉ dẫn hình ảnh của cảnh, giữ nguyên nội dung; không còn cắt âm thầm ở 400 ký tự.
4. Camera, chuyển cảnh, trạng thái đầu/cuối.
5. Một đoạn continuity.
6. Một mô tả giọng chung, đúng lời thoại cảnh hiện tại, một đoạn audio.

Không thêm `CONTINUE THE PREVIOUS VIDEO`, `VOICE MATCH RULES`, thoại cảnh trước, deadline mở lời 0,2 giây, ép ngữ điệu chưa kết câu hay tốc độ nói thứ hai.

Prompt goal có giới hạn nội bộ 4.000 ký tự. Chỉ bối cảnh chung và trạng thái vật lý được rút ngắn khi cần; không cắt scene action, giọng hoặc thoại. Nếu vẫn quá dài, chặn trước khi enqueue thay vì gửi prompt mất nội dung.

## Vị trí chỉnh sửa

- [Prompt planner](../ai-service/app/prompts/video_story_planner.md): cách dẫn dắt người xem và lập chỉ dẫn hình ảnh.
- [Schema](../ai-service/app/pipelines/planner_schema.py): cấu trúc, thời lượng và định dạng lời nói, không quota.
- [Planner](../ai-service/app/pipelines/story_planner.py): gọi LLM và chuyển đầu ra thành kế hoạch.
- [Kiểm tra thoại](../ai-service/app/pipelines/dialogue_timing.py): kiểm tra văn bản, không sửa bằng LLM.
- [Grounding](../ai-service/app/pipelines/reference_grounding.py): đối chiếu nội dung với ảnh.
- [Ráp prompt cảnh](../backend/app/services/video_continuity.py): hình, chuyển cảnh, giọng và thoại.
- [Compiler PoYo](../ai-service/app/providers/poyo_veo_compiler.py): payload cuối, mode ảnh và audio.
- [Kết quả review](VEO_PROMPT_REVIEW.md): xung đột đã sửa, code đã bỏ và giới hạn còn lại.

Endpoint nội bộ `/internal/v1/planner/fit-dialogue` đã được gỡ vì không còn caller production. `platform_dialogue.py` và các hàm budget/helper không còn được dùng cũng đã bỏ. Nhánh TTS riêng vẫn còn caller nên được giữ; không đưa trở lại luồng native Veo.

## Áp dụng và kiểm chứng

Khởi động lại backend và AI service để nạp code mới, rồi lập bản nháp mới. Không tự chuyển đổi các bản nháp đã lưu. Chỉ restart không sửa được những chỉ dẫn mâu thuẫn đã nằm trong `video_prompt` của bản cũ.

Các test dùng HTTP giả và database tạm, không tiêu credit. Chúng kiểm tra cấu trúc, dữ liệu và nội dung prompt thực tế, không chứng minh chất lượng video hoặc thời lượng giọng ở mọi lần sinh.

Native Veo không có bảo đảm khóa danh tính giọng hoặc khuôn mặt giữa các request. Giữ kiểm tra chất lượng audio/ảnh và bước duyệt bản nháp; đánh giá chất lượng lời thoại cần thêm chạy thử thực tế có kiểm soát.
