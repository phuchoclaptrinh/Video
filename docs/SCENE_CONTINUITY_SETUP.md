# Nối cảnh tự động với PoYo

Project tạo tuần tự từng clip. Sau khi clip trước hoàn tất, backend trích frame cuối, cấp URL HTTPS có chữ ký hết hạn sau 24 giờ và gửi URL đó làm frame đầu cho clip tiếp theo. Có thể tải ảnh tham chiếu ban đầu lên giao diện để cố định nhân vật ngay từ clip đầu.

## Cấu hình bắt buộc

PoYo phải tải được ảnh từ backend qua HTTPS. Khi chạy local, hãy tạo HTTPS tunnel trỏ về cổng backend mà `scripts/run_local.py` in ra, không trỏ vào cổng frontend. Điền URL public vào `.env` ở thư mục gốc:

```env
PUBLIC_BASE_URL=https://your-public-backend.example.com
INTERNAL_API_KEY=your-stable-long-random-secret
```

URL phải truy cập được từ Internet và phục vụ đường dẫn `/api/v1/continuity-reference/...`. Giữ `INTERNAL_API_KEY` ổn định giữa các lần chạy để chữ ký URL đang được PoYo tải không mất hiệu lực khi ứng dụng khởi động lại. Khởi động lại `python scripts/run_local.py` sau khi sửa `.env`.

Nếu `PUBLIC_BASE_URL` không phải HTTPS, project không gửi batch trả phí. Giao diện cũng hiển thị trạng thái cấu hình để tránh tạo năm clip độc lập mà không có frame tham chiếu.

## Giới hạn

Mỗi cảnh vẫn là một tác vụ Veo riêng, nhưng cảnh tiếp theo được điều kiện hóa bằng frame cuối cảnh liền trước. Cách này cải thiện độ liên tục về nhân vật và bố cục, song chuyển động giữa hai lần sinh không được bảo đảm giống một cú quay liên tục. URL ảnh đã ký có hiệu lực tạm thời; không chia sẻ URL này.
