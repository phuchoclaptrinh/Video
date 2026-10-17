# Prompt Veo và điều chỉnh thời lượng lời thoại

> Cập nhật 09/10/2026: đã bỏ quota từ/âm tiết, kể cả khoảng 19–23, và gỡ endpoint `fit-dialogue` không còn được gọi. Không có lượt LLM tự sửa timing trong luồng native Veo. Xem [planner hiện hành](PLANNER_FIRST_PASS_SCRIPT.md) và [review prompt](VEO_PROMPT_REVIEW.md); nội dung bên dưới chỉ ghi lại lịch sử.

> Tài liệu này ghi nhận bước chỉnh trước. Các prompt và công thức dưới đây được giữ làm lịch sử, không phải cấu hình hiện tại. Giữ tư thế ổn định khi nói và ở ranh giới cảnh, không thêm chuyển động để lấp thời gian thừa.

## Thay đổi

- Bỏ `VOICE_MATCH_RULES`, câu `CONTINUE THE PREVIOUS VIDEO` và `PREVIOUS DIALOGUE` khỏi prompt gửi Veo.
- Mỗi cảnh nhận một đoạn `VOICE DESCRIPTION (not dialogue)` lấy từ `voice_bible`. Không thêm profile khác bên cạnh, không yêu cầu so khớp với audio chưa được gửi vào model.
- Vẫn giữ ảnh tham chiếu, first frame, nhận diện khuôn mặt, trang phục, đạo cụ và trạng thái chuyển cảnh. Giữ native audio, không thay bằng TTS.
- Thời lượng từng cảnh được ghi rõ trong hướng dẫn thoại. Bắt đầu nói sớm, nói đủ câu và chỉ để một khoảng giữ tư thế ngắn ở cuối.

## Tự điều chỉnh thoại

Budget dùng thời lượng cảnh thực tế và tốc độ trong mô tả giọng, không mặc định mọi cảnh phải 8 giây. Với cấu hình hiện tại:

| Preset | Tốc độ | Budget 8 giây |
| --- | --- | --- |
| podcast | 2,6–3,0 âm tiết/giây | 18–22 âm tiết |
| koc | 3,2–3,6 âm tiết/giây | 23–27 âm tiết |

Ở lần lỗi đã kiểm tra, ba cảnh có lần lượt 24, 12 và 6 âm tiết. Chỉ dẫn gửi LLM chưa đủ: trước đây validator chỉ kiểm tra giới hạn chung 4–50 từ. Luồng mới kiểm tra budget và gọi LLM chỉnh thoại tối đa một lượt khi cần:

1. Sau khi planner hoàn thiện cấu trúc và phân đoạn, kiểm tra từng cảnh. Bổ sung nội dung liên quan cho câu quá ngắn, rút gọn câu quá dài.
2. Sau khi đọc ảnh tham chiếu và sửa kịch bản theo ảnh, kiểm tra lại budget. Không để bước đọc ảnh đưa kịch bản trở lại trạng thái thiếu thoại.
3. Trước khi tạo một cảnh native từ bản nháp đã lưu, kiểm tra thoại sau khi làm sạch ký hiệu. Nếu lệch, gọi `/internal/v1/planner/fit-dialogue` để sửa riêng cảnh đó. Các cảnh lân cận chỉ là dữ liệu cho LLM lập kịch bản, không được gửi làm thoại cho Veo.
4. Giữ nguyên thời lượng, prompt hình ảnh, trạng thái cảnh và mô tả giọng. Cập nhật `full_voice_script`, bản nháp, bản kịch bản và nội dung chat cùng nhau.
5. Không chỉnh lại job đã gửi hoặc đã hoàn thành. Cảnh PoYo lỗi được xác nhận chưa tính phí có thể được sửa khi người dùng thử lại. Nếu bản nháp bị sửa hoặc cảnh được gửi trong lúc chờ LLM, dừng thay vì ghi đè.
6. Nếu LLM vẫn trả thoại sai budget, báo `planner_speech_timing_mismatch` trước khi enqueue video, không lặp tạo video để thử vận may.

Không bịa đặc tính sản phẩm, giá, nội dung sách hay trải nghiệm cá nhân để lấp thời gian. Có thể bổ sung lời mời, phản ứng hoặc giải thích có căn cứ. Không thay tốc độ giọng để ép câu dài vào cảnh.

## Ví dụ cảnh 8 giây

Đây là ví dụ minh họa, không phải kết quả đã gọi API tạo video:

```text
VOICE DESCRIPTION (not dialogue): Vietnamese; adult female voice; Hanoi Northern accent with clear tones and consonants; comfortable mid register, warm clear timbre, balanced resonance, smooth texture, minimal breathiness and low nasality; gentle conversational delivery with short natural clause pauses; pace 2.6-3.0 syllables/s; stable volume, close dry microphone.

Generate picture and native audio together. Spoken Vietnamese line, exactly once: "Mình mời bạn cùng tìm hiểu đôi giày này để xem có phù hợp với nhu cầu của mình không."

Speech timing: this scene lasts 8 seconds. Begin speaking promptly at the pace in the voice description, with short natural clause pauses. Finish the complete line before the scene ends, leaving only a brief relaxed hold. Synchronize lips with native speech.
```

Câu minh họa có 20 âm tiết tính theo khoảng trắng; thời gian đọc ước tính khoảng 6,7–7,7 giây, chưa phải kết quả đo audio. Phần prompt hình ảnh vẫn được builder thêm riêng.

## Vị trí code

- `backend/app/services/video_continuity.py`: dựng prompt Veo.
- `ai-service/app/prompts/video_story_planner.md`: hướng dẫn LLM lập kịch bản và mô tả giọng.
- `ai-service/app/pipelines/dialogue_timing.py`: xác định cảnh lệch budget và tự sửa có giới hạn.
- `ai-service/app/pipelines/story_planner.py`: áp dụng bước sửa vào planner.
- `ai-service/app/pipelines/reference_grounding.py`: áp dụng sau khi đọc ảnh.
- `ai-service/app/pipelines/video_api.py`: endpoint nội bộ `fit-dialogue`, có xác thực.
- `backend/app/services/platform_dialogue.py`: kiểm tra trước khi gửi cảnh, lưu lại thoại đã sửa.
- `backend/app/services/platform_generation.py`: nối bước kiểm tra vào luồng tạo video, giữ tính idempotent.
- `ai-service/app/core/voice_presets.py` và mirror ở backend: cấu hình tốc độ và budget; có test parity.

## Áp dụng và giới hạn

Khởi động lại backend và AI service để nạp code mới. Với bản nháp cũ, khi thử lại cảnh chưa tạo hoặc cảnh lỗi chưa tính phí, bước kiểm tra sẽ tự sửa thoại nếu cần; không cần bấm thêm nút điền thoại. Video và cảnh đã tạo xong không tự thay đổi.

Bước sửa thoại có thể phát sinh một yêu cầu LLM, nhưng không gọi PoYo nếu kết quả chưa đạt. Bản nháp đã vừa budget không gọi thêm LLM ở bước này. Không đổi model, API key hay tunnel và không tự tạo lại video trong quá trình kiểm thử.

Budget là ước tính bằng âm tiết, không phải đo thời gian audio chính xác. Veo vẫn có thể đọc nhanh/chậm khác hướng dẫn, đổi chất giọng hoặc gặp lỗi xử lý audio. Một mô tả giọng chung không phải khóa danh tính giọng và không bảo đảm các cảnh giống giọng tuyệt đối.

## Kiểm chứng

- AI service: 215 test đạt. Backend: 183 test đạt; sau điều chỉnh cuối đã chạy lại 46 test tập trung vào prompt và bước sửa thoại. File test Celery worker chưa chạy vì môi trường chưa cài `celery`.
- Test mock kiểm tra ca 24/12/6 âm tiết, tự bổ sung hoặc rút gọn về budget, không đổi hình ảnh/thời lượng, không sửa cảnh ngoài phạm vi và không gọi thêm LLM khi đã vừa budget.
- Test backend kiểm tra lưu lại bản nháp, phiên bản kịch bản và chat; chặn enqueue khi LLM vẫn trả câu quá ngắn; giữ job cũ khi gọi lại; không ghi đè khi có chỉnh sửa hoặc job được gửi đồng thời.
- Dựng offline từ bản nháp thật, thay riêng câu cảnh 3 bằng ví dụ 20 âm tiết: builder 2.514 ký tự, một mô tả giọng, đủ ba ảnh tham chiếu, không có các khối đã yêu cầu bỏ. Câu minh họa chỉ tồn tại trong bộ nhớ của phép kiểm tra, không ghi vào database thật.
- Ruff và compileall kiểm tra các module đã thay đổi. Không gọi API trả phí trong kiểm thử.
