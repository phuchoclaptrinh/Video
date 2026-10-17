# Workspace ảnh → kịch bản → video

## Bố cục

VO3 Studio là workspace sáng, ưu tiên nội dung trực quan thay vì nhiều tab riêng. Thanh trên cùng gồm tên dự án, ba bước Yêu cầu → Kịch bản → Thành phẩm (bước đã xong có dấu tích) và các nút trao đổi, thư viện, dự án mới, cài đặt. Màu sắc lấy từ biến CSS trong `globals.css`, đổi màu chủ đạo chỉ cần sửa `--accent`.

- **Yêu cầu:** bộ ảnh đầu vào, thông tin sản phẩm (tên, chất liệu, kích cỡ, giá, ưu đãi, khách hàng mục tiêu), tỷ lệ và tổng thời lượng. Planner coi thông tin này là dữ kiện thật của người bán; chế độ Clip trực tiếp vẫn dùng ô yêu cầu tự do. Chọn/kéo thả/dán tối đa 6 ảnh JPEG/PNG/WebP, tối đa 8 MB mỗi ảnh; có thể đổi ảnh chính. Chọn Một video hoặc Chiến dịch để lập 3/5/10 kịch bản.
- **Kịch bản:** storyboard từng cảnh, lời thoại, thời lượng và chỉ dẫn hình ảnh/chuyển động. Ảnh ở mỗi cảnh được ghi rõ là ảnh tham chiếu chung, không phải ảnh AI tạo riêng cho cảnh.
- **Thành phẩm:** trạng thái tạo cảnh, ghép video, trình phát có âm thanh và tải video cuối.
- **Model & thiết lập:** provider/model video và chế độ clip trực tiếp hoặc kịch bản nhiều cảnh.
- **Cài đặt:** key PoYo, Seedvis, OpenAI; provider/model ảnh; bộ nhớ và tài khoản.
- **Trao đổi với AI:** hộp thoại trong cùng dự án, giữ lịch sử và bộ nhớ.
- **Tên dự án:** mở lịch sử. **Thư viện video:** xem các video đã tạo.

Trên điện thoại, mỗi bước chiếm vùng làm việc riêng. Trên desktop, vùng nhập yêu cầu nằm bên trái và storyboard/video ở bên phải. Các đường dẫn `/chat`, `/gallery`, `/login` vẫn chuyển về `/`; dữ liệu cũ không bị xóa.

## Luồng end-to-end

1. Chọn ảnh và nhập thông tin sản phẩm. Mặc định yêu cầu video 20 giây; model có thể cần làm tròn theo thời lượng cảnh được hỗ trợ.
2. Backend kiểm tra quyền sở hữu ảnh, đọc file đã chuẩn hóa, tạo JPEG tối đa 1024px để gửi sang planner.
3. Planner nhận **cả bộ ảnh, yêu cầu, context hội thoại và ghi nhớ** trong cùng request đa phương thức. Khi có ảnh, mặc định ưu tiên kịch bản quảng cáo bán hàng.
4. AI trả bản nháp có định hướng hình ảnh, lời thoại và tối đa 5 cảnh. Không tự bịa thông số sản phẩm hay chữ không đọc được trong ảnh.
5. Ảnh chính trở thành first frame/anchor cho video. Những ảnh còn lại cung cấp chi tiết cho planner, không bị gửi vượt giới hạn ảnh của adapter video. Khi không có ảnh, tùy chọn tự tạo ảnh tham chiếu có thể phát sinh phí API ảnh.
6. Xem/chỉnh bản nháp, xác nhận phí rồi nhấn **Duyệt và tạo video**. UI gọi duyệt trước, sau đó yêu cầu render đúng `expected_draft_id`. Nếu phiên bản đổi, backend trả 409 thay vì tạo nhầm bản mới.
7. Pipeline hiện có tạo các cảnh, kiểm tra và tự ghép. UI cập nhật trạng thái bằng polling; khi hoàn thành có thể phát hoặc tải video.

Lập kịch bản vẫn tiêu usage OpenAI. Không có bước tự động tạo video có phí khi vừa nhập yêu cầu.

Luồng chiến dịch lưu tiến độ và các bản nháp độc lập trong DB, có thử lại phần thiếu và xuất JSON. Xem [MARKETING_CAMPAIGNS.md](MARKETING_CAMPAIGNS.md) để biết cách dùng, điều kiện worker và giới hạn.

### Chạy nhiều video song song

- Trong khung chiến dịch, **Tạo tất cả video** duyệt và gửi lần lượt mọi kịch bản chưa tạo (bỏ qua bản nháp cũ hoặc còn chỗ trống chờ đọc ảnh), sau một lần xác nhận phí. Gửi xong, các video chạy song song; mỗi thẻ kịch bản hiện trạng thái và số cảnh.
- Các cảnh trong **một** video vẫn chạy lần lượt vì cảnh sau nối từ khung cuối cảnh trước. Số luồng tăng theo số video chạy cùng lúc.
- **Luồng sản xuất** ở đầu canvas liệt kê mọi video đang tạo trong dự án; bấm một thẻ để mở tiến độ. Với Seedvis, nhãn `Seedvis x/y luồng · n cảnh chờ` lấy từ `GET /api/v1/seedvis/capacity` (chỉ trả tổng số, không lộ key). Hết luồng thì cảnh tự xếp hàng, thêm key ở trang admin để tăng số luồng.
- Ở chế độ **Một video**, có thể lập kịch bản mới trong lúc video trước đang tạo; video cũ vẫn hiện trên bảng.

## Điều kiện chạy thật

- Cấu hình OpenAI planner và provider video trong Cài đặt.
- Dùng model có native audio, ảnh tham chiếu và thời lượng cố định để tạo kịch bản nhiều cảnh có thoại. Model khác dùng **Clip trực tiếp**.
- PoYo cần `PUBLIC_BASE_URL` HTTPS công khai trỏ đến backend để tải ảnh/frame. Seedvis truyền ảnh inline nên không cần tunnel cho việc này.
- Backend, AI service và worker phải chạy để tác vụ tiến triển. Chỉ mở frontend không đủ.
- Giọng và nhận diện nhân vật của native video là best-effort; UI mới không khóa được giọng hay khuôn mặt tuyệt đối.
- Base64 ảnh chỉ được gửi đến AI service/OpenAI, không lưu trong bản nháp, context hay chat. Asset gốc được lưu và kiểm tra quyền trên backend.

Xem [MULTI_PROVIDER_SETUP.md](MULTI_PROVIDER_SETUP.md), [SCENE_CONTINUITY_SETUP.md](SCENE_CONTINUITY_SETUP.md) và [MVP_QUICKSTART.md](MVP_QUICKSTART.md).

## Các file chính

| File | Vai trò |
| --- | --- |
| `frontend/src/components/workspace/FlowWorkspace.tsx` | Bố cục, các bước, lịch sử và chọn bản nháp |
| `WorkspaceComposer.tsx` | Ảnh, brief và thiết lập tạo |
| `WorkflowStage.tsx` | Storyboard, tiến độ và video thành phẩm |
| `AssetCanvas.tsx` | Thư viện, xem lớn, tải và thao tác tác vụ |
| `WorkspaceInspector.tsx` | Hội thoại dự án |
| `WorkspaceSettings.tsx` | Key và bộ nhớ |
| `frontend/src/hooks/use-video-workspace.ts` | Upload, plan, duyệt/render và polling |
| `frontend/src/hooks/use-image-preview.ts` | Preview file và thu hồi object URL |
| `frontend/src/app/globals.css` | Design tokens (màu, bo góc, đổ bóng), font Inter, nút, ô nhập, hộp thoại, đăng nhập |
| `frontend/src/app/workflow.css` | Bố cục workspace, khung yêu cầu, storyboard, thành phẩm và responsive |
| `backend/app/services/platform_planning.py` | Kiểm tra asset, gửi ảnh/context cho planner |
| `ai-service/app/pipelines/video_api.py` | Hợp đồng planner nội bộ |
| `ai-service/app/pipelines/story_planner.py` | Prompt grounding và request đa phương thức |

Định dạng input hình ảnh theo [tài liệu OpenAI chính thức](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat). Giữ model planner hiện có, không tự đổi model hay key.

## Khởi động frontend

Trong `Create_video_VO3/frontend`:

```powershell
$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8100'
npm.cmd run dev -- --hostname 127.0.0.1 --port 3000
```

Mở `http://127.0.0.1:3000`. Khởi động lại backend/AI service để nhận hợp đồng planner mới.

## Kiểm tra

- Test backend: ảnh thực sự được gửi cho planner, giới hạn kích thước, chặn asset của người khác, ảnh bị mất, không lưu base64, từ chối render sai bản nháp.
- Test AI: ảnh và context trong cùng request, giữ model/schema, tương thích text-only, chặn URL tùy ý ở input nội bộ.
- Test trình duyệt qua API giả lập: upload → bản nháp → consent → duyệt/render → tạo cảnh → ghép → phát/tải; desktop và mobile.
- `npm.cmd run typecheck`, `npm.cmd run lint`, `npm.cmd run build`.

Các bài kiểm tra không gọi tạo ảnh/video thật và không đánh giá chất lượng đầu ra của provider live.

Kết quả kiểm tra sau cập nhật nhiều ảnh/chiến dịch: 311 test AI service và bộ 260 test backend qua; các thay đổi cuối về idempotency, giữ góc bán hàng khi sửa và xử lý lỗi có thêm các lượt test tập trung. Backend bỏ qua `test_video_worker.py` vì môi trường chưa có Celery. Luồng chiến dịch và một video qua ở 1440, 1920, 768, 390 và 320px; thêm kiểm tra lỗi/upload/revision ở desktop và mobile. Typecheck và lint không có lỗi, lint còn 9 cảnh báo dùng thẻ `img`. Build trong bản sao QA compile và prerender thành công; bước đóng gói standalone có cảnh báo symlink Windows do bản sao dùng junction `node_modules`, chưa xác nhận artifact deploy từ bản sao này.


## Trang quản trị (`/admin`)

Admin là email trong `ADMIN_EMAILS` của `.env` (phân cách bằng dấu phẩy) hoặc tài khoản được admin cấp quyền trong trang. Để trống `ADMIN_EMAILS` thì mọi tài khoản đều là admin, giống hành vi cũ khi chạy một mình. Biểu tượng khiên trên thanh trên cùng chỉ hiện với admin.

- **Tổng quan:** số người dùng, video 24 giờ/7 ngày, video đang chạy, tỉ lệ lỗi, số cảnh đã tạo theo provider, luồng Seedvis đang dùng, biểu đồ 14 ngày và lỗi hay gặp.
- **Video & hàng đợi:** video của mọi người dùng theo trạng thái, tiến độ từng cảnh, key Seedvis đang dùng; hủy video đang chạy; hàng đợi cảnh đang chờ hoặc đang tạo.
- **API key:** nhiều key Seedvis, key PoYo và OpenAI. Chỉ admin xem và sửa được key; người dùng thường không còn thấy key trong Cài đặt.
- **Người dùng:** tìm theo email, số dự án và video, lần hoạt động gần nhất, số credit (bấm để cộng/trừ kèm ghi chú và xem lịch sử); cấp/bỏ quyền admin, khóa/mở khóa (khóa thì đăng xuất ngay và không đăng nhập được). Không tự khóa hay tự bỏ quyền của chính mình; admin trong `.env` chỉ đổi được trong `.env`.
- **Credit:** bảng giá áp dụng cho thành viên, admin không bị trừ.
  - Tặng một lần cho tài khoản mới (mặc định 100). Tài khoản có từ trước khi có credit cũng được tặng một lần khi nâng cấp.
  - Render video tính theo "N credit / S giây" cho từng model `provider:model` (vd 40 credit / 8 giây = một clip 8 giây). Clip dài hơn S giây tính thêm theo từng khối S giây (Seedance 20 credit / 5 giây: clip 15 giây = 60). Model chưa đặt giá dùng giá mặc định; provider `fake` (demo) không tính.
  - Ô "Hiện" cạnh mỗi model: bỏ chọn để ẩn model khỏi người dùng (server cũng chặn); admin vẫn dùng được.
  - Mỗi lần lập kịch bản (kể cả từng kịch bản trong chiến dịch), mỗi lần lập chiến lược chiến dịch và mỗi ảnh anchor/keyframe tính một mức cố định.

### Cách trừ credit

- **Render:** trước khi bắt đầu một video, server kiểm tra số dư đủ cho toàn bộ (giá clip của từng cảnh + một ảnh mỗi cảnh). Mỗi cảnh bị trừ khi gửi sang provider. Worker tự hoàn nếu cảnh kết thúc `FAILED`/`CANCELLED` mà không ra video; cảnh đã ra video thì không hoàn, kể cả khi tạo lại vì lỗi chất lượng.
- **Lập kịch bản, chiến dịch và ảnh:** kiểm tra đủ credit trước khi gọi AI, chỉ trừ khi gọi thành công.
- **Thiếu credit:** API trả `402` kèm số cần và số còn lại. Giao diện hiện "Dự kiến dùng N credit · còn M" cạnh các nút có phí và cảnh báo khi không đủ. Số dư hiện trên thanh trên cùng, lịch sử giao dịch nằm trong Cài đặt.
- **Sổ giao dịch:** mọi biến động nằm trong `platform_credit_ledger` với mã tham chiếu duy nhất, nên gửi lại hay hoàn lại không bị tính hai lần. Để trống `ADMIN_EMAILS` thì mọi tài khoản là admin và không bị trừ.

Bảng `platform_users` được tự thêm cột `is_admin`, `disabled`, `created` khi backend khởi động; dữ liệu cũ giữ nguyên.
