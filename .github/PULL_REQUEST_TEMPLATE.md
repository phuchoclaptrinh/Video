## Mô tả

<!-- Thay đổi gì và tại sao. Nếu sửa bug, ghi rõ nguyên nhân gốc. -->

Closes #

## Loại thay đổi

- [ ] `feat` — tính năng mới
- [ ] `fix` — sửa lỗi
- [ ] `refactor` — đổi cấu trúc, không đổi hành vi
- [ ] `perf` — tối ưu hiệu năng
- [ ] `docs` / `test` / `chore` / `ci`
- [ ] Breaking change

## Ảnh hưởng tới đâu

- [ ] backend
- [ ] ai-service (prompt engine / provider)
- [ ] frontend
- [ ] infra / CI-CD
- [ ] database (có migration)

## Đã kiểm tra

- [ ] `make test` chạy pass
- [ ] `make lint` không còn lỗi
- [ ] Đã chạy thử thủ công trên môi trường dev
- [ ] Có migration → đã chạy `alembic upgrade head` **và** `alembic downgrade -1` thử

## Checklist bắt buộc

- [ ] Không hardcode API key / secret trong code
- [ ] Biến môi trường mới đã thêm vào `.env.example` **và** `docker-compose.yml`
- [ ] Endpoint mới đã có kiểm tra quyền + rate limit
- [ ] Thay đổi liên quan gọi provider video → đã cân nhắc chi phí quota

## Ảnh chụp / video demo

<!-- Bắt buộc nếu đổi giao diện -->

## Lưu ý khi deploy

<!-- Cần đặt biến môi trường mới? Cần chạy lệnh thủ công? Có downtime không? -->
