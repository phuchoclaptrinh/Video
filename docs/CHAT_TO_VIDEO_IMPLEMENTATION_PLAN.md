# Kế hoạch phát triển: từ chat đến video hoàn chỉnh

## 1. Mục tiêu sản phẩm

Người dùng mô tả video bằng ngôn ngữ tự nhiên trong một cuộc chat. Hệ thống dùng ngữ cảnh đã lưu để lập kịch bản 1-5 cảnh, cho xem và chỉnh kịch bản, tạo tư liệu tham chiếu, dựng từng clip 8 giây, kiểm tra tính liên tục, tạo một giọng đọc xuyên suốt, ghép và xuất một MP4 có thể xem/tải ngay trong chat. Mỗi bước có trạng thái, chi phí dự kiến, khả năng thử lại và lịch sử để mở lại sau.

Luồng đích:

`Yêu cầu trong chat -> hiểu ý định + ngữ cảnh -> StoryPlan có phiên bản -> duyệt -> tạo ảnh/giọng -> tạo clip tuần tự -> kiểm tra từng cảnh -> ghép âm thanh/hình ảnh -> kiểm tra MP4 -> trả kết quả trong chat`

**Giới hạn thực tế:** các clip sinh riêng bằng model video không thể được bảo đảm liền mạch tuyệt đối như một cú quay liên tục. Hệ thống phải đo, báo chất lượng và chỉ sửa cảnh lỗi; không nên hứa rằng một ảnh chung hoặc một hiệu ứng chuyển cảnh sẽ giải quyết mọi sai khác.

## 2. Hiện trạng đã kiểm tra trong code

| Thành phần | Hiện có | Khoảng cách tới mục tiêu |
| --- | --- | --- |
| Chat và lịch sử | Lưu hội thoại/tin nhắn trong SQLite theo tài khoản; có ghi nhớ do người dùng tự thêm. | Chat gửi tối đa 24 tin gần đây và 20 ghi nhớ, chưa có tóm tắt dài hạn theo dự án. Planner video chỉ nhận prompt mới, chưa nhận ngữ cảnh chat hoặc ghi nhớ. |
| Tạo video trong chat | Chế độ `Tạo video` gọi `/plans`, lưu một thẻ kế hoạch trong hội thoại, rồi gọi `/generate`. | UI cố định 8 giây, 16:9, phong cách cinematic. Dữ liệu kế hoạch/job được nhúng JSON vào `content` tin nhắn; frontend gửi bản sao job về backend. Chưa có chỉnh sửa kế hoạch trong chat. |
| Planner | OpenAI sinh `BookStoryPlan` 1-5 cảnh và kiểm tra schema. | Prompt và schema chỉ dành cho podcast sách với một nữ host/studio cố định. Chưa có scene state, thời điểm lời thoại, kế hoạch chuyển cảnh hoặc ước lượng thời lượng lời đọc dựa trên âm thanh thật. |
| Hình ảnh tham chiếu | Tạo một ảnh neo chung và có hàm trích frame cuối clip. | Khi ảnh neo chung tồn tại, PoYo dùng ảnh đó làm `FIRST_FRAME` cho mọi clip; nhánh dùng frame cuối clip trước không được chọn. Có nguy cơ mỗi cảnh lại bắt đầu từ cùng tư thế và tạo cú nhảy hình. |
| Giọng nói | TTS đọc toàn bộ lời dẫn thành một file; FFmpeg đặt file này lên video ghép. | Chưa đo độ dài và các khoảng lặng của audio trước khi render. Bộ lọc `apad` có thể kéo audio ngắn thành khoảng im lặng đến hết video. |
| Ghép và QA | FFmpeg chuẩn hóa kích thước/fps rồi nối clip, mã hóa MP4; kiểm tra file có audio/video và giải mã được. | Nối hình là ghép thẳng; chưa kiểm tra lệch màu, vị trí nhân vật, nhảy chuyển động, khớp lời nói và khoảng lặng. |
| Chạy lại khi lỗi | Có lưu job, retry ghép, một số trạng thái phục hồi. | Chưa có báo cáo lỗi theo cảnh, ngân sách retry/chi phí rõ trong chat, hoặc sửa riêng cảnh có chất lượng kém. |

Vị trí code chính: `frontend/src/app/chat/page.tsx`, `backend/app/services/platform.py`, `backend/app/main.py`, `ai-service/app/pipelines/story_planner.py`, `ai-service/app/prompts/video_story_planner.md`, `ai-service/app/providers/poyo_veo_compiler.py`.

## 3. Thay đổi kiến trúc cần làm

### 3.1 Tách dự án video khỏi nội dung tin nhắn

Tạo thực thể `video_projects` (chủ sở hữu, conversation_id, brief, trạng thái), `story_plan_versions` (JSON theo schema, version, lý do sửa), `scenes` (thứ tự, clip 8 giây, lời thoại, prompt, trạng thái đầu/cuối, job_id), `media_assets` (ảnh neo, frame cuối, giọng đọc, MP4, checksum), `quality_reports` (các phép đo và quyết định sửa). Tin nhắn chat chỉ tham chiếu `project_id`/`render_id`; backend lấy trạng thái job thật từ database. Không để client ghi một đối tượng job tùy ý vào tin nhắn.

Mọi bảng có `user_id`; endpoint kiểm tra quyền sở hữu. Thêm migration có thể chạy trên dữ liệu SQLite hiện tại. Khi có nhiều worker/người dùng đồng thời, chuyển metadata sang PostgreSQL và media sang lưu trữ đối tượng; không đổi API công khai.

### 3.2 Quản lý ngữ cảnh cho LLM

Chia ngữ cảnh thành: (1) yêu cầu hiện tại, (2) ghi nhớ người dùng đã chủ động lưu, (3) tóm tắt hội thoại/dự án, (4) `visual_bible` và `voice_bible` của video, (5) StoryPlan phiên bản đã duyệt, (6) trạng thái/kết quả của các cảnh đã tạo. Ghi rõ nguồn và thời điểm cập nhật từng mục; thay đổi ở chat phải tạo phiên bản kế hoạch mới nếu ảnh hưởng video.

Planner nhận `project_context` có cấu trúc thay vì chỉ chuỗi prompt. Tóm tắt hội thoại khi vượt ngân sách token; lưu cả bản tóm tắt và liên kết đến tin nhắn nguồn. Chỉ đưa dữ liệu liên quan vào lần lập/sửa kịch bản; không gửi toàn bộ lịch sử thô. Quy tắc hệ thống và dữ liệu người dùng phải tách vai trò trong prompt.

### 3.3 StoryPlan dùng được cho nhiều loại video

Giữ mẫu podcast sách như một preset, thêm schema chung: `goal`, `audience`, `format`, `aspect_ratio`, `visual_bible`, `voice_bible`, `duration_target`, `reference_strategy`, `scenes[]`. Mỗi cảnh có `purpose`, `video_prompt`, `voice_script`, `start_state`, `end_state`, `transition_intent`, `duration_seconds=8`, và mốc thời gian mong muốn của lời dẫn. Giới hạn tối đa 5 cảnh ở tầng validator, không chỉ trong prompt.

LLM chọn số cảnh nhỏ nhất đủ kể chuyện. Validator kiểm tra tổng thời lượng, trật tự cảnh, lời thoại không lặp, nhân vật/bối cảnh bất biến khi yêu cầu liên tục, và trạng thái đầu cảnh sau tương thích với cuối cảnh trước. Cho người dùng sửa brief hoặc từng cảnh rồi lập lại **bản nháp**, không tự sinh clip trả phí sau mỗi lần chat. Sau khi duyệt, đóng băng `plan_version` dùng để render.

### 3.4 Liên tục hình ảnh giữa các clip

Ảnh neo chung định nghĩa nhân vật, trang phục, đạo cụ, ánh sáng, góc máy và bảng màu. Clip đầu dùng ảnh neo làm frame đầu. Từ clip thứ hai, ưu tiên frame cuối clip trước làm frame đầu; đưa `visual_bible` vào prompt để tránh trôi nhân vật/bối cảnh. Nếu provider/model đã kiểm chứng hỗ trợ cả frame đầu và cuối, có thể cấp thêm frame đích cho chuyển động; phải kiểm tra với provider thực tế trước khi bật mặc định.

Sau mỗi clip: trích 3-5 frame ở cuối và đầu, loại frame mờ/lỗi, so sánh vị trí chủ thể, màu, sáng, bố cục, vật thể chính và nhảy chuyển động. Lưu chỉ số cùng ảnh đối chiếu. Nếu vượt ngưỡng, thử sửa prompt hoặc chỉ tạo lại clip kế tiếp, tối đa số lần cấu hình được và luôn tính chi phí mới. Để tránh trôi dần qua 5 cảnh, đối chiếu thêm với ảnh neo gốc; không thay frame cuối bằng ảnh neo gốc một cách âm thầm.

Lúc ghép, chọn điểm cắt ở khoảng frame ổn định và trim vài frame đầu/cuối nếu có artifact. Dùng hard cut khi hai frame khớp; chỉ dùng chuyển mềm rất ngắn cho kiểu cảnh phù hợp. Không dùng crossfade để che nhân vật/đạo cụ bị thay đổi, vì dễ tạo bóng đôi. Nếu continuity kém, báo `NEEDS_REVIEW` và cho xem cặp frame lỗi.

### 3.5 Lời nói và timeline không có khoảng trống ngoài ý đồ

Tiếp tục dùng **một lần TTS cho toàn bộ lời dẫn**, một voice/pacing cố định. Trước khi tạo clip trả phí, ước lượng số từ cho `8 × số cảnh` giây; sau khi tạo TTS, đo thời lượng thật và phát hiện khoảng lặng đầu, cuối và giữa câu. Dùng transcript alignment/ASR để gắn mốc thời gian cho câu/đoạn và kiểm tra lời đọc đúng nội dung.

Nếu audio ngắn hoặc dài hơn timeline, ưu tiên sửa lời dẫn và tạo lại TTS. Có thể điều chỉnh tốc độ rất nhẹ trong giới hạn đã đo; không dùng `apad` như cách hoàn tất một bản audio thiếu lời. Các khoảng nghỉ có dụng ý được ghi trong kịch bản; khoảng im lặng ngoài ý đồ phải bị cảnh báo. Timeline hình ảnh lấy theo thời lượng audio đã duyệt; câu nói được phân cho cảnh ở điểm ngắt tự nhiên, không cắt lời giữa âm tiết. Nhạc nền (nếu có) là tùy chọn và không thay thế lời dẫn thiếu.

### 3.6 Worker, chi phí và trạng thái

Chuyển quy trình thành các bước có thể chạy lại độc lập: `PLANNING -> REVIEW -> PREPARING_MEDIA -> GENERATING_SCENE_n -> SCENE_QA -> MERGING -> FINAL_QA -> COMPLETED`; các lỗi có mã cụ thể và bước có thể thử lại. Dùng idempotency key theo `project_id + plan_version + scene_index + attempt` để tránh tạo clip trùng khi mạng lỗi hoặc reload UI. Lưu artifact trung gian ngay sau mỗi bước; job đang chạy được backend/worker cập nhật kể cả khi đóng trình duyệt.

Trước khi xác nhận render, UI hiển thị số cảnh, loại API sẽ dùng và ước lượng chi phí; khi sửa một cảnh, hiển thị chi phí bổ sung. Chỉ worker/server được thay đổi trạng thái job. Chat nhận tiến độ từ endpoint dự án/render; giao diện không cần ghi bản sao trạng thái về DB mỗi lần poll.

## 4. API và giao diện đề xuất

- `POST /api/v1/video-projects`: tạo project từ brief và `conversation_id`.
- `POST /api/v1/video-projects/{id}/plans`: lập hoặc sửa StoryPlan từ context đã lưu.
- `GET /api/v1/video-projects/{id}`: brief, plan version, cảnh, assets, tiến độ, QA, chi phí.
- `POST /api/v1/video-projects/{id}/renders`: xác nhận chi phí và render đúng plan version.
- `POST /api/v1/video-projects/{id}/scenes/{index}/retry`: sửa riêng cảnh, có lý do và chi phí.
- `GET /api/v1/video-projects/{id}/video`: chỉ trả MP4 đã qua final QA.

Trong chat: một thẻ project hiển thị kịch bản có thể chỉnh, ảnh neo, timeline lời dẫn, nút duyệt; sau đó hiển thị từng cảnh, cảnh bị lỗi, nút sửa cảnh, và video cuối. Chat tiếp theo như “giữ nhân vật nhưng đổi câu mở đầu” cập nhật brief/plan version của đúng project. Các endpoint `/plans` và `/generations` hiện tại vẫn dùng được cho studio cũ trong giai đoạn chuyển đổi.

## 5. Thứ tự triển khai và tiêu chí hoàn thành

| Giai đoạn | Công việc chính | Chấp nhận khi |
| --- | --- | --- |
| 1. Nền dữ liệu và context | Migration project/plan/scene/asset; server sở hữu trạng thái; gom ngữ cảnh chat + ghi nhớ + tóm tắt. | Reload hoặc đổi thiết bị vẫn mở đúng plan/job/video; không thể gắn job của người khác; planner nhận đúng preference đã lưu. |
| 2. Planner tổng quát | Schema/version mới; preset podcast sách; preview và sửa 1 cảnh; validator số cảnh/timeline/continuity. | Yêu cầu ngoài podcast sách tạo được 1-5 cảnh phù hợp; không có cảnh sai schema; chỉnh plan chưa gọi provider video. |
| 3. Âm thanh trước render | TTS một track; đo duration, alignment, silence; sửa script/TTS khi lệch. | Audio có cùng giọng, bắt đầu/kết thúc sát timeline, không có đoạn im lặng ngoài ý đồ vượt ngưỡng QA; không cần `apad` để che thiếu audio. |
| 4. Nối cảnh và QA | Frame cuối -> frame đầu, kiểm tra với ảnh neo; scene QA; retry riêng cảnh; chọn điểm cắt. | Bộ ví dụ 2/3/5 cảnh giữ nhân vật/bối cảnh; lỗi nhảy cảnh được đánh dấu và sửa riêng; giới hạn retry/chi phí được tôn trọng. |
| 5. Ghép và khôi phục | FFmpeg timeline rõ ràng; final QA video/audio; trạng thái worker/idempotency; UI tiến độ và xem/tải. | MP4 phát được cả hình lẫn tiếng; thời lượng đúng; restart backend hoặc reload trang không tạo thêm job và vẫn tiếp tục/khôi phục được. |

**Thứ tự MVP đáng làm trước:** giai đoạn 1 -> 2 -> 3 -> 4 -> 5. Có thể làm giao diện dự án từng phần theo mỗi giai đoạn. Trước khi chạy PoYo thật, dùng mock provider và clip fixture để kiểm tra lỗi ghép, audio và trạng thái mà không tốn credit.

## 6. Bộ kiểm thử và số đo

- Unit/contract: schema StoryPlan, context precedence, ownership, versioning, idempotency, chính sách retry, ánh xạ lỗi provider.
- Integration: 1/2/3/5 cảnh bằng provider giả; TTS ngắn/dài, audio không có tiếng, clip thiếu audio, clip sai kích thước/fps, file hỏng, restart worker khi đang render.
- Media QA: decode toàn bộ MP4; audio/video duration và đồng bộ; đo khoảng lặng đầu/cuối/giữa; so sánh frame biên và ảnh neo; lưu báo cáo QA theo project.
- Đánh giá thủ công trước live: ít nhất 10 brief đa dạng (podcast, sản phẩm, kể chuyện, có/không có nhân vật) với rubric 1-5 cho nội dung, giọng đọc, liên tục hình, chuyển cảnh và chi phí. Chỉ dùng một số ít mẫu PoYo thật sau khi các bài kiểm thử giả đã qua.

Ngưỡng khởi đầu để thử nghiệm, cần hiệu chỉnh trên video thực: lệch audio so với timeline dưới 0,25 giây; khoảng lặng ngoài ý đồ đầu/cuối dưới 0,2 giây; khoảng lặng giữa câu trên 0,5 giây phải được review; tối đa 2 lần tạo lại cho mỗi cảnh. Ngưỡng hình ảnh cần hiệu chỉnh theo loại cảnh, không áp dụng một điểm số duy nhất cho mọi video.

## 7. Việc nên làm đầu tiên trong repo

1. Viết migration và model `video_project`/`story_plan_version`/`scene`/`asset`; đổi thẻ video trong chat sang tham chiếu `project_id`.
2. Đổi endpoint planner để nhận brief + context có cấu trúc; giữ prompt podcast sách như preset, thêm schema tổng quát và validation.
3. Tạo bài kiểm thử tái hiện chính xác việc ảnh neo đang được dùng cho mọi clip PoYo, rồi sửa chiến lược frame đầu cảnh sau.
4. Thêm bước audio QA trước khi submit clip trả phí; không duyệt audio chỉ vì FFmpeg giải mã được.
5. Thêm scene QA và màn xem cặp frame lỗi; sau đó mới tối ưu hiệu ứng chuyển cảnh và chi phí.

Không cần thay toàn bộ hệ thống trong một lần: mỗi giai đoạn nên có migration, test và UI hoạt động độc lập. Các tài liệu cũ mô tả “luôn năm cảnh” cần cập nhật sau giai đoạn 2 vì planner hiện đã cho phép 1-5 cảnh.
