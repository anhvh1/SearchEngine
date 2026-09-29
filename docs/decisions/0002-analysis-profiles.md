# ADR-0002: Tích hợp mới thông qua hồ sơ phân tích có phiên bản

## Trạng thái

Proposed — mở rộng ADR-0001; chưa triển khai.

## Ngày

2026-09-16

## Bối cảnh

Nguồn event/AI tiếp tục mở rộng. Code theo từng tên alarm làm hệ khó bảo trì; model tự đoán mọi payload không bảo đảm đúng nghĩa. Milestone cung cấp danh mục event types, alarm messages và interface nhận dữ liệu nhưng không tự cung cấp đầy đủ schema nghiệp vụ vendor.

## Đề xuất

Tách discovery, collector, profile service, analysis workers và search. Profile khai báo phạm vi nguồn, field mapping, bước phân tích, output schema, ACL/retention/quota; có test, version, activate và rollback. Nguồn lạ nhận/tìm cơ bản theo chính sách, không tự kích hoạt phân tích media. Model đề xuất mapping và xử lý theo schema, không tự quyết định policy.

## Đánh đổi

Tăng công cụ quản trị và kiểm thử nhưng giảm sửa plugin khi thêm nguồn chuẩn. Không loại bỏ nhu cầu connector cho dữ liệu ngoài Milestone hoặc parser cho định dạng mới. Tránh cả hai cực: hard-code mọi tích hợp và để LLM tự quyết định ý nghĩa/quyền/lưu trữ.

## Căn cứ

[Kiến trúc V2 và phạm vi chức năng](../../research/milestone/ARCHITECTURE-V2.md).
