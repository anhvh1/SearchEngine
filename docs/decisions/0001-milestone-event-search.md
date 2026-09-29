# ADR-0001: Thu nhận event/alarm và tìm kiếm qua backend riêng

## Trạng thái

Proposed — chưa triển khai, cần kiểm chứng trên release XProtect đích.

## Ngày

2026-09-15

## Bối cảnh

Người dùng cần tìm kiếm text/voice trong Milestone, khai thác alarm từ nhiều hệ tích hợp. Backup có 113 alarm, 74 snapshot và không có object/vendor payload ở các bảng alarm. Model chạy trực tiếp trong Event Server sẽ tăng rủi ro độ trễ và lỗi ảnh hưởng host.

## Đề xuất

Collector MIP BackgroundPlugin trong Event Server chỉ thu nhận và chuyển dữ liệu. Backend độc lập quản lý lưu bền, chuẩn hóa, chỉ mục và model. Smart Client WorkSpacePlugin hiển thị tìm kiếm hội thoại; SearchAgentPlugin bổ sung cho kết quả video. SQL backup dùng nghiên cứu/PoC, API và message được hỗ trợ dùng production.

## Lựa chọn khác

- Query trực tiếp VMS khi hỏi: đơn giản nhưng thiếu index ngữ nghĩa và phụ thuộc latency upstream.
- SQL production: phụ thuộc schema nội bộ và khó bảo toàn quyền.
- Service ngoài dùng API/MIP: vẫn là fallback hợp lệ nếu collector in-process không đạt kiểm tra tải/độ bền.

## Hệ quả

Phải quản lý eventual consistency, ACL, retention/xóa, replay, khoảng mất event và mapping version. Không cam kết exactly-once hoặc mọi metadata đều đi qua Event Server. Chưa chốt model/engine để tránh lựa chọn thiếu benchmark.

## Căn cứ

[Phân tích chi tiết và nguồn](../../research/milestone/EVENT-SERVER-SEARCH-ARCHITECTURE.md).
