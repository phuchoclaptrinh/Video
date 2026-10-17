# Kiểm tra và triển khai chuỗi cảnh PoYo Veo 3.1 Fast

Đây là báo cáo đợt triển khai trước. Refactor mới về `sound`, model official tùy cấu hình, duration theo capability, voice modes và điểm cắt/frame dùng chung được ghi tại [VOICE_AND_BOUNDARY_REFACTOR.md](VOICE_AND_BOUNDARY_REFACTOR.md). Không tự đổi model đang chạy; các kết luận không có native video continuation cho fast thường vẫn giữ nguyên.

## 1. Kết luận quan trọng

Giữ nguyên provider **PoYo**, model **`veo3.1-fast`** và tiếng nói do model video tạo. Không đổi sang model official, không thêm TTS, không sửa key hoặc tunnel.

**Chưa xác minh được hợp đồng native video continuation cho model này.** Tài liệu hiện đọc được chưa chỉ ra endpoint nối video, trường đầu vào nhận video trước, thời lượng extension hay cách giữ voice identity. Vì vậy không thêm tham số phỏng đoán vào request trả phí, và không đổi `native_continuation` thành `true` chỉ bằng một cờ cấu hình.

Đã sửa tầng kiến trúc để quản lý capability, đầu ra video thực tế, phụ thuộc tuần tự, retry và provenance. Với hợp đồng hiện tại, cảnh sau vẫn là request tạo clip mới có ảnh tham chiếu từ cảnh trước: **image fallback BEST_EFFORT, không phải provider-native continuation**. Chất giọng và khuôn mặt vẫn có thể thay đổi; thay đổi này không chứng minh đã giải quyết tuyệt đối hai vấn đề đó.

## 2. Hợp đồng API đã kiểm tra

### Model và request thực tế

Code đang dùng `PoyoVeoProvider` và `PoyoVeoCompiler`:

```text
POST api/generate/submit
GET  api/generate/status/{task_id}
Authorization: Bearer <key đã cấu hình>
```

Payload submit hiện tại:

```json
{
  "model": "veo3.1-fast",
  "input": {
    "prompt": "...",
    "duration": 8,
    "aspect_ratio": "16:9",
    "resolution": "720p"
  }
}
```

Khi có ảnh, compiler thêm `image_urls`. Hai ảnh dùng `generation_type=frame`; ba ảnh dùng `generation_type=reference`. Không thay đổi compiler trong đợt triển khai này. Các chế độ ảnh và clip 8 giây được mô tả trong [PoYo VEO 3.1 API](https://docs.poyo.ai/api-manual/video-series/veo-3-1).

Status provider được chuẩn hóa như sau:

| PoYo | Runtime |
| --- | --- |
| `not_started` | `queued` |
| `running` | `generating` |
| `finished` | `completed` |
| `failed` | `failed` |

Khi hoàn tất, adapter lấy `data.files[]` có `file_type=video`, chuyển `file_url` thành `ProviderResult.outputs[].source_url`. URL này là đầu ra video thực tế, không phải task ID. Xem [PoYo Task Status](https://docs.poyo.ai/api-manual/task-management/status).

### Không lấy hợp đồng của model khác để áp dụng

Trang [VEO 3.1 Official](https://docs.poyo.ai/api-manual/video-series/veo-3-1-official) mô tả họ model khác, ví dụ `veo3.1-fast-official`. Không lấy trường `sound` của họ official để gửi vào `veo3.1-fast`, không tự chuyển model để làm request trông giống continuation.

### Những phần chưa xác minh được

- Chưa tìm thấy extension contract cho đúng `veo3.1-fast` trong trang model, trang status và danh mục [llms.txt](https://docs.poyo.ai/llms.txt) đã đọc.
- Không tải được một số đường dẫn raw JSON/catalog trong lần kiểm tra này; yêu cầu đọc JSON bằng PowerShell cũng gặp lỗi kết nối TLS. Không coi việc thiếu tài liệu là bằng chứng mọi dịch vụ PoYo/Veo đều không hỗ trợ continuation.
- Không thực hiện request tạo video trả phí để thử một endpoint hoặc trường chưa được công bố.
- Capability `video_continuation=false` có nghĩa **chưa có hợp đồng đã xác minh và adapter đã triển khai**, không phải khẳng định về toàn bộ khả năng của nhà cung cấp.
- Nếu PoYo cung cấp hợp đồng extension chính thức sau này, cần triển khai riêng adapter, thời lượng và semantics đầu ra, rồi bổ sung test trước khi bật.

## 3. Capability là nguồn quyết định

Thêm endpoint nội bộ có xác thực:

```text
GET /internal/v1/video/{provider}/capabilities
X-Internal-Key: <key nội bộ>
```

Với adapter PoYo Fast hiện tại:

```json
{
  "provider": "poyo",
  "model": "veo3.1-fast",
  "native_audio": true,
  "first_frame": true,
  "reference_images": true,
  "video_continuation": false,
  "continuation_requires_video_url": false,
  "voice_identity_guaranteed": false,
  "generation_durations": [8],
  "continuation_durations": [],
  "continuation_output": "UNSUPPORTED",
  "reason": "video_continuation_not_documented_for_model"
}
```

Backend cache theo provider/model, lưu snapshot vào metadata. Nếu AI service cũ chưa có endpoint hoặc phản hồi sai định dạng, dùng capability fallback bảo thủ với nguồn `DOCUMENTED_FALLBACK`, có cảnh báo trong log. Nếu model hai service không khớp, chặn bằng `video_provider_model_mismatch`; không tự đổi sang quality hoặc official.

`SEGMENT` và `CUMULATIVE` chỉ là giá trị schema chuẩn bị cho hợp đồng tương lai. Chưa có nhánh extension nào phát sinh hai dạng này. Nếu chỉ sửa capability thành `video_continuation=true` mà chưa có adapter thật, strategy chặn bằng `native_continuation_adapter_not_implemented` trước enqueue.

## 4. Flow generation sau khi sửa

```text
Duyệt kịch bản có voice_bible toàn cục
  -> Kiểm tra model/capability và ảnh đầu vào
  -> G1: tạo cảnh 1, native audio
  -> Poll provider đến finished
  -> Download V1, lưu file và journal COMPLETED
  -> Lưu URL V1 + đường dẫn asset + thời lượng thực tế
  -> Kiểm tra hình/tiếng, lấy frame ở cuối cửa sổ được giữ lại
  -> Nếu không có lỗi chặn: cho phép cảnh 2
  -> G2: xác minh parent là G1 và asset V1 thực sự tồn tại
  -> Hiện tại: gửi ảnh từ V1 + ảnh nhân vật/bối cảnh, native audio
  -> Download V2, lưu kết quả và kiểm tra
  -> G3: parent là G2/V2, không mặc định dùng lại G1/V1
  -> Nối các clip đã qua kiểm tra, giữ đồng bộ hình/tiếng
  -> Kiểm tra file hoàn chỉnh
```

Backend yêu cầu parent đúng người dùng, đúng scene trước, journal đã `COMPLETED`, không đang chờ validation, không `FAILED/INVALIDATED`, có bản ghi video và file thật trong storage. **Một generation ID hoặc JPEG đã cache không đủ để thay thế đầu ra video bị mất.**

`ContinuityRequest` nhận `parent_video_url` và `parent_asset_path` từ đầu ra thật. Hiện URL được lưu để truy vết; vì API chưa xác minh nhận video, **không gửi URL này như video input tới PoYo**. Ảnh fallback được trích từ asset video đã tải về. Nếu URL provider không có, lưu đường dẫn API video nội bộ có xác thực với `output_url_kind=AUTHENTICATED_LOCAL`; không giả vờ URL đó công khai để provider truy cập.

Lifecycle journal và cơ chế xử lý submit không rõ kết quả vẫn giữ nguyên. Không tự submit lại request có nguy cơ đã trừ credit.

## 5. Voice continuity và trạng thái

`voice_bible` vẫn là hướng dẫn trong prompt: ngôn ngữ, accent, giới tính/tuổi giọng, âm sắc, cách nói, nhịp độ, cảm xúc và `pronunciation`. Thêm hỗ trợ trường pronunciation khi planner trả object; string cũ vẫn tương thích.

Không tạo voice ID từ text. Adapter hiện tại không có audio reference hoặc lời cam kết giữ speaker identity. Metadata phân biệt:

| Trạng thái | Ý nghĩa |
| --- | --- |
| `GUARANTEED` | Dành cho cơ chế có guarantee đã xác minh; hiện không gán trạng thái này |
| `BEST_EFFORT` | Native audio và hướng dẫn giọng toàn cục; chưa chứng minh cùng speaker |
| `UNSUPPORTED` | Không có native audio phù hợp hoặc không có khả năng continuity tương ứng |
| `FAILED` | Cảnh/chuỗi thất bại kiểm tra hoặc bị invalidated |

`validation_status=PASSED` chỉ nói cảnh qua những kiểm tra đang có. `continuity_status` sau bước đó là `BEST_EFFORT` hoặc `UNSUPPORTED`, không phải chứng nhận cùng mặt/cùng giọng. Các trạng thái vận hành `PENDING`, `WAITING_FOR_PARENT`, `GENERATING`, `VALIDATING`, `FAILED`, `INVALIDATED` vẫn được giữ.

Với kịch bản native mới, cờ nội bộ `native_audio=true` đi xuyên scene chain; prompt yêu cầu thoại khớp môi và merge giữ audio gốc. Không thêm trường `native_audio`, `sound` hoặc `generate_audio` chưa được xác minh vào payload PoYo. Seedance giữ cách bật native audio riêng đã có. Batch narration cũ không bị đổi audio mode giữa chừng.

Các phép kiểm tra ASR, lặp thoại, audio giải mã, silence/gain và frame từ đợt triển khai `Voice.md` vẫn được dùng. Chúng không phải speaker embedding hoặc face recognition và có thể bỏ sót lỗi. Xem [báo cáo Voice.md](VOICE_CONTINUITY_IMPLEMENTATION.md).

## 6. Metadata, duration và log

Dùng cột JSON `continuity_metadata` hiện có trong `platform_video_scenes`; không thêm migration trong đợt này. Các trường chính:

```text
generation_id, provider_task_id
parent_generation_id, parent_video_url, parent_asset_path
output_video_url, output_url_kind, output_asset_path
duration_seconds
native_audio, native_continuation, continuation_generation_id
reference_mode, continuity_mode, fallback_reason
provider_capabilities
validation_status, continuity_status, continuity_error
voice_continuity_status, voice_continuity_error
voice_identity_verified=false, accent_verified=false
retry_count, invalidated_generations
```

`duration_seconds` là thời lượng file clip đo được sau khi tải về, không cố định 8. Clip thông thường vẫn được yêu cầu 8 giây theo hợp đồng model; thời lượng dự kiến chưa phải thời lượng output. Video cuối được đo lại sau trim/merge. Không suy ra duration extension từ clip thường vì extension chưa có hợp đồng.

`continuation_generation_id=null` và `native_continuation=false` là đúng với fallback hiện tại. Không ghép mù các output cumulative: hiện chỉ tạo các segment thông thường. Để nhận extension cumulative trong tương lai cần bóc phần mới hoặc dùng output cuối theo hợp đồng cụ thể, kèm test riêng.

Log mới có story/scene/model, native audio, continuation, parent, lý do fallback và trạng thái voice. URL trong log chỉ giữ scheme/hostname, che toàn bộ path/query/userinfo và thêm hash nhận diện. URL đầy đủ và asset lưu trong DB để debug, không log key hoặc signed token.

## 7. Retry và invalidation

1. Tìm cảnh lỗi sớm nhất từ journal và metadata validation, không chỉ nhìn generation cuối batch.
2. Giữ nguyên các generation thành công trước cảnh lỗi.
3. Retry cảnh lỗi từ đúng output parent còn tồn tại, giữ global voice bible.
4. Xóa liên kết generation đang dùng của cảnh lỗi và các cảnh phía sau. Đánh dấu downstream `INVALIDATED`, lưu lịch sử generation/output cũ; không xóa file/journal trả phí.
5. Vô hiệu hóa quality report cũ của các cảnh bị ảnh hưởng và report final media. Scene mới không kế thừa kết quả kiểm tra đã lỗi thời.
6. Tạo lại downstream từ parent mới. Chỉ giữ prefix cũ không bị ảnh hưởng.

Nếu downstream còn chạy hoặc kết quả submit chưa rõ, chặn sửa/retry bằng `continuity_downstream_active`. Không bỏ job có thể đã tính phí chỉ để reset chain. Cần chờ đối soát/hoàn tất trước.

Giữ chính sách retry có sẵn: lỗi PoYo xác minh chưa tính phí, hoặc clip đã hoàn tất nhưng bị quality gate từ chối và người dùng xác nhận tạo lại. Không mở retry tự động cho mọi lỗi trả phí/ambiguous.

File native merge dùng hash của danh sách generation trong chain. Retry đổi generation sẽ đổi tên cache, tránh trả về video cũ. Nhánh narration cũ giữ naming tương thích.

## 8. File thay đổi trong yêu cầu này

### Backend

| File | Thay đổi |
| --- | --- |
| `backend/app/services/video_provider_capabilities.py` | File mới: capability bảo thủ và che URL trong log |
| `backend/app/services/video_execution_client.py` | GET capability nội bộ, không áp schema task lên capability |
| `backend/app/services/video_continuity.py` | Strategy đọc capability, giữ provenance output parent, chặn extension chưa triển khai |
| `backend/app/services/platform_scene_continuity.py` | Capability cache, output thật, validation/status, tìm lỗi và invalidate chain |
| `backend/app/services/platform_generation.py` | Truyền output parent, kiểm tra capability/duration, metadata và log |
| `backend/app/services/platform_execution.py` | Ghi output scene sau khi persistence hoàn tất hoặc phục hồi |
| `backend/app/services/platform_story_preparation.py` | Retry từ cảnh lỗi sớm nhất, giữ prefix, chặn downstream đang chạy |
| `backend/app/services/platform_story_render.py` | Ghi output/duration, merge cache theo generation chain |
| `backend/app/services/platform_projects.py` | Sửa đúng cảnh lỗi, không sửa scene trước đó hoặc bỏ downstream đang chạy |

### AI service

| File | Thay đổi |
| --- | --- |
| `ai-service/app/schemas/provider.py` | Schema capability; result task cũ không đổi |
| `ai-service/app/providers/base.py` | Method capability mặc định bảo thủ, không thêm abstract method bắt buộc |
| `ai-service/app/providers/poyo.py` | Capability theo đúng model compiler; không tạo endpoint extension |
| `ai-service/app/providers/seedance.py` | Capability của adapter hiện tại; fake không tuyên bố có voice thật |
| `ai-service/app/pipelines/video_api.py` | Endpoint capability có xác thực nội bộ |
| `ai-service/app/pipelines/story_planner.py` | Chuẩn hóa thêm pronunciation trong voice bible |
| `ai-service/app/prompts/video_story_planner.md` | Hướng dẫn pronunciation toàn cục |

### Tests và tài liệu

- `backend/tests/test_platform.py`: single scene, chain 3 scene, parent asset/URL, retry giữa chain, downstream active, model mismatch và cache.
- `backend/tests/test_video_continuity_strategy.py`: reject extension chỉ có cờ, giữ parent provenance, URL log không lộ secret.
- `backend/tests/test_video_execution_http.py`: GET capability qua AI service HTTP thật chạy loopback trong test.
- `ai-service/tests/test_video_execution.py`: xác thực/capability, payload PoYo chỉ dùng field đã có, fake không có native voice.
- `docs/POYO_CONTINUATION_AUDIT.md`: báo cáo này.
- `docs/VOICE_CONTINUITY_IMPLEMENTATION.md`: cập nhật phân biệt validation với continuity và liên kết báo cáo mới.

Không sửa frontend, endpoint public, compiler PoYo, cấu hình key hoặc DB sản xuất trong đợt này. Worktree có nhiều file thay đổi từ trước; danh sách trên không đại diện mọi mục trong `git status`.

## 9. Kết quả kiểm chứng

Backend, chạy trong `backend`:

```powershell
python -m pytest tests --ignore=tests/test_video_worker.py -q -o addopts= -x
```

**92 passed**, 4 cảnh báo deprecation, thời gian lượt cuối 173,06 giây. Không có test fail trong lượt này.

AI service, chạy trong `ai-service`:

```powershell
python -m pytest tests -q -o addopts= -x
```

**41 passed**. Các request provider trả phí được mock; không dùng test để khẳng định chất lượng giọng thực tế.

Frontend: `npx.cmd tsc --noEmit` passed. Python: `python -m compileall -q backend/app ai-service/app` passed. Ruff 0.16.3: `python -m ruff check` passed trên các file Python liệt kê ở mục 8, không phải lint toàn bộ repository. `git diff --check` không có lỗi whitespace trong file tracked; không đại diện toàn bộ các file untracked đã có trong workspace.

**Bị chặn bởi môi trường:** collection của `backend/tests/test_video_worker.py` lỗi `ModuleNotFoundError: celery`, chưa chạy test Celery worker. Không tự cài thêm dependency toàn cục.

Coverage chức năng của các test mới:

| Case | Kết quả |
| --- | --- |
| Một scene: 1 generation, native audio, không giả continuation | Pass |
| Ba scene: G1/V1 -> G2/V2 -> G3, tuần tự theo parent thật | Pass với image fallback; không phải test extension PoYo |
| Cảnh 2 lưu URL và asset V1, cảnh 3 lưu V2 | Pass |
| Native audio và voice bible đi xuyên chain | Pass |
| Retry G2 giữ G1, invalidate G3, G3 mới dùng V2 mới | Pass |
| Không có native extension: BEST_EFFORT, không GUARANTEED | Pass |
| Capability true nhưng chưa có extension adapter | Pass: chặn trước enqueue |
| Parent chỉ có JPEG cache nhưng mất video thật | Pass: chặn |
| Downstream còn chạy / backend-AI model không khớp | Pass: chặn, không đổi model hoặc bỏ job |
| Providers khác và API/frontend hiện có | Suite backend/AI và TypeScript pass; Celery chưa kiểm chứng |

## 10. Áp dụng và giới hạn còn lại

Khởi động lại **backend và AI service** để nạp code và endpoint capability mới. Không cần thêm key TTS. Kiểm tra capability trả đúng provider/model trước khi duyệt một job trả phí mới.

Chưa gọi PoYo trả phí và chưa nghe/xem video production trong đợt này. Các test xác nhận quản lý chain, provenance, fallback và retry; không xác nhận model giữ khuôn mặt/giọng trong mọi output.

Nếu yêu cầu bắt buộc là native continuation có giữ speaker identity, còn cần hợp đồng chính thức của PoYo cho đúng model: endpoint, loại video input, duration extension, output là segment hay cumulative, và guarantee audio identity. Không có các thông tin đó thì không thể triển khai đúng yêu cầu này chỉ bằng prompt hoặc gán `native_continuation=true`.
