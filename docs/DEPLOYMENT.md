# Cài đặt và cấu hình

## Backend PostgreSQL

Python 3.12+, PostgreSQL có extension `vector`, quyền tạo bảng. Khởi tạo extension bằng tài khoản quản trị một lần; sau đó dùng tài khoản ứng dụng riêng có quyền với bảng của project. Không dùng database Milestone làm database backend.

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
```

Tạo file bằng `python -m search_engine.cli init`. Trong `config.local.json`, đặt `database` là DSN PostgreSQL với database dành riêng. File chứa bí mật; giới hạn quyền đọc cho tài khoản chạy dịch vụ. `host` mặc định loopback. Nếu truy cập từ máy Milestone khác, đặt backend sau reverse proxy HTTPS; giới hạn body tối đa 12 MiB, timeout và kết nối đồng thời. Chạy **một process Uvicorn**; chưa hỗ trợ nhiều process worker trên cùng database.

Các role backend:
- `collector`: POST dữ liệu, chỉ site trong `sites`.
- `reader`: tìm và xem bằng chứng, chỉ cặp site/source trong `grants`.
- `admin`: quản trị toàn bộ hồ sơ, mẫu dữ liệu và vận hành. Chỉ cấp cho người được phép xem dữ liệu tất cả site. Admin không tự động có quyền reader.

Thu hồi/đổi token hoặc quyền trong cấu hình yêu cầu khởi động lại backend. Đây là cơ chế quyền ứng dụng riêng; không phải SSO hoặc đồng bộ quyền Milestone. Không dùng collector token làm tài khoản người dùng.

## Backend dạng Windows service (exe)

Build: `./scripts/build-backend.ps1` tạo `dist/MilestoneSearch.Backend-<thời gian>.zip` (một file `MilestoneSearch.Backend.exe`, không cần cài Python) và `dist/MilestoneSearch.Backend-source-<thời gian>.zip` (mã nguồn backend, web, test, script build).

Cài đặt: nhấp đúp `MilestoneSearch.Backend.exe`, chấp nhận UAC. Chương trình:
1. Chép exe vào `C:\Program Files\MilestoneSearch\Backend`.
2. Lần đầu tạo cấu hình `C:\ProgramData\MilestoneSearch\backend\config.json`: chép từ `config.json`/`config.local.json` đặt cạnh exe nếu có, nếu không thì tạo mới với token ngẫu nhiên và SQLite. Thư mục chỉ SYSTEM và Administrators đọc được. Lần sau giữ nguyên cấu hình.
3. Đăng ký service `MilestoneSearchBackend` (Automatic, tài khoản LocalSystem, tự restart khi lỗi sau 5/15/60 giây). Nếu database là PostgreSQL trên máy này, service khởi động sau service `postgresql*`.
4. Khởi động service và kiểm tra HTTP. Log: `C:\ProgramData\MilestoneSearch\backend\logs`.

Nâng cấp hoặc áp dụng thay đổi `database`: chạy lại exe mới/cũ. Sửa cấu hình khác: `MilestoneSearch.Backend.exe restart`. Lệnh khác: `status`, `start`, `stop`, `uninstall` (giữ cấu hình/dữ liệu), `console` (chạy foreground để gỡ lỗi), và các lệnh CLI `init`, `seed`, `import`, `backfill`, `discover`, `openapi` (mặc định dùng cấu hình trong ProgramData). Biến môi trường như `MILESTONE_USERNAME`/`MILESTONE_PASSWORD` phải đặt ở mức Machine rồi restart service. Exe không tự mở Windows Firewall.

## Build plugin

Chạy `scripts/build-plugin.ps1`. Gói zip trong `dist` gồm `MilestoneSearch.dll`, `plugin.def`, Newtonsoft.Json và WebView2 dependencies. Cần WebView2 Evergreen Runtime trên máy Client. Build hiện dùng MIP DLL trong `C:\Program Files\Milestone\XProtect Smart Client`; override bằng `-SdkPath` khi build cho release khác.

Build tạo ba ZIP độc lập. Cài `MilestoneSearch.EventServer` trên Event Server, `MilestoneSearch.Management` trên máy Management Client và `MilestoneSearch.SmartClient` trên từng máy vận hành cần tìm kiếm. Mỗi gói đặt trong thư mục riêng dưới MIPPlugins; không trộn DLL/plugin.def giữa các gói, không chép vào thư mục PSIM mẫu và không ghi đè plugin khác. Đóng Client trước khi thay DLL; chỉ restart Event Server vào thời điểm triển khai đã bố trí. Script build không cài vào host hoặc tự restart dịch vụ.

## Management Client

1. Vào MIP Plug-ins → Milestone Search. Cấp quyền plugin `ConfigureSearch` cho quản trị viên phù hợp.
2. Điền Backend URL: HTTPS, hoặc HTTP khi là loopback hay IP LAN nội bộ (10.x, 172.16–31.x, 192.168.x, 169.254.x, IPv6 ULA/link-local). Hostname luôn phải HTTPS. HTTP gửi token không mã hóa; chỉ dùng trong mạng tin cậy. Backend đặt `"host": "0.0.0.0"` và mở firewall cổng 8765. Site ID trùng cấu hình backend. Lưu trước khi bật collector.
3. Đăng nhập console bằng token admin backend. Tạo hồ sơ, thử payload thật, sau đó kích hoạt.
4. Configuration URL/site/enabled được lưu qua `SaveItemConfiguration`; bí mật không lưu trong cấu hình Milestone chia sẻ.

## Event Server

Thiết lập biến môi trường `MILESTONE_SEARCH_COLLECTOR_TOKEN` cho process/tài khoản dịch vụ Event Server, bằng token collector backend. Đảm bảo tài khoản dịch vụ có quyền ghi `%ProgramData%\MilestoneSearch\outbox` và thư mục này chỉ được phép đọc bởi tài khoản dịch vụ/quản trị.

Plugin chỉ nạp cấu hình sau `ConfigurationChangedIndication`; chưa có cấu hình hoặc disabled thì không nhận. Nhận `NewEventIndication` singular, `NewAlarmIndication`, `ChangedAlarmIndication`. Callback ghi file rồi worker gửi HTTP; quota mặc định 256 MiB. Khi đầy đĩa/quota hoặc dữ liệu SDK không hợp lệ, xem MIP log `MilestoneSearch`; không coi khoảng này là có đủ dữ liệu. File chưa ACK giữ lại khi restart. HTTP 408/429/5xx và lỗi mạng được thử lại; 4xx vĩnh viễn được chuyển sang `%ProgramData%\MilestoneSearch\outbox\dead-letter` để không chặn bản ghi sau. Dead-letter có thể chứa payload nhạy cảm và cần cùng ACL/retention với outbox. Ghi file đồng bộ có chi phí I/O: cần benchmark trên Event Server đích trước tải cao.

## REST đối soát

Backend cần API Gateway Milestone và token đọc alarm/event types của từng site, khác collector token. Thêm cấu hình dạng:

```json
{"milestone":{"lab":{"url":"https://vms.example","token_env":"MILESTONE_TOKEN"}}}
```

Đặt token trong môi trường process backend. Token phải được cấp mới khi hết hạn; bản hiện tại chưa có refresh identity provider tự động. Thay đổi alarm đưa yêu cầu vào queue rồi REST lấy bản mới. Không cấu hình API hoặc token hết hạn thì queue giữ pending và báo lỗi trong Vận hành. Mỗi site cấu hình được đồng bộ định kỳ, mặc định 300 giây; có thể đặt `sync_interval_seconds` (tối thiểu 30). Scheduler không thêm job định kỳ khi job cũ còn pending. Backfill CLI: `python -m search_engine.cli backfill --url https://vms.example --site lab`. Không tự kết luận alarm bị xóa khi nhận HTTP 404 vì có thể là quyền truy cập thay đổi.

## AI

Máy phát triển hiện dùng Ollama cục bộ với `qwen3:4b` cho lập kế hoạch/RAG và `qwen3-embedding:0.6b` (1024 chiều) cho semantic search. Backend gửi `think:false`, nhiệt độ 0 và giới hạn output để tránh chuỗi suy luận dài trên CPU. Thử nghiệm ngắn cho thấy bản 1.7B chọn cả bằng chứng không liên quan, vì vậy chỉ giữ để đánh giá và không dùng mặc định. Cấu hình `ai.ollama_url`, `ai.chat_model`, `ai.embedding_model`, `ai.max_output_tokens` theo máy triển khai. Worker embedding chạy nền, chỉ mục PostgreSQL dùng cosine với ACL và bộ lọc; không giới hạn 200 bản ghi như fallback SQLite. Truy vấn hiện dùng exact vector scan, chưa có ANN theo dimension: cần benchmark và index phù hợp trước dữ liệu lớn.

ASR: cài `python -m pip install -e '.[voice]'`, đặt `ai.whisper_model` thành thư mục model cục bộ và chọn `whisper_device`. Không tự tải model hoặc gửi âm thanh ra cloud. Người dùng kiểm tra transcript trước khi tìm. RAG kiểm tra cấu trúc và ID trích dẫn, nhưng điều này không bảo đảm mọi diễn giải của model đều đúng; cần bộ đánh giá nghiệp vụ.

## Smart Client và playback

Mở workspace AI Search; đăng nhập reader backend. Chọn bản ghi có camera, nhấn mở video. WebView chỉ nhận origin cấu hình; bridge chỉ hỗ trợ playback camera/thời gian, không hỗ trợ chạy lệnh hoặc điều khiển cửa. Camera được resolve trong phiên Milestone hiện tại và mở ở chế độ playback. Quyền video và retention vẫn do Milestone quyết định. Cần kiểm chứng nút này trên Smart Client thật; browser độc lập chỉ hiển thị tham chiếu.

## Sao lưu và retention

Backup database backend bằng công cụ PostgreSQL, lưu file cấu hình riêng an toàn, giữ spool chưa ACK. API admin DELETE `/api/operations/retention?before=<ISO UTC>` xóa content/index/vector và giữ tombstone. Retention tự động chỉ chạy khi cấu hình `"retention":{"enabled":true,"days":30}`; chạy mỗi giờ, ngày tối thiểu là 1. Mặc định không tự xóa. Không xóa video hoặc dữ liệu SQL của Milestone.

## Đăng nhập và cấu hình tự động (bản 2026-09-28)

- **Đăng nhập:** dùng tài khoản Milestone (như Smart Client). Trong Smart Client và Management Client, plugin tự chuyển phiên Milestone hiện tại nên không cần đăng nhập lại. Mã quản trị trong `config.json` vẫn dùng được qua liên kết "Dùng mã truy cập".
- **Quyền:** mặc định `full_access` bật: mọi người đăng nhập được thấy toàn bộ dữ liệu và chức năng quản trị. Đặt `"full_access": false` để phạm vi tìm kiếm theo camera mà tài khoản Milestone được xem (quản trị viên Milestone thấy toàn site).
- **Máy chủ Milestone:** backend tự phát hiện Management Server trên cùng máy (`http://localhost`). Máy khác: nhập địa chỉ trong tab **Vận hành → Kết nối Milestone**.
- **Tài khoản đồng bộ nền** (tên sự kiện, trạng thái alarm): nhập một lần ở **Vận hành → Kết nối Milestone**; mật khẩu mã hóa bằng Windows DPAPI, token tự gia hạn. Thứ tự ưu tiên: giá trị nhập trên giao diện > `config.json` > tự phát hiện.
- **Collector:** plugin Event Server mặc định bật, site `main`, backend `http://127.0.0.1:8765`. Token collector đọc từ biến `MILESTONE_SEARCH_COLLECTOR_TOKEN`, nếu không có thì từ `C:\ProgramData\MilestoneSearch\collector.token` do service backend ghi (chỉ SYSTEM, Administrators và tài khoản Event Server đọc được).
- **Firewall:** trình cài exe mở cổng backend cho mạng con cục bộ (`remoteip=localsubnet`); `uninstall` xóa rule.
- **Tìm kiếm:** tên camera/thiết bị, tên sự kiện, trạng thái và mức ưu tiên được đưa vào chỉ mục. Lần đầu chạy bản này, backend tự xử lý lại toàn bộ bản ghi cũ để cập nhật chỉ mục.

## Trích xuất tự động và tìm theo người (bản 2026-09-28, P1–P4)

- Mọi payload (ObjectList, CustomData dạng JSON/XML, câu chữ trong Name/Message) được tự tách thành thuộc tính; không cần hồ sơ phân tích. Lần đầu chạy bản này, backend tự xử lý lại toàn bộ dữ liệu cũ.
- Tên người được so khớp không phụ thuộc dấu và thứ tự từ; event và alarm của cùng một lần phát hiện, cùng các lần lặp trong 60 giây, được gộp thành một lần xuất hiện.
- Khuôn câu của các tích hợp (FaceMe, chấm công, biển số, thẻ) được tự học mỗi 10 phút; xem, tắt hoặc duyệt trong **⚙ Trạng thái hệ thống → Hệ thống đã tự hiểu các loại sự kiện**.
- Tùy chọn AI trong `ai`: `chat_model` bật nút "Nhờ AI đề xuất" (đề xuất phải được duyệt); `vision_model` (model thị giác của Ollama) bật nút tìm theo ảnh. Ảnh tải lên chỉ dùng để mô tả, không lưu.
- Kiểm tra trên dữ liệu thật: restore backup Milestone vào SQL Server phân tích (`research/milestone/restore-backup.ps1`) rồi chạy `python research/milestone/replay_backup.py --db <file tạm>.db`.
