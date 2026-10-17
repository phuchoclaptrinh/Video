# Phân chia công việc triển khai hệ thống AI Video Generation cho nhóm 4 người

## 1. Mục tiêu chung

Nhóm triển khai hệ thống AI tạo video từ:

- Text → Video
- Image → Video
- Text + Image → Video

Kiến trúc tổng quát:

```text
USER
 │
 ▼
API Gateway / Auth / Quota
 │
 ▼
Media Ingest + Asset Registry
 │
 ▼
Input Safety & Risk Analysis
 │
 ▼
Policy Engine
 │
 ▼
Multimodal LLM Intent Analyzer / Planner
 │
 ▼
GenerationPlan
 │
 ▼
GenerationSpec
 │
 ▼
Validation + Policy Preflight
 │
 ▼
Capability Resolver / Router
 │
 ▼
Prompt Compiler / Provider Adapter
 │
 ▼
Async Generation Orchestrator
 │
 ▼
Seedance
 │
 ▼
Output Guard
 │
 ▼
Storage / Delivery
 │
 ▼
USER
```

Nguyên tắc triển khai:

> **LLM hiểu và đề xuất → code deterministic kiểm soát → Seedance thực thi → guardrail kiểm tra → HITL xử lý case rủi ro/không chắc chắn.**

---

# 2. Phân chia ownership

| Thành viên | Ownership chính | Kết quả bàn giao |
|---|---|---|
| **Người 1 — Backend Platform & Asset** | API, auth, upload ảnh, Asset Registry, DB, storage, quota | Backend platform và asset lifecycle hoàn chỉnh |
| **Người 2 — AI Planner & Safety** | Multimodal LLM, intent, planner, GenerationPlan, semantic guard, policy | AI reasoning layer + safety/policy layer |
| **Người 3 — Video Generation & Orchestration** | Seedance, GenerationSpec execution, compiler, provider adapter, queue, retry | Generation engine chạy async |
| **Người 4 — Frontend, Output QA & Deployment** | UI, progress, output QA, logging, monitoring, Docker, CI/CD | Hệ thống end-to-end deploy được |

---

# 3. Người 1 — Backend Platform, Asset & Data

## 3.1 Phạm vi phụ trách

Người 1 chịu trách nhiệm xây nền tảng backend và quản lý dữ liệu/media.

### API Gateway / Backend API

Implement:

- Authentication
- Authorization
- Request ID
- Idempotency key
- Rate limiting
- Quota sơ bộ
- Request validation
- Generation CRUD

Các API tối thiểu:

```http
POST /v1/assets
GET  /v1/assets/{asset_id}

POST /v1/generations
GET  /v1/generations/{generation_id}
POST /v1/generations/{generation_id}/cancel
```

---

## 3.2 Image Ingest

Flow:

```text
User Upload
    ↓
MIME/type validation
    ↓
File size validation
    ↓
Image dimension validation
    ↓
Decode validation
    ↓
Normalize
    ↓
Hash
    ↓
Object Storage
    ↓
Create Asset Record
```

Nhiệm vụ:

- kiểm tra MIME thực tế;
- kiểm tra file corrupt;
- giới hạn kích thước;
- kiểm tra width/height;
- normalize orientation/colorspace;
- resize nếu cần;
- SHA-256;
- lưu vào object storage.

---

## 3.3 Asset Registry

Data model tối thiểu:

```json
{
  "asset_id": "asset_001",
  "owner_user_id": "usr_123",
  "tenant_id": "tenant_123",

  "media_type": "image",
  "mime_type": "image/jpeg",

  "width": 1920,
  "height": 1080,
  "size_bytes": 1234567,

  "storage_uri": "s3://...",
  "sha256": "...",

  "moderation_status": "PENDING",
  "rights_status": "UNKNOWN",
  "consent_status": "UNKNOWN",

  "created_at": "..."
}
```

---

## 3.4 Database

Người 1 chịu trách nhiệm schema/migration cho tối thiểu:

```text
users
assets
generations
generation_attempts
policy_decisions
```

Có thể sử dụng:

```text
PostgreSQL
+
S3 / MinIO / Cloudflare R2
```

---

## 3.5 Definition of Done

- [ ] Upload image hoạt động.
- [ ] Validate được MIME/file size/dimension.
- [ ] Image được normalize và lưu storage.
- [ ] Tạo được `asset_id`.
- [ ] Asset metadata được lưu DB.
- [ ] Tạo generation record được.
- [ ] Có API lấy generation status.
- [ ] Có cancel endpoint.
- [ ] Có auth/rate-limit/quota cơ bản.
- [ ] `request_id`, `asset_id`, `generation_id` được thống nhất xuyên hệ thống.

---

# 4. Người 2 — Multimodal LLM, Intent, Planner & Guardrails

## 4.1 Phạm vi phụ trách

Người 2 phụ trách AI reasoning layer.

Multimodal LLM có thể sử dụng:

```text
GPT-5.6 Luna
hoặc
GPT-4o-mini
```

Model phải nhận được:

```text
text
+
image
```

---

## 4.2 Input Safety & Contextual Risk

Input:

```text
user_text
+
image
+
asset metadata
```

Output:

```json
{
  "sexual": 0.02,
  "violence": 0.10,
  "real_person": 0.91,
  "possible_minor": 0.03,
  "identity_manipulation": 0.10,
  "prompt_injection": 0.01
}
```

LLM/model chỉ trả `RiskSignals`.

Không tự gọi Seedance và không tự enforce final policy.

---

## 4.3 Policy Engine

Người 2 định nghĩa policy rules/schema.

Policy execution phải deterministic.

Input:

```text
RiskSignals
+
Asset Context
+
Consent/Rights
+
User/Tenant Context
```

Output:

```text
ALLOW
ALLOW_WITH_CONSTRAINTS
BLOCK
NEEDS_CONSENT
NEEDS_USER_INPUT
HITL
```

Ví dụ:

```json
{
  "decision": "ALLOW_WITH_CONSTRAINTS",
  "risk_level": "medium",
  "constraints": [
    "preserve_reference_identity"
  ]
}
```

---

## 4.4 Intent Analyzer

Nhiệm vụ:

> Xác định người dùng muốn hệ thống thực hiện operation gì.

MVP operations:

```text
TEXT_GENERATION
IMAGE_ANIMATION
REFERENCE_GENERATION
```

Không chỉ dùng:

```text
has_image = true/false
```

mà phải hiểu semantic intent.

---

## 4.5 Multimodal Planner

Planner tạo `GenerationPlan`.

Ví dụ:

```json
{
  "intent": "REFERENCE_GENERATION",

  "goal": "Create a cinematic running scene",

  "references": [
    {
      "asset_id": "asset_001",
      "role": "CHARACTER_REFERENCE"
    }
  ],

  "shots": [
    {
      "subject": {
        "action": "run forward"
      },

      "environment": {
        "setting": "rainy city street"
      },

      "camera": {
        "movement": "tracking",
        "position": "rear"
      },

      "style": {
        "look": "cinematic"
      }
    }
  ],

  "assumptions": []
}
```

---

## 4.6 Semantic Plan Guard

So sánh:

```text
Original User Request
+
Image Context
+
GenerationPlan
```

Kiểm tra:

- intent có được giữ nguyên không;
- planner có hallucinate action mới không;
- image/text có conflict không;
- reference role có đúng không;
- có cần hỏi lại user không;
- planning có tạo thêm safety risk không.

Output:

```json
{
  "intent_preserved": true,
  "unsupported_additions": [],
  "reference_consistency": true,
  "requires_user_clarification": false
}
```

---

## 4.7 Evaluation dataset

Người 2 chuẩn bị tập test tối thiểu 50–100 case gồm:

```text
text-only
image-only
text + image
Vietnamese
English
underspecified request
image/text conflict
real-person case
possible-minor case
borderline safety
prompt injection
visual prompt injection
camera instructions
identity preservation
```

---

## 4.8 Definition of Done

- [ ] Text → structured `GenerationPlan`.
- [ ] Image → structured `GenerationPlan`.
- [ ] Text + Image → structured `GenerationPlan`.
- [ ] JSON schema ổn định.
- [ ] Intent classification hoạt động.
- [ ] Reference role assignment hoạt động.
- [ ] RiskSignals hoạt động.
- [ ] Deterministic policy decision hoạt động.
- [ ] Semantic plan guard hoạt động.
- [ ] Có evaluation dataset và script đánh giá.

---

# 5. Người 3 — Video Generation, Seedance & Async Orchestration

## 5.1 Phạm vi phụ trách

Người 3 sở hữu execution layer.

Input chính:

```text
GenerationSpec
```

Output:

```text
Generated Video / Provider Result
```

---

## 5.2 GenerationSpec Execution

Người 3 phối hợp với Người 2 để chốt schema `GenerationSpec`.

Ví dụ:

```json
{
  "intent": {
    "operation": "REFERENCE_GENERATION"
  },

  "output": {
    "duration_seconds": 5,
    "aspect_ratio": "16:9"
  },

  "references": [
    {
      "asset_id": "asset_001",
      "role": "CHARACTER_REFERENCE"
    }
  ],

  "story": {
    "shots": []
  },

  "constraints": {
    "preserve_identity": true
  }
}
```

---

## 5.3 Deterministic Validation

Validate:

- required fields;
- duration;
- aspect ratio;
- reference compatibility;
- model capability;
- provider limits.

Không gửi invalid request sang provider.

---

## 5.4 Capability Resolver / Router

MVP:

```text
GenerationSpec
      ↓
Resolve operation
      ↓
Check Seedance capability
      ↓
Select Seedance configuration
```

Kiến trúc phải dễ mở rộng sang provider/model khác trong tương lai.

---

## 5.5 Seedance Prompt Compiler

Chuyển:

```text
GenerationSpec
```

thành prompt/payload phù hợp với Seedance.

Ví dụ:

```json
{
  "action": "turn toward camera",
  "camera": {
    "movement": "dolly_in",
    "speed": "slow"
  }
}
```

→

```text
The subject slowly turns toward the camera.
The camera performs a smooth, slow dolly-in.
Preserve facial identity, clothing and composition.
Use natural movement without abrupt changes.
```

Compiler nên version được:

```text
seedance_compiler_v1
seedance_compiler_v2
```

---

## 5.6 Provider Adapter

Interface đề xuất:

```python
class VideoProviderAdapter:

    def supports(self, spec):
        ...

    def compile(self, spec):
        ...

    def submit(self, request):
        ...

    def get_status(self, task_id):
        ...

    def cancel(self, task_id):
        ...

    def normalize_result(self, response):
        ...
```

Implementation:

```text
SeedanceAdapter
```

---

## 5.7 Async Generation Orchestrator

Flow:

```text
Generation Job
      ↓
Queue
      ↓
Worker
      ↓
Seedance Submit
      ↓
provider_task_id
      ↓
Poll
      ↓
Completed / Failed
```

Có thể dùng:

```text
Redis
+
Celery / RQ / BullMQ / equivalent worker system
```

Orchestrator chịu trách nhiệm:

- queue;
- idempotency;
- retry;
- exponential backoff;
- timeout;
- cancellation;
- provider polling;
- attempt history;
- provider task recovery.

---

## 5.8 Retry Policy

```text
400
→ DO NOT RETRY

policy blocked
→ DO NOT RETRY

429
→ exponential backoff

500/503
→ retry

timeout nhưng provider job đã tồn tại
→ query existing job
→ không submit generation thứ hai
```

Nguyên tắc:

```text
retry API request
!=
retry generation
```

---

## 5.9 Definition of Done

- [ ] Text-to-video chạy được với Seedance.
- [ ] Image + text → video chạy được.
- [ ] Provider adapter hoàn chỉnh.
- [ ] Prompt compiler hoạt động.
- [ ] Capability check hoạt động.
- [ ] Queue + worker hoạt động.
- [ ] Polling provider status hoạt động.
- [ ] Retry bounded.
- [ ] Cancellation hoạt động.
- [ ] Idempotency hoạt động.
- [ ] Không tạo duplicate paid generation khi timeout/retry.
- [ ] Provider result được normalize về format nội bộ.

---

# 6. Người 4 — Frontend, Output QA, Observability & Deployment

## 6.1 Frontend

UI MVP:

```text
┌────────────────────────────────┐
│ Create Video                   │
│                                │
│ [ Upload Image ] optional      │
│                                │
│ Prompt                         │
│ _____________________________  │
│                                │
│ Duration       [5 sec]         │
│ Aspect Ratio   [16:9]          │
│                                │
│       Generate Video           │
└────────────────────────────────┘
```

Sau submit hiển thị trạng thái:

```text
Preparing
   ↓
Planning
   ↓
Queued
   ↓
Generating
   ↓
Reviewing
   ↓
Completed
```

Frontend sử dụng:

```http
GET /v1/generations/{generation_id}
```

để lấy status.

---

## 6.2 Output Technical QA

Implement:

```text
Generated Video
      ↓
Decode test
      ↓
Duration validation
      ↓
Resolution validation
      ↓
Black/frozen frame checks
```

Có thể sử dụng FFmpeg cho inspection/frame extraction.

---

## 6.3 Basic Output Safety

Flow MVP:

```text
Generated Video
      ↓
Sample Frames
      ↓
Image Safety Classifier
      ↓
RiskSignals
```

Advanced semantic video evaluation có thể để phase sau.

---

## 6.4 Observability

Phải trace được:

```text
request_id
generation_id
asset_ids
provider_job_id
planner_version
compiler_version
policy_version
provider
model
latency
cost
errors
```

Metrics cơ bản:

```text
request count
generation success rate
LLM error rate
provider error rate
queue depth
generation latency
retry rate
cost
```

---

## 6.5 Deployment

Người 4 chịu trách nhiệm Docker hóa.

MVP:

```text
docker-compose
│
├── frontend
├── api
├── worker
├── postgres
├── redis
└── minio
```

Production topology:

```text
Frontend
   ↓
Load Balancer / Reverse Proxy
   ↓
Backend API
   │
   ├── PostgreSQL
   ├── Redis
   ├── Object Storage
   └── Workers
          │
          ├── OpenAI API
          └── Seedance API
```

---

## 6.6 CI/CD và môi trường

Tối thiểu:

```text
development
staging
production
```

Quản lý:

- environment variables;
- API secrets;
- migration;
- build;
- test;
- deployment;
- health checks.

---

## 6.7 Definition of Done

- [ ] UI upload image hoạt động.
- [ ] Prompt input hoạt động.
- [ ] Generate request hoạt động.
- [ ] Generation progress hiển thị đúng.
- [ ] Video playback hoạt động.
- [ ] Technical output QA hoạt động.
- [ ] Docker Compose chạy end-to-end.
- [ ] Logs có request/generation IDs.
- [ ] Health checks có sẵn.
- [ ] Basic metrics có sẵn.
- [ ] Deploy được staging.
- [ ] Có tài liệu deploy.

---

# 7. Shared Contracts giữa 4 người

Các contract này phải chốt ngay từ đầu.

---

## 7.1 Asset Contract

Owner chính: **Người 1**

Consumer: Người 2, Người 3.

```json
{
  "asset_id": "asset_001",
  "media_type": "image",
  "storage_uri": "...",
  "mime_type": "image/jpeg",
  "width": 1920,
  "height": 1080,
  "moderation_status": "PASS"
}
```

---

## 7.2 GenerationPlan Contract

Owner chính: **Người 2**

```json
{
  "intent": "REFERENCE_GENERATION",
  "goal": "...",
  "references": [],
  "shots": [],
  "assumptions": []
}
```

---

## 7.3 GenerationSpec Contract

Owner: **Người 2 + Người 3**

Đây là contract quan trọng nhất giữa AI layer và execution layer.

Mọi thay đổi schema phải được review bởi cả hai.

---

## 7.4 Generation Status Contract

Owner: **Người 1 + Người 3**

Consumer: Người 4.

```json
{
  "generation_id": "gen_123",
  "state": "GENERATING",
  "progress": null,
  "output": null,
  "error": null
}
```

State enum:

```text
RECEIVED
INGESTING
PRECHECK
PLANNING
PLAN_VALIDATION
WAITING_USER_APPROVAL
READY
QUEUED
SUBMITTED
GENERATING
POST_PROCESSING
OUTPUT_REVIEW
COMPLETED

BLOCKED
FAILED
CANCELLED
NEEDS_USER_ACTION
HITL
```

---

# 8. Cấu trúc repository đề xuất

```text
video-ai/
│
├── apps/
│   ├── api/                  # Người 1
│   ├── worker/               # Người 3
│   └── web/                  # Người 4
│
├── packages/
│   ├── assets/               # Người 1
│   ├── planner/              # Người 2
│   ├── safety/               # Người 2
│   ├── policy/               # Người 2
│   ├── generation-spec/      # Người 2 + Người 3
│   ├── providers/
│   │   └── seedance/         # Người 3
│   ├── orchestration/        # Người 3
│   └── observability/        # Người 4
│
├── infrastructure/           # Người 4
│   ├── docker/
│   └── deployment/
│
├── tests/
│
└── docs/
```

Quy tắc:

> `generation-spec` là shared contract. Không thành viên nào tự ý thay đổi mà không review.

---

# 9. Kế hoạch theo Sprint

## Sprint 1 — Skeleton + Contract

### Người 1

- [ ] API skeleton
- [ ] PostgreSQL
- [ ] Asset upload
- [ ] Asset DB
- [ ] Generation CRUD

### Người 2

- [ ] `GenerationPlan` schema
- [ ] `GenerationSpec` draft
- [ ] Planner prototype
- [ ] Mock risk analyzer

### Người 3

- [ ] Provider adapter interface
- [ ] Seedance client skeleton
- [ ] Fake provider
- [ ] Worker skeleton

### Người 4

- [ ] Frontend skeleton
- [ ] Generate form
- [ ] Docker Compose
- [ ] CI/CD skeleton

### Sprint 1 integration target

Phải chạy được:

```text
Frontend
 ↓
API
 ↓
Fake Planner
 ↓
Fake Seedance
 ↓
Fake Video
 ↓
Frontend
```

Mục tiêu là có end-to-end skeleton càng sớm càng tốt.

---

# 10. Sprint 2 — Real AI + Real Video

### Người 1

- [ ] Image validation/normalize
- [ ] Object storage
- [ ] Quota
- [ ] Generation state persistence

### Người 2

- [ ] GPT multimodal planner
- [ ] Input guard
- [ ] Policy engine
- [ ] Semantic plan guard
- [ ] Evaluation cases

### Người 3

- [ ] Real Seedance API
- [ ] Prompt compiler
- [ ] Queue
- [ ] Worker
- [ ] Polling
- [ ] Retry

### Người 4

- [ ] Status polling
- [ ] Video result UI
- [ ] Error UI
- [ ] Structured logging
- [ ] Initial staging deployment

### Sprint 2 integration target

Phải chạy được:

```text
Text
  ↓
Real Video
```

và:

```text
Image + Text
      ↓
Real Video
```

end-to-end.

---

# 11. Sprint 3 — Production Safety & Reliability

### Người 1

- [ ] Auth hardening
- [ ] Rate limit
- [ ] Quota
- [ ] Asset security
- [ ] Idempotency persistence

### Người 2

- [ ] Safety evaluation
- [ ] Policy tests
- [ ] Prompt injection tests
- [ ] Visual prompt injection tests
- [ ] Real-person edge cases

### Người 3

- [ ] Timeout handling
- [ ] Cancellation
- [ ] Retry classification
- [ ] Provider task recovery
- [ ] Cost tracking

### Người 4

- [ ] Output technical QA
- [ ] Output frame moderation
- [ ] Monitoring dashboard
- [ ] Production Docker config
- [ ] Health checks

---

# 12. Sprint 4 — Release

Cả team:

- [ ] Integration testing
- [ ] Load testing
- [ ] Failure testing
- [ ] Security testing
- [ ] Prompt evaluation
- [ ] Image input evaluation
- [ ] Provider outage simulation
- [ ] Cost evaluation
- [ ] Staging soak test
- [ ] Production release

---

# 13. Integration Rules

## 13.1 Không chờ nhau code xong mới tích hợp

Ngay Sprint 1:

```text
Người 1 cung cấp API contract.
Người 2 cung cấp fake GenerationPlan.
Người 3 cung cấp fake video provider.
Người 4 nối frontend vào API.
```

Sau đó từng fake component được thay bằng implementation thật.

---

## 13.2 Không truyền raw LLM output trực tiếp sang Seedance

Phải:

```text
LLM
 ↓
GenerationPlan
 ↓
GenerationSpec
 ↓
Validation
 ↓
Prompt Compiler
 ↓
Seedance
```

---

## 13.3 Không để LLM tự quyết policy

```text
LLM / classifiers
      ↓
RiskSignals
      ↓
Deterministic Policy Engine
```

---

## 13.4 Không để provider-specific fields leak vào core schema

Core:

```text
GenerationSpec
```

Provider layer:

```text
SeedanceRequest
```

---

## 13.5 Mọi paid generation phải async và idempotent

Không giữ HTTP request mở đến khi video render xong.

---

# 14. Tech Lead / Integration Owner

Nếu cần chọn một người làm Tech Lead, nên chọn:

```text
Người 1
hoặc
Người 3
```

vì hai người nằm ở trung tâm backend/execution integration.

Tech Lead chịu trách nhiệm:

- API contracts;
- state machine;
- shared schema;
- architecture consistency;
- merge/release coordination;
- integration schedule;
- technical decisions khi có conflict.

Tech Lead không nhất thiết phải code nhiều hơn các thành viên khác.

---

# 15. Bản giao việc ngắn gọn

## Member 1 — Platform Backend

```text
API
Auth
Image Upload
Asset Registry
Database
Storage
Quota
Generation CRUD
```

## Member 2 — AI & Safety

```text
GPT-4o-mini / GPT-5.6 Luna
Input Risk Analysis
Intent Recognition
Multimodal Planner
GenerationPlan
GenerationSpec
Semantic Guard
Policy Rules
Evaluation
```

## Member 3 — Generation Engine

```text
GenerationSpec Validation
Capability Resolver
Seedance Prompt Compiler
Seedance Adapter
Queue
Worker
Async Orchestrator
Polling
Retry
Timeout
Cancellation
```

## Member 4 — Product & Deployment

```text
Frontend
Generation Progress
Video Result UI
Output QA
Logging
Metrics
Docker
CI/CD
Staging
Production Deployment
```

---

# 16. Definition of Project Done

Hệ thống được coi là deployable khi:

- [ ] User login/auth được.
- [ ] User upload image được.
- [ ] Text-only generation hoạt động.
- [ ] Text + image generation hoạt động.
- [ ] LLM trả structured plan.
- [ ] Policy/validation chặn invalid request.
- [ ] Seedance được gọi qua provider adapter.
- [ ] Generation chạy async.
- [ ] Retry/idempotency hoạt động.
- [ ] Status hiển thị trên frontend.
- [ ] Video được kiểm tra cơ bản trước delivery.
- [ ] Video được lưu và playback được.
- [ ] Logs/traces liên kết được request → generation → provider task.
- [ ] Cost được track.
- [ ] Docker/staging deploy chạy end-to-end.
- [ ] Có integration tests cho happy path và failure path.
