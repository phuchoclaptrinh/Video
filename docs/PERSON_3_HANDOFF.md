# Bàn giao phần Người 3

## Phạm vi đã triển khai

Engine chạy riêng theo contract `GenerationSpec` mục 13 của `AI_CODE_ARCHITECTURE_CONTEXT.md`. Đây là contract nested (`request`, `intent`, `output`, `references`, `story`), thay cho ví dụ dạng phẳng trong tài liệu giải thích ban đầu.

- Schema và validation xác định; từ chối field lạ, asset thiếu và operation không tương thích.
- Seedance compiler phiên bản `seedance_compiler_v1`, capability profile tập trung.
- Adapter Seedance qua HTTP: submit, status, cancel task còn chờ, chuẩn hóa output và usage.
- Fake provider lưu task trong SQLite để các request/process cùng đọc được. URL video trả về là fixture minh họa, không phải video thật.
- Internal API riêng, xác thực `X-Internal-Key`, yêu cầu preflight `ALLOW` và mã quyết định policy.
- Orchestrator với sổ trạng thái SQLAlchemy riêng, atomic claim, submit intent, idempotency, timeout, retry giới hạn, cancellation và đối soát.
- Celery worker và Beat phục hồi job đến hạn. Không chiếm worker để sleep trong lúc chờ provider.
- CLI và test unit, HTTP xuyên service, Celery eager.

Không chỉnh `app/main.py`, API người dùng, auth, model/migration DB của nền tảng, planner, policy engine, post-processing, frontend, Docker hoặc CI/CD. Các file khung có sẵn được điền chỉ gồm `providers/base.py`, `providers/registry.py`, `providers/seedance.py`, `workers/celery_app.py`, `workers/tasks/video_tasks.py`. Các module khác của Người 3 là file mới.

## Các điểm tích hợp

| Thành viên | Cần cung cấp/tiếp nhận |
| --- | --- |
| Người 1 | Gọi `enqueue_video(provider, payload)` sau auth/quota/ownership; trả `202` cùng `generation_id`; dùng `ExecutionStore.get()` cho tiến trình; gọi `cancel_video()` sau kiểm tra quyền. |
| Người 1 | Resolve asset thành URL HTTPS provider đọc được hoặc JPEG Base64 data URI; không gửi `s3://` trực tiếp. Cấu hình DB dùng chung giữa các worker. |
| Người 2 | Cung cấp `GenerationSpec` nested, quyết định preflight `ALLOW`, `policy_decision_id` và asset có `moderation_status=APPROVED`. |
| Người 4 | Đọc job `POST_PROCESSING` và `result.outputs`, tải video về storage rồi thực hiện output guard. Engine không tự đánh dấu video đã qua QA. |
| Người 4 | Đưa entrypoint và biến môi trường bên dưới vào cấu hình triển khai; bật worker và Beat. |

Các endpoint `/internal/v1/video/.../tasks` là lệnh provider cấp thấp: chỉ worker đáng tin cậy được gọi. Idempotency nằm tại `enqueue_video` và journal backend; không dùng endpoint submit cấp thấp làm API công khai hoặc retry POST trực tiếp.

Preflight ở đây là assertion từ backend đáng tin cậy. Người 3 không tự thực hiện policy, không tự chứng minh quyết định `ALLOW` đúng và không nhận assertion này trực tiếp từ frontend.

## Contract và giới hạn MVP

Ví dụ chạy nằm tại:

- [Text-to-video](person3-examples/text-to-video.json)
- [Image-to-video](person3-examples/image-to-video.json)

Capability mặc định nhắm Seedance 1.5 Pro: 4–12 giây, 480p/720p/1080p, các tỷ lệ được khai báo trong `SeedanceCapabilities`. Model được chọn bằng `SEEDANCE_MODEL`; khi đổi dòng model phải cập nhật profile tương ứng.

MVP nhận tối đa một shot, ảnh `FIRST_FRAME` và tùy chọn `LAST_FRAME`. `CHARACTER_REFERENCE`/`STYLE_REFERENCE` hiện bị từ chối vì không thể tự chuyển chúng thành ảnh đầu mà vẫn giữ đúng ý nghĩa contract. Video hiện đặt `generate_audio=false`. Priority là preference chưa dùng để chọn nhiều model. `max_attempts` tính cả lần gọi đầu và bị giới hạn thêm bởi cấu hình retry server.

API key và model không nằm trong spec. Compiler không sửa nội dung qua LLM. Asset phải do phần upload/asset registry kiểm tra trước; engine chỉ kiểm tra contract và sự khớp ID, không tải/giải mã ảnh.

## Chạy AI service riêng

Tại thư mục `ai-service`, dùng môi trường Python có dependencies từ `requirements.txt`. Trong PowerShell:

```powershell
$env:INTERNAL_API_KEY = 'local-person3-development-key'
$env:VIDEO_ENABLE_FAKE = 'true'
$env:VIDEO_FAKE_DB = './storage/person3-fake.sqlite3'
python -m uvicorn app.pipelines.video_api:app --host 127.0.0.1 --port 8001
```

API docs: `http://127.0.0.1:8001/docs`. Chạy trên port khác nếu 8001 đã được sử dụng, đồng thời chỉnh `AI_SERVICE_URL` phía backend.

Các endpoint:

```text
POST /internal/v1/video/{provider}/validate
POST /internal/v1/video/{provider}/tasks
GET  /internal/v1/video/{provider}/tasks/{task_id}
POST /internal/v1/video/{provider}/tasks/{task_id}/cancel
GET  /health
```

`provider` là `fake` hoặc `seedance`. Fake chỉ được đăng ký khi `VIDEO_ENABLE_FAKE=true`. Seedance chỉ được đăng ký khi có `SEEDANCE_API_KEY`. Không có fallback ngầm sang provider khác.

Để dùng Seedance thật, cấu hình trong terminal chạy AI service:

```powershell
$env:SEEDANCE_API_KEY = '<API key của tài khoản>'
$env:SEEDANCE_MODEL = 'seedance-1-5-pro-251215'
$env:SEEDANCE_BASE_URL = 'https://ark.ap-southeast.bytepluses.com/api/v3'
```

Model ID phải tồn tại và được cấp quyền trong tài khoản. Engine chấp nhận URL HTTPS hoặc ảnh JPEG dạng Base64 data URI; luồng ứng dụng local dùng Base64 để không phải công khai ảnh hay cài object storage. Hai giá trị policy trong JSON mẫu chỉ phục vụ demo fake, không phải kết quả kiểm duyệt thật.

## Chạy backend engine và worker

Tại thư mục `backend`, dùng môi trường Python có dependencies từ `requirements.txt`. Đặt các biến sau trong mỗi terminal chạy CLI/worker/Beat:

```powershell
$env:INTERNAL_API_KEY = 'local-person3-development-key'
$env:AI_SERVICE_URL = 'http://127.0.0.1:8001'
$env:VIDEO_EXECUTION_DATABASE_URL = 'sqlite:///person3-execution.sqlite3'
$env:CELERY_BROKER_URL = 'redis://localhost:6379/0'
$env:CELERY_RESULT_BACKEND = 'redis://localhost:6379/1'
python -m app.workers.video_cli init
```

`init` chỉ tạo bảng `video_execution_jobs` bằng metadata riêng. Không chạy migration hay tạo bảng user/asset của Người 1. SQLite dùng cho local; nhiều máy/container cần trỏ cùng PostgreSQL bằng URL sync `postgresql+psycopg://...`. Không dùng đường dẫn SQLite tương đối khác nhau giữa các worker. Việc đưa bảng này vào quy trình migration chung là điểm tích hợp với Người 1.

Tạo job fake:

```powershell
python -m app.workers.video_cli enqueue ../docs/person3-examples/text-to-video.json --provider fake
```

CLI trả `generation_id`. Khi Redis đang chạy, mở worker và Beat trong hai terminal:

```powershell
python -m celery -A app.workers.celery_app worker --pool=solo --loglevel=info -Q video,default
```

```powershell
python -m celery -A app.workers.celery_app beat --loglevel=info
```

`--pool=solo` phục vụ local Windows. Trên Linux, cấu hình worker hiện có của project dùng được. Beat quét job đến hạn mỗi 15 giây; `VIDEO_POLL_INTERVAL_SECONDS` là khoảng cách tối thiểu giữa hai lần poll nên thời gian thực còn phụ thuộc nhịp Beat.

Nếu chưa có Redis, thực thi từng bước bằng CLI để kiểm tra luồng:

```powershell
python -m app.workers.video_cli tick <generation_id>
python -m app.workers.video_cli status <generation_id>
python -m app.workers.video_cli cancel <generation_id>
```

Lần `tick` đầu submit; các lần tiếp theo poll. CLI không in prompt và signed URL vào terminal. Payload và kết quả đầy đủ nằm trong journal để worker và output QA sử dụng; áp dụng quyền truy cập/retention DB theo nền tảng của nhóm.

## Retry, hủy và đối soát

- Cùng tenant/request ID và payload chuẩn hóa trả cùng job; payload khác bị `idempotency_conflict`.
- Claim sử dụng UPDATE có điều kiện và lease token. Worker mất lease không được ghi đè worker mới.
- `SUBMITTING` được lưu trước HTTP POST. Recovery gặp intent chưa có task ID chuyển `NEEDS_USER_ACTION`, không tự submit lại.
- Retry có backoff/jitter cho lỗi kết nối chắc chắn chưa gửi, 429 và lỗi tạm thời khi poll. HTTP 5xx/read timeout trên POST được coi là chưa rõ kết quả và cần đối soát.
- Timeout tổng hoặc hết retry khi đã có task giữ task ID trong `NEEDS_USER_ACTION`; có thể provider vẫn đang chạy và tính phí.
- Hủy trước submit ngăn gửi request. Với Seedance, chỉ task queued có thể hủy; task running tiếp tục được poll để giữ kết quả thực tế. Hủy không đảm bảo hoàn tiền. API DELETE của provider có thể đua với thay đổi trạng thái; lỗi không xác định cần đối soát.
- Video sinh xong chuyển `POST_PROCESSING` kể cả khi yêu cầu hủy đến quá muộn. Người 4 và backend quyết định có giao output cho người dùng hay không.

Sau khi người vận hành xác minh đúng task của generation trong console provider:

```powershell
python -m app.workers.video_cli reconcile <generation_id> <provider_task_id>
```

Lệnh chỉ nhận job `NEEDS_USER_ACTION`, kiểm tra task tồn tại rồi tiếp tục poll với thời hạn mới; không tạo video mới. Task ID đã biết không thể bị thay bằng task khác. Đây là thao tác vận hành tin cậy, không được expose trực tiếp cho người dùng cuối.

`usage` lưu token do provider trả. `estimated_cost=null` vì chưa có bảng giá/tỷ giá theo tài khoản, không giả định chi phí bằng 0. Log phát sự kiện có generation ID, task ID, status và attempts để Người 4 nối monitoring.

## Kiểm thử

Chạy riêng từng service vì cả hai có package tên `app`:

```powershell
# Trong ai-service
python -m pytest tests/test_video_execution.py -o addopts= -q
```

```powershell
# Trong backend
python -m pytest tests/test_video_execution.py tests/test_video_execution_http.py tests/test_video_worker.py -o addopts= -q
```

Cần pytest, pytest-asyncio và các runtime dependency. Test HTTP tự mở service ở port ngẫu nhiên và dừng sau khi hoàn tất. Test worker dùng Celery eager/memory; không chứng minh kết nối Redis production. Test fake không gọi Seedance và không phát sinh phí.

Đã xác minh toàn bộ ứng dụng bằng fake provider và kiểm tra khởi động cấu hình live. Chưa gửi tác vụ bằng tài khoản Seedance thật, Redis hoặc PostgreSQL thật. Không coi kết quả fake hay mock là bằng chứng đã tạo video thật.

## Tham chiếu API

- [BytePlus: Create video task](https://docs.byteplus.com/en/docs/ModelArk/1520757)
- [BytePlus: Retrieve video task](https://docs.byteplus.com/en/docs/ModelArk/1521309)
- [BytePlus: Cancel/delete video task](https://docs.byteplus.com/en/docs/ModelArk/1521720)
- [BytePlus SA: API reference và DELETE semantics](https://github.com/byteplus-sa/modelark-mcp/blob/main/docs/api-reference.md)

Thông tin API được đối chiếu ngày 14/09/2026; cần smoke test với model/tài khoản được cấp quyền trước khi sử dụng thật.
