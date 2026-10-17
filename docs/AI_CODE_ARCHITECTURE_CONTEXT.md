# AI Coding Context — Video Generation System Architecture

> File này là bản tóm tắt kiến trúc dành cho AI coding agent.  
> Scope hiện tại: **input chỉ gồm text và image**, output là **video**.

---

## 1. System Goal

Xây dựng backend cho hệ thống AI tạo video từ:

1. `text -> video`
2. `image -> video`
3. `text + image -> video`

AI core gồm:

- **Multimodal LLM / VLM**: hiểu text + image, nhận diện intent, lập kế hoạch.
- **Video Generation Provider**: Seedance hiện tại, có thể thay/ thêm provider sau.

Nguyên tắc bắt buộc:

```text
LLM understands and proposes.
Deterministic code validates and decides.
Video model executes.
Humans resolve high-risk uncertainty.
```

Không xây hệ thống theo kiểu:

```text
User -> LLM -> raw prompt -> Seedance
```

Mà theo:

```text
User
 -> Ingest
 -> Safety/Risk
 -> Policy
 -> Multimodal Planner
 -> GenerationPlan
 -> GenerationSpec
 -> Validation
 -> Policy Preflight
 -> Capability/Router
 -> Prompt Compiler
 -> Async Orchestrator
 -> Provider Adapter
 -> Seedance
 -> Output Guard
 -> Delivery
```

---

# 2. High-Level Architecture

```text
                    POLICY CONTROL PLANE
             rules / risk / consent / ACL
               quota / cost / model rules
                          |
                          v

USER: text / image
        |
        v
API Gateway / Auth / Rate Limit / Quota
        |
        v
Image Ingest + Asset Registry
        |
        +-- MIME/type validation
        +-- decode validation
        +-- file size / dimension validation
        +-- normalization
        +-- hash / dedup
        +-- metadata
        +-- rights / consent metadata
        +-- ingest moderation
        |
        v
Input Safety & Risk Analysis
        |
        v
Deterministic Policy Decision
        |
        +-- BLOCK
        +-- NEEDS_CONSENT
        +-- NEEDS_USER_INPUT
        +-- HITL
        +-- ALLOW
              |
              v
Multimodal Intent Analyzer / Planner
              |
              v
GenerationPlan
              |
              +-- optional user preview / approval
              |
              v
GenerationSpec
              |
              v
Schema + Cross-field Validation
              |
              v
Semantic Plan Guard
              |
              v
Policy Preflight
              |
              v
Capability Resolver + Model Router
              |
              v
Provider Prompt Compiler / Adapter
              |
              v
ProviderRequest
              |
              v
Async Generation Orchestrator
              |
              +-- Queue
              +-- Idempotency
              +-- Retry/backoff
              +-- Timeout
              +-- Cancellation
              +-- bounded fallback
              |
              v
Seedance
              |
              v
Generated Video
              |
              v
Post-processing
              |
              +-- technical inspection
              +-- frame sampling
              +-- optional audio analysis
              +-- video understanding
              |
              v
Output Safety + Quality + Alignment
              |
        +-----+---------+-----------+
        |               |           |
      BLOCK           REPAIR       PASS
        |               |           |
       HITL         bounded retry   |
                                    v
                             Storage / Delivery
                                    |
                                    v
                                   USER
```

Cross-cutting:

```text
Observability / Trace / Audit / Evaluation / Cost / Feedback
```

---

# 3. Trust Boundary

LLM/VLM là probabilistic component.

```text
Multimodal LLM
      |
      v
GenerationPlan
      |
====== TRUST BOUNDARY ======
      |
      v
Deterministic Core
```

## LLM được phép

- hiểu user text;
- hiểu image;
- nhận diện intent;
- xác định semantic role của image;
- tạo creative plan;
- xác định subject / action / environment;
- xác định camera / style / lighting;
- phát hiện ambiguity;
- đề xuất constraints;
- semantic safety reasoning;
- đánh giá intent preservation;
- output alignment evaluation;
- đề xuất prompt/spec repair.

## LLM KHÔNG được phép

- trực tiếp gọi video provider;
- tự quyết final `ALLOW/BLOCK`;
- bypass policy;
- xác nhận consent thay cho hệ thống;
- tự nâng quota;
- tự chọn model bất chấp capability/policy;
- tự retry vô hạn;
- tự chi vượt budget;
- thay đổi ACL / tenant policy;
- tự tạo provider credentials hoặc endpoint.

---

# 4. AI Components

## 4.1 Multimodal LLM / VLM

Có thể sử dụng:

```text
GPT-5.6 Luna
GPT-4o-mini
```

hoặc model tương đương có:

- text input;
- image input;
- structured output;
- đủ khả năng image understanding.

MVP có thể sử dụng **cùng một model với nhiều system prompt/schema khác nhau**.

Ví dụ:

```text
LLM call #1 -> Contextual Risk Analyzer
LLM call #2 -> Intent + Planner
LLM call #3 -> Semantic Plan Guard
```

Không cần 3 model riêng.

---

## 4.2 Specialized AI Models

Không cần dùng LLM cho mọi guard.

Các task phù hợp với classifier/model chuyên biệt:

```text
NSFW detection
violence detection
person detection
possible-minor risk
face detection
frame safety
```

Output là `RiskSignals`, không trực tiếp ra quyết định business.

---

# 5. Component Responsibility Matrix

| Component | Implementation |
|---|---|
| API Gateway/Auth | deterministic |
| Rate limit/quota | deterministic |
| MIME/file validation | deterministic |
| Image decode validation | deterministic |
| Normalize image | deterministic |
| Hash/dedup | deterministic |
| Asset metadata | deterministic |
| NSFW/person detection | specialized AI |
| Contextual safety reasoning | multimodal LLM/VLM |
| Final input policy decision | deterministic |
| Intent recognition | multimodal LLM |
| Creative planning | multimodal LLM |
| Reference-role detection | multimodal LLM |
| Schema validation | deterministic |
| Cross-field validation | deterministic |
| Semantic plan validation | LLM/VLM optional |
| Consent state lookup | deterministic |
| Cost/quota preflight | deterministic |
| Capability resolution | deterministic |
| Model routing | deterministic/scoring |
| Prompt compiler | deterministic preferred |
| Provider adapter | deterministic |
| Queue/orchestrator | deterministic |
| Retry/backoff | deterministic |
| Technical output QA | deterministic |
| Output frame safety | specialized AI |
| Semantic video alignment | VLM |
| Prompt repair proposal | LLM |
| Final output decision | deterministic |
| HITL | human |

---

# 6. Input Modes

## 6.1 Text only

```text
text
 -> planner
 -> TEXT_GENERATION
 -> GenerationSpec
 -> Seedance text generation
```

Example:

```text
"Tạo video một con mèo đen chạy dưới mưa ở Tokyo,
camera tracking từ phía sau."
```

---

## 6.2 Image only

```text
image
 -> image understanding
 -> infer/propose motion
 -> if confidence sufficient:
       generate
    else:
       NEEDS_USER_INPUT
```

System không nên tự hallucinate một transformation lớn.

---

## 6.3 Text + Image

```text
image + instruction
 -> multimodal understanding
 -> determine image role
 -> create plan
 -> generate reference-based video
```

Example:

```text
Image: woman standing on beach

Text:
"Cho cô ấy quay sang nhìn camera,
tóc bay nhẹ, camera từ từ tiến lại gần."
```

---

# 7. Asset Registry

Không truyền raw image binary xuyên qua core services.

Flow:

```text
Upload
 -> validate
 -> normalize
 -> store
 -> create asset_id
```

Downstream chỉ dùng:

```text
asset_id
```

## Minimal Asset Schema

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

  "moderation_status": "PASS",

  "contains_person": true,
  "possible_minor": false,

  "rights_status": "UNKNOWN",
  "likeness_consent_status": "UNKNOWN",

  "created_at": "..."
}
```

---

# 8. Input Safety & Risk Analysis

Không gọi box này đơn giản là `LLM Safety Guard`.

Nó là aggregation của nhiều analyzers:

```text
Text classifier
Image safety classifier
Person/minor detector
Prompt-injection detector
Contextual multimodal LLM
            |
            v
        RiskSignals
```

Example:

```json
{
  "sexual": 0.02,
  "violence": 0.10,
  "possible_minor": 0.03,
  "real_person": 0.92,
  "identity_manipulation": 0.12,
  "prompt_injection": 0.01
}
```

Analyzer không được trực tiếp call Seedance.

---

# 9. Policy Engine

Policy Engine là deterministic.

Input:

```text
RiskSignals
Asset metadata
Rights/consent state
User context
Tenant policy
Policy version
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

Example:

```json
{
  "decision": "ALLOW_WITH_CONSTRAINTS",
  "risk_level": "medium",
  "policy_version": "policy_v1",
  "constraints": [
    "preserve_reference_identity",
    "max_duration_10"
  ],
  "reasons": [
    "REAL_PERSON_REFERENCE"
  ]
}
```

Policy service có thể được gọi nhiều lần:

```text
Policy.evaluate(stage="input")
Policy.evaluate(stage="plan")
Policy.evaluate(stage="pre_generation")
Policy.evaluate(stage="output")
```

---

# 10. Intent Analyzer + Planner

## Intent Analyzer

Xác định:

```text
User muốn operation gì?
```

MVP operations:

```text
TEXT_GENERATION
IMAGE_ANIMATION
REFERENCE_GENERATION
```

Không route chỉ dựa trên `has_image`.

---

## Planner

Xác định:

```text
subject
action
environment
camera
style
lighting
motion
constraints
reference role
duration preference
aspect ratio preference
assumptions
```

Example:

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

# 11. Reference Roles

Không dùng:

```json
{
  "has_image": true
}
```

Dùng semantic role:

```text
FIRST_FRAME
CHARACTER_REFERENCE
STYLE_REFERENCE
ENVIRONMENT_REFERENCE
OBJECT_REFERENCE
```

MVP có thể chỉ implement:

```text
FIRST_FRAME
CHARACTER_REFERENCE
```

nhưng enum/schema nên mở rộng được.

---

# 12. GenerationPlan vs GenerationSpec vs ProviderRequest

## GenerationPlan

Owner:

```text
LLM
```

Ý nghĩa:

```text
Interpretation of user creative intent
```

---

## GenerationSpec

Owner:

```text
Core system
```

Ý nghĩa:

```text
Canonical validated executable contract
```

---

## ProviderRequest

Owner:

```text
Provider Compiler / Adapter
```

Ý nghĩa:

```text
Provider-specific payload
```

Flow:

```text
User Intent
    ↓
GenerationPlan
    ↓
GenerationSpec
    ↓
ProviderRequest
```

---

# 13. GenerationSpec

Không chứa Seedance-specific syntax.

Example:

```json
{
  "schema_version": "1.0",

  "request": {
    "request_id": "req_123",
    "user_id": "usr_123",
    "tenant_id": "tenant_123"
  },

  "intent": {
    "operation": "REFERENCE_GENERATION",
    "goal": "Create a cinematic running scene"
  },

  "output": {
    "media_type": "video",
    "duration_seconds": 5,
    "aspect_ratio": "16:9",
    "resolution_preference": "1080p"
  },

  "references": [
    {
      "asset_id": "asset_001",
      "role": "CHARACTER_REFERENCE",
      "strength": "high"
    }
  ],

  "story": {
    "shots": [
      {
        "shot_id": "shot_1",

        "subject": {
          "action": "runs through a rainy street"
        },

        "environment": {
          "setting": "night city",
          "weather": "rain"
        },

        "camera": {
          "shot_size": "medium_full",
          "angle": "rear",
          "movement": "tracking",
          "speed": "slow"
        },

        "style": {
          "look": "cinematic",
          "lighting": "neon"
        }
      }
    ]
  },

  "constraints": {
    "preserve_identity": true,
    "avoid_text_overlay": true
  },

  "execution_preferences": {
    "priority": "quality",
    "max_attempts": 2
  }
}
```

Do NOT add:

```text
seedance_prompt
seedance_model_id
provider_api_key
provider_endpoint
```

vào canonical `GenerationSpec`.

---

# 14. Validation

## Deterministic Validation

Validate:

```text
JSON syntax
schema
required fields
enum
duration
aspect ratio
reference asset type
cross-field compatibility
```

Examples:

```text
CHARACTER_REFERENCE + non-image asset -> INVALID

duration > configured limit -> INVALID

unsupported aspect ratio -> INVALID
```

---

## Semantic Plan Guard

Có thể dùng LLM/VLM để kiểm tra:

```text
Original user request
+
Asset context
+
GenerationPlan
```

Questions:

```text
Was user intent preserved?
Did planner add unsupported actions?
Does text conflict with image?
Is reference role correct?
Does plan introduce new safety risk?
Does user clarification need to be requested?
```

Example:

```json
{
  "intent_preserved": true,
  "unsupported_additions": [],
  "reference_consistency": true,
  "requires_user_clarification": false
}
```

---

# 15. Policy Preflight

Ngay trước paid generation, validate:

```text
operation allowed?
references valid?
rights/consent sufficient?
duration supported?
resolution supported?
user quota sufficient?
estimated cost acceptable?
model allowed?
provider healthy?
```

Không submit provider nếu preflight fail.

---

# 16. Capability Resolver

Model capabilities phải là config/data, không hard-code trong business logic.

Example:

```json
{
  "seedance-current": {
    "inputs": [
      "text",
      "image"
    ],

    "operations": [
      "TEXT_GENERATION",
      "REFERENCE_GENERATION"
    ],

    "status": "AVAILABLE"
  }
}
```

MVP có một provider vẫn giữ abstraction này.

---

# 17. Model Router

MVP:

```text
GenerationSpec
 -> determine operation
 -> check Seedance capability
 -> select Seedance configuration
```

Future:

```text
candidate models
 -> hard capability filtering
 -> policy filtering
 -> region/tenant filtering
 -> provider health
 -> cost/latency/quality scoring
 -> selected model
```

LLM không trực tiếp chọn provider cuối cùng.

---

# 18. Provider Prompt Compiler

Purpose:

```text
WHAT user wants
      ↓
HOW Seedance should be prompted
```

Baseline: deterministic/template compiler.

Example input:

```json
{
  "action": "turn toward camera",
  "camera": {
    "movement": "dolly_in",
    "speed": "slow"
  }
}
```

Possible compiled prompt:

```text
The subject slowly turns toward the camera.
The camera performs a smooth, slow dolly-in.
Preserve facial identity, clothing and composition.
Use natural subtle motion with no abrupt camera changes.
```

Version compiler:

```text
seedance_compiler_v1
seedance_compiler_v2
```

Optional future:

```text
GenerationSpec
 -> constrained LLM prompt optimizer
 -> deterministic validation
 -> ProviderRequest
```

---

# 19. Provider Adapter Interface

Suggested interface:

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
FutureProviderAdapter
```

---

# 20. Async Orchestrator

Never keep the original HTTP request open until generation completes.

Create:

```http
POST /v1/generations
```

Return:

```text
202 Accepted
generation_id
```

Then:

```text
Queue
 -> Worker
 -> Provider submit
 -> provider_task_id
 -> poll/callback
 -> complete/fail
```

Responsibilities:

```text
queue
idempotency
retry/backoff
timeout
cancellation
attempt history
provider task recovery
bounded fallback
```

---

# 21. Retry Rules

```text
400 invalid request
 -> DO NOT RETRY

policy block
 -> DO NOT RETRY

429
 -> exponential backoff

500/503
 -> retry

client timeout + provider job already exists
 -> query existing provider job
 -> DO NOT create duplicate generation
```

Important:

```text
retry API request != retry generation
```

---

# 22. Generation State Machine

```text
RECEIVED
  ↓
INGESTING
  ↓
PRECHECK
  ↓
PLANNING
  ↓
PLAN_VALIDATION
  ↓
WAITING_USER_APPROVAL   optional
  ↓
READY
  ↓
QUEUED
  ↓
SUBMITTED
  ↓
GENERATING
  ↓
POST_PROCESSING
  ↓
OUTPUT_REVIEW
  ↓
COMPLETED
```

Side/terminal states:

```text
BLOCKED
FAILED
CANCELLED
HITL
NEEDS_USER_ACTION
```

---

# 23. Output Guard

Output Guard phải gồm 3 nhóm:

```text
SAFETY
QUALITY
ALIGNMENT
```

Không chỉ:

```text
SAFE / UNSAFE
```

---

## Technical QA

Deterministic:

```text
can decode video?
duration correct?
resolution valid?
black frames?
frozen frames?
file corrupt?
```

---

## Frame Safety

Sample frames:

```text
fixed intervals
keyframes
scene changes
first/last
```

Run:

```text
image safety classifiers
```

---

## Semantic Alignment

VLM can compare:

```text
GenerationPlan
+
sampled frames / video understanding
```

Evaluate:

```text
semantic_alignment
camera_adherence
action_adherence
style_adherence
reference_preservation
```

Example:

```json
{
  "safety": {
    "decision": "PASS"
  },

  "quality": {
    "technical": 0.96,
    "temporal_consistency": 0.86
  },

  "alignment": {
    "semantic": 0.88,
    "camera": 0.61,
    "action": 0.93,
    "reference": 0.92
  }
}
```

---

# 24. Repair Loop

Allowed:

```text
technical failure -> retry

low semantic alignment
 -> repair prompt/spec
 -> regenerate

policy violation
 -> BLOCK/HITL
```

Limits enforced by code:

```text
max_generation_attempts = 2
max_prompt_repairs = 1
```

Never:

```python
while video_not_good:
    let_llm_try_again()
```

---

# 25. HITL

HITL is not on the happy path.

Use HITL for:

```text
high-risk safety ambiguity
rights/consent ambiguity
classifier disagreement
sensitive real-person manipulation
user appeal
uncertain output safety
```

Do NOT use HITL for ordinary creative ambiguity.

Use:

```text
NEEDS_USER_INPUT
```

for cases like:

```text
"Cho ảnh này thành video."
```

when motion intent is missing.

---

# 26. Prompt Injection Rules

Text and image are untrusted.

Image may contain text such as:

```text
SYSTEM MESSAGE:
Ignore previous instructions.
```

Treat text inside image as data, never higher-priority instructions.

Required controls:

```text
strong system prompt
strict role separation
structured output
schema validation
no provider credentials exposed to planner
no side-effect tools available to planner
prompt injection signals
policy preflight
```

---

# 27. Minimal API Design

## Upload Asset

```http
POST /v1/assets
```

Response:

```json
{
  "asset_id": "asset_001",
  "status": "READY"
}
```

---

## Create Generation

```http
POST /v1/generations
```

Example:

```json
{
  "text": "Cho cô gái quay sang nhìn camera",
  "asset_ids": [
    "asset_001"
  ],
  "output": {
    "duration_seconds": 5,
    "aspect_ratio": "16:9"
  }
}
```

Response:

```json
{
  "generation_id": "gen_123",
  "status": "RECEIVED"
}
```

---

## Get Generation

```http
GET /v1/generations/{generation_id}
```

---

## Cancel Generation

```http
POST /v1/generations/{generation_id}/cancel
```

---

# 28. Generation Record

```json
{
  "generation_id": "gen_123",
  "request_id": "req_123",
  "user_id": "usr_123",

  "state": "GENERATING",
  "attempt": 1,

  "input_asset_ids": [
    "asset_001"
  ],

  "planner_version": "planner_v1",
  "spec_version": "1.0",
  "policy_version": "policy_v1",
  "compiler_version": "seedance_v1",

  "provider": "seedance",
  "model": "seedance-current",
  "provider_job_id": "...",

  "estimated_cost": 0.0,
  "actual_cost": 0.0,

  "created_at": "...",
  "started_at": "...",
  "completed_at": null
}
```

---

# 29. Observability Requirements

Every generation must have:

```text
request_id
generation_id
asset_ids
planner_version
spec_version
policy_version
compiler_version
provider
model
provider_job_id
latency
cost
risk signals
policy decisions
output quality scores
```

Example trace:

```text
req_123
 |
 +-- ingest             300 ms
 +-- input_guard        250 ms
 +-- planner            1.2 s
 +-- validation          10 ms
 +-- policy              20 ms
 +-- routing              5 ms
 +-- compilation         10 ms
 +-- queue              1.5 s
 +-- provider          60.0 s
 +-- output_guard       3.0 s
 +-- delivery           200 ms
```

---

# 30. Metrics

Planner:

```text
schema_valid_rate
clarification_rate
wrong_reference_role_rate
plan_repair_rate
user_plan_edit_rate
```

Generation:

```text
provider_success_rate
provider_rejection_rate
retry_rate
p50_generation_latency
p95_generation_latency
```

Quality:

```text
semantic_alignment
reference_adherence
camera_adherence
temporal_consistency
user_accept_rate
regeneration_rate
```

Safety:

```text
input_block_rate
output_block_rate
HITL_rate
HITL_overturn_rate
false_positive_estimate
false_negative_estimate
```

Cost:

```text
cost_per_requested_generation
cost_per_delivered_video
cost_per_accepted_video
planner_cost
moderation_cost
generation_cost
regeneration_cost
wasted_generation_cost
```

Primary product cost metric:

```text
Cost per Accepted Video
```

---

# 31. MVP Implementation Scope

Implement now:

```text
API Gateway
Asset Upload
Image Ingest
Asset Registry
Input Safety
Policy Engine
Multimodal LLM Planner
GenerationPlan
GenerationSpec
Schema/Cross-field Validation
Basic Semantic Guard
Seedance Capability Resolver
Seedance Prompt Compiler
Async Orchestrator
Seedance Adapter
Basic Output Guard
Storage
Audit/Tracing
Cost Tracking
```

Do not overbuild yet:

```text
multi-provider dynamic optimizer
advanced HITL platform
complex automatic repair agent
multi-shot generation
video/audio input ingestion
full provenance infrastructure
many-layer agent framework
```

---

# 32. Suggested Initial Modules

```text
src/
├── api/
│   ├── assets/
│   └── generations/
│
├── assets/
│   ├── ingest_service
│   ├── validator
│   ├── normalizer
│   └── repository
│
├── safety/
│   ├── input_analyzers
│   ├── semantic_guard
│   ├── output_guard
│   └── risk_models
│
├── policy/
│   ├── engine
│   ├── rules
│   └── models
│
├── planner/
│   ├── multimodal_planner
│   ├── schemas
│   └── prompts
│
├── generation/
│   ├── plan
│   ├── spec
│   ├── validator
│   ├── capability_registry
│   └── router
│
├── providers/
│   └── seedance/
│       ├── adapter
│       ├── compiler
│       ├── client
│       └── schemas
│
├── orchestration/
│   ├── jobs
│   ├── queue
│   ├── retries
│   └── state_machine
│
├── storage/
│
├── observability/
│
└── config/
```

Exact language/framework is not prescribed by this document.

---

# 33. Coding Constraints for AI Agent

When implementing code from this architecture, follow these rules:

1. **Do not let the LLM call Seedance directly.**
2. **Do not pass raw LLM text directly to provider.**
3. **Use structured `GenerationPlan`.**
4. **Convert to canonical `GenerationSpec`.**
5. **Validate all LLM output deterministically.**
6. **Keep policy decisions outside the LLM.**
7. **Do not hard-code `if image -> I2V` as the long-term router abstraction.**
8. **Treat images and user text as untrusted input.**
9. **Provider-specific fields belong only in provider adapter/compiler.**
10. **All paid generation must pass preflight.**
11. **All generation must be async/idempotent.**
12. **Retry must be bounded.**
13. **LLM repair loops must be bounded.**
14. **Every generation must have trace/audit metadata.**
15. **Output safety is different from output quality/alignment.**
16. **HITL is for safety/compliance ambiguity, not normal creative questions.**
17. **Use `NEEDS_USER_INPUT` for missing creative intent.**
18. **Capability/model settings must be configurable, not scattered through business logic.**
19. **Never store provider credentials in LLM prompts/context.**
20. **Design interfaces so LLM model and video provider can be replaced independently.**

---

# 34. Architecture Summary

Canonical flow:

```text
Text/Image
   ↓
Asset Ingest
   ↓
Safety/Risk Signals
   ↓
Deterministic Policy
   ↓
Multimodal LLM Intent + Planner
   ↓
GenerationPlan
   ↓
GenerationSpec
   ↓
Deterministic Validation
   ↓
Semantic Guard
   ↓
Policy Preflight
   ↓
Capability Resolver / Router
   ↓
Provider Prompt Compiler
   ↓
Async Orchestrator
   ↓
Seedance Adapter
   ↓
Seedance Generation
   ↓
Technical + Safety + Alignment Guard
   ↓
Final Deterministic Decision
   ↓
Storage / Delivery
```

Core boundary:

```text
LLM = understand / propose
AI classifiers = detect
Policy engine = decide
Backend = enforce
Seedance = generate
Human = resolve high-risk uncertainty
```

This is the reference architecture that implementation should follow.
