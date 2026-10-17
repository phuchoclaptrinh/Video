# LLM lập kịch bản podcast sách

Planner dùng quy tắc sáng tạo được chắt lọc từ `video_promt.txt`, gửi yêu cầu tới OpenAI Chat Completions API và kiểm tra kết quả thành cấu trúc dữ liệu trước khi hiển thị.

## Cấu hình

1. Mở **API key** trong VO3 Studio.
2. Nhập OpenAI API key tại **OpenAI LLM planner** rồi lưu.
3. Nhập tên sách và chủ đề hoặc cảm xúc muốn kể.
4. Xem năm phân đoạn, xác nhận tạo đủ năm clip rồi chờ hệ thống tự ghép thành một video.

LLM key được mã hóa trong credential vault của AI service. PoYo API key là credential riêng cho việc tạo video. Không gửi key LLM sang frontend khác ngoài lần nhập; backend chuyển tiếp yêu cầu lập kịch bản tới AI service.

Model mặc định là `gpt-4o-mini`; có thể thay bằng biến `OPENAI_PLANNER_MODEL` trong `.env`. API key cần được cấp quyền sử dụng API và có billing phù hợp.

## Giới hạn hiện tại

Planner sinh đúng năm clip, mỗi clip dài 8 giây, kèm một voice line tối đa 30 từ. Hệ thống gửi năm tác vụ tới video provider, chờ đủ kết quả rồi ghép thành một MP4 khoảng 40 giây và giữ âm thanh. Với PoYo, mỗi clip là một lượt tạo riêng và có thể bị tính phí; giao diện yêu cầu xác nhận trước khi gửi.

Phần ghép dùng FFmpeg qua gói `imageio-ffmpeg`, đã khai báo trong `backend/requirements.txt`. Khi thiết lập môi trường Python mới, cài dependencies backend bằng `python -m pip install -r backend/requirements.txt`.

LLM có thể không biết chính xác nội dung một số sách. Khi đó planner được hướng dẫn tránh bịa trích dẫn, số trang hoặc thông tin cụ thể; hãy cung cấp thêm chủ đề hoặc thông điệp mong muốn trong ô nhập.
