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

**Đọc và ghi đồng thời (PostgreSQL).** Việc ghi (nhập dữ liệu, xử lý, học quy tắc) dùng một kết nối và chạy tuần tự; việc đọc (tìm kiếm, ảnh, trạng thái) dùng tối đa 8 kết nối riêng và không chờ việc ghi. Backend cần thêm 9 kết nối PostgreSQL, trong giới hạn mặc định `max_connections = 100`. SQLite chỉ có một kết nối nên vẫn đọc tuần tự (chỉ dùng thử nghiệm).

**Lần khởi động đầu sau khi nâng cấp lên bản 2026-10-03** backend tự thêm hai cột `event_type`, `event_family` vào `records`, điền cho bản ghi cũ, tạo chỉ mục rồi dọn bảng (`VACUUM`). Việc này chạy một lần; với khoảng 1 triệu bản ghi có thể mất vài phút, trong lúc đó backend chưa nhận yêu cầu, plugin Event Server giữ alarm trong hàng đợi và gửi lại sau.

## Backend dạng Windows service (exe)

Build: `./scripts/build-backend.ps1` tạo `dist/MilestoneSearch.Backend-<thời gian>.zip` (một file `MilestoneSearch.Backend.exe`, không cần cài Python) và `dist/MilestoneSearch.Backend-source-<thời gian>.zip` (mã nguồn backend, web, test, script build).

Cài đặt: nhấp đúp `MilestoneSearch.Backend.exe`, chấp nhận UAC. Chương trình:
1. Chép exe vào `C:\Program Files\MilestoneSearch\Backend`.
2. Lần đầu tạo cấu hình `C:\ProgramData\MilestoneSearch\backend\config.json`: chép từ `config.json`/`config.local.json` đặt cạnh exe nếu có, nếu không thì tạo mới với token ngẫu nhiên và SQLite. Thư mục chỉ SYSTEM và Administrators đọc được. Lần sau giữ nguyên cấu hình.
3. Đăng ký service `MilestoneSearchBackend` (Automatic, tài khoản LocalSystem, tự restart khi lỗi sau 5/15/60 giây). Nếu database là PostgreSQL trên máy này, backend tự thử kết nối lại tối đa 5 phút khi khởi động (không đặt phụ thuộc service Windows vào PostgreSQL: từng gặp trường hợp Windows chờ PostgreSQL khởi động xong bị quá giờ trong lúc phục hồi sau tắt đột ngột, tự đánh dấu service PostgreSQL là Stopped dù tiến trình vẫn chạy tốt, rồi làm backend không khởi động được — lỗi `1068`).
4. Khởi động service và kiểm tra HTTP. Log: `C:\ProgramData\MilestoneSearch\backend\logs`.

Nâng cấp hoặc áp dụng thay đổi `database`: chạy lại exe mới/cũ. Sửa cấu hình khác: `MilestoneSearch.Backend.exe restart`. Lệnh khác: `status`, `start`, `stop`, `uninstall` (giữ cấu hình/dữ liệu), `console` (chạy foreground để gỡ lỗi), và các lệnh CLI `init`, `seed`, `import`, `backfill`, `discover`, `openapi` (mặc định dùng cấu hình trong ProgramData). Biến môi trường như `MILESTONE_USERNAME`/`MILESTONE_PASSWORD` phải đặt ở mức Machine rồi restart service. Exe không tự mở Windows Firewall.

## HTTPS cho trình duyệt (micro)

Trình duyệt chỉ cho dùng micro trên trang HTTPS (hoặc `localhost`). Backend chạy song song hai cổng trên cùng `host`:
- `port` (mặc định 8765, HTTP): plugin Management/Event Server/Smart Client và collector dùng cổng này, không đổi.
- `https_port` (mặc định 8443, HTTPS): người dùng mở `https://<máy chủ>:8443/` để tìm bằng giọng nói. Đặt `"https_port": 0` để tắt.

Chứng chỉ:
- **Mặc định — CA riêng của backend.** Lần chạy đầu backend tạo một CA (hạn 10 năm, không bao giờ đổi) và chứng chỉ máy chủ ghi đủ tên máy và mọi địa chỉ IPv4 hiện có, lưu trong `C:\ProgramData\MilestoneSearch\backend\tls` (chỉ SYSTEM/Administrators đọc được). Chứng chỉ máy chủ tự cấp lại khi đổi IP/tên máy hoặc còn dưới 30 ngày; CA giữ nguyên nên máy khách không phải cài lại. Thêm tên/địa chỉ khác (DNS nội bộ, NAT): `"tls": {"names": ["search.congty.local"]}` rồi `restart`.
- Trình cài exe tự thêm CA vào *Trusted Root* của máy chủ.
- **Tự động trên máy có Smart Client:** plugin Event Server lấy CA từ backend (ở cùng máy hay máy khác đều được) kèm chữ ký HMAC bằng token collector — máy chen giữa trong LAN không có token nên không thay được CA — rồi lưu vào cấu hình Milestone; plugin Smart Client khi khởi động kiểm tra CA đã được tin chưa, chưa thì cài: chạy quyền Administrator thì cài im lặng vào máy, người dùng thường thì Windows hỏi xác nhận một lần (không tắt được hộp này). Chọn *No* thì không hỏi lại; xóa `%LOCALAPPDATA%\MilestoneSearch\ca-declined.txt` để được hỏi lại. Cần token collector đã cấu hình trên máy Event Server (xem *Backend và Milestone khác máy*).
- **Thủ công (máy không có Smart Client):** tải `http://<máy chủ>:8765/ca.crt` (trang web cũng hiện link này khi bấm micro trên trang HTTP), rồi chạy với quyền Administrator `certutil -addstore -f Root SearchEngine-CA.crt`, hoặc nhấp đúp file → *Install Certificate* → *Local Machine* → *Trusted Root Certification Authorities*. Nhiều máy: phân phối bằng Group Policy (*Computer Configuration → Windows Settings → Security Settings → Public Key Policies → Trusted Root Certification Authorities*). Chrome và Edge dùng kho chứng chỉ Windows; Firefox cần bật `security.enterprise_roots.enabled` nếu chưa nhận.
- **Dùng chứng chỉ của CA công ty thay thế:** `"tls": {"cert_file": "C:\\...\\server.pem", "key_file": "C:\\...\\server.key"}` (PEM; file cert gồm cả chuỗi trung gian). Khi đó không tạo CA riêng và `/ca.crt` trả 404.

Firewall: trình cài mở cả hai cổng cho mạng con cục bộ và mọi dải IP nội bộ (10/8, 172.16/12, 192.168/16), để máy ở VLAN khác trong LAN cũng vào được; đổi bằng `"firewall_remote_ip"` (cú pháp `remoteip` của netsh, ví dụ `"LocalSubnet,10.20.0.0/16"`) rồi chạy lại exe. Phiên đăng nhập trên trang HTTP và HTTPS là riêng (khác origin): mở trang HTTPS cần đăng nhập lại một lần.

## Backend và Milestone khác máy

Backend (máy A) và Milestone Management/Event Server (máy B) có thể tách rời; mọi máy trong LAN dùng địa chỉ của A.
1. **A — backend:** `"host": "0.0.0.0"` (mặc định), chạy exe; firewall mở 8765/8443 cho các dải IP nội bộ. Trong trang quản trị → *Kết nối Milestone*: URL `https://<B>` (máy khác bắt buộc HTTPS; Management Server phải bật mã hóa), tài khoản dịch vụ, chọn kiểm tra chứng chỉ nếu chứng chỉ của B được tin. Người dùng Smart Client vẫn tự đăng nhập: backend xác thực token của họ với B.
2. **Management Client:** Backend URL = `http://<IP của A>:8765` (không dùng `127.0.0.1` — giá trị này dùng chung cho collector trên B và Smart Client trên mọi máy).
3. **B — Event Server:** token collector chỉ tự ghi ra file trên máy chạy backend, nên trên B đặt một lần (CMD quyền Administrator) rồi restart Event Server:
   `setx /M MILESTONE_SEARCH_COLLECTOR_TOKEN "<token>"` — `<token>` là `token` của principal có role `collector` trong `C:\ProgramData\MilestoneSearch\backend\config.json` trên A. Token là bí mật: không gửi qua chat/email.
4. **Smart Client (mọi máy):** không cần cấu hình; tab AI Search dùng Backend URL ở bước 2, và CA HTTPS được tự cài như mục trên khi bước 3 xong.
5. Truy cập bằng tên DNS thay vì IP: thêm tên vào `"tls": {"names": [...]}` trên A rồi `restart`.

## Build plugin

Chạy `scripts/build-plugin.ps1`. Gói zip trong `dist` gồm `MilestoneSearch.dll`, `plugin.def`, Newtonsoft.Json và WebView2 dependencies. Cần WebView2 Evergreen Runtime trên máy Client. Build hiện dùng MIP DLL trong `C:\Program Files\Milestone\XProtect Smart Client`; override bằng `-SdkPath` khi build cho release khác.

Build tạo ba ZIP độc lập. Cài `MilestoneSearch.EventServer` trên Event Server, `MilestoneSearch.Management` trên máy Management Client và `MilestoneSearch.SmartClient` trên từng máy vận hành cần tìm kiếm. Mỗi gói đặt trong thư mục riêng dưới MIPPlugins; không trộn DLL/plugin.def giữa các gói, không chép vào thư mục PSIM mẫu và không ghi đè plugin khác. Đóng Client trước khi thay DLL; chỉ restart Event Server vào thời điểm triển khai đã bố trí. Script build không cài vào host hoặc tự restart dịch vụ.

## Management Client

1. Vào MIP Plug-ins → Milestone Search. Cấp quyền plugin `ConfigureSearch` cho quản trị viên phù hợp.
2. Điền Backend URL: HTTPS, hoặc HTTP khi là loopback hay IP LAN nội bộ (10.x, 172.16–31.x, 192.168.x, 169.254.x, IPv6 ULA/link-local). Hostname luôn phải HTTPS. HTTP gửi token không mã hóa; chỉ dùng trong mạng tin cậy. Backend đặt `"host": "0.0.0.0"` và mở firewall cổng 8765 (và 8443 cho HTTPS). Site ID trùng cấu hình backend. Lưu trước khi bật collector.
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

Backup database backend bằng công cụ PostgreSQL, lưu file cấu hình riêng an toàn, giữ spool chưa ACK.

- **Số ngày lưu:** trang quản trị → *Lưu trữ dữ liệu*, đặt số ngày (0 = giữ mãi, tối đa 3650). Áp dụng trong khoảng 10 giây sau khi lưu, sau đó mỗi giờ tự xóa sự kiện, alarm và ảnh cũ hơn số ngày này. Xóa theo từng đợt 2.000 bản ghi nên việc nhận dữ liệu không bị dừng. Giá trị trên trang quản trị được ưu tiên hơn `"retention":{"enabled":true,"days":30}` trong file cấu hình; mặc định không tự xóa.
- **Xóa toàn bộ dữ liệu:** cùng mục, gõ `XÓA` để xác nhận. Xóa mọi sự kiện, alarm, ảnh, chỉ mục và danh sách tên đã thấy; giữ kết nối Milestone/Active Guard, quy tắc đã học, nhật ký quản trị và vị trí đồng bộ Active Guard (không nhập lại dữ liệu cũ). Không hoàn tác được — sao lưu trước nếu cần.
- Bản ghi đã xóa để lại một dấu nhỏ không có nội dung để thông báo cũ gửi lại không làm nó xuất hiện lại. API: `DELETE /api/operations/retention?before=<ISO UTC>`, `PUT /api/settings/retention`, `POST /api/operations/wipe`. Không xóa video hoặc dữ liệu của Milestone, Active Guard.

## Đăng nhập và cấu hình tự động (bản 2026-09-28)

- **Đăng nhập:** dùng tài khoản Milestone (như Smart Client). Trong Smart Client và Management Client, plugin tự chuyển phiên Milestone hiện tại nên không cần đăng nhập lại. Mã quản trị trong `config.json` vẫn dùng được qua liên kết "Dùng mã truy cập".
- **Quyền:** mặc định `full_access` bật: mọi người đăng nhập được thấy toàn bộ dữ liệu và chức năng quản trị. Đặt `"full_access": false` để phạm vi tìm kiếm theo camera mà tài khoản Milestone được xem (quản trị viên Milestone thấy toàn site).
- **Máy chủ Milestone:** backend tự phát hiện Management Server trên cùng máy (`http://localhost`). Máy khác: nhập địa chỉ trong tab **Vận hành → Kết nối Milestone**.
- **Tài khoản đồng bộ nền** (tên sự kiện, trạng thái alarm): nhập một lần ở **Vận hành → Kết nối Milestone**; mật khẩu mã hóa bằng Windows DPAPI, token tự gia hạn. Thứ tự ưu tiên: giá trị nhập trên giao diện > `config.json` > tự phát hiện.
- **Collector:** plugin Event Server mặc định bật, site `main`, backend `http://127.0.0.1:8765`. Token collector đọc từ biến `MILESTONE_SEARCH_COLLECTOR_TOKEN`, nếu không có thì từ `C:\ProgramData\MilestoneSearch\collector.token` do service backend ghi (chỉ SYSTEM, Administrators và tài khoản Event Server đọc được).
- **Firewall:** trình cài exe mở cổng backend (HTTP và HTTPS) cho mạng con cục bộ và các dải IP nội bộ (`remoteip=localsubnet`); `uninstall` xóa rule.
- **Tìm kiếm:** tên camera/thiết bị, tên sự kiện, trạng thái và mức ưu tiên được đưa vào chỉ mục. Lần đầu chạy bản này, backend tự xử lý lại toàn bộ bản ghi cũ để cập nhật chỉ mục.

## Trích xuất tự động và tìm theo người (bản 2026-09-28, P1–P4)

- Mọi payload (ObjectList, CustomData dạng JSON/XML, câu chữ trong Name/Message) được tự tách thành thuộc tính; không cần hồ sơ phân tích. Lần đầu chạy bản này, backend tự xử lý lại toàn bộ dữ liệu cũ.
- Tên người được so khớp không phụ thuộc dấu và thứ tự từ; event và alarm của cùng một lần phát hiện, cùng các lần lặp trong 60 giây, được gộp thành một lần xuất hiện.
- Khuôn câu của các tích hợp (FaceMe, chấm công, biển số, thẻ) được tự học mỗi 10 phút; xem, tắt hoặc duyệt trong **⚙ Trạng thái hệ thống → Hệ thống đã tự hiểu các loại sự kiện**.
- Tùy chọn AI trong `ai`: `chat_model` bật nút "Nhờ AI đề xuất" (đề xuất phải được duyệt); `vision_model` (model thị giác của Ollama) bật nút tìm theo ảnh. Ảnh tải lên chỉ dùng để mô tả, không lưu.
- Kiểm tra trên dữ liệu thật: restore backup Milestone vào SQL Server phân tích (`research/milestone/restore-backup.ps1`) rồi chạy `python research/milestone/replay_backup.py --db <file tạm>.db`.

## Active Guard: tìm theo đặc điểm người, xe, biển số và ảnh khuôn mặt

Active Guard chỉ gửi sang Milestone những lần khớp watchlist. Toàn bộ best shot cùng thuộc tính (giới tính, tuổi, tóc, màu áo/quần/giày, mũ, kính, khẩu trang, râu, túi, loại và màu xe, biển số) nằm trong Active Guard server và đọc được qua **WebAPI v1.1** (HTTP Digest, cổng 8090; tài liệu: https://i-pro.com/products_and_solutions/en/activeguardapi/English/index.html).

1. Trên máy chủ Active Guard nên tạo một tài khoản chỉ dùng cho việc đọc (API này chỉ đọc dữ liệu tìm kiếm mà connector dùng).
2. Mở giao diện tìm kiếm, biểu tượng ⚙ → **Active Guard** → nhập địa chỉ (ví dụ `http://192.168.100.11:8090`), tài khoản, mật khẩu → **Kiểm tra và lưu**. **Nhiều server:** lặp lại cho từng server (ví dụ `.11`, `.4`, `.3`); mỗi server có cursor, trạng thái và lỗi riêng, một server mất kết nối không làm dừng các server còn lại. Dữ liệu nằm ở site `main` (Milestone mà các Active Guard đăng ký vào); đổi bằng `site_id` trong PUT `/api/settings/activeguard`. Mật khẩu được mã hóa DPAPI, không nằm trong cấu hình chia sẻ.
3. Backend tự nhập best shot mới mỗi phút (mặc định nhập lại 24 giờ gần nhất, tối đa 300 ảnh mỗi lượt). Chỉnh trong `config.json` mục `activeguard`: `types` (`people`, `vehicle`, `lpr`, `face`), `lookback_hours`, `max_per_cycle`, `interval_seconds`, `min_score` (ngưỡng tin cậy của thuộc tính, mặc định 0.5).
4. Tìm kiếm ví dụ: "nam áo đỏ quần đen đội mũ", "phụ nữ đeo khẩu trang", "người cao tuổi tóc trắng", "xe tải màu trắng", "xe máy đỏ". Biểu tượng máy ảnh: tải ảnh khuôn mặt lên, Active Guard tự tìm các khuôn mặt giống (7 ngày gần nhất, ngưỡng giống 70%). Với ảnh toàn thân cần thêm `ai.vision_model`.

**Chẩn đoán trước khi dùng thật:** chạy lệnh dưới một lần. Lệnh chỉ đọc, hỏi mật khẩu ngay trên màn hình (hoặc biến môi trường `IAG_PASSWORD`), không lưu mật khẩu, và ghi ra một file JSON gồm danh sách camera, điểm thuộc tính của vài ảnh mẫu (không có ảnh) để đối chiếu với dữ liệu thật:

    MilestoneSearch.Backend.exe iag-probe --url http://192.168.100.11:8090 --user <tài khoản> --file iag-probe.json

Dữ liệu tìm khuôn mặt theo ảnh gửi ảnh lên chính Active Guard server; backend không lưu ảnh tải lên.
