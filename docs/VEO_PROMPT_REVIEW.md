# Review pipeline planner và prompt Veo

Ngày: 09/10/2026. Phạm vi: lập kịch bản, grounding theo ảnh, dựng prompt cảnh, compiler/adapter PoYo, continuity và kiểm tra native audio. Không phải audit toàn bộ bảo mật, frontend hoặc hạ tầng.

## Phát hiện và xử lý

### 1. Ý nghĩa ảnh đầu/cuối bị mất khi compile ba ảnh

Mức: cao với request có bộ ảnh `CHARACTER_REFERENCE + FIRST_FRAME + LAST_FRAME`.

Compiler trước đây chấp nhận bộ này nhưng gửi `generation_type=reference`; khi đó ảnh thứ ba là ảnh tham chiếu, không còn là ràng buộc khung hình kết thúc. Đây là lỗi hợp đồng payload, không thể khắc phục chỉ bằng câu “giữ frame cuối” trong prompt.

Đã chặn tổ hợp đó trước khi submit, và giữ ánh xạ:

| Input được hỗ trợ | Payload |
| --- | --- |
| Một `FIRST_FRAME` | Một ảnh mở đầu, không gắn mode ba ảnh |
| `FIRST_FRAME + LAST_FRAME` | Hai URL đúng thứ tự, `generation_type=frame` |
| `CHARACTER_REFERENCE + STYLE_REFERENCE + FIRST_FRAME` | Ba ảnh ở `generation_type=reference`; ảnh cuối chỉ hướng dẫn bố cục mở đầu, không cam kết frame chính xác |
| Model không hỗ trợ ảnh | Từ chối capability, không coi image-enabled là đủ |

Xem [compiler](../ai-service/app/providers/poyo_veo_compiler.py). Version compiler đã tăng lên `poyo_veo_compiler_v3`. Contract ảnh được đối chiếu với [PoYo Veo 3.1](https://docs.poyo.ai/api-manual/video-series/veo-3-1) và [bản official](https://docs.poyo.ai/api-manual/video-series/veo-3-1-official).

### 2. Chuyển shot nhưng prompt vẫn giữ khung hình cũ

Mức: cao khi kịch bản yêu cầu chuyển bố cục/góc quay.

Trước đây prompt cố định tư thế mở đầu theo frame trước và giữ bối cảnh/ánh sáng ngay cả khi cảnh có chuyển shot có chủ ý. Đã tách rõ:

- `continue`: giữ khung hình, dùng trạng thái frame trước, không tự thêm camera move.
- `intentional_camera_transition`: bắt đầu từ frame trước, cho phép đúng chuyển động camera/thay đổi hình đã lập kế hoạch; không khóa toàn cảnh.
- `hard_cut`, `match_cut`, `fade_to_black`: dùng trạng thái mở đầu mới; ảnh trước chỉ là context. Nếu dùng reference mode, không gọi ảnh đó là frame mở đầu bắt buộc.
- Cut/fade là việc của bước ghép. Prompt yêu cầu shot hiển thị đầy đủ, không bắt Veo tự fade thêm lần nữa.
- Cảnh đầu không bị mô tả nhầm là có “previous shot”.
- Nếu đang dùng chính frame cuối cảnh trước làm frame đầu bắt buộc, nhưng kế hoạch lại yêu cầu cut sang bố cục khác, chặn rõ `scene_cut_requires_new_opening_image_or_reference_mode` trước enqueue. Không âm thầm gửi hai lệnh trái nhau.

Giới hạn: với model/duration chỉ hỗ trợ first-frame continuation, một cut cần ảnh mở đầu mới hoặc mode reference phù hợp. Chưa tự sinh ảnh mới cho cut, chưa tự đổi model hoặc tăng chi phí. Luồng mặc định vẫn ưu tiên `continue`.

Code: [video_continuity.py](../backend/app/services/video_continuity.py).

### 3. Prompt và bước trim audio hiểu transition cũ khác nhau

Mức: vừa, ảnh hưởng độ mượt ở điểm nối.

Prompt coi transition thiếu/không nhận diện là `continue`, nhưng audio QC trước đây so sánh raw string. Bản nháp cũ có thể giữ tư thế đầu trong prompt rồi lại bị cắt phần mở đầu im lặng.

Đã dùng chung `scene_transition` cho prompt, log và native audio QC. Cảnh liên tục không mất tư thế mở đầu vì trim leading silence. Đổi `NATIVE_REVIEW_PROFILE` thành `continuity-v3` để kết quả QC cache được đánh giá lại khi đi qua review, thay vì tiếp tục dùng quyết định cũ.

Code: [platform_native_audio.py](../backend/app/services/platform_native_audio.py), [platform_generation.py](../backend/app/services/platform_generation.py).

### 4. Đổi ảnh nhưng cache vẫn trỏ tới bản cũ

Mức: vừa. Phát hiện trực tiếp trong lượt chạy lại test.

URL ảnh dùng `mtime_ns`; hai ảnh khác nhau có thể cùng timestamp, nên giao diện tiếp tục dùng URL cũ. Crop nhân vật/bối cảnh cũng có thể còn từ ảnh trước.

Đã dùng fingerprint nội dung cho version URL và xóa crop cũ sau khi thay thành công ảnh nguồn. Test ép hai ảnh có cùng timestamp để tái hiện ổn định. Không sửa database thật hoặc tự tạo lại ảnh/video.

Code: [platform_projects.py](../backend/app/services/platform_projects.py). Đổi lại, tạo URL hiện cần đọc/hash ảnh nguồn; chưa tối ưu cache fingerprint cho tải lớn.

### 5. Ràng buộc nội dung và hướng dẫn âm thanh quá cứng

Mức: vừa. Đã xử lý cùng đợt cải thiện planner.

- Bỏ quota 19–23, budget min/max và giới hạn 4–50 từ/âm tiết.
- Planner ưu tiên hook có giá trị, tiến trình nội dung, chi tiết có căn cứ và câu trọn ý.
- Không ép mọi cảnh nối giữa câu, đổi dấu kết câu thành dấu phẩy, mở lời trong 0,2 giây hoặc giữ ngữ điệu chưa kết thúc.
- Không khóa giọng hoặc giả định PoYo nhận audio/video cảnh trước.
- Một voice profile chung; `video_prompt` chỉ mô tả hình. Chỉ thoại của cảnh hiện tại đi vào phần spoken line.
- Giữ đầy đủ scene action, bỏ cắt âm thầm 400 ký tự; reject prompt quá lớn thay vì làm mất chi tiết.

Chi tiết: [planner hiện hành](PLANNER_FIRST_PASS_SCRIPT.md). Giới hạn kích thước dữ liệu 500 ký tự thoại và goal 4.000 ký tự vẫn còn, không phải quota âm tiết hoặc cam kết thời gian nói.

## Code đã bỏ, code được giữ

Đã bỏ vì không còn caller production hoặc trùng kiểm tra:

- `backend/app/services/platform_dialogue.py` và mixin kiểm tra native dialogue; guard thực tế nằm trong `build_scene_prompt`.
- Endpoint `/internal/v1/planner/fit-dialogue`, request models và mã lỗi timing cũ chỉ phục vụ nhánh này.
- `speech_budget`, `scene_speech_budget`, `voice_preset_for_profile` và tham số preset/selected_indices không còn dùng trong validator/schema.
- Trường `PoyoVeoCapabilities.duration` cũ; giữ `generation_durations`.
- Test gọi helper/endpoint đã gỡ; thay bằng test đường `Platform.generate`, kiểm tra prompt, snapshot DB và không gọi planner để viết lại.

Được giữ:

- Nhánh TTS/điều chỉnh narration riêng vẫn có caller; không xóa chỉ vì người dùng hiện chọn native Veo.
- Kiểm tra lỗi provider, xác thực API key, trạng thái job, duyệt chi phí, QC và các guard dữ liệu.
- Tương thích dữ liệu bản nháp cũ khi còn được dùng. Không lọc/xóa bừa nội dung prompt cũ bằng regex.
- Việc bỏ `spec.story.shots` mặc định khỏi clip payload đã có sẵn; không coi đó là lỗi mới. Nó ngăn mô tả camera tổng quát bị compiler ghép vào từng cảnh.

Trách nhiệm file hiện tại: planner viết kế hoạch; schema kiểm tra contract; backend dựng một prompt cảnh; compiler ánh xạ provider; QC đánh giá output. Chưa cần thêm một tầng “prompt manager” hoặc module dùng chung mới giữa hai service.

## Kiểm chứng

- AI service: **249 test qua**.
- Backend: **215 test qua**, bỏ `tests/test_video_worker.py` vì môi trường thiếu Celery.
- Ruff trên các file code/test đã sửa: qua.
- Test dùng HTTP giả, key giả và SQLite tạm. Không gọi OpenAI/PoYo thật, không tiêu credit.
- Các warning Pydantic/Starlette và fixture scope của pytest-asyncio vẫn còn; không mở rộng đợt này sang nâng thư viện.
- Payload mẫu dưới đây đi qua backend builder rồi compiler thật, offline. Goal: 2.056 ký tự; prompt cuối: 2130 ký tự. Nội dung mẫu được viết để kiểm tra pipeline, không phải kết quả LLM thực tế.
- Mẫu có một voice profile, đúng scene action/thoại, ba URL giả ở mode reference. Model `veo3.1-fast` không gửi `sound`; bản official mới có điều khiển audio theo contract hiện tại.

## Prompt mẫu cuối cùng

```text
Scene 2: one 8-second shot.
Visual context: Use the character reference for identity and the style reference for the setting. The final reference guides the opening pose, props and framing; image references guide appearance, not the voice or a previous recording.
Scene action: Eye-level medium shot of the same Vietnamese adult presenter at the same desk. She holds the unbranded headphones at chest height and gently angles one ear cushion toward the lens. Soft window light reveals the cushion texture; her face remains unobstructed and the camera stays still.
Camera and transition: Hold the existing camera framing and angle for this continuous shot. Transition intent: continue.
Starting state: Follow the visible pose, prop positions and framing in the supplied previous-frame image.
Ending state: The presenter faces the camera with the ear cushion visible at chest height.
Continuity: maintain subject appearance, wardrobe, props and lighting. Use natural expression and only the planned restrained action; keep the physical state stable at the boundary, without unrequested swaying, prop changes or abrupt pose resets.
VOICE DESCRIPTION (not dialogue): Vietnamese; adult female speaker; Hanoi Northern accent, clear tones and consonants; warm clear mid register, smooth texture, minimal breathiness and low nasality; friendly conversational delivery, natural emphasis and brief breaths; stable volume, close dry microphone; pace 2.6-3.0 syllables/s.
Spoken Vietnamese line, exactly once: "Đừng chỉ nhìn kiểu dáng. Hãy thử độ vừa tai và cảm giác của phần đệm trước khi quyết định.".
Audio: Generate picture and native audio together. Use the global voice pace in the voice description. Deliver the complete line naturally within the shot with brief breaths and normal sentence intonation. Do not rush, stretch vowels, cut off words or add dialogue or repetition. For an on-screen speaker, synchronize lips with native speech and keep the face visible; no reading down or page-turning during speech. Keep speech clearly audible over scene ambience.
Preserve the subject's identity and appearance.
Do not add text overlays.
```

## Giới hạn và áp dụng

- Không có bộ kiểm chứng ngữ nghĩa bảo đảm mọi `video_prompt` do LLM hoặc người dùng viết đều không tự mâu thuẫn. Prompt/schema mới giảm nguyên nhân; bản cũ cần lập lại nếu đã chứa hướng dẫn sai.
- Không có quota không đồng nghĩa với mọi câu đều nói vừa thời lượng. Chưa đo giọng trước khi Veo sinh native audio; cần nghe/xem output thực tế và giữ QC.
- Native Veo không bảo đảm cùng khuôn mặt/giọng tuyệt đối qua các request. Ảnh tham chiếu và voice description là hướng dẫn, không phải khóa danh tính.
- Chưa đánh giá chất lượng bằng API thật hoặc benchmark thời gian sinh video.
- Khởi động lại backend và AI service, rồi lập bản nháp mới để dùng planner mới. Không tự thay bản nháp đã duyệt, không tạo lại video cũ và không tự chạy tác vụ trả phí.
