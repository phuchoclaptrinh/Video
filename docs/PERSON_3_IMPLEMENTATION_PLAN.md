# Kế hoạch code của Người 3

## 1. Mục tiêu

Xây dựng pipeline bất đồng bộ nhận `GenerationSpec`, validate, compile sang request Seedance, submit/poll/cancel qua provider adapter và trả kết quả chuẩn hóa. Pipeline phải có retry giới hạn, idempotency và khả năng khôi phục sau khi worker restart.

## 2. Nguyên tắc triển khai

- Hoàn thành luồng với fake provider trước khi gọi API tính phí.
- Ổn định schema và interface trước khi tích hợp Seedance.
- Dữ liệu riêng của provider chỉ tồn tại trong compiler/adapter.
- Mỗi bước phải có đầu vào, đầu ra và lỗi rõ ràng.
- Lưu `provider_task_id` sớm nhất có thể sau submit.
- Không submit lại nếu chưa xác định được task cũ có tồn tại hay không.
- Unit test logic thuần, mock HTTP provider và integration test queue.

## 3. Cấu trúc code dự kiến

```text
ai-service/app/
|-- core/
|   |-- config.py
|   `-- exceptions.py
|-- schemas/
|   |-- generation.py
|   `-- provider.py
|-- providers/
|   |-- base.py
|   |-- registry.py
|   |-- fake.py
|   `-- seedance.py
|-- prompt_engine/
|   `-- seedance_compiler.py
|-- pipelines/
|   `-- video_generation.py
`-- main.py

backend/app/
|-- services/
|   |-- ai_service_client.py
|   `-- generation_orchestrator.py
|-- workers/
|   |-- celery_app.py
|   `-- video_tasks.py
`-- main.py
```

## 4. Phase 0 - Chốt contract

### Công việc

- Chốt `GenerationSpec` với Người 2.
- Chốt asset contract với Người 1.
- Chốt generation status và state transition với Người 1.
- Chốt normalized output với Người 4.
- Xác định capability của model Seedance sẽ dùng.

### Đầu ra

- JSON mẫu cho text-to-video và image-to-video.
- JSON mẫu cho normalized success và error.
- Danh sách enum và validation rule.

### Điều kiện hoàn thành

- Cả nhóm sử dụng cùng tên field và status.
- Contract không chứa field riêng của provider.

## 5. Phase 1 - Schema, validation và capability

### File

- `ai-service/app/schemas/generation.py`
- `ai-service/app/schemas/provider.py`
- `ai-service/app/core/exceptions.py`

### Công việc

1. Tạo Pydantic model cho asset reference và `GenerationSpec`.
2. Tạo enum aspect ratio, resolution, generation mode và provider status.
3. Viết field validation và cross-field validation.
4. Tạo normalized task/result/error schema.
5. Tạo capability model cho từng provider/model.
6. Viết hàm kiểm tra spec với capability.

### Test bắt buộc

- Prompt rỗng bị từ chối.
- Duration không hỗ trợ bị từ chối.
- Image-to-video thiếu image bị từ chối.
- Text-to-video không bắt buộc image.
- Sai MIME type hoặc media type bị từ chối.
- Tổ hợp model/resolution/aspect ratio không hỗ trợ bị từ chối.

## 6. Phase 2 - Provider interface, registry và fake provider

### File

- `ai-service/app/providers/base.py`
- `ai-service/app/providers/registry.py`
- `ai-service/app/providers/fake.py`

### Công việc

1. Định nghĩa abstract adapter interface.
2. Định nghĩa các provider exception.
3. Viết provider registry theo tên provider.
4. Viết fake provider lưu task trong memory cho unit test.
5. Mô phỏng `queued -> processing -> completed`.
6. Hỗ trợ fake failure, timeout và cancellation.
7. Normalize fake response bằng cùng schema với Seedance.

### Test bắt buộc

- Registry trả đúng adapter.
- Submit trả task ID.
- Poll chuyển trạng thái đúng.
- Cancel chuyển sang trạng thái kết thúc.
- Failure được normalize.
- Provider không hỗ trợ spec phải fail trước submit.

### Kết quả cần demo

```text
GenerationSpec -> FakeVideoProvider -> polling -> NormalizedVideoResult
```

## 7. Phase 3 - Video generation pipeline

### File

- `ai-service/app/pipelines/video_generation.py`
- `ai-service/app/main.py`

### Công việc

1. Nhận spec từ internal API.
2. Validate và capability check.
3. Chọn adapter từ registry.
4. Compile request.
5. Submit hoặc poll dựa trên `provider_task_id`.
6. Normalize status/result/error.
7. Tạo internal endpoint cho submit, status và cancel.
8. Bảo vệ endpoint bằng `INTERNAL_API_KEY`.

### Endpoint dự kiến

```text
POST /internal/v1/video-tasks
GET  /internal/v1/video-tasks/{provider_task_id}
POST /internal/v1/video-tasks/{provider_task_id}/cancel
```

### Test bắt buộc

- Authentication thiếu hoặc sai.
- Submit spec hợp lệ.
- Validation error trả lỗi chuẩn hóa.
- Status endpoint trả normalized status.
- Cancel có tính idempotent.

## 8. Phase 4 - Seedance compiler và adapter

### File

- `ai-service/app/prompt_engine/seedance_compiler.py`
- `ai-service/app/providers/seedance.py`
- `ai-service/app/core/config.py`

### Công việc compiler

1. Chọn model theo mode và capability.
2. Chuyển prompt/negative prompt thành payload Seedance.
3. Ánh xạ duration, aspect ratio và resolution.
4. Đưa reference image vào request image-to-video.
5. Không ghi API key hoặc signed URL vào log.

### Công việc adapter

1. Tạo HTTP client có timeout và authentication.
2. Implement `submit`, `get_status` và `cancel`.
3. Ánh xạ provider status sang internal status.
4. Ánh xạ provider error sang exception taxonomy.
5. Normalize output URL, metadata và usage/cost.

### Test bắt buộc

- Payload text-to-video và image-to-video đúng.
- API key không xuất hiện trong log/error.
- 400, 401/403, 429, 500/503 và timeout được phân loại đúng.
- Tất cả provider status được map.
- Response thiếu field bắt buộc tạo permanent provider error.

### Điều kiện hoàn thành

- Mocked API tests pass.
- Chạy được smoke test text-to-video và image-to-video thật.
- Hai luồng trả cùng normalized schema.

## 9. Phase 5 - Backend queue và worker

### File

- `backend/app/workers/celery_app.py`
- `backend/app/workers/video_tasks.py`
- `backend/app/services/ai_service_client.py`
- `backend/app/services/generation_orchestrator.py`

### Công việc

1. Cấu hình Celery bằng Redis và tạo queue `video`.
2. Orchestrator tạo generation rồi enqueue task.
3. Worker tải generation từ database.
4. Bỏ qua generation ở trạng thái kết thúc.
5. Submit nếu chưa có `provider_task_id`.
6. Lưu `provider_task_id` trong transaction.
7. Poll task cũ nếu đã có task ID.
8. Cập nhật status và normalized result.
9. Chuyển output sang post-processing.

### State transition dự kiến

```text
READY -> QUEUED -> SUBMITTED -> GENERATING -> POST_PROCESSING -> COMPLETED
                                     |-> FAILED | CANCELLED | BLOCKED
```

### Test bắt buộc

- API trả `202` và generation ID.
- Worker có task ID thì poll, không submit lại.
- Generation kết thúc không được xử lý lại.
- Hai worker không submit cùng generation.
- Fake provider chạy end-to-end qua API, Redis, Celery và database.

## 10. Phase 6 - Retry, timeout và cancellation

### Công việc

1. Tạo retry classifier dùng chung.
2. Thêm exponential backoff và jitter.
3. Giới hạn số lần retry và tổng thời gian.
4. Phân biệt submit retry và polling retry.
5. Không retry validation, policy và permanent error.
6. Retry 429 và temporary provider error.
7. Khi submit timeout, kiểm tra task cũ trước khi submit mới.
8. Worker kiểm tra cancellation trước submit và mỗi lần poll.

### Cấu hình dự kiến

```text
PROVIDER_MAX_RETRIES=3
PROVIDER_RETRY_BACKOFF_SECONDS=5
PROVIDER_POLL_INTERVAL_SECONDS=10
PROVIDER_JOB_TIMEOUT_SECONDS=900
```

### Test bắt buộc

- 400 không retry.
- 429 và 500/503 retry có giới hạn.
- Poll timeout không tạo task mới.
- Cancel trước submit không gọi provider.
- Cancel sau submit dừng poll và gọi provider cancel nếu hỗ trợ.

## 11. Phase 7 - Idempotency và recovery

### Công việc

1. Tạo unique idempotency key cho generation.
2. Thêm database constraint chống duplicate submit record.
3. Dùng distributed lock hoặc atomic state transition.
4. Thêm recovery task tìm generation bị kẹt.
5. Poll lại provider task sau worker restart.
6. Cảnh báo nếu submit timeout mà không xác định được provider task.

### Tình huống cần chứng minh

```text
Worker crash trước submit        -> worker mới có thể submit
Worker crash sau khi lưu task ID -> worker mới tiếp tục poll
Worker crash sau provider submit
nhưng trước khi lưu task ID      -> không tự submit lại nếu có nguy cơ tính phí trùng
```

Nếu Seedance không hỗ trợ idempotency key hoặc query theo client request ID, generation cần chuyển sang trạng thái chờ đối soát thay vì submit lại.

### Test bắt buộc

- Duplicate Celery delivery không tạo duplicate provider task.
- Hai worker chạy đồng thời chỉ một worker submit.
- Worker restart tiếp tục poll task cũ.
- Recovery task không chạm vào generation đã kết thúc.

## 12. Phase 8 - Observability và cost tracking

- Structured log với `generation_id`, `provider`, `provider_task_id`, `attempt` và `status`.
- Không log credential, signed URL đầy đủ hoặc nội dung nhạy cảm.
- Theo dõi submit count, completion count, failure count, retry count và latency.
- Ghi usage/cost estimate theo generation.
- Thêm health check cho Redis, worker và AI service.
- Thêm correlation ID xuyên backend, worker và AI service.

## 13. Kế hoạch theo sprint

### Sprint 1 - Nền tảng và fake flow

- Chốt contract.
- Schema, validator và capability checker.
- Provider interface, registry và fake provider.
- Pipeline và Celery worker skeleton.

Demo:

```text
API -> queue -> worker -> fake provider -> polling -> normalized result
```

### Sprint 2 - Seedance thật

- Seedance compiler và HTTP adapter.
- Text-to-video và image-to-video.
- Polling, normalized result và mocked provider tests.

Demo:

```text
Text/Image + prompt -> Seedance -> video result
```

### Sprint 3 - Production hardening

- Retry classification, timeout và cancellation.
- Idempotency, distributed locking và provider task recovery.
- Cost tracking, logging, metrics và integration tests.

Demo:

```text
Worker restart/timeout/retry -> khôi phục task cũ -> không tính phí trùng
```

## 14. Thứ tự làm trong 5 ngày đầu

| Ngày | Công việc | Kết quả |
| --- | --- | --- |
| 1 | Chốt contract và status | Sample JSON và enum đã review |
| 2 | Schema, validation, capability | Unit tests pass |
| 3 | Provider interface, registry, fake provider | Fake submit/poll/cancel pass |
| 4 | Pipeline và internal API | Fake flow chạy trong AI service |
| 5 | Celery worker skeleton | Demo fake end-to-end qua queue |

## 15. Checklist trước khi merge

- [ ] Code không để lộ API key trong log.
- [ ] `GenerationSpec` không có provider-specific field.
- [ ] Validation và capability check chạy trước submit.
- [ ] Adapter trả schema nội bộ, không trả raw response.
- [ ] Retry có giới hạn.
- [ ] `provider_task_id` được lưu và tái sử dụng.
- [ ] Duplicate task delivery không duplicate submit.
- [ ] Cancellation có tính idempotent.
- [ ] Trạng thái kết thúc không bị ghi đè.
- [ ] Unit test và integration test liên quan đều pass.
- [ ] Có tài liệu config và sample environment variables.

## 16. Các quyết định cần hỏi nhóm sớm

1. Database schema của generation và provider task do ai chốt?
2. Seedance có hỗ trợ idempotency key hoặc client request ID không?
3. Seedance có endpoint cancel thật hay chỉ dừng polling nội bộ?
4. AI service tự poll hay backend Celery worker điều phối từng lần poll?
5. Output URL có hết hạn không và ai tải video về object storage?
6. Cost do provider trả về hay phải ước tính từ model/duration/resolution?
7. Trạng thái nào là terminal và ai có quyền thay đổi từng trạng thái?

