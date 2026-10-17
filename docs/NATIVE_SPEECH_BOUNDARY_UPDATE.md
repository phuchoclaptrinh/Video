# Lời thoại nối cảnh và lấp đầy clip native Veo

> Cập nhật 09/10/2026: đã bỏ toàn bộ quota từ/âm tiết, kể cả khoảng 19–23. Không ép nối câu, lấp đầy clip hoặc tự sửa timing trong luồng native Veo. Xem [planner hiện hành](PLANNER_FIRST_PASS_SCRIPT.md) và [review prompt](VEO_PROMPT_REVIEW.md). Công thức, endpoint và mẫu bên dưới chỉ là lịch sử triển khai.

## Phạm vi

Đã chỉnh kịch bản, prompt và budget theo yêu cầu. Không sửa `native_trim_window`, `_merge_story_videos`, `silencedetect` hoặc logic trim/merge. Không đổi schema, provider, model hay vendor. Giữ first frame, ảnh nhân vật/bối cảnh, identity lock và transition.

Không gọi OpenAI/PoYo thật, không tạo video trả phí, không sửa database đang dùng và không bật/tắt server. Các prompt bên dưới được tạo offline bằng code thật: chuẩn hóa lời thoại, kiểm tra `BookStoryPlan`, gọi `build_scene_prompt`, rồi compile bằng `PoyoVeoCompiler("veo3.1-fast")`. Ảnh trong request minh họa chỉ dùng URL giả lập.

## Budget mới

```python
speaking_window = duration_seconds - 0.4
minimum = floor(speaking_window * (pace_min + pace_max) / 2)
maximum = floor(speaking_window * pace_max)
# Hai giá trị vẫn được kẹp trong [4, 50].
```

| Preset | Thời lượng | Budget mới |
| --- | --- | --- |
| podcast 2,6–3,0 | 4 giây | 10–10 âm tiết |
| podcast 2,6–3,0 | 6 giây | 15–16 âm tiết |
| podcast 2,6–3,0 | 8 giây | 21–22 âm tiết |
| koc 3,2–3,6 | 8 giây | 25–27 âm tiết |

Planner và bước sửa thoại được yêu cầu viết gần mức max, không dưới min. Budget vẫn dùng tốc độ trong profile thực tế của bản nháp; bảng trên không ghi đè tốc độ người dùng đã chọn. Số âm tiết là ước lượng theo token cách nhau bằng khoảng trắng, không phải thời gian audio đã đo.

## Cách nối thoại

- Video nhiều cảnh: cắt bên trong một câu tại chỗ ngắt vế tự nhiên, không tách tên riêng, cụm từ gắn chặt hay số và đơn vị.
- Mọi cảnh trừ cảnh cuối kết thúc bằng dấu phẩy. Cảnh tiếp theo nối câu đó, không chào lại hoặc lặp từ ở ranh giới. Chỉ cảnh cuối kết thúc bằng dấu chấm, hỏi hoặc than.
- Chuẩn hóa tự động chỉ đổi dấu câu và hoa/thường, không thêm, bớt hay đổi thứ tự từ. Chỉ viết thường các phần mở đầu vế nhận diện được; tên riêng, chữ viết hoa toàn bộ hoặc trường hợp chưa rõ được giữ nguyên.
- Kiểm tra partition của bản trả về gốc vẫn chạy trước chuẩn hóa; lỗi thiếu/lặp từ không bị che đi bằng cách join lại.
- Bước chỉnh budget có thể bổ sung/rút gọn nội dung có căn cứ. Sau bước đó, chuẩn hóa lại ranh giới và join `full_voice_script`.
- Khi chỉnh riêng một cảnh, các cảnh không được phép chỉnh giữ nguyên, kể cả dấu câu; không sửa hồi tố thoại của cảnh đã tạo.

## Diff Native Audio

Trích những thay đổi chính trong `build_scene_prompt`:

```diff
- Begin speaking promptly at the pace in the voice description, with short natural clause pauses.
- Finish the complete line before the scene ends, leaving only a brief relaxed hold.
- Small prop actions only before or after the line.
- Then hold a stable pose; no extra gestures, body swaying or facial morphing.
+ Start speaking immediately, within 0.2 seconds of the first frame.
+ Speak continuously at the global voice pace in the voice description so the line ends about {duration - 0.4:.1f} seconds into the {duration}-second clip; do not finish early and leave silence.
+ Do not delay or interrupt speech for prop actions.
+ Body, hands and props stay stable while speaking; no extra gestures, body swaying or facial morphing.
+ [Cảnh chưa cuối] This line is the first part of a sentence that continues in the next shot. Keep a continuing, unfinished intonation at the end; do not drop the pitch or sound finished, and do not pause after the last word.
+ [Cảnh sau cảnh đầu] This line continues a sentence already in progress from the previous shot; start mid-sentence with continuing intonation, no fresh introduction or pause before the first word.
```

Continuation native giữ thân/tay/đạo cụ ổn định trong lúc vẫn nói. Giá trị `end_state` mặc định cũng đổi tương ứng. Nhánh không tạo native audio vẫn giữ hướng dẫn tư thế cũ, không thêm lời thoại hay timing native.

Vẫn chỉ có một `VOICE DESCRIPTION`, không đưa lại `VOICE_MATCH_RULES`, `CONTINUE THE PREVIOUS VIDEO` hay lời thoại cảnh trước vào prompt. Profile và lời thoại không bị cắt để giảm độ dài; chỉ context bổ trợ được rút gọn như trước.

## Plan Mẫu Sau Chuẩn Hóa

Ba cảnh 8 giây, preset podcast, mỗi cảnh **22 âm tiết**. Đầu vào minh họa kết thúc cả ba đoạn bằng dấu chấm, đoạn hai bắt đầu “Cho”, đoạn ba bắt đầu “Và”. Đầu ra dưới đây chỉ sửa dấu câu/hoa thường; `voice_words()` của toàn bài không đổi và validator partition pass.

```json
{
  "video_type": "podcast",
  "book_title": "",
  "audience": "Người muốn dành thời gian nhìn lại một ngày",
  "core_value": "Một lời chia sẻ liền mạch về việc dừng lại và lắng nghe chính mình.",
  "visual_continuity": "One adult Vietnamese woman with an oval face, dark brown eyes, straight dark hair and a white blouse, seated in the same reading room. One unbranded book held at chest height; soft daylight, eye-level medium shot, locked camera.",
  "visual_bible": "One adult Vietnamese woman with an oval face, dark brown eyes, straight dark hair and a white blouse, seated in the same reading room. One unbranded book held at chest height; soft daylight, eye-level medium shot, locked camera.",
  "voice_bible": "Vietnamese; adult woman age twenty-five to thirty; Hanoi Northern accent, clear tones and consonants; natural mid pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; warm, gentle, reflective conversation, clear enunciation, short natural clause pauses, no drawn-out vowels or whispering; pace 2.6-3.0 syllables/s; stable volume, close dry microphone.",
  "full_voice_script": "Có những ngày mình thấy mệt mỏi mà chẳng hiểu vì sao, cứ lặng lẽ làm cho xong mọi việc trong ngày, cho đến khi đọc cuốn sách này mình muốn dành thời gian nhìn lại những điều diễn ra trong ngày hôm nay, và lắng nghe chính mình nhiều hơn một chút để hiểu thêm điều gì đang cần được thay đổi trong cuộc sống.",
  "reference_image_prompt": "One adult Vietnamese woman with an oval face, dark brown eyes, straight dark hair and a white blouse, seated in the same reading room. One unbranded book held at chest height; soft daylight, eye-level medium shot, locked camera. Photorealistic natural skin texture and believable hands; no readable text or recording equipment.",
  "duration_target_seconds": 24,
  "requested_duration_seconds": 24,
  "clips": [
    {
      "index": 1,
      "beat": "Một ngày mệt mỏi",
      "video_prompt": "The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.",
      "voice_script": "Có những ngày mình thấy mệt mỏi mà chẳng hiểu vì sao, cứ lặng lẽ làm cho xong mọi việc trong ngày,",
      "purpose": "Continue the same reflective message without repeating prior words.",
      "start_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "end_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "transition_intent": "continue",
      "duration_seconds": 8,
      "audio_start_ms": null,
      "audio_end_ms": null
    },
    {
      "index": 2,
      "beat": "Dành thời gian nhìn lại",
      "video_prompt": "The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.",
      "voice_script": "cho đến khi đọc cuốn sách này mình muốn dành thời gian nhìn lại những điều diễn ra trong ngày hôm nay,",
      "purpose": "Continue the same reflective message without repeating prior words.",
      "start_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "end_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "transition_intent": "continue",
      "duration_seconds": 8,
      "audio_start_ms": null,
      "audio_end_ms": null
    },
    {
      "index": 3,
      "beat": "Lắng nghe chính mình",
      "video_prompt": "The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.",
      "voice_script": "và lắng nghe chính mình nhiều hơn một chút để hiểu thêm điều gì đang cần được thay đổi trong cuộc sống.",
      "purpose": "Continue the same reflective message without repeating prior words.",
      "start_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "end_state": "The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable while speaking.",
      "transition_intent": "continue",
      "duration_seconds": 8,
      "audio_start_ms": null,
      "audio_end_ms": null
    }
  ]
}
```

## Prompt Mẫu Gửi Veo

Đây là toàn bộ `input.prompt` sau compiler, không phải bản rút gọn. Hai câu cuối về identity/text overlay là phần compiler đã có sẵn, không thay trong task này.

| Cảnh | Vai trò | Độ dài builder | Độ dài sau compiler |
| --- | --- | --- | --- |
| 1 | Mở đầu, câu chưa kết thúc | 2629 | 2703 |
| 2 | Giữa, nối cả trước và sau | 3369 | 3443 |
| 3 | Cuối, nối trước nhưng không dặn nối sang cảnh sau | 3163 | 3237 |

### Cảnh 1

```text
VOICE DESCRIPTION (not dialogue): Vietnamese; adult woman age twenty-five to thirty; Hanoi Northern accent, clear tones and consonants; natural mid pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; warm, gentle, reflective conversation, clear enunciation, short natural clause pauses, no drawn-out vowels or whispering; pace 2.6-3.0 syllables/s; stable volume, close dry microphone.
Generate picture and native audio together. Spoken Vietnamese line, exactly once: "Có những ngày mình thấy mệt mỏi mà chẳng hiểu vì sao, cứ lặng lẽ làm cho xong mọi việc trong ngày,".
Speech timing: this scene lasts 8 seconds. Start speaking immediately, within 0.2 seconds of the first frame. Speak continuously at the global voice pace in the voice description so the line ends about 7.6 seconds into the 8-second clip; do not finish early and leave silence. Synchronize lips with native speech. No extra dialogue, subtitles or repetition. On-screen speakers face the camera, face visible; no reading down or page-turning during speech. Do not delay or interrupt speech for prop actions. Body, hands and props stay stable while speaking; no extra gestures, body swaying or facial morphing.
This line is the first part of a sentence that continues in the next shot. Keep a continuing, unfinished intonation at the end; do not drop the pitch or sound finished, and do not pause after the last word.
The supplied first frame is the exact opening composition. Continue from its visible state.
CONTINUITY LOCK: Preserve the subject identity and important props. Visual bible: The supplied reference image is the authority for appearance, wardrobe, props and location.
Scene action: The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.
Starting state: The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable [...]
Ending state: The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable [...]
Transition intent: continue
Identity lock: keep the same face shape, eyes, nose, skin tone, hair and wardrobe in every frame. Motion budget: one continuous locked shot at the same distance and angle. Use natural blinks and only the requested small action. Keep props visible and stable. No unrequested walking, swaying, prop swapping, zooms, pans, facial morphing or time jumps. For an explicitly planned camera or location transition only, follow the transition intent.
Preserve the subject's identity and appearance.
Do not add text overlays.
```

### Cảnh 2

```text
Continue seamlessly from the supplied opening-state image. Begin in exactly its visible physical state: do not reset pose, hands, prop position, facial orientation, camera, background, lighting or wardrobe. One planned small action only; no strong head turn or large hand gesture near the boundary. Keep body, hands and props in a stable position at the end while speech continues.
VOICE DESCRIPTION (not dialogue): Vietnamese; adult woman age twenty-five to thirty; Hanoi Northern accent, clear tones and consonants; natural mid pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; warm, gentle, reflective conversation, clear enunciation, short natural clause pauses, no drawn-out vowels or whispering; pace 2.6-3.0 syllables/s; stable volume, close dry microphone.
Generate picture and native audio together. Spoken Vietnamese line, exactly once: "cho đến khi đọc cuốn sách này mình muốn dành thời gian nhìn lại những điều diễn ra trong ngày hôm nay,".
Speech timing: this scene lasts 8 seconds. Start speaking immediately, within 0.2 seconds of the first frame. Speak continuously at the global voice pace in the voice description so the line ends about 7.6 seconds into the 8-second clip; do not finish early and leave silence. Synchronize lips with native speech. No extra dialogue, subtitles or repetition. On-screen speakers face the camera, face visible; no reading down or page-turning during speech. Do not delay or interrupt speech for prop actions. Body, hands and props stay stable while speaking; no extra gestures, body swaying or facial morphing.
This line is the first part of a sentence that continues in the next shot. Keep a continuing, unfinished intonation at the end; do not drop the pitch or sound finished, and do not pause after the last word.
This line continues a sentence already in progress from the previous shot; start mid-sentence with continuing intonation, no fresh introduction or pause before the first word.
The character reference defines identity; the style reference defines the set. The final reference shows the previous scene's ending pose and framing (or the shared opening image). Follow that reference for the current state, and the character reference for identity. Start as close to that state as possible.
CONTINUITY LOCK: Preserve the subject identity and important props. Visual bible: The supplied reference image is the authority for appearance, wardrobe, props and location.
Scene action: The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.
Starting state: Use the actual visible pose and composition in the supplied previous frame.
Ending state: The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable [...]
Transition intent: continue
Identity lock: keep the same face shape, eyes, nose, skin tone, hair and wardrobe in every frame. Motion budget: one continuous locked shot at the same distance and angle. Use natural blinks and only the requested small action. Keep props visible and stable. No unrequested walking, swaying, prop swapping, zooms, pans, facial morphing or time jumps. For an explicitly planned camera or location transition only, follow the transition intent.
Preserve the subject's identity and appearance.
Do not add text overlays.
```

### Cảnh 3

```text
Continue seamlessly from the supplied opening-state image. Begin in exactly its visible physical state: do not reset pose, hands, prop position, facial orientation, camera, background, lighting or wardrobe. One planned small action only; no strong head turn or large hand gesture near the boundary. Keep body, hands and props in a stable position at the end while speech continues.
VOICE DESCRIPTION (not dialogue): Vietnamese; adult woman age twenty-five to thirty; Hanoi Northern accent, clear tones and consonants; natural mid pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; warm, gentle, reflective conversation, clear enunciation, short natural clause pauses, no drawn-out vowels or whispering; pace 2.6-3.0 syllables/s; stable volume, close dry microphone.
Generate picture and native audio together. Spoken Vietnamese line, exactly once: "và lắng nghe chính mình nhiều hơn một chút để hiểu thêm điều gì đang cần được thay đổi trong cuộc sống.".
Speech timing: this scene lasts 8 seconds. Start speaking immediately, within 0.2 seconds of the first frame. Speak continuously at the global voice pace in the voice description so the line ends about 7.6 seconds into the 8-second clip; do not finish early and leave silence. Synchronize lips with native speech. No extra dialogue, subtitles or repetition. On-screen speakers face the camera, face visible; no reading down or page-turning during speech. Do not delay or interrupt speech for prop actions. Body, hands and props stay stable while speaking; no extra gestures, body swaying or facial morphing.
This line continues a sentence already in progress from the previous shot; start mid-sentence with continuing intonation, no fresh introduction or pause before the first word.
The character reference defines identity; the style reference defines the set. The final reference shows the previous scene's ending pose and framing (or the shared opening image). Follow that reference for the current state, and the character reference for identity. Start as close to that state as possible.
CONTINUITY LOCK: Preserve the subject identity and important props. Visual bible: The supplied reference image is the authority for appearance, wardrobe, props and location.
Scene action: The same presenter faces the camera holding the same book at chest height. Body, hands and props stay stable while speaking through the end; only natural blinks and subtle mouth movement.
Starting state: Use the actual visible pose and composition in the supplied previous frame.
Ending state: The same seated presenter faces the camera, holding the same book at chest height; body and props stay stable [...]
Transition intent: continue
Identity lock: keep the same face shape, eyes, nose, skin tone, hair and wardrobe in every frame. Motion budget: one continuous locked shot at the same distance and angle. Use natural blinks and only the requested small action. Keep props visible and stable. No unrequested walking, swaying, prop swapping, zooms, pans, facial morphing or time jumps. For an explicitly planned camera or location transition only, follow the transition intent.
Preserve the subject's identity and appearance.
Do not add text overlays.
```

## File Đã Sửa

### Code Và Prompt

- [ai-service/app/core/voice_presets.py](../ai-service/app/core/voice_presets.py): Công thức budget mới.
- [backend/app/core/voice_presets.py](../backend/app/core/voice_presets.py): Bản mirror cùng công thức; giữ parity.
- [ai-service/app/prompts/video_story_planner.md](../ai-service/app/prompts/video_story_planner.md): Chia bên trong câu, mục tiêu gần max, giữ tư thế trong lúc nói.
- [ai-service/app/pipelines/dialogue_boundaries.py](../ai-service/app/pipelines/dialogue_boundaries.py): Helper mới chỉ chuẩn hóa dấu câu và chữ đầu đoạn khi nhận diện được phần nối câu.
- [ai-service/app/pipelines/story_planner.py](../ai-service/app/pipelines/story_planner.py): Prompt repair, fallback visual, chuẩn hóa sau parse và kiểm tra lại partition.
- [ai-service/app/pipelines/dialogue_timing.py](../ai-service/app/pipelines/dialogue_timing.py): Luật nối câu cho bước chỉnh budget; chuẩn hóa cả khi không cần gọi LLM, không sửa cảnh ngoài danh sách cho phép.
- [ai-service/app/pipelines/reference_grounding.py](../ai-service/app/pipelines/reference_grounding.py): Luật nối câu sau khi đọc ảnh; đầu ra được chuẩn hóa qua cùng bước chỉnh thoại.
- [backend/app/services/video_continuity.py](../backend/app/services/video_continuity.py): Chỉ dẫn timing/ngữ điệu theo vị trí cảnh và thời lượng thực tế; không cắt profile hay lời thoại.
- [backend/app/services/spoken_text.py](../backend/app/services/spoken_text.py): Giữ dấu phẩy cuối lời thoại, vẫn bỏ dấu phẩy thừa ở đầu.

### Test

- [ai-service/tests/test_dialogue_boundaries.py](../ai-service/tests/test_dialogue_boundaries.py)
- [ai-service/tests/test_dialogue_timing.py](../ai-service/tests/test_dialogue_timing.py)
- [ai-service/tests/test_reference_grounding.py](../ai-service/tests/test_reference_grounding.py)
- [ai-service/tests/test_story_dialogue.py](../ai-service/tests/test_story_dialogue.py)
- [ai-service/tests/test_story_duration.py](../ai-service/tests/test_story_duration.py)
- [ai-service/tests/test_video_execution.py](../ai-service/tests/test_video_execution.py)
- [ai-service/tests/test_voice_presets.py](../ai-service/tests/test_voice_presets.py)
- [backend/tests/test_platform.py](../backend/tests/test_platform.py)
- [backend/tests/test_platform_dialogue.py](../backend/tests/test_platform_dialogue.py)
- [backend/tests/test_spoken_text.py](../backend/tests/test_spoken_text.py)
- [backend/tests/test_video_continuity_strategy.py](../backend/tests/test_video_continuity_strategy.py)

### Tài Liệu

- [NATIVE_SPEECH_BOUNDARY_UPDATE.md](NATIVE_SPEECH_BOUNDARY_UPDATE.md): báo cáo này.
- [VEO_PROMPT_DIALOGUE_TIMING.md](VEO_PROMPT_DIALOGUE_TIMING.md): đánh dấu tài liệu bước trước và liên kết sang budget/chỉ dẫn mới.

## Kiểm Chứng

| Kiểm tra | Kết quả |
| --- | --- |
| AI-service, toàn bộ test | 225 pass, 0 fail |
| Backend, toàn bộ test ngoài file worker | 188 pass, 0 fail |
| Worker Celery | Không thu thập `tests/test_video_worker.py`: môi trường chưa cài `celery` |
| Ruff trên các file Python sửa trong task | Pass |
| Budget AI/backend parity | Pass |
| Chuẩn hóa từ không đổi, partition/schema, tên riêng/acronym | Pass |
| Timing 4/6/8 giây; cảnh giữa/cuối; dấu phẩy; profile 600 ký tự và context dài | Pass |
| Câu nối không bị báo lặp, lặp thật vẫn bị báo lỗi | Pass |
| Plan và 3 prompt mẫu offline | Pass; 22 âm tiết/cảnh; prompt đều dưới 4000 ký tự |
| Video/audio thật sau thay đổi | Chưa chạy; không phát sinh credit |

Lệnh chạy từ từng thư mục:

```powershell
# Trong ai-service:
python -m pytest -q --tb=short

# Trong backend:
python -m pytest -q --ignore=tests/test_video_worker.py --tb=short
```

Đã chạy thêm vòng không sinh coverage với `-o addopts=` trước vòng dùng cấu hình pytest mặc định. Không cần `--basetemp` vì temp của môi trường không bị chặn. Báo cáo coverage XML backend được pytest sinh lại, không phải sửa code thủ công.

Các cảnh báo còn lại là deprecation đã có của pytest-asyncio, Starlette/httpx và `Field(strip_whitespace=...)` trong API hiện tại; không sửa các phần này ngoài phạm vi.

## Rủi Ro Và Cách Thử

- Prompt không phải cam kết thời gian hoặc ngữ điệu của Veo. Model vẫn có thể kết thúc câu sớm, hạ giọng, đổi chất giọng, nói chậm hoặc nhanh hơn mô tả.
- Budget cao hơn làm giảm khả năng đuôi im dài nhưng có thể khiến lời thoại tràn clip nếu model nói chậm; từ cuối có thể bị cắt. Đặc biệt cần kiểm tra budget hẹp 4 giây (10 âm tiết).
- Thay dấu chấm bằng dấu phẩy không tự biến hai câu độc lập thành một câu nối tự nhiên. Luật planner/grounding/fit chịu trách nhiệm nội dung; hậu xử lý chỉ là lớp sửa định dạng.
- Dấu phẩy vẫn có thể được Veo hiểu thành một khoảng nghỉ. Chỉ dẫn unfinished intonation và không nghỉ cuối đoạn là best effort; logic chất lượng/trim/merge hiện hữu giữ nguyên.
- Profile nói “ngắt vế tự nhiên” vẫn cho phép nghỉ ngắn bên trong câu, không yêu cầu đọc phẳng hoặc bỏ mọi khoảng thở.
- Video/job đã tạo và bản nháp đã lưu không bị tự tạo lại hoặc ghi đè. Khởi động lại backend/AI-service nếu không dùng auto-reload và lập **bản nháp mới** để nhận đầy đủ luật chia câu mới. Kiểm tra dấu phẩy ở cảnh chưa cuối, chữ nối câu ở cảnh sau và số âm tiết trước khi duyệt.
- Cần một lần tạo thật có chủ đích để đo leading/trailing silence và nghe chỗ nối; task này chưa có bằng chứng rằng mọi video mới đã hết ngắt tiếng.

