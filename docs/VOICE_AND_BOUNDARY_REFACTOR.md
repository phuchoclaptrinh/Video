# Refactor voice và điểm nối cảnh

> Lưu ý 09/10/2026: các phần budget, tự sửa thoại và prompt nối câu trong báo cáo này là lịch sử, không còn mô tả luồng native Veo hiện tại. Đã bỏ quota âm tiết và code không còn caller. Xem [planner hiện hành](PLANNER_FIRST_PASS_SCRIPT.md) và [review prompt](VEO_PROMPT_REVIEW.md).

Ngày kiểm tra: 06/10/2026. Báo cáo này bổ sung cho `VOICE_CONTINUITY_IMPLEMENTATION.md` và `POYO_CONTINUATION_AUDIT.md`.

## 1. Kết luận

Đã sửa planner, truyền cờ audio, lựa chọn duration, continuity frame, điểm cắt đồng bộ audio/video và metadata voice mode. Không đổi provider/model đang cấu hình, không sửa `.env`, key, tunnel hoặc dữ liệu production, không gọi API trả phí khi kiểm thử.

**Chưa thể bảo đảm một chất giọng và một khuôn mặt tuyệt đối giữa các cảnh Veo.** Repo có TTS OpenAI nhưng chưa có speech alignment hoặc lip-sync thật. `consistent_voice` hiện là boundary kiến trúc có kiểm tra readiness, chưa phải chế độ xuất video hoàn chỉnh. Yêu cầu chế độ này được fallback rõ ràng sang `native_veo`, không âm thầm thay thoại bằng voiceover lệch môi.

## 2. Root Causes

| Vấn đề | Nguyên nhân tìm thấy | Thay đổi |
| --- | --- | --- |
| Đổi giọng | Mỗi clip là generation độc lập; `voice_bible` không phải speaker ID | Tách voice mode, công khai best effort và thiếu infrastructure |
| Không tắt được native audio | PoYo chưa được truyền cờ; không phân biệt false mặc định với false được yêu cầu | Dùng optional flag ở API; giữ false qua validate; mapping tại compiler theo model |
| Mọi clip thành 8 giây | Parser ghi đè duration; backend/UI cũng giả định 8 | Planner nhận capability adapter; giữ duration đã chọn; cộng duration từng clip |
| Dialogue rời rạc | Segment từng cảnh chưa được kiểm tra với một bài nói toàn video | Thêm `full_voice_script` và kiểm tra partition đúng thứ tự |
| Jump dù có frame thật | Frame tham chiếu và compositor có thể dùng hai điểm kết thúc khác nhau | Lưu điểm cuối đã chọn và dùng lại khi cắt cả audio/video |
| Bỏ mất opening pose | Xóa silence đầu cảnh bằng cách cắt video có thể bỏ khung mở đầu continuity | Với cảnh sau có `continue`, giữ `trim_start_seconds=0`; báo pause còn lại |
| Dùng lại bản ghép cũ | Cache native chỉ phụ thuộc chuỗi generation | Thêm phiên bản chính sách review/boundary vào fingerprint file xuất |

Parser cũng được sửa để tạo `visual_bible` sau khi đã suy ra `visual_continuity`, tránh đưa `None` vào trường string khi LLM thiếu cả hai.

## 3. Hợp Đồng PoYo Thực Tế

Không áp dụng hợp đồng của model khác vào `veo3.1-fast` đang dùng.

| Họ model | Duration thông thường | Điều khiển audio | Ba ảnh reference |
| --- | --- | --- | --- |
| `veo3.1-fast` thường | 8 | Chưa có field tắt audio được xác nhận | Có; duration 8 |
| `veo3.1-fast-official` | 4, 6, 8 | `input.sound` boolean | Có; chỉ duration 8 |

Các giới hạn này được đối chiếu từ [PoYo VEO 3.1](https://docs.poyo.ai/api-manual/video-series/veo-3-1) và [PoYo VEO 3.1 Official](https://docs.poyo.ai/api-manual/video-series/veo-3-1-official). Compiler đã thêm hỗ trợ họ official khi được cấu hình rõ ràng, không tự chuyển model hoặc suy diễn giá/credit. Đổi model cần cấu hình thống nhất backend và AI service rồi khởi động lại.

Với official, lite không nhận ba ảnh reference; cặp ảnh frame và output 1080p của lite yêu cầu duration 8. Compiler chặn tổ hợp không hợp lệ trước submit. Planner lấy duration thông thường từ capability; continuity chọn tổ hợp ảnh phù hợp từng duration.

### Mapping audio chính xác

```text
GenerateInput.native_audio: bool | None
  -> PlatformGenerationMixin.generate
  -> spec.output.generate_audio
  -> GenerationOrchestrator.enqueue: validate + lưu canonical request
  -> VideoGenerationPipeline.submit
  -> PoyoVeoCompiler.compile
  -> PoyoVeoProvider.submit: POST api/generate/submit
```

Ở compiler official, nếu flag được cung cấp: `input.sound = bool(spec.output.generate_audio)`. Nếu bỏ flag, giữ mặc định có tiếng của provider. AI `/validate` không được xóa false đã được cung cấp rõ ràng cho PoYo. Test MockTransport kiểm tra JSON HTTP cuối cùng, không chỉ payload ở backend.

Với fast thường, không gửi field `sound` phỏng đoán. Explicit `native_audio=false` trả lỗi `native_audio_disable_unsupported_model` trước enqueue/submit, thay vì nhận yêu cầu rồi vẫn sinh tiếng. Vì vậy tiêu chí tắt tiếng trên model này **không thể hoàn thành bằng hợp đồng API đã xác minh**; cần model có `audio_control=true`.

Story batch hiện vẫn cần thoại native: `native_audio=false` cho cả batch bị chặn, kể cả official, vì chưa có pipeline consistent voice hoàn chỉnh để thay thế. Single clip official nhận true/false đúng theo API. Seedance tiếp tục dùng compiler riêng với `generate_audio` của provider đó.

## 4. Luồng Sau Refactor

```text
UI / chat + context đã lưu
  -> backend plan: lấy capability của model đang chạy
  -> AI planner: đọc video_story_planner.md + capability
  -> full_voice_script -> clips không overlap, duration hợp lệ
  -> duyệt + xác nhận trả phí
  -> voice decision -> lưu requested/effective mode + limitations
  -> tạo cảnh 1
  -> poll -> download -> persist -> kiểm tra speech và media
  -> chọn frame thật -> signed HTTPS URL
  -> cảnh N+1 nhận references + parent provenance của cảnh N
  -> compositor dùng các cửa sổ cắt đã lưu cho cả video và audio
  -> concat, encode AAC ở final export, kiểm tra giải mã AV
```

Capability ở AI adapter là nguồn chính, backend nhận qua `/internal/v1/video/{provider}/capabilities`. Khi service không trả được capability, backend dùng fallback có tài liệu, ghi log; không tự bật video extension. Model backend/AI không khớp thì trả lỗi yêu cầu restart.

Schema có `duration_seconds`, `full_voice_script`, và optional `audio_start_ms`/`audio_end_ms`. Không ép `6+8+6` thành `8+8+8` khi capability cho phép. Timestamp phải là integer, đi thành cặp, có độ dài dương, không overlap và không dài hơn scene. Không cho LLM bịa timestamp alignment. Kịch bản cũ thiếu duration vẫn dùng 8 để tương thích.

### Frame và identity reference

Parent phải thuộc đúng user, là cảnh liền trước, hoàn tất và đã lưu video thật. `output_video_url` và đường dẫn asset được lưu làm provenance; không gửi URL video trước thành input khi adapter chưa hỗ trợ video continuation.

`_extract_continuity_frame` đọc MP4 đã download. Nếu review đo được silence cuối đủ dài, nó thử tối đa ba frame trong phần đuôi an toàn, xếp hạng sharpness/low motion bằng Pillow và giữ thêm tối đa 200 ms so với điểm cắt có speech padding. Không chọn sớm hơn điểm đó để tránh cắt lời. Không có dữ liệu silence thì giữ đường lấy frame cuối đã giải mã. Đây không phải CV nhận diện mắt nhắm, khuôn mặt hoặc khẩu hình; metadata ghi `face_pose_verified=false`.

`composition_end_seconds` và thời điểm frame được lưu theo scene/generation. Compositor dùng cùng điểm cuối đó cho `trim` và `atrim`. Cảnh `continue` từ scene 2 giữ opening pose thay vì bỏ silence đầu. Pause native có thể còn và được báo, không được chữa bằng dịch audio độc lập.

Ảnh global được crop thành character/style reference; frame cảnh trước là opening-state reference riêng. Fast adaptive với duration 8 giữ cả ba. **Trên wire, ba ảnh là `generation_type=reference`, không phải một first frame được khóa chính xác.** `FIRST_FRAME` nội bộ trong tổ hợp này chỉ mang ý nghĩa opening-state guidance; metadata `first_frame_exact=false`.

Official duration 4/6 không cho ba ảnh, nên dùng first-frame mode thay vì gửi tổ hợp sai. Khi đó character anchor riêng không được gửi; đây là trade-off capability, không phải bảo đảm identity. Ưu tiên identity anchor riêng cần duration/reference mode mà provider hỗ trợ.

Không thêm crossfade 0.3-0.5 giây hoặc blend mặc định. Các cut chủ ý vẫn giữ; chỉ giữ hiệu ứng `fade_to_black` được planner yêu cầu. Chưa thêm optional blend 2-3 frame vì không có phép kiểm chứng đủ tốt để tránh double face/ghost hands.

## 5. Voice Modes Và Phần Còn Thiếu

| Mode | Hiện trạng |
| --- | --- |
| `native_veo` | Hoạt động; Veo tạo hình và thoại cùng nhau. Speaker identity giữa clip chưa được khóa |
| `consistent_voice` | Chỉ sẵn sàng khi có TTS + alignment + lip-sync thật + provider tắt audio được. Hiện fallback native kèm lý do |

`voice_pipeline.py` tách `MasterVoiceSynthesizer`, `SpeechAligner`, `LipSyncAdapter`, `SceneAudioWindow` và bước `MasterVoicePreparation`. `PlannerMasterVoice` tận dụng endpoint TTS có sẵn, yêu cầu một WAV cho toàn script, không gọi TTS theo scene. Bước preparation chặn trước TTS trả phí nếu chưa có aligner/lip-sync. Nó chưa thực hiện scene render, lip-sync hoặc final export; cần nối các implementation thật vào pipeline trước khi bật readiness.

Endpoint TTS cũ giữ MP3 mặc định để không phá narration đã lưu; có thêm lựa chọn WAV. [OpenAI TTS](https://developers.openai.com/api/docs/guides/text-to-speech) hỗ trợ WAV. Không đổi model/voice TTS, không thêm vendor. Không khẳng định TTS mô phỏng chính xác giọng Veo.

Native export dùng audio trong từng clip, decode vào filter graph, giữ giới hạn gain hiện có, resample 48 kHz và encode AAC cuối. Đây **không phải một master speaker**. Luồng narration MP3 cũ vẫn được phục hồi để tương thích; không gọi nó là consistent voice có lip-sync. Không áp chính sách LUFS mới hoặc time-stretch mạnh trong refactor này.

API/config và job metadata công khai voice modes, readiness và limitations. UI chat hiển thị thoại Veo chưa khóa giọng, cùng thông báo fallback khi được yêu cầu consistent voice. Không cung cấp nút giả cho một pipeline chưa vận hành được.

## 6. Logging Và An Toàn

Backend log story/scene, model, duration, voice mode, native audio, parent generation, loại reference và transition. Provider log giá trị `sound_sent_to_provider` từ JSON cuối; fast thường ghi `NOT_SENT_PROVIDER_DEFAULT`. Không log key, base64 hoặc signed URL; parent URL được redacted/hash như trước.

Giữ billing confirmation, credential forwarding, strict execution schema, request IDs, retry và xử lý submit có kết quả không xác định. Retry giữa chuỗi vẫn giữ parent thật, vô hiệu hóa downstream theo logic hiện có, không tự tạo lại cảnh trả phí. Không sửa file output lịch sử hoặc tự render lại batch đã hoàn tất.

## 7. Các File Thay Đổi

Các file sau thuộc đợt refactor này; `git status` còn có nhiều thay đổi có sẵn từ các đợt trước.

| File | Vai trò |
| --- | --- |
| `ai-service/app/prompts/video_story_planner.md` | Bài nói toàn video, capability durations, hành động nhỏ |
| `ai-service/app/pipelines/story_planner.py` | Schema/parser, partition script, timestamps, capability context |
| `ai-service/app/pipelines/video_api.py` | Capability vào planner, giữ explicit audio false, lựa chọn WAV |
| `ai-service/app/providers/poyo_veo_compiler.py` | Official durations, mapping sound một nơi, giới hạn ảnh |
| `ai-service/app/providers/poyo.py` | Expose capability và log JSON audio cuối |
| `ai-service/app/providers/seedance.py` | Expose audio control của adapter 1.5 |
| `ai-service/app/schemas/provider.py` | Capability audio/duration reference |
| `backend/app/main.py` | Input voice/audio, config capability/readiness |
| `backend/app/services/video_provider_capabilities.py` | Mirror contract và fallback có tài liệu |
| `backend/app/services/video_execution_client.py` | Timeout riêng ngắn cho đọc capability |
| `backend/app/services/platform_planning.py` | Validate duration theo model, chuyển provider vào planner |
| `backend/app/services/platform_generation.py` | Truyền audio, voice policy, duration và reference metadata |
| `backend/app/services/platform_projects.py` | Recompute full script sau revision/grounding |
| `backend/app/services/voice_pipeline.py` | Boundary master TTS/alignment/lip-sync và chọn mode |
| `backend/app/services/video_continuity.py` | Chọn reference theo duration, prompt không reset physical state |
| `backend/app/services/continuity_frames.py` | Chọn sharp/low-motion frame, không giả nhận diện mặt |
| `backend/app/services/platform_continuity.py` | Extract frame thật, lưu endpoint dùng chung |
| `backend/app/services/platform_scene_continuity.py` | Đọc trim đã chọn, xóa metadata khi invalidation |
| `backend/app/services/platform_native_audio.py` | Giữ opening pose cho continuous scene, version review |
| `backend/app/services/platform_story_preparation.py` | Persist/expose voice decision và cộng duration |
| `backend/app/services/platform_story_render.py` | Trim AV cùng window, target động, version cache xuất |
| `frontend/src/types/video-event.ts` | Duration/full script và voice metadata |
| `frontend/src/components/chat/VideoEventCard.tsx` | Duration thực và trạng thái voice rõ ràng |
| `frontend/src/app/page.tsx` | Tổng duration động, bỏ mô tả sai về TTS mặc định |
| `ai-service/tests/test_video_execution.py` | Wire sound, duration, planner, reference constraints |
| `backend/tests/test_voice_pipeline.py` | Readiness, TTS một lần, timing, frame ranking |
| `backend/tests/test_video_continuity_strategy.py` | Reference mode theo duration |
| `backend/tests/test_platform.py` | API audio/mode, parent/retry, FFmpeg AV và cache |
| `docs/VOICE_AND_BOUNDARY_REFACTOR.md` | Báo cáo này |
| `docs/POYO_CONTINUATION_AUDIT.md` | Dẫn sang báo cáo mới, giữ kết quả lịch sử |

## 8. Kiểm Chứng

- AI service: **55 tests passed**, gồm validate -> actual HTTP JSON với `sound=true/false`, `6+8+6`, overlap/partition, Seedance và fake.
- Backend không gồm Celery: **121 tests passed**, gồm API explicit audio-off, fallback, parent ownership/sequence, retry/idempotency, và FFmpeg thực.
- Celery worker: **1 test passed** với dependency đã có trong `.venv/person3-deps`; Python mặc định không thấy Celery. Không cài thêm hoặc thay đổi dependency.
- Sau khi bổ sung version cache, chạy lại **15 test liên quan: passed**, gồm cache fingerprint, frame/compositor, parent/retry, audio flag và mode fallback.
- `npx tsc --noEmit --incremental false`: pass.
- Ruff trên các module service/adapter/schema/test Python chỉnh sửa: pass. `compileall` backend/AI: pass.
- `git diff --check`: pass; có thông báo CRLF/LF ở file chat đã có thay đổi trước, không tự rewrite file đó.

Các lệnh test từ thư mục service tương ứng:

```powershell
# ai-service
python -m pytest tests -q -o addopts=
# backend
python -m pytest tests -q -o addopts= --ignore=tests/test_video_worker.py
python -c "import sys, pytest; sys.path.insert(0, r'D:\ToolVeo3\Create_video_VO3\.venv\person3-deps'); raise SystemExit(pytest.main(['tests/test_video_worker.py', '-q', '-o', 'addopts=']))"
```

HTTP provider được mock; video/AV composition dùng file media sinh trong thư mục test tạm. Chưa tạo video trả phí, chưa nghe/xem output PoYo thật hoặc đo tỷ lệ drift trên một dataset thực tế. Test pass xác minh data flow và regression, không chứng minh native voice/identity đã đồng nhất hoàn toàn.

## 9. Để Có Consistent Voice Thật

1. Chọn adapter/model có cơ chế tắt audio đã xác minh, hoặc xác minh cơ chế tương đương cho model hiện tại.
2. Cắm speech alignment thật để lấy window từ một master WAV, không lấy timestamp do LLM đoán.
3. Cắm lip-sync thật dùng chính WAV/window đó; xử lý failure trước khi ghép final.
4. Nối preparation, render im tiếng, lip-sync và compositor bằng trạng thái/persistence có thể retry; chỉ lúc đó mới bật `consistent_voice_available`.
5. Kiểm thử trả phí có người nghe/xem: accent/timbre, đủ lời, khẩu hình và frame boundary. Không dùng prompt `same voice` làm tiêu chí đạt.

## 10. Bổ Sung: Lỗi Partition Kịch Bản

Sau refactor, kiểm tra `full_voice_script` nguyên văn quá chặt đã gây `planner_schema_mismatch` khi LLM thay đổi dấu câu/chữ hoa, Unicode hoặc viết lại segment. JSON mode đang dùng không bảo đảm các quan hệ nội dung giữa trường; kiểm tra và retry vẫn cần ở ứng dụng. Xem [OpenAI Docs: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Đã sửa parser để:

- So sánh từ sau chuẩn hóa Unicode NFC/case và dấu câu thông thường, vẫn giữ dấu tiếng Việt, số, dấu âm, phần thập phân và ký hiệu đơn vị. Khi chỉ khác định dạng, dựng lại full script từ các segment đã validate, không gọi LLM thêm.
- Nếu khác từ thật, thử chia lại bài nói tổng thể đúng một lần, chỉ nhận mảng `voice_scripts`, giữ nguyên visual/reference/voice bible, số cảnh và duration. Không âm thầm bỏ bài nói gốc hoặc chấp nhận thiếu/lặp từ.
- Kiểm tra lại schema và toàn bộ partition sau repair. Repair giới hạn timeout request 30 giây; không tự tạo video. Nếu không sửa được, trả `planner_dialogue_mismatch` và thông báo tiếng Việt cụ thể.
- Prompt yêu cầu full script cuối cùng được ghép từ các segment cuối, không viết lại độc lập sau khi chỉnh cảnh.

Kiểm chứng bổ sung: toàn AI service **83 test passed**; backend **3 test liên quan passed**. Test có response OpenAI mock và endpoint app thật, không dùng API key thật hay dịch vụ trả phí. Đã kiểm tra repair không lặp vô hạn, giữ duration, không nhận thêm/thiếu từ, không bỏ dấu hoặc thay số và giữ phân loại lỗi quota.

File bổ sung/chỉnh sửa: `ai-service/app/pipelines/story_planner.py`, `ai-service/app/prompts/video_story_planner.md`, `ai-service/app/pipelines/video_api.py`, `ai-service/tests/test_story_dialogue.py`, `backend/app/services/platform_planning.py`, `backend/tests/test_platform.py` và báo cáo này. AI service và backend cần nạp lại code để áp dụng.

## 11. Bổ sung: Kết nối planner và tổng thời lượng

Lỗi `ReadError` xảy ra khi đọc phản hồi mạng; không đồng nghĩa với key hết quota hay schema kịch bản sai. HTTPX phân biệt lỗi mạng với HTTP status; retry ở transport mặc định không xử lý lỗi đọc phản hồi. Tham khảo [HTTPX exceptions](https://www.python-httpx.org/exceptions/) và [HTTPX transports](https://www.python-httpx.org/advanced/transports/).

Module `planner_transport.py` cho toàn lượt lập kịch bản một retry mạng chung, chờ 0,5 giây, dùng kết nối mới giữa các POST. Giữ kiểm tra TLS và cấu hình proxy từ môi trường; không tắt bảo mật để vượt lỗi. HTTP 401/403/404/429/5xx không tự retry. Deadline tổng 120 giây bao gồm lập bản nháp, sửa duration, sửa partition và retry mạng; timeout đọc riêng là 90 giây cho story/duration và 30 giây cho dialogue repair. Không retry bước submit video PoYo trả phí.

Các bước tự sửa vẫn hữu hạn: tối đa một duration repair và một dialogue repair, cùng một retry mạng cho toàn lượt, tức tối đa 4 POST LLM nếu cần cả hai loại sửa. Request LLM đã gửi có thể tính phí dù client không nhận được phản hồi; retry không bảo đảm mọi lỗi mạng đều phục hồi. Log có phase, attempt, loại lỗi và elapsed, không có key hoặc nội dung prompt. API/UI phân biệt `planner_connection_failed`, `planner_timeout`, `planner_upstream_error` với lỗi schema.

Kiểm tra GET OpenAI không dùng key đã nhận HTTP 401 như dự kiến, xác nhận đường HTTPS có thể truy cập tại thời điểm kiểm tra. Không xác định được nguyên nhân kết nối POST trước đó bị ngắt chỉ từ log `ReadError`; chưa gọi planner bằng key thật để kiểm thử.

Đồng thời đã sửa việc yêu cầu video 20 giây bị lập thành một cảnh 8 giây. Chi tiết contract, giới hạn adapter, bảo toàn thời lượng khi replan và cách test ở [STORY_DURATION_PLANNING.md](STORY_DURATION_PLANNING.md). Kết quả kiểm chứng hiện tại: **156 test AI**, **11 test backend liên quan** passed; TypeScript, Ruff, compile và diff check passed. Không thay model, key, tunnel, billing hoặc output lịch sử.

## 12. Bổ sung: Prompt giọng chi tiết dùng chung

Phần này ghi nhận thay đổi ban đầu. Tốc độ, lượng thoại và fallback giọng hiện tại đã được cập nhật ở phần 13 bên dưới.

Theo yêu cầu giữ thoại Veo, đã tăng chi tiết prompt giọng thay vì thay tiếng bằng TTS. Prompt planner có phần `Voice direction`, yêu cầu chọn một `voice_bible` cho toàn video, tối đa 600 ký tự: ngôn ngữ, vùng giọng, giới tính/tuổi trưởng thành, cao độ, âm sắc, cộng hưởng, độ mịn/khàn, hơi thở, độ âm mũi, phong cách, tốc độ, cảm xúc/ngữ điệu, phát âm, âm lượng và đặc tính microphone. Các lựa chọn của người dùng được ưu tiên; không mặc định giọng nữ miền Bắc cho một nhân vật nam hoặc yêu cầu giọng khác.

Ví dụ cho KOC nữ Việt Nam, nếu người dùng không yêu cầu khác:

```text
Vietnamese; female adult 25-30; Hanoi Northern accent, clear native tones/consonants; natural mid-high pitch, warm clear timbre, light chest resonance, smooth texture, slight breathiness, low nasality; friendly confident conversation, restrained emotion, gentle phrase endings; steady 2.5-3 syllables/s, short clause pauses; stable moderate volume, close dry mic, no echo. Preserve accent, register, resonance, texture and delivery across all scenes.
```

Backend đặt cùng một dòng `GLOBAL VOICE INSTRUCTION` và khối `VOICE MATCH RULES` vào tất cả scene có native audio, kể cả scene 1. Không lấy voice profile riêng từ từng clip; profile toàn video có ưu tiên, không được đọc thành lời. Parser vẫn hỗ trợ profile dạng dict cũ, nhưng giữ thêm các thuộc tính mới thay vì bỏ chúng. Nếu thiếu mô tả giọng, backend có fallback chi tiết nhưng trung tính, không ép giới tính/vùng giọng.

Để không phát sinh lỗi goal vượt giới hạn 4.000 ký tự của contract khi thêm chỉ dẫn giọng, builder có thể rút gọn phần mô tả visual/action/state/transition bổ trợ. Không rút dòng thoại đang cần nói hoặc khối profile đã chuẩn hóa theo giới hạn 600 ký tự. Các chỉ dẫn giữ hình, khẩu hình và âm thanh native vẫn được giữ; không thêm audio reference, voice ID giả hoặc claim đã khóa giọng.

Kiểm chứng: **157 test AI service passed**, **23 test continuity helper passed** và **6 test backend chuỗi scene/reference passed**; Ruff, compile và diff check passed. Có test ba scene dùng cùng profile, giữ các thuộc tính giọng dạng dict, tôn trọng speaker nam/giọng miền Nam, fallback, prompt dài và native audio-off. Test HTTP PoYo dùng MockTransport chứng minh khối profile vẫn có trong payload gửi từng scene, không gọi API trả phí. Chưa xác minh bằng nghe một video PoYo mới; prompt chi tiết không bảo đảm đồng nhất speaker nếu provider không có cơ chế voice reference.

File chỉnh sửa trong lần này: `ai-service/app/prompts/video_story_planner.md`, `ai-service/app/pipelines/story_planner.py`, `backend/app/services/video_continuity.py`, các test liên quan và báo cáo này. Khởi động lại AI service/backend, rồi lập bản nháp mới để có profile chi tiết do LLM chọn. Các scene được tạo mới sẽ nhận khối chỉ dẫn chung; video đã hoàn tất không bị tạo lại hay thay tiếng tự động.

## 13. Bổ sung: Giọng Bắc, nhịp nhanh hơn và lời thoại sạch

Phần này ghi nhận thay đổi trước đây. Preset, tốc độ và budget hiện tại dùng `VOICE_PACE=podcast` mặc định; xem [báo cáo gỡ xung đột prompt](VOICE_PRESET_CONFLICT_FIX.md). Các chỉ thị nhịp nhanh cố định mô tả dưới đây đã được thay bằng preset.

Theo yêu cầu mới, planner mặc định giọng Bắc Hà Nội, rõ dấu/thanh và phụ âm, trò chuyện tươi tắn, tự nhiên, ít hơi thở và ít âm mũi. Nhịp mục tiêu tăng từ 2,5–3 lên **3,2–3,6 âm tiết tiếng Việt/giây**, tránh kéo dài nguyên âm hoặc ngắt nghỉ diễn kịch. Giới tính vẫn theo nhân vật, không ép giọng nữ cho nhân vật nam; vùng giọng chỉ thay đổi khi yêu cầu hiện tại nói rõ vùng khác. Fallback khi LLM bỏ trống profile cũng dùng giọng Bắc và nhịp mới.

Planner nhận ngân sách lời thoại theo thời lượng thực tế từ `scene_speech_budget`:

| Thời lượng cảnh | Lượng thoại mục tiêu |
| --- | --- |
| 4 giây | 12–14 âm tiết |
| 6 giây | 18–21 âm tiết |
| 8 giây | 24–28 âm tiết |
| 12 giây | 36–42 âm tiết |

Đếm theo khoảng trắng là ước lượng cho lời Việt, không phải alignment đo được. Mục tiêu vẫn trong giới hạn schema 50 token/cảnh. LLM cần bổ sung ý có liên quan, không bịa thông tin sách/sản phẩm, lặp câu hoặc thêm từ đệm để đủ số lượng. Không thêm lượt gọi LLM tự động chỉ để đạt số âm tiết. Bước đọc ảnh/chỉnh kịch bản nhận cả voice profile, thời lượng và ngân sách riêng cho từng cảnh, không còn mặc định tất cả cảnh là 8 giây hoặc rút về 20–24 âm tiết.

Module `backend/app/services/spoken_text.py` xử lý **chỉ trường lời thoại**:

- Chuẩn hóa Unicode NFC, giữ dấu tiếng Việt và dấu câu thông thường `. , ? !`.
- Bỏ Markdown, emoji, dấu ngoặc/dấu nháy, bullet và ký tự ẩn; gộp khoảng trắng, thay dấu ba chấm bằng dấu chấm.
- Chuyển ký hiệu có ý nghĩa thành từ đọc: `20%` thành `20 phần trăm`, `$5` thành `5 đô la Mỹ`, `−5°C` thành `âm 5 độ C`; giữ phần thập phân và đơn vị tiền. Không âm thầm biến số âm thành số dương.
- Planner được yêu cầu viết số/giá/đơn vị bằng chữ ngay từ đầu. Bộ làm sạch không phải bộ đọc số hay từ viết tắt tiếng Việt hoàn chỉnh; tên riêng/thuật ngữ vẫn cần xem lại trong bản nháp.
- Sau khi **kiểm tra partition gốc**, làm sạch segment rồi dựng lại `full_voice_script`. Lưu nhất quán vào draft, plan version, scene và sự kiện chat theo luồng hiện có. Không dùng làm sạch để che mất lỗi phân chia nội dung.
- Nếu lời thoại gốc còn placeholder trong ngoặc, scene giữ cờ `unresolved_voice_placeholder`; bỏ ngoặc không khiến kịch bản được duyệt/gửi đi trả phí. Bước đọc ảnh vẫn được kích hoạt để hoàn thiện nội dung, và chỉ gỡ cờ khi cảnh được sửa bằng lời thoại không còn placeholder.
- Áp dụng cho planner, chỉnh theo ảnh, sửa cảnh lỗi, payload gửi Veo và expected text của ASR. Lời thoại chỉ còn ký hiệu bị chặn trước khi tạo video. Khi sửa video lỗi, không ghi lại hoặc làm sạch cảnh đã hoàn thành trước đó.

Mỗi cảnh native vẫn nhận cùng profile và quy tắc nhịp 3,2–3,6; nhịp mới ưu tiên hơn nhãn slow/moderate của profile cũ. Giữ âm thanh gốc Veo và khẩu hình, không thay TTS, không tăng tốc audio riêng bằng FFmpeg. Giữ giới hạn prompt 4.000 ký tự và không cắt lời thoại để vừa giới hạn.

Để áp dụng đầy đủ, khởi động lại AI service/backend và **lập bản nháp mới**. Bản nháp cũ không tự được viết thêm lời; video đã hoàn thành không bị tạo lại. Hướng dẫn prompt không phải voice ID và không bảo đảm provider giữ chính xác một chất giọng qua các lượt sinh độc lập; cần nghe output mới để đánh giá kết quả thực tế.

Kiểm chứng lần cập nhật này: **172 test AI service**, **164 test backend** passed. Backend chạy `python -m pytest tests -q -o addopts= --ignore=tests/test_video_worker.py`, không chạy riêng test Celery worker vì dependency Celery không nằm trong Python mặc định. Test dùng mock API và media tạm, không tạo video trả phí hoặc cập nhật DB thật. Bao gồm bảo toàn dấu/ý nghĩa số và đơn vị, chuẩn hóa lặp lại không đổi kết quả, partition trước/sau làm sạch, placeholder trước khi duyệt/submit, không sửa cảnh đã hoàn tất, expected ASR khớp lời thoại sạch, shared voice/nhịp qua ba cảnh và giới hạn prompt dài.

Ruff trên các file Python liên quan passed, với ngoại lệ CLI `F401/I001` cho `platform_shared.py` vốn là module re-export các import dùng ở mixin khác; không xóa các import đó hoặc đổi cấu hình lint của project. Compile Python và diff/whitespace check passed. Không đổi model, API key, tunnel, thời lượng hay cơ chế billing; không thêm dependency.
