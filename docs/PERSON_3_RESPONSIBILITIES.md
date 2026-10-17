# Nhiệm vụ của Người 3 - Video Generation và Async Orchestration

## 1. Vai trò tổng quan

Người 3 chịu trách nhiệm biến `GenerationSpec` đã được phê duyệt thành video thật thông qua Seedance, đồng thời bảo đảm quá trình sinh video chạy nền ổn định, có thể khôi phục và không tạo trùng các lần sinh video có tính phí.

Phạm vi bắt đầu khi nhận `GenerationSpec` hợp lệ từ Người 2 và kết thúc khi bàn giao kết quả provider đã được chuẩn hóa cho Người 4 hậu xử lý.

```text
GenerationSpec
    -> Validation và capability check
    -> Prompt compiler
    -> Provider adapter
    -> Queue và worker
    -> Seedance submit/poll/cancel
    -> Normalized result
    -> Người 4
```

## 2. Đầu vào và đầu ra

### Đầu vào

Người 3 nhận một `GenerationSpec` độc lập với provider:

```json
{
  "generation_id": "gen_123",
  "prompt": "A cinematic shot of a modern city at sunrise",
  "negative_prompt": "blur, watermark, distorted objects",
  "duration_seconds": 8,
  "aspect_ratio": "16:9",
  "resolution": "1080p",
  "reference_assets": [
    {
      "asset_id": "asset_123",
      "media_type": "image",
      "storage_uri": "s3://bucket/reference.png",
      "mime_type": "image/png"
    }
  ]
}
```

`GenerationSpec` không được chứa API key, endpoint, model ID hoặc raw request riêng của Seedance.

### Đầu ra

Kết quả Seedance phải được chuyển thành định dạng nội bộ thống nhất:

```json
{
  "provider": "seedance",
  "provider_task_id": "task_456",
  "status": "completed",
  "outputs": [
    {
      "media_type": "video",
      "source_url": "https://provider.example/video.mp4",
      "mime_type": "video/mp4",
      "duration_seconds": 8
    }
  ],
  "usage": {
    "estimated_cost": 0.0
  }
}
```

Không chuyển raw response của provider sang module khác.

## 3. Các nhiệm vụ chính

### 3.1. Validation `GenerationSpec`

Kiểm tra trước khi gửi request tốn phí:

- Prompt tồn tại và không rỗng.
- Duration, aspect ratio và resolution được hỗ trợ.
- Reference asset đúng loại media và có URI hợp lệ.
- Các trường không mâu thuẫn với nhau.
- Xác định đúng text-to-video hoặc image-to-video.
- Generation chưa bị hủy, hoàn thành hoặc submit trước đó.

Dữ liệu không hợp lệ phải dừng sớm và không được gọi provider.

### 3.2. Capability checker

Capability checker quyết định provider có thực thi được spec hay không. Capability nên được lưu bằng config hoặc dữ liệu tập trung, không viết rải rác trong nhiều câu lệnh `if`.

Cần kiểm tra:

- Chế độ text-to-video và image-to-video.
- Duration, aspect ratio và resolution.
- Số lượng, loại reference asset.
- Model phù hợp với chế độ generation.

### 3.3. Prompt compiler

Compiler chuyển `GenerationSpec` chung thành request riêng của Seedance:

```text
GenerationSpec -> model + prompt + parameters + reference image
```

Compiler chỉ chuyển đổi dữ liệu; không gọi HTTP API, không retry và không cập nhật database.

### 3.4. Provider adapter

Các provider phải tuân theo interface chung:

```python
class VideoProviderAdapter:
    def supports(self, spec): ...
    def compile(self, spec): ...
    async def submit(self, request): ...
    async def get_status(self, task_id): ...
    async def cancel(self, task_id): ...
    def normalize_result(self, response): ...
```

Cần có hai implementation:

- `FakeVideoProvider`: phục vụ phát triển, demo và test không tốn phí.
- `SeedanceProvider`: gọi API thật và chuẩn hóa response.

Adapter là nơi duy nhất được biết endpoint, authentication, model ID và schema riêng của Seedance.

### 3.5. Async orchestration

Generation là tác vụ dài nên API không chờ video hoàn thành:

```text
POST /v1/generations
    -> tạo generation record
    -> enqueue Celery task
    -> trả HTTP 202 + generation_id
```

Worker thực hiện:

1. Tải generation và `GenerationSpec`.
2. Kiểm tra trạng thái, cancellation và idempotency.
3. Validate và capability check.
4. Compile provider request.
5. Submit nếu chưa có `provider_task_id`.
6. Lưu `provider_task_id` ngay sau khi provider trả về.
7. Poll task đã có.
8. Chuẩn hóa kết quả hoặc lỗi.
9. Chuyển generation sang bước hậu xử lý.

### 3.6. Retry và timeout

| Tình huống | Xử lý |
| --- | --- |
| Validation error / HTTP 400 | Không retry |
| Policy block | Không retry, chuyển `BLOCKED` |
| HTTP 429 | Exponential backoff có jitter |
| HTTP 500/503 | Retry có giới hạn |
| Network error trước submit | Có thể retry request |
| Timeout sau submit | Tìm task bằng `provider_task_id`, không submit lại |
| Generation đã cancel | Dừng submit và polling |

Quy tắc quan trọng: retry một request kỹ thuật không đồng nghĩa với tạo một generation mới.

### 3.7. Idempotency và chống tính phí trùng

Mỗi generation cần idempotency key ổn định. Trước khi submit, worker phải kiểm tra:

- Generation đã có `provider_task_id` hay chưa.
- Worker khác có đang xử lý generation hay không.
- Generation đã ở trạng thái kết thúc hay chưa.
- Provider task cũ có thể tiếp tục polling hay không.

Khi worker restart hoặc mất kết nối sau submit, worker phải khôi phục task cũ. Không được tự động tạo task Seedance mới chỉ vì không nhận được response hoàn chỉnh.

### 3.8. Cancellation và recovery

- Trước submit: đánh dấu `CANCELLED`, không gọi provider.
- Sau submit: gọi provider cancel nếu được hỗ trợ, sau đó dừng polling.
- Khi worker restart: đọc lại trạng thái và không tiếp tục generation đã hủy.

Recovery phải dựa trên dữ liệu đã lưu trong database, không dựa vào bộ nhớ worker.

## 4. Trạng thái liên quan

```text
READY -> QUEUED -> SUBMITTED -> GENERATING
      -> POST_PROCESSING
      -> FAILED
      -> CANCELLED
      -> BLOCKED
```

Mỗi lần chuyển trạng thái cần có quy tắc rõ ràng. Worker không được cập nhật lùi hoặc ghi đè trạng thái kết thúc.

## 5. Ranh giới phối hợp

### Với Người 1

- Nhận asset contract và `storage_uri` có thể truy cập.
- Thống nhất generation record, status enum và cách cập nhật database.
- Thống nhất idempotency key và cơ chế lock.

### Với Người 2

- Cùng sở hữu contract `GenerationSpec`.
- Thống nhất required fields, enum và validation rules.
- Người 2 tạo spec; Người 3 kiểm tra capability và thực thi spec.

### Với Người 4

- Bàn giao normalized result, không bàn giao raw response.
- Thống nhất metadata cần cho output guard và post-processing.
- Thống nhất cách báo lỗi khi output thiếu, hỏng hoặc bị provider từ chối.

## 6. Các file thuộc phạm vi Người 3

```text
ai-service/app/
|-- schemas/
|   |-- generation.py
|   `-- provider.py
|-- providers/
|   |-- base.py
|   |-- registry.py
|   |-- fake.py
|   `-- seedance.py
`-- pipelines/
    `-- video_generation.py

backend/app/
|-- services/
|   |-- ai_service_client.py
|   `-- generation_orchestrator.py
`-- workers/
    |-- celery_app.py
    `-- video_tasks.py
```

Tên file có thể thay đổi theo convention của repository nhưng ranh giới module cần được giữ nguyên.

## 7. Definition of Done

- Text-to-video chạy được với Seedance.
- Image + text-to-video chạy được với Seedance.
- Provider interface và registry hoạt động.
- Fake provider hỗ trợ test end-to-end.
- Prompt compiler và capability checker có test.
- API trả `202`, generation được xử lý qua Celery/Redis.
- Worker poll và cập nhật status đúng.
- Retry có giới hạn và phân loại lỗi.
- Cancellation hoạt động.
- Worker restart có thể khôi phục provider task.
- Không tạo duplicate paid generation khi timeout/retry.
- Provider result được chuẩn hóa trước khi bàn giao cho Người 4.

## 8. Ngoài phạm vi

Người 3 không chịu trách nhiệm chính cho:

- LLM lập kế hoạch nội dung hoặc tạo `GenerationPlan`.
- Quyết định policy allow/block cuối cùng.
- Upload và quản lý asset gốc.
- Hậu xử lý video, output moderation và delivery UI.
- Cho phép LLM tự gọi provider, tự retry hoặc truy cập credential.

