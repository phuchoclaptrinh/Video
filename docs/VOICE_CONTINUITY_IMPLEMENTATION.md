# Báo cáo triển khai Voice.md

Phần capability, provenance video thật, retry downstream và trạng thái đã được cập nhật tiếp trong [báo cáo PoYo continuation](POYO_CONTINUATION_AUDIT.md). Kết quả test ở mục 10 bên dưới là của đợt triển khai Voice.md trước đó.

## 1. Phạm vi và giới hạn

Đã sửa trực tiếp pipeline tạo video nhiều cảnh. Với kịch bản mới dùng PoYo hoặc Seedance 1.5 Pro, hình, tiếng và khẩu hình vẫn do model video tạo. Không thay native audio bằng TTS bên ngoài.

**Không thể bảo đảm cùng một chất giọng tuyệt đối bằng API đang tích hợp.** Các adapter hiện tại chưa có đầu vào nối video trước hoặc tham chiếu giọng. Frame, ảnh nhân vật và prompt chỉ là hướng dẫn best-effort, không phải voice ID. `validation_status=PASSED` có nghĩa không phát hiện lỗi chặn trong các phép kiểm tra đang có, không có nghĩa đã xác minh cùng khuôn mặt hoặc cùng giọng. `continuity_status=BEST_EFFORT` thể hiện giới hạn đó.

Không tự tạo profile giọng, không gửi `parent_generation_id` hay `continuation_generation_id` thành tham số provider không được hỗ trợ.

## 2. Khả năng provider hiện tại

| Adapter/model | Cách nối hiện đã tích hợp | Native audio cho kịch bản mới | Giới hạn |
| --- | --- | --- | --- |
| PoYo `veo3.1-fast` | Cảnh đầu dùng ảnh chung; mặc định `adaptive`, cảnh sau dùng ba ảnh: nhân vật, bối cảnh, frame cuối cảnh trước | Giữ tiếng trong video đầu ra | Ba ảnh là reference mode, không phải bảo đảm khung đầu chính xác hoặc nối audio |
| PoYo `veo3.1-quality` | Frame cuối cảnh trước làm ảnh mở đầu | Giữ tiếng trong video đầu ra | Không dùng reference mode ba ảnh |
| PoYo `veo3.1-lite` | Không có image continuity | Không phù hợp pipeline nhiều cảnh cần ảnh nối | Chặn reference không được hỗ trợ trước khi gửi |
| Seedance `seedance-1-5-pro*` | Frame cuối cảnh trước làm `first_frame` | Gửi `generate_audio=true` | Không gửi video/audio reference cho adapter 1.5 |
| Seedance model khác | Giữ đường tạo video cũ | Kịch bản mới có thoại bị chặn nếu model không hỗ trợ native audio | Không tự chuyển sang TTS |
| `fake` | Giữ mô phỏng hiện tại | Không có phép kiểm chứng giọng thật | Không thể dùng kết quả fake để đánh giá chất lượng model |

**Native video continuation thực sự:** chưa được triển khai cho adapter/model hiện tại. Đây là kết luận về hợp đồng API đang dùng, không phải khẳng định mọi sản phẩm của provider đều thiếu continuation.

PoYo công bố clip 8 giây, image input cho fast/quality, reference mode ba ảnh cho fast và không hỗ trợ image input ở lite. Hợp đồng này chưa mô tả đầu vào nối video hoặc giọng: [PoYo VEO 3.1 API](https://docs.poyo.ai/api-manual/video-series/veo-3-1).

Seedance 1.5 có tham số native audio và first/last frame; những khả năng ở dòng model khác không tự động áp dụng cho adapter 1.5: [BytePlus CreateContentsGenerationsTasks](https://api.byteplus.com/api-explorer/?action=CreateContentsGenerationsTasks&groupName=Chat+API&serviceCode=ark&version=2024-01-01), [tài liệu tạo video](https://docs.byteplus.com/ko/docs/modelark/create-video-generation-task-api).

## 3. Pipeline sau khi sửa

```text
Người dùng duyệt kịch bản
  -> Chuẩn bị ảnh nhân vật/bối cảnh chung
  -> Tạo cảnh 1 với native audio
  -> Chờ COMPLETED và lưu video
  -> Kiểm tra video/audio giải mã được
  -> Kiểm tra lời thoại và đo im lặng/âm lượng
  -> Lưu cửa sổ thời gian sẽ giữ lại
  -> Lấy frame cuối trong cửa sổ đó
  -> Kiểm tra hình và cập nhật continuity metadata
  -> validation PASSED: tạo cảnh 2 từ frame cảnh 1, continuity BEST_EFFORT
  -> FAILED: dừng, không tạo cảnh phụ thuộc
  -> Lặp lại cho từng cảnh
  -> Cắt hình/tiếng cùng cửa sổ, nối native audio
  -> Kiểm tra file hoàn chỉnh và lưu kết quả
```

`_validate_scene_parent()` chặn gọi cảnh sau nếu thiếu parent, parent không đúng cảnh/người dùng, job chưa hoàn tất hoặc kiểm tra parent đang chờ/thất bại. Kiểm tra diễn ra trước enqueue job mới. Dữ liệu cũ chưa có metadata vẫn đọc được; pipeline xử lý theo nhánh tương thích hiện tại.

`fake` vẫn mô phỏng bằng fixture; không coi các job fake là phép thử nối video thật. Batch cũ giữ `audio_mode` đã lưu, kể cả nhánh narration cũ, không tự thay đổi trong lúc xử lý.

## 4. Planner và prompt

- Một `voice_bible` toàn cục được dùng nguyên hướng dẫn cho mọi cảnh: ngôn ngữ, vùng giọng, giới tính, tuổi giọng, âm sắc, cách nói, nhịp độ, cảm xúc, đặc điểm phát âm.
- Planner nhận được cả object mô tả giọng và string cũ. Object được chuẩn hóa thành string, nên không đổi kiểu dữ liệu frontend đang dùng.
- Cảnh sau bắt đầu prompt bằng `CONTINUE THE PREVIOUS VIDEO`.
- Đoạn cuối thoại trước được đưa vào dưới nhãn context-only: không nói lại, không mở lời giới thiệu mới.
- Cho phép lời thoại là một mệnh đề tiếp nối; không yêu cầu mỗi cảnh kết thúc một câu độc lập. Validator chấp nhận đoạn 4-50 token tách bằng khoảng trắng; planner vẫn phải chọn lượng thoại phù hợp 8 giây.
- Frame trước là hướng dẫn trạng thái/pose hiện tại, ảnh nhân vật hướng dẫn identity, ảnh bối cảnh hướng dẫn style. Không tự đổi người nói, đạo cụ hoặc góc máy.
- `start_state`, `end_state`, `transition_intent` được đưa vào prompt và lưu metadata.
- Với thay đổi shot có chủ đích, prompt cho phép thay đổi theo kịch bản thay vì đồng thời yêu cầu khóa camera.

Transition được planner chọn: `continue`, `hard_cut`, `match_cut`, `intentional_camera_transition`, `fade_to_black`. Hiện `hard_cut` và `match_cut` đều nối trực tiếp; chất lượng match-cut phụ thuộc trạng thái đã lập kế hoạch và output model, chưa có bộ tự căn pose/hành động. Chỉ `fade_to_black` thêm fade hình ngắn 0,15 giây; không crossfade tiếng.

## 5. Kiểm tra âm thanh

| Kiểm tra | Xử lý hiện tại |
| --- | --- |
| Không có audio giải mã được | FAILED, không tạo cảnh sau |
| ASR không nghe được thoại, lệch nhiều so với script | FAILED |
| Thiếu đáng kể phần thoại dự kiến | FAILED |
| Lặp từ/cụm từ trong cảnh | FAILED khi bộ so sánh phát hiện lặp ngoài script |
| Cuối cảnh trước bị nói lại ở đầu cảnh sau | FAILED khi phát hiện cụm trùng không có trong script |
| Im lặng đầu/cuối, tốc độ/âm lượng thay đổi | Đo, lưu metrics và WARN khi vượt ngưỡng |
| Không gọi được ASR hoặc chưa có key planner | WARN, không giả vờ đã kiểm tra thoại |
| Cùng speaker/chất giọng, cùng accent | Chưa có bộ xác minh đáng tin cậy; WARN `native_voice_identity_unverified` |
| Bị cắt phụ âm/âm tiết ở biên | Kiểm tra thiếu thoại qua ASR và giữ đệm; chưa có forced alignment để xác định chắc chắn |

ASR dùng endpoint có sẵn và key planner đã lưu. Đây là nhận dạng tiếng nói, không phải tổng hợp tiếng nói. Nó có thể phát sinh phí và thêm thời gian xử lý.

Thống kê `active_audio_seconds`, `active_volume_db`, tốc độ nói là **ước lượng từ tín hiệu âm thanh**, không phải speaker embedding hoặc VAD riêng cho lời nói. Ambience/nhạc nền có thể làm sai lệch ước lượng; không dùng chúng để kết luận đổi người nói. ASR cũng có thể nghe sai, vì vậy quality gate không bảo đảm phát hiện mọi lỗi.

Kiểm tra hình hiện tại dựa trên chênh lệch pixel toàn frame, không phải nhận dạng khuôn mặt. Khi có chuyển shot được planner đánh dấu, bỏ kết luận FAILED chỉ dựa trên pixel và ghi WARN chưa xác minh identity.

## 6. Nối hình và tiếng

- Phân tích near-silence ở ngưỡng -45 dB; giữ đệm 120 ms quanh tín hiệu.
- Chỉ bỏ khoảng đầu/cuối đủ dài; không xóa khoảng im lặng bên trong câu và không xóa toàn clip im lặng để che lỗi.
- Dùng cùng `start/end` cho FFmpeg `trim` và `atrim`, reset timestamp rồi concat. Không dời audio riêng so với khẩu hình.
- Frame dùng nối cảnh được lấy từ phần hình thực sự giữ lại, không lấy pose trong đoạn đuôi đã bị bỏ.
- Không kéo dài/co giãn tiếng, không đổi pitch, không crossfade dài làm chồng từ.
- Điều chỉnh gain hằng số theo clip đầu, giới hạn khoảng +/-6 dB và tránh vượt peak -1 dB; không đổi danh tính giọng.
- Thời lượng video hoàn chỉnh là thời lượng sau cắt, không bắt buộc bằng số cảnh nhân 8 giây.

Nếu có ambience xuyên suốt ở đuôi, phép near-silence không xem đó là im lặng; đoạn đó có thể vẫn được giữ. Muốn tự loại khoảng không có lời nói nhưng vẫn có ambience cần bổ sung VAD/word alignment, không nên cắt mù.

## 7. Metadata và retry

Thêm một cột JSON `continuity_metadata` vào bảng SQLite MVP `platform_video_scenes`. Startup tự thêm cột cho DB cũ theo pattern migration hiện có; không xóa hoặc tạo lại bảng. `payload` và schema planner/frontend vẫn giữ nguyên.

Metadata gồm:

```json
{
  "generation_id": "scene-job-id",
  "parent_generation_id": "previous-scene-job-id",
  "continuation_generation_id": null,
  "previous_scene_id": "previous-scene-row-id",
  "next_scene_id": "next-scene-row-id",
  "start_state": "...",
  "end_state": "...",
  "voice_script": "...",
  "voice_bible": "...",
  "reference_mode": "image_references",
  "native_continuation": false,
  "voice_identity_verified": false,
  "accent_verified": false,
  "validation_status": "PASSED",
  "continuity_status": "BEST_EFFORT",
  "voice_continuity_status": "BEST_EFFORT",
  "continuity_error": null,
  "retry_count": 0
}
```

`parent_generation_id` là ID nội bộ để xác minh phụ thuộc, không phải ID gửi vào continuation API. `continuation_generation_id=null` vì adapter chưa dùng API continuation thật.

Khi cảnh lỗi, giữ các scene thành công trước đó; retry cảnh lỗi bằng frame parent và global voice bible. Cảnh đã render nhưng bị quality gate từ chối nhận request ID mới khi người dùng xác nhận tạo lại. Không tự trả phí lặp lại không giới hạn. Frontend vẫn sử dụng luồng duyệt/retry hiện tại.

Tham số audio mới mặc định false ở schema nội bộ; khi false, response canonical của `/validate` bỏ trường mới để tránh đổi fingerprint idempotency của request cũ. Chỉ giữ trường khi chủ động bật native audio.

## 8. File mới trong lần triển khai này

| File | Vai trò |
| --- | --- |
| `backend/app/services/video_continuity.py` | Strategy theo model, reference hợp lệ, global voice instruction, scene prompt |
| `backend/app/services/platform_scene_continuity.py` | Chặn dependency sai, lưu provenance và retry, đọc cửa sổ frame |
| `backend/app/services/native_audio_quality.py` | So sánh thoại, lặp xuyên cảnh, silence window, gain |
| `backend/app/services/platform_native_audio.py` | Native audio review, ASR, signal metrics, quality reports |
| `backend/tests/test_video_continuity_strategy.py` | Test strategy, thoại nối, transition, trim, gain |
| `docs/VOICE_CONTINUITY_IMPLEMENTATION.md` | Báo cáo này |

## 9. File hiện có đã sửa

| File | Thay đổi |
| --- | --- |
| `backend/app/services/platform.py` | Kết nối mixin, migration metadata |
| `backend/app/services/platform_shared.py` | Khai báo cột JSON |
| `backend/app/services/platform_generation.py` | Dùng strategy/prompt, kiểm tra parent trước enqueue, bật audio Seedance |
| `backend/app/services/platform_story_preparation.py` | Native audio cho batch mới, trạng thái đầu, retry quality failure, thời lượng thực |
| `backend/app/services/platform_story_render.py` | Validation trước cảnh phụ thuộc, lấy frame sau review, trim/gain/concat native audio |
| `backend/app/services/platform_continuity.py` | Frame theo cửa sổ đã giữ, không kết luận face identity từ shot đổi có chủ đích |
| `backend/app/services/platform_projects.py` | Cho sửa cảnh thiếu/lỗi native audio |
| `backend/tests/test_platform.py` | Dependency, retry riêng, ASR, FFmpeg, migration, no-TTS |
| `ai-service/app/pipelines/story_planner.py` | Voice bible object/string tương thích, thoại mệnh đề tiếp nối |
| `ai-service/app/prompts/video_story_planner.md` | Giọng toàn cục, câu liên tục, camera/transition |
| `ai-service/app/pipelines/reference_grounding.py` | Giữ thoại liên tục khi chỉnh theo ảnh |
| `ai-service/app/schemas/generation.py` | Native audio opt-in nội bộ |
| `ai-service/app/providers/seedance_compiler.py` | Compile tham số native audio có kiểm tra model |
| `ai-service/app/pipelines/video_api.py` | Canonical validation tương thích fingerprint cũ |
| `ai-service/tests/test_video_execution.py` | Voice bible, thoại nối, audio contract, canonical shape |
| `frontend/src/components/chat/VideoEventCard.tsx` | Nhãn tiếng Việt cho lỗi/cảnh báo và retry thiếu audio |
| `frontend/src/types/video-event.ts` | Metrics chấp nhận boolean kiểm chứng |

Không xóa provider, không sửa key/tunnel, không sửa `Voice.md`, không tạo video trả phí trong đợt kiểm tra này. Worktree có nhiều thay đổi từ trước; danh sách trên chỉ là phạm vi của yêu cầu này, không đại diện toàn bộ `git status`.

## 10. Kiểm chứng

Lệnh backend chạy trong thư mục `backend`:

```powershell
python -m pytest tests --ignore=tests/test_video_worker.py -q -o addopts= -x
```

Kết quả lượt đầy đủ: **82 passed**. Test mới bổ sung sau lượt đó kiểm tra chặn model không hỗ trợ audio cũng đã **passed**; đã chạy lại cả hai trường hợp chuẩn bị native audio PoYo/Seedance. Tổng cộng 83 test backend khác nhau đã kiểm chứng, chưa bao gồm Celery worker.

AI service, chạy trong `ai-service`:

```powershell
python -m pytest tests -q -o addopts= -x
```

Kết quả: **39 passed**.

Frontend: `npx.cmd tsc --noEmit` thành công. `python -m compileall -q backend/app ai-service/app` thành công. `git diff --check` không có lỗi whitespace trong file tracked; có cảnh báo CRLF vốn có.

Test bao gồm: cảnh đầu, cảnh nối, parent chưa hoàn tất/chưa qua validation, ownership, ảnh parent, retry cảnh đã hoàn thành nhưng lỗi chất lượng, thoại nối và lặp xuyên cảnh, structured voice bible tương thích string, silence/gain, ghép FFmpeg thật và đồng bộ thời lượng audio/video, frame từ cửa sổ đã cắt, giữ dữ liệu khi migration SQLite.

**Chưa chạy được:** `tests/test_video_worker.py` lỗi collection vì `ModuleNotFoundError: celery`. Môi trường cũng chưa có Ruff. Không gọi PoYo/Seedance trả phí để chứng minh chất giọng thực tế; unit/integration test không thay thế việc xem và nghe video thật.

## 11. Cách áp dụng và thử thật

1. Khởi động lại backend và AI service để nạp code/schema mới; tải lại trang chat.
2. Giữ PoYo model hỗ trợ ảnh và HTTPS tunnel tới backend đúng như cấu hình hiện tại. Không cần thêm TTS key.
3. Tạo kịch bản mới 2-3 cảnh với cùng nhân vật, một bối cảnh và một vùng giọng rõ ràng. Duyệt rồi mới tạo.
4. Theo dõi cảnh 1 hoàn tất và qua review trước khi cảnh 2 được gửi. Kiểm tra metadata/reference của scene sau trỏ đúng parent.
5. Nghe các chỗ nối; kiểm tra đủ thoại, không lặp từ cuối cảnh trước, không lệch khẩu hình. Quality report nói rõ nếu chưa xác minh cùng giọng.
6. Nếu scene bị FAILED, chỉnh hoặc retry riêng scene đó qua luồng xác nhận phí hiện tại. Giữ nguyên các scene đã qua kiểm tra trước nó.

Với PoYo, có thể chọn rõ `POYO_CONTINUITY_MODE=first_frame` để dùng frame cuối làm ảnh mở đầu thay vì ba reference; mặc định `adaptive` giữ cả ảnh identity/style ở cảnh sau của fast. Cả hai đều là image guidance, không phải audio continuation hoặc voice locking.
