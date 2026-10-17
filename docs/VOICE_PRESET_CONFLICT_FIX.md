# Báo cáo gỡ xung đột prompt giọng Veo

> Báo cáo mốc triển khai trước. Theo yêu cầu mới, `VOICE_MATCH_RULES`, `CONTINUE THE PREVIOUS VIDEO` và lời thoại cảnh trước đã được bỏ khỏi prompt Veo. Luồng hiện tại dùng một mô tả giọng và tự điều chỉnh thoại theo thời lượng; xem [VEO_PROMPT_DIALOGUE_TIMING.md](VEO_PROMPT_DIALOGUE_TIMING.md).

## 1. Kết quả

Tốc độ và delivery mặc định nằm trong `ai-service/app/core/voice_presets.py`, với một mirror tối thiểu ở backend. Test parity so sánh toàn bộ cấu hình hai preset, default profile và budget ở nhiều thời lượng; hai service không import chéo nhau.

`VOICE_PACE` mặc định là `podcast`. `VOICE_MATCH_RULES` chỉ giữ danh tính speaker, không còn tốc độ hay cảm xúc. Native prompt dùng `voice_bible` nguyên vẹn của plan; preset chỉ là fallback khi profile thiếu. Không rút profile về 600 ký tự trong builder nữa. Nếu không thể vừa giới hạn 4.000 ký tự sau khi rút context bổ trợ, builder báo lỗi trước submit thay vì cắt profile hoặc lời thoại.

Không đổi schema JSON của story plan, provider, model, API key, tunnel, billing hay các cơ chế first-frame/identity/transition. Giữ native audio và khẩu hình; không bật nhánh TTS thay thế cho video native. `voice_identity_guaranteed` vẫn là `False`, không tuyên bố khóa giọng.

## 2. Preset và budget

| Preset | Âm tiết/giây | Delivery | Budget cảnh 8 giây |
| --- | --- | --- | --- |
| `podcast` | 2,6–3,0 | Ấm, nhẹ, sâu lắng, rõ chữ; nghỉ ngắn giữa các vế; không kéo nguyên âm, thì thầm hoặc diễn kịch | 18–22 |
| `koc` | 3,2–3,6 | Thân thiện, sôi nổi, tự tin, rõ chữ; nghỉ ngắn giữa các vế | 23–27 |

Budget dùng đúng công thức `floor(duration * pace_min * 0.9)` và `floor(duration * pace_max * 0.95)`, rồi giới hạn trong 4–50 token để phù hợp schema hiện tại. **KOC 8 giây theo công thức này là 23–27, không phải 24–28** như một mục test trong yêu cầu. Không viết cứng mốc 24–28 ở bất kỳ planner/grounding/native rule nào.

Chỉ yêu cầu rõ về lời nói mới đổi preset, chẳng hạn `nói nhanh hơn`, `đọc chậm`, `giọng podcast`, `giọng KOC`. Vai trò KOC hoặc chuyển động/camera nhanh/chậm không tự đổi tốc độ giọng. Khoảng tốc độ cụ thể như `1,8-2,2 âm tiết/giây` được ưu tiên và tính lại budget; không thêm field vào output. Parser hỗ trợ các cách viết đã kiểm thử, không phải bộ hiểu mọi biến thể ngôn ngữ tự nhiên. Ví dụ và chỉ dẫn planner được chèn động từ cấu hình đã chọn, không có tuổi cố định 25–30; tuổi/giới tính/vùng giọng theo nhân vật và context liên quan.

Grounding dùng tốc độ định lượng trong profile đã lưu, kể cả khi biến môi trường sau đó đổi. Với profile định tính cũ, nhận diện các mô tả như `slow-moderate`, `podcast`, `brisk`; khi chưa xác định được, dùng preference rõ trong yêu cầu gốc rồi preset môi trường. Không chèn một delivery mới mâu thuẫn với profile hiện có.

TTS lấy cùng default profile từ preset hoặc dùng nguyên `voice_bible` được gửi vào. Code trước sửa chưa có `voice_bible` trong `VoiceInput`, khác với giả định trong yêu cầu; đã bổ sung trường tùy chọn `str`, mặc định rỗng, tối đa 600 ký tự. Đây là request API nội bộ, không đổi JSON story plan và không làm hỏng caller cũ. Backend gửi profile cả lượt tạo narration đầu và lượt điều chỉnh; cache narration có hash profile để không tái dùng tiếng của profile khác.

## 3. File đã tạo hoặc sửa

| File | Thay đổi |
| --- | --- |
| `ai-service/app/core/voice_presets.py` | Tạo: preset, default profile, budget, nhận diện yêu cầu tốc độ/profile, chỉ dẫn TTS |
| `backend/app/core/voice_presets.py` | Tạo: mirror tối thiểu preset/default/budget |
| `ai-service/app/pipelines/story_duration.py` | Budget từ preset, không còn hệ số tốc độ riêng |
| `ai-service/app/pipelines/story_planner.py` | Policy và ví dụ động; fallback theo preset, giữ context qua repair |
| `ai-service/app/prompts/video_story_planner.md` | Bỏ số/tính từ tốc độ cố định và tuổi ví dụ; thêm quy tắc mặt nhìn camera khi thoại |
| `ai-service/app/pipelines/reference_grounding.py` | Giữ profile, tính budget theo tốc độ thực của plan, không ép preset khác |
| `ai-service/app/pipelines/video_api.py` | Thêm `VoiceInput.voice_bible`; instructions TTS từ helper chung |
| `backend/app/services/video_continuity.py` | Luật danh tính trung tính; profile nguyên vẹn; lời thoại theo pace/pauses của profile |
| `backend/app/services/platform_story_preparation.py` | Gửi profile đến TTS và expansion; cache theo hash profile |
| `.env.example` | Thêm `VOICE_PACE=podcast` |
| `ai-service/.env.example` | Thêm `VOICE_PACE=podcast` |
| `backend/.env.example` | Thêm `VOICE_PACE=podcast`, nhắc giữ cùng giá trị với AI service |
| `docker-compose.mvp.yml` | Truyền `VOICE_PACE` vào cả hai container |
| `ai-service/tests/test_voice_presets.py` | Tạo: parity, default/config sai, budget, override, grounding profile và TTS mock |
| `ai-service/tests/test_story_duration.py` | Cập nhật budget/fallback; kiểm tra system policy và output schema |
| `ai-service/tests/test_reference_grounding.py` | Kiểm tra cả hai preset, profile/budget không đổi theo env mới |
| `backend/tests/test_video_continuity_strategy.py` | Kiểm tra một nguồn tốc độ, profile chậm, clip 1/N, không cắt profile dài |
| `backend/tests/test_platform.py` | Cập nhật assert native; kiểm tra forwarding và cache TTS |
| `docs/VOICE_AND_BOUNDARY_REFACTOR.md` | Đánh dấu section 13 là lịch sử và dẫn tới báo cáo mới |
| `docs/VOICE_PRESET_CONFLICT_FIX.md` | Báo cáo này |

## 4. Diff ngắn trước và sau

`VOICE_MATCH_RULES`:

```diff
- Same speaker, accent, age, pitch, resonance, texture and mic distance.
- Brisk natural 3.2-3.6 Vietnamese syllables/s takes priority over older slow/moderate pace labels.
- No recasting or scene-specific voices.
+ Same single speaker, accent, vocal age, pitch, resonance, texture and mic distance as the global voice instruction.
+ No recasting, no scene-specific voices.
```

Đoạn native-audio trong `build_scene_prompt`:

```diff
- voice = textwrap.shorten(voice_instruction(plan.get("voice_bible")), 600)
+ voice = voice_instruction(plan.get("voice_bible")) if native_audio else ""
- Start within 0.5s, lively and clear, no drawn-out vowels or long pauses.
- Carry phrase intonation without a fresh introduction or silent gap.
- Speak the full line once; never stretch it to fill the shot.
+ Begin speaking promptly with the pace and pauses in the global voice instruction.
+ Carry phrase intonation without reintroductions or repeating words.
+ On-screen speakers face the camera, face visible; no reading down or page-turning during speech.
+ Small prop actions only before or after the line.
```

Giữ nguyên lời thoại `exactly once`, đồng bộ môi, cấm mumbling/stuttering/repetition, luật giữ dáng sau khi nói và giới hạn prompt. Không thêm đoạn preset thứ hai bên cạnh một `voice_bible` đã có.

## 5. Prompt mẫu đầy đủ cho cảnh 2

Được dựng bằng `build_scene_prompt` với dữ liệu minh họa, preset `podcast`, nhân vật nữ trưởng thành 18–25 tuổi, reference mode thực của `veo3.1-fast` và parent scene 1. Dài **3.437 ký tự**, chưa gửi đến PoYo. Lời cảnh 2 có 19 âm tiết theo khoảng trắng; không phải số đo alignment.

```text
CONTINUE THE PREVIOUS VIDEO. This is a direct continuation of the previous shot. Keep the same person, face, hair, clothing, props, voice and delivery. Preserve setting, lighting and framing except for the explicitly planned transition. Continue the existing action; do not restart it or introduce another speaker.
Continue seamlessly from the supplied opening-state image. Begin in exactly its visible physical state: do not reset pose, hands, prop position, facial orientation, camera, background, lighting or wardrobe. One planned small action only; no strong head turn or large hand gesture near the boundary. End in a stable pose.
GLOBAL VOICE INSTRUCTION (unchanged for every scene): Vietnamese; female adult 18-25; Hanoi Northern accent, clear tones and consonants; natural pitch, warm clear timbre, balanced resonance, smooth texture, minimal breathiness, low nasality; warm, gentle, reflective conversation, clear enunciation, short natural clause pauses; no drawn-out vowels, whispering or theatrical delivery; pace 2.6-3.0 syllables/s; stable volume, close dry microphone. Preserve the same vocal traits across all scenes.
VOICE MATCH RULES (not dialogue): Same single speaker, accent, vocal age, pitch, resonance, texture and mic distance as the global voice instruction. No recasting, no scene-specific voices.
Generate picture and native audio together. Spoken Vietnamese line, exactly once: "Đôi khi một trang sách cũng giúp mình nhìn lại những điều nhỏ bé mà mình đã bỏ qua.".
Synchronize lips with native speech. Keep accent, age, timbre and delivery. No extra dialogue, subtitles, mumbling, stuttering or repetition. Begin speaking promptly with the pace and pauses in the global voice instruction. Carry phrase intonation without reintroductions or repeating words. On-screen speakers face the camera, face visible; no reading down or page-turning during speech. Small prop actions only before or after the line. Then hold a stable pose; no extra gestures, body swaying or facial morphing.
PREVIOUS DIALOGUE, CONTEXT ONLY, DO NOT SPEAK OR REPEAT: "Hôm nay mình muốn chia sẻ cuốn sách này, như một lời nhắc dịu dàng để chậm lại."
The character reference defines identity; the style reference defines the set. The final reference shows the previous scene's ending pose and framing (or the shared opening image). Follow that reference for the current state, and the character reference for identity. Start as close to that state as possible.
CONTINUITY LOCK: Preserve the subject identity and important props. Visual bible: The supplied reference image is the authority for appearance, wardrobe, props and location.
Scene action: The same seated presenter faces the camera and holds the same book still at chest height. Her face stays unobstructed throughout speech.
Starting state: Use the actual visible pose and composition in the supplied previous frame.
Ending state: Seated facing the camera, book still at chest height, hands and body relaxed.
Transition intent: continue
Identity lock: keep the same face shape, eyes, nose, skin tone, hair and wardrobe in every frame. Motion budget: one continuous locked shot at the same distance and angle. Use natural blinks and only the requested small action. Keep props visible and stable. No unrequested walking, swaying, prop swapping, zooms, pans, facial morphing or time jumps. For an explicitly planned camera or location transition only, follow the transition intent.
```

## 6. Kiểm thử

| Kiểm tra | Kết quả cuối |
| --- | --- |
| Toàn AI service | 200 passed, 0 failed |
| Toàn backend, loại riêng file Celery worker | 172 passed, 0 failed |
| Test backend tập trung trước lượt toàn bộ | 47 passed, 72 deselected |
| Ruff các file Python tạo/sửa | Passed |
| Compile Python và whitespace/diff check | Passed |

Lệnh từ thư mục service tương ứng:

```powershell
# ai-service
python -m pytest tests -q -o addopts= -x
# backend
python -m pytest tests -q -o addopts= --ignore=tests/test_video_worker.py -x
```

`tests/test_video_worker.py` được loại khỏi lượt backend vì Python mặc định không có `celery`, đã kiểm tra bằng `importlib.util.find_spec`. Đây là file không được collect, không phải một test pass hay pytest skip tự động. Không cài thêm dependency. Temp hệ thống hoạt động nên không cần `--basetemp` trong workspace. Còn các warning deprecation có sẵn của pytest-asyncio, Starlette/httpx và Pydantic; không sửa cấu hình/dependency ngoài phạm vi.

Các test xác minh cả hai preset, parity giữa service, profile chậm không bị override, system prompt chỉ chứa tốc độ preset đã chọn, tuổi ví dụ không bị viết cứng, budget theo yêu cầu riêng, profile saved khi env đổi, clip 1/N chung một instruction, giữ profile dài/thoại và fail trước submit nếu không vừa 4.000 ký tự. HTTP OpenAI/PoYo được mock; không gọi API trả phí, không sửa DB/output thật. Đây là kiểm chứng data flow, không phải đánh giá chất giọng bằng một video mới.

## 7. Giới hạn còn lại

- Veo vẫn sinh từng clip độc lập. Provider không có speaker ID/audio reference trong contract đang dùng; profile không bảo đảm đồng nhất chất giọng. Cờ guarantee không thay đổi.
- Không có validator ngữ nghĩa hoàn chỉnh cho `voice_bible` tự do. Profile/visual prompt cũ hoặc chỉnh tay vẫn có thể tự chứa hai phong cách trái nhau; builder không âm thầm viết lại chúng. Bản nháp mới được hướng dẫn tránh điều này. Kiểm tra ASR hiện có không xác minh speaker identity hay vùng giọng.
- Nhận diện tốc độ từ mô tả định tính cũ và câu yêu cầu là heuristic có test cho các cách viết phổ biến. Khi cần budget chính xác theo ý định, dùng một khoảng `âm tiết/giây` rõ ràng; số âm tiết theo khoảng trắng vẫn chỉ là ước lượng.
- Chất giọng thực của TTS còn phụ thuộc `OPENAI_TTS_MODEL` và `OPENAI_TTS_VOICE` hiện có. Profile là chỉ dẫn style, không thay được speaker của model. Nhánh narration cũ vẫn có xử lý timing/retime giới hạn theo code hiện có; không đổi compositor trong lần này.
- Hành động, biểu cảm, framing và nội dung thoại vẫn có thể tác động cách Veo nói. Chỉ thêm quy tắc mặt thấy rõ/nhìn camera, không cúi đọc hoặc lật trang khi nói; không refactor xử lý hình ảnh hay sửa lại video đã hoàn tất.

## 8. Áp dụng

Đặt `VOICE_PACE=podcast` hoặc `VOICE_PACE=koc` trong `.env` gốc khi dùng `scripts/run_local.py`, hoặc cung cấp biến môi trường cho cả hai service khi chạy riêng. Các `.env.example` chỉ là mẫu; chưa sửa `.env` chứa key thật. Nếu chạy riêng từ PowerShell, có thể đặt `$env:VOICE_PACE = 'podcast'` trước khi khởi động từng service.

Khởi động lại backend và AI service để nạp code, sau đó lập bản nháp mới để nhận policy/budget mới. Plan cũ vẫn giữ `voice_bible` đã lưu; không tự tạo lại clip đã hoàn tất, không tự thay giọng và không tự phát sinh credit.
