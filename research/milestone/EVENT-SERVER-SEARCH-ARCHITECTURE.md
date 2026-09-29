# Phân tích dữ liệu và phương án plugin tìm kiếm Milestone

**Kiến trúc hiện hành:** [V2 — hồ sơ phân tích theo nguồn/event](ARCHITECTURE-V2.md). Tài liệu này giữ kết quả khảo sát DB và SDK làm căn cứ; các lựa chọn thiết kế mới được tập trung tại V2.

Ngày: 15-09-2026. Trạng thái: đề xuất kiến trúc dựa trên schema, truy vấn backup thực và tài liệu SDK; chưa triển khai hoặc kiểm thử plugin trên Event Server thật. Database khảo sát: `localhost/MilestoneResearch_20260915`, bản phục hồi chỉ đọc của `Surveillance.bak`.

## 1. Kết luận

Có thể xây dựng hệ tìm kiếm text/voice bằng cách thu nhận event/alarm tại **XProtect Event Server**, lập chỉ mục độc lập và đưa giao diện trở lại Smart Client. Alarm Manager là giao diện; thành phần host plugin được đề xuất là Event Server, không phải một service riêng tên Alarm Manager.

Đề xuất dùng ba thành phần: **Event Server collector plugin**, **dịch vụ tìm kiếm/AI độc lập**, **Smart Client search workspace**. Model không trực tiếp nghe SDK: collector nhận dữ liệu, backend chuẩn hóa/lập chỉ mục, model hiểu câu hỏi và tạo câu trả lời có bằng chứng. Không cần sao chép toàn bộ video để thực hiện tìm kiếm alarm.

## 2. Milestone đang lưu gì trong bản backup

| Dữ liệu kiểm tra | Kết quả thực | Ý nghĩa |
|---|---|---|
| `Central.Alarms`, `Alarm_T001` | 113 mỗi bảng | Hai biểu diễn của tập alarm; không cộng thành 226 |
| Alarm người lạ | 83: 51 thông điệp tiếng Việt + 18 và 14 `FACEME.UNKNOWN_PERSON` | Chuẩn hóa bằng mapping đã kiểm chứng; giữ text gốc |
| Alarm người đã đăng ký | 4 `FACEME.PERSON` | Chỉ khẳng định hệ nguồn đã báo nhận diện |
| Alarm nghiệp vụ khác | 8 `CARD_ACCESS_DENIED`, 8 `VEHICLE_WITHOUT_LICENSE_PLATE`, 8 check-in/out, 2 `Intruder Human` | Đủ tạo bộ câu hỏi PoC nhiều nhóm |
| `T031_Snapshot_T032` | 74 hàng, cả 74 có ảnh nhị phân | Có thể làm preview/ảnh bằng chứng; chưa suy ra mỗi ảnh thuộc một người riêng |
| `T014_Object_T015`, `T001_Vendor_T035` | 0 | Không có `Object.Data`, confidence hoặc `Vendor.CustomData` trong các bảng alarm này |
| `Alarm_AccessControl` | 0 | Alarm mang tên truy cập không chứng minh có dữ liệu MIP-AC chuyên biệt |
| `AlarmUpdate` | 113 | Có dữ liệu cập nhật; chưa xác nhận đó là toàn bộ lịch sử xử lý |
| `Event_Active`, `Event_Inactive` | 142 và 191 | 333 event, đều có tên `Database Deleting Recordings Before Set Retention Size` |
| Event payload | 142 và 191 hàng trong các bảng `_Data` | Mẫu đã đọc là XML `BaseEvent`; 167 event có `ObjectData` nhưng không phải thuộc tính nhận diện AI |
| `Event_Bulk`, `Central.Events` | 0 | Không nên chọn bảng theo tên rồi giả định đây là kho duy nhất |
| Alarm có tag | 111/113 | Chưa xác minh tag là external event ID, person ID hay giá trị khác |
| Thời gian alarm | 18–27/08/2026 theo giá trị SQL | Cần đối chiếu UTC/UI trước dùng làm bộ lọc |
| Thiết bị | 9 camera bật; 99 metadata device, 2 bật | Cấu hình enabled không chứng minh metadata được truyền/ghi |

Hai metadata device được bật:

- `a1b4476c-3d19-4437-845b-c3737d8a90fe`: i-PRO WV-X15600-V2L — Metadata 1.
- `c8e16d63-f76a-425a-bfc7-6a7882fd9ed1`: UNIVIEW IPC2122LR3-PF40-C — MetaData [0].

Recording được cung cấp là bản lấy một phần, camera i-PRO ngày 08/09. Chưa có căn cứ kết luận hệ thực thiếu video hay metadata chỉ vì bản sao không chứa chúng. Chưa kiểm thử đọc luồng metadata bằng SDK. SQL schema của Incident Manager có sẵn, nhưng không có backup dữ liệu `Surveillance_IM` để khảo sát nội dung incident.

## 3. Các trường và cách lấy

| Nhóm | Nơi lưu trong schema đã cung cấp | Interface đề xuất |
|---|---|---|
| Alarm list | `Central.Alarms`: ID, time, touched, message/name, source/camera, tag, type, category, priority/state, assignee, description | REST Alarms API hoặc MIP AlarmClient |
| Alarm đầy đủ | `Alarm_T001`, EventHeader, Source/FQID, Rule/Object/Vendor và bảng con | Lấy detail thay vì chỉ dùng AlarmLine |
| Snapshot | SnapshotList → Snapshot; có Image, Path, kích thước, time offset | Alarms snapshots endpoint; không gửi binary vào embedding văn bản |
| Event history | Active/Inactive + Data, các bảng ACS tương ứng | Events API hoặc MIP AlarmClient; phụ thuộc version và retention |
| Cấu hình camera/rule | Devices, Items, configuration | Configuration API/MIP Configuration, mapping có phiên bản |
| Metadata liên tục | Media database của Recording Server | MetadataLiveSource/MetadataPlaybackSource; collector riêng |
| Hồ sơ incident | RepoIncident, comments, activity, sequences ở DB IM | Public interface chưa xác minh; không gộp với Alarm API |

[Alarms API](https://doc.developer.milestonesys.com/mipvmsapi/api/alarms-rest/v1/) cung cấp list/detail, history, snapshots và bộ lọc cập nhật. Các endpoint REST tương đối: `/api/rest/v1/alarms`, `/alarms/{id}`, `/alarms/{id}/history`, `/alarms/{id}/snapshots`. Kiểm tra base URL và version khi lập trình. REST yêu cầu XProtect 2023 R2 trở lên cùng quyền/license tương ứng.

[Events API](https://doc.developer.milestonesys.com/mipvmsapi/api/events-rest/v1/) đọc event đã được lưu; event không được lưu theo retention sẽ không được trả lại. Không diễn giải Active/Inactive thành trạng thái xử lý New/Closed của alarm. Backup có thể dùng SQL để bootstrap PoC; production ưu tiên API, không ghi hoặc thêm trigger vào schema Milestone.

## 4. Thu nhận bên trong Event Server

### Cấu trúc và vòng đời

Assembly cung cấp `PluginDefinition` và ít nhất một `BackgroundPlugin`; giới hạn collector chạy ở môi trường Event Server. Trong `Init()`, đăng ký receiver và khởi động worker; trong `Close()`, bỏ đăng ký, dừng có thời hạn và lưu trạng thái hàng đợi. Không mặc định configuration đã sẵn ngay lúc khởi tạo: xử lý `Server.ConfigurationChangedIndication` để nạp/làm mới cấu hình. [BackgroundPlugin reference](https://doc.developer.milestonesys.com/mipsdk/MIPhelp/class_video_o_s_1_1_platform_1_1_background_1_1_background_plugin.html).

### Những message cần nghe

| Message | Xử lý đề xuất |
|---|---|
| `Server.NewEventsIndication` **hoặc** `Server.NewEventIndication` | Nhận event; chọn một, không đăng ký cả hai |
| `Server.NewAlarmIndication` | Nhận alarm mới; đọc payload đầy đủ hoặc xếp lịch lấy detail |
| `Server.ChangedAlarmIndication` | Cập nhật hoặc xóa theo change hint; nếu chỉ có ID thì lấy detail |
| `Server.ChangedAlarmHistoryIndication` | Kiểm chứng trên release đích nếu cần đồng bộ lịch sử |
| `Server.ConfigurationChangedIndication` | Làm mới mapping thiết bị, rule và cấu hình plugin |

Đăng ký trong Event Server qua `EnvironmentManager.Instance.RegisterReceiver` với `MessageIdFilter`; giữ registration token để `UnRegisterReceiver`. [Messaging integration](https://doc.developer.milestonesys.com/html/reference/architecture/plug-in_integration.html).

Message API có khác biệt theo môi trường và phiên bản. Ví dụ Smart Client có thể nhận AlarmLine khi alarm đổi; từ các release 2026 có `ChangedAlarmDataDetailed`. Không cast mọi callback thành `Alarm`. Nguồn: [Server messages](https://doc.developer.milestonesys.com/mipsdk/MIPhelp/class_video_o_s_1_1_platform_1_1_messaging_1_1_message_id_1_1_server.html), [ChangedAlarmData](https://doc.developer.milestonesys.com/mipsdk/MIPhelp/class_video_o_s_1_1_platform_1_1_messaging_1_1_changed_alarm_data.html).

### Callback phải làm ít việc

Callback sao chép các trường cần thiết vào envelope độc lập và đưa vào hàng đợi giới hạn; không gọi LLM, tải video, embedding, hoặc HTTP chờ lâu trong callback. Worker chuyển dữ liệu sang spool bền vững và backend. Thiết kế queue/spool phải có giới hạn dung lượng, chỉ số mất dữ liệu, retry, dead-letter và quy trình replay.

Đây không phải bảo đảm exactly-once: crash giữa callback và ghi spool vẫn có thể mất event. Đồng bộ bù được với dữ liệu còn lưu ở Milestone; event không được lưu sẽ có khoảng mất không khôi phục được. Nếu yêu cầu không mất bất kỳ event nào, cần bổ sung khả năng replay ở hệ nguồn hoặc broker được nguồn hỗ trợ.

### Đồng bộ lịch sử và realtime

1. Bắt đầu receiver và ghi mốc checkpoint.
2. Đọc lịch sử theo trang, với khoảng chồng lấn thời gian để chống race.
3. Upsert alarm theo site + GUID nguồn; giữ local ID riêng. Không dùng text hoặc Tag làm khóa duy nhất.
4. Lưu event và alarm ở hai loại record riêng; liên kết theo ID có căn cứ, không đếm cả hai thành hai sự cố.
5. Nhận cập nhật theo message và đối soát API định kỳ, dùng `lastUpdatedTime` khi API hỗ trợ. Xử lý bản đến muộn, replay và bản cũ không được ghi đè bản mới.
6. Đồng bộ xóa/retention bằng change hint và đối soát; không coi lỗi quyền hoặc HTTP lỗi là bằng chứng bản ghi đã bị xóa.

Chỉ nhận được event đi qua Event Server, không tự đọc mọi dữ liệu nằm trong plugin hoặc database vendor. [Event Server integration](https://doc.developer.milestonesys.com/mipsdk/reference/architecture/authorization.html) và [AlarmEventViewer sample](https://doc.developer.milestonesys.com/mipsdk/samples/ComponentSamples/AlarmEventViewer/README.html) là cơ sở PoC.

## 5. Mô hình dữ liệu cho chỉ mục

Một event/alarm là một đơn vị tìm kiếm, không cắt tùy ý thành các đoạn như manual. Các trường đề xuất:

- Định danh: `site_id`, `record_kind`, `source_guid`, `local_id`, `external_id`, `schema_version`.
- Thời gian: `occurred_at_utc`, `source_timezone`, `updated_at`, `ingested_at`, `expires_at`.
- Nội dung gốc: `message`, `name`, `description`, `type`, `tag`, `raw_payload_ref`.
- Chuẩn hóa: `event_family`, `event_code`, `reported_person_name`, `plate`, `delay_minutes`, `direction`, `zone_id`, `camera_ids`, `device_id`.
- Workflow: `state`, `priority`, `category`, `assignee`, `history_refs`.
- Bằng chứng: `snapshot_refs`, `video_time_range`, `related_record_ids`, `availability_status`.
- Quản trị: `acl_scope`, `mapping_version`, `model_version`, `derived_fields`, `provenance`, `confidence`.

Tách field gốc và field suy luận. Tên người trong alarm là nhận định của hệ nguồn; không suy ra danh tính từ ảnh bằng LLM. Quan hệ giữa hai event phải có cửa/camera/vùng/thời gian hoặc ID liên kết; cùng thời điểm không chứng minh cùng người.

Mapping ban đầu: FaceMe known/unknown → recognition; CARD_ACCESS_DENIED/SIPASS → access; plate/vehicle/container → vehicle; intrusion/crossline → perimeter; check-in/out → attendance; system storage events → system_health. Chưa gộp `Intruder`, `IntruderHuman`, `Python Intrusion`, `PSIM.INTRUSION` nếu chưa đối chiếu rule/source. Những tên hiển thị trong dropdown không chứng minh đang có dữ liệu của mọi loại.

## 6. Model và RAG

Đề xuất baseline không fine-tune: mapping/rule parser trước, model ngôn ngữ xử lý trường hợp còn mơ hồ. Chọn model cụ thể sau benchmark tiếng Việt, phần cứng và yêu cầu on-prem; chưa có căn cứ chốt số GPU hoặc chi phí.

| Thành phần | Vai trò | Điều kiện đánh giá |
|---|---|---|
| Parser có cấu trúc | Tách tên, loại, số phút từ message mẫu | Đúng field và giữ nguyên bản gốc |
| LLM hiểu truy vấn | Sinh query plan JSON có schema: thời gian, địa điểm, filters, phép đếm/chuỗi sự kiện | Validate enum, quyền, thời gian; không sinh SQL tự do |
| Embedding đa ngôn ngữ | Tìm “người lạ” qua nhiều biến thể diễn đạt | Recall@k tiếng Việt có/không dấu |
| Reranker tùy chọn | Xếp hạng tập ứng viên | Giá trị chất lượng so với độ trễ |
| LLM trả lời | Tóm tắt các record đã truy xuất và dẫn ID/thời gian | Không bịa sự kiện, phân biệt không có dữ liệu và không có kết quả |
| ASR | Voice → transcript dùng chung query pipeline | Lỗi tên, biển số, số phút, phủ định trong môi trường thực |
| Vision/VLM giai đoạn sau | Bổ sung mô tả snapshot/clip | Chỉ mô tả điều quan sát được, ghi nguồn model |

Index chính gồm trường có cấu trúc + full text + vector. Câu hỏi “bao nhiêu lần”, “muộn trên 60 phút”, hoặc “từ chối thẻ rồi mở cửa trong 30 giây” chạy bằng truy vấn chính xác/temporal join trên tập được phép; không lấy top-k vector rồi đếm.

Ngữ cảnh RAG gồm hai kho tách biệt: (a) event/alarm và bằng chứng; (b) mapping site/camera/door/giờ làm việc/SOP và định nghĩa event. Manual SDK dành cho trợ lý kỹ thuật, không dùng làm bằng chứng một sự kiện đã xảy ra. Caption/summary tổng hợp là dữ liệu dẫn xuất có version, có thể tái tạo; raw record là căn cứ.

Chưa chọn engine chỉ mục. Giai đoạn PoC đo baseline structured/full-text trước; chỉ thêm vector nếu cải thiện câu hỏi ngữ nghĩa. Tránh tăng chi phí vận hành nhiều database khi chưa có số liệu tải.

## 7. Plugin giao diện Smart Client

Đề xuất tab riêng **AI Search** bằng `WorkSpacePlugin` cho chat/voice, bảng alarm, timeline và kết quả không có camera. Đây là điểm mở rộng chính thức; tham khảo [SCWorkSpace](https://doc.developer.milestonesys.com/mipsdk/samples/PluginSamples/SCWorkSpace/README.html).

Thêm `SearchAgentPlugin` ở giai đoạn tiếp theo cho kết quả liên quan recording trong Search workspace có sẵn. `SearchDefinition` nhận scope thời gian/camera và trả kết quả; custom filters hỗ trợ input tùy biến. Không ép alarm không có camera thành video search result giả. [Search Agent guide](https://doc.developer.milestonesys.com/mipsdk/gettingstarted/intro_searchagent.html).

UI hiển thị câu hỏi/transcript, điều kiện hiểu được, độ mới chỉ mục, kết quả và căn cứ. Bấm kết quả mở playback theo camera/time; hiển thị rõ snapshot còn nhưng recording đã hết hạn. Không cần sửa nội bộ Alarm Manager hoặc đọc chữ trên màn hình.

Backend phải xác thực người dùng và thực thi quyền trước retrieval/reranking/LLM; không tin camera IDs do client tự gửi. Quyền đọc alarm có thể khác quyền video. Cần kiểm chứng cơ chế chuyển danh tính Smart Client sang backend và mapping ACL trên release đích. Một service account có quyền rộng chỉ dành cho ingestion không được dùng để lộ mọi record cho mọi người dùng.

## 8. Phương án triển khai và đánh đổi

| Phương án | Đánh giá |
|---|---|
| Chỉ query Milestone khi người dùng hỏi | Ít đồng bộ nhưng không có chỉ mục ngữ nghĩa riêng; khó tránh tải/độ trễ khi tăng dữ liệu |
| Đọc SQL trực tiếp production | Hiểu được schema nhưng phụ thuộc nội bộ, khó giữ quyền/retention và version; dùng cho phân tích backup |
| Service ngoài chỉ dùng API/MIP | Giảm rủi ro host Event Server; phương án thay thế tốt nếu không cần plugin trong process |
| Collector trong Event Server + backend riêng | Phù hợp yêu cầu hiện tại, truy cập message nội bộ; cần chứng minh plugin không ảnh hưởng host |

Ưu tiên thu nhận chỉ đọc. Chưa tạo alarm dẫn xuất từ model trong PoC; nếu bổ sung sau phải có namespace nguồn, lineage, chống vòng lặp và phân biệt cảnh báo gốc với suy luận.

## 9. PoC và tiêu chí quyết định

1. **Replay backup:** 113 alarm và 74 snapshot, mapping field, truy vấn có cấu trúc và bộ 30–50 câu hỏi. Không coi tập nhỏ là benchmark production.
2. **Collector lab:** phát từng nhóm FaceMe/ACS/parking/intrusion, so raw callback với API và UI; đo callback latency, CPU/memory, ingest lag, dropped messages.
3. **Độ bền:** backend offline, disk đầy, restart Event Server, reconnect, burst, duplicate, đổi trạng thái/xóa và failover. Có chỉ báo coverage gap; không hứa thu đủ event không có replay.
4. **UI và quyền:** hai user khác quyền, đổi quyền khi đang đăng nhập, alarm không có camera, recording hết hạn; không có dữ liệu trái quyền trong cả kết quả và câu trả lời.
5. **Chất lượng:** exact filter/count, temporal correlation, semantic recall/precision, voice slot accuracy, trích dẫn ID đúng và tỉ lệ trả lời không có căn cứ.
6. **Tải:** tăng dần số record và event/giây, đo p50/p95, indexing lag và ảnh hưởng Event Server so baseline. Chốt SLA sau khi biết tải mục tiêu.
7. **Metadata bổ sung:** kiểm tra hai metadata device enabled có thực sự ghi, dùng MetadataPlaybackSource đọc mẫu; đối chiếu thêm Active Guard/FaceMe khi trường cần tìm không có trong Milestone.

Còn cần xác định: XProtect/SDK/Smart Client build và license thực, quyền API, yêu cầu identity backend, tốc độ event đỉnh, retention, GPU/on-prem/cloud. Phiên bản SQL Server 16 không cho biết phiên bản XProtect. Bộ hồ sơ này đủ làm thiết kế và PoC offline; chưa đủ để cam kết tính tương thích plugin production.
