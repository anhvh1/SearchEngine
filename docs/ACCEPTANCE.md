# Phạm vi nghiệm thu

## Đã có code và kiểm thử cục bộ

- Envelope có timezone, khóa site/kind/source ID, chống trùng và bản cập nhật cũ.
- PostgreSQL inbox/profile/audit/context, full text GIN, pgvector cosine và ACL trước xếp hạng.
- Worker embedding nền, bỏ kết quả nếu nội dung thay đổi trong lúc inference, vô hiệu hóa vector khi record cập nhật.
- Hồ sơ immutable version, thử mẫu, activate/rollback, conflict, replay, retention.
- API role collector/admin/reader, deny-by-default source grants, không nhận ACL do client cung cấp.
- REST paging, giữ checkpoint khi lỗi, chặn pagination khác origin, queue đối soát có trạng thái lỗi.
- Plugin Event Server có outbox/ACK/retry/dead-letter; Management ItemNode và lưu cấu hình; Smart Client WPF workspace + WebView2 + playback bridge.
- Giao diện browser: đăng nhập, tìm, bằng chứng, hồ sơ, discovery, vận hành, desktop/mobile.

## Cần nghiệm thu trên host thực

- Load plugin trong Event Server/Management/Smart Client đúng phiên bản và license.
- Payload từng hãng, nguồn, quyền, reload cấu hình, mất mạng/restart/quota, update và delete alarm.
- Playback camera/time chính xác và trường hợp thiếu quyền/hết recording.
- Backend API Gateway/token, discovery thực tế, kiểm tra nguồn REST và SDK có cùng identity.
- Chat và embedding đã chạy thật trên máy phát triển bằng Ollama (`qwen3:4b`, `qwen3-embedding:0.6b`); semantic search và RAG trả trích dẫn hợp lệ trong smoke test. Cần bộ dữ liệu nghiệp vụ và benchmark tải trước production. ASR vẫn chưa cài model giọng nói.

## Chưa hoàn tất so với toàn bộ tầm nhìn V2

- SSO và đồng bộ quyền Milestone theo user/role, thu hồi quyền tự động.
- Metadata streaming/playback collector; adapter dữ liệu FaceMe/Active Guard nằm ngoài alarm; Incident Manager API.
- VLM snapshot/clip, tương quan chuỗi sự kiện, fine-tuning/evaluation corpus quy mô lớn.
- Quản lý token REST tự refresh và đối soát delete chắc chắn. Đã có lịch backfill/discovery/retention theo cấu hình.
- Toàn bộ rule/camera/zone/time context và ngữ cảnh SOP có UI phiên bản; hiện snapshot context chứa event types/alarm messages.
- ANN tuning, nhiều worker/HA, stress test, installer service và migration tự động giữa các schema version. Backend đã ghi/kiểm tra schema version và từ chối version không hỗ trợ.

Không gọi bản build này là hoàn thành production chỉ vì compile và unit test đạt. Mọi phần chưa nghiệm thu phải giữ hiển thị giới hạn trong vận hành.
