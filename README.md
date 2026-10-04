# Milestone Search

Tìm event/alarm bằng văn bản, hồ sơ phân tích có phiên bản và AI tùy chọn. Backend PostgreSQL độc lập với database Milestone. Plugin có ba điểm chạy: Event Server, Management Client và Smart Client.

## Thành phần

| Thành phần | Mã nguồn |
|---|---|
| Event Server: nhận thông báo, hàng đợi đĩa, gửi lại | [CollectorPlugin.cs](plugins/MilestoneSearch/CollectorPlugin.cs), [Outbox.cs](plugins/MilestoneSearch/Outbox.cs) |
| Management Client: node cấu hình, quản trị hồ sơ | [Management.cs](plugins/MilestoneSearch/Management.cs) |
| Smart Client: workspace, WebView2, mở playback | [SmartClient.cs](plugins/MilestoneSearch/SmartClient.cs) |
| API, xác thực và phân quyền | [api.py](backend/search_engine/api.py) |
| Inbox, chuẩn hóa, hồ sơ, tìm kiếm và pgvector | [store.py](backend/search_engine/store.py), [database.py](backend/search_engine/database.py) |
| REST discovery, backfill, đối soát alarm | [connectors.py](backend/search_engine/connectors.py) |
| Embedding, query planner, RAG, ASR tùy chọn | [ai.py](backend/search_engine/ai.py) |
| Giao diện dùng chung trong hai Client | [web](web/) |

Hai dự án trong `docs/PsimEvent` và `docs/PsimManagement` là tài liệu tham khảo, không phải plugin đầu ra. Project này có GUID độc lập và không sửa hai dự án đó.

## Tìm "lần gần nhất ở mỗi camera"

Bật ô **"Mỗi camera chỉ lấy 1 kết quả gần nhất"** dưới khung tìm (web và tab AI Search của Smart Client), hoặc thêm "mỗi camera"/"từng camera" vào câu hỏi: mỗi camera chỉ còn bản ghi phù hợp mới nhất, xếp từ gần đến xa so với lúc tìm. Ví dụ "nam áo trắng đội mũ trong 10 phút qua mỗi camera" với 5 camera trả về tối đa 5 dòng. API: `POST /api/ask` với `"latest_per_source": true` (hoặc `POST /api/search` cùng tham số). Camera được phân biệt theo nguồn dữ liệu: cùng một camera vật lý có thể hiện hai dòng nếu vừa có best shot Active Guard vừa có alarm Milestone.

## Chạy backend

```powershell
python -m pip install -e '.[test]'
# Chỉ chạy init khi chưa có file; không ghi đè cấu hình hiện tại.
python -m search_engine.cli init --config config.local.json
# Sửa database thành PostgreSQL DSN trong file cục bộ.
python -m search_engine.cli serve --config config.local.json
```

Mở `http://127.0.0.1:8765`, dùng token của đúng principal trong cấu hình cục bộ. Không có mật khẩu mặc định. `config.local.json`, thư mục `data` và `dist` được loại khỏi version control. Trên máy hiện tại database ứng dụng là `milestone_search`; database kiểm thử là `milestone_search_test`.

`python -m search_engine.cli seed --config config.local.json` thêm năm alarm **mô phỏng**; không sử dụng làm bằng chứng nghiệp vụ. Các nguồn demo có quyền riêng. Cấu hình cấp quyền nguồn thật phải dùng đúng cặp `[site_id, source_id]`; collector cần site trong `sites`.

Triển khai production trên Windows: build `MilestoneSearch.Backend.exe` bằng `./scripts/build-backend.ps1`; chạy exe là cài và khởi động service `MilestoneSearchBackend`. Xem [DEPLOYMENT](docs/DEPLOYMENT.md#backend-dạng-windows-service-exe).

## Build và kiểm thử

```powershell
./scripts/verify.ps1
./scripts/build-plugin.ps1
```

Ba plugin target .NET Framework 4.8 và có gói riêng cho Event Server, Management Client, Smart Client. Build với thư viện MIP cài trên máy; gói không chứa DLL SDK Milestone. Hướng dẫn cài và cấu hình: [DEPLOYMENT](docs/DEPLOYMENT.md). Phạm vi đã kiểm chứng và phần còn thiếu: [ACCEPTANCE](docs/ACCEPTANCE.md).

API OpenAPI tại `/docs` và `/openapi.json`. Xuất ra file: `python -m search_engine.cli openapi --config config.local.json --file docs/openapi.json`.

## Trạng thái

Đã có code cho ba plugin, backend, giao diện và các adapter model. REST discovery đã kết nối hệ XProtect thử nghiệm; Ollama chat/embedding đã được chạy smoke test trên máy phát triển. Plugin chưa được cài/nghiệm thu trong các host XProtect đang chạy. Chưa tự động đồng bộ quyền Milestone vào backend, chưa có connector streaming metadata/vendor/Incident Manager hoàn chỉnh. Xem bảng nghiệm thu trước khi dùng production.
