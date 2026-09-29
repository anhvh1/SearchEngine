# Hồ sơ tài liệu Milestone phục vụ tìm kiếm text/voice

**Thiết kế hiện hành 16-09-2026:** [Kiến trúc V2 — model, plugin và hồ sơ phân tích](ARCHITECTURE-V2.md). Tách rõ discovery, cấu hình nguồn mới, xử lý dữ liệu, tìm kiếm và vòng đời model.

**Phân tích mới 15-09-2026:** [Dữ liệu thực, Event Server collector và plugin tìm kiếm](EVENT-SERVER-SEARCH-ARCHITECTURE.md). Có căn cứ từ backup SQL và tài liệu SDK; chưa triển khai plugin production.

Ngày rà soát: **14-09-2026**. Giả định Milestone = **XProtect**, ACS = Access Control System, BMS = Building Management System. Chưa biết edition/release, hãng ACS/BMS/parking, quy mô camera và yêu cầu on-premise của hệ đích.

## Kết quả và giới hạn

- [CATALOG.md](CATALOG.md): danh mục nguồn chính thức, ưu tiên đọc và ghi chú phiên bản.
- [sources.json](sources.json): danh mục dạng máy đọc để nhập vào công cụ quản lý tri thức.
- [download-manifest.json](download-manifest.json): trạng thái tải PDF, URL cuối, thời gian, kích thước và SHA-256.
- [pdf/](pdf/): bản PDF công khai tải được, đặt tên theo ID trong danh mục.
- [SEARCH-MODEL.md](SEARCH-MODEL.md): đề xuất mô hình dữ liệu, luồng tìm kiếm và bài kiểm chứng.

Đây là bộ nguồn có chọn lọc bao phủ các nhóm được yêu cầu, **không phải toàn bộ tài liệu từng phát hành hoặc toàn bộ hệ thống đã tích hợp trên thị trường**. Chưa mirror toàn bộ website, chưa đọc từng trang của mọi PDF, chưa truy cập tài liệu có đăng nhập và chưa kiểm thử với XProtect thực tế. Ngày search engine crawl không được dùng làm ngày phát hành tài liệu. Tài liệu lịch sử được giữ để tham khảo và ghi rõ; URL `latest` không phải phiên bản bất biến.

## Các nguồn nên đọc trước

Trạng thái tải: **67 nguồn trong danh mục, 21 URL PDF được thử, 16 PDF tải được**. Năm nguồn M027 (FaceMe verification), M030 (i-PRO operating instructions cũ), M048 (IPsens), M050 (BACnet) và M058 (BriefCam verification) bị chuyển về trang chủ khi tải trực tiếp. Nội dung trích dẫn của chúng đến từ phần công cụ tìm kiếm còn truy xuất được, không phải từ file cục bộ. Cần xin bản hiện hành từ hãng nếu dùng triển khai. Manual cài đặt i-PRO mới M067 đã tải được.

| Mục tiêu | Nguồn | Cần lấy ra |
|---|---|---|
| Cài đặt và vận hành | [VMS admin 2025 R2](https://doc.milestonesys.com/mc/pdf/2025r2/en-US/MilestoneXProtectVMSproducts_AdministratorManual_en-US.pdf), [Smart Client 2025 R2](https://doc.milestonesys.com/sc/pdf/2025r2/en-US/MilestoneXProtectSmartClient_UserManual_en-US.pdf) | Thành phần server, camera, recording/retention, Search, roles, bookmarks, evidence |
| Chọn API | [MIP API overview](https://doc.developer.milestonesys.com/mipvmsapi/api-overview/) | Endpoint, authentication, dữ liệu có thể đọc, yêu cầu release |
| Event và alarm | [Events API](https://doc.developer.milestonesys.com/mipvmsapi/api/events-rest/v1/), [Alarms API](https://doc.developer.milestonesys.com/mipvmsapi/api/alarms-rest/v1/) | Historical retrieval, state, priority, snapshot, filter, retention |
| Metadata đối tượng | [Metadata introduction](https://doc.developer.milestonesys.com/mipsdk/gettingstarted/intro_metadata.html), [Playback sample](https://doc.developer.milestonesys.com/mipsdk/samples/ComponentSamples/MetadataPlaybackViewer/README.html) | Đọc metadata live/recorded; inventory trường thực tế |
| Plugin tìm kiếm | [Search Agent guide](https://doc.developer.milestonesys.com/mipsdk/gettingstarted/intro_searchagent.html) | Gắn backend tìm kiếm vào Search workspace của Smart Client |
| AI ngoài VMS | [AI Bridge integrator v2.0](https://doc.milestonesys.com/AIB/PDF/v2_0/en-US/MilestoneAIBridge_IntegratorManual_en-US.pdf) | Kết nối analytics với video và kết quả phân tích |

2025 R2 được dùng làm bộ manual công khai đã tìm thấy, không được coi là release mới nhất. Đã có [system requirements 2026 R1](https://www.milestonesys.com/support/help-and-documentation/system-requirements/xprotect-corporate-2026-r1/); cần chốt release hệ đích rồi đối chiếu tài liệu trên [Documentation Portal](https://doc.milestonesys.com/en-US/).

## Ma trận tích hợp và ý nghĩa cho tìm kiếm

| Nhóm | Bằng chứng tích hợp | Ý nghĩa / phần cần xác minh |
|---|---|---|
| FaceMe | [Verification của Milestone](https://www.milestonesys.com/globalassets/marketplace/uploaded-assets/0013x00002ho0cfqaa/cyberlink-faceme-11032021.pdf) xác nhận FaceMe 5.4.0 với Professional+ 2020 R2 qua ONVIF Bridge + HTTP | Chỉ là tổ hợp thử năm 2021. Xin Setup Guide, Integration Overview, Release Notes hiện hành và API lịch sử nhận diện. |
| FaceMe Locator | [FaceMe Security overview](https://www.cyberlink.com/faceme/solution/security/overview?trk=products_details_guest_secondary_call_to_action) có Milestone trong nhóm VMS, nhưng phần Locator liệt kê Genetec, Network Optix, Hanwha | Không suy ra FaceMe Locator hỗ trợ tìm kiếm lịch sử trong Milestone. |
| i-PRO Active Guard | [Operating instructions](https://www.milestonesys.com/globalassets/marketplace/uploaded-assets/0010o00001oqdwnqam/i-pro-active-guard-plugin-for-xprotect---installer-guide-v1.5.0.pdf) mô tả video vào XProtect, best-shot/metadata vào Active Guard server | Cần kiểm tra API/SDK Active Guard cho unified search; plugin hiện được nhúng trong Smart Client không bảo đảm mọi metadata đã nằm trong VMS. |
| i-PRO AI-VMD | [FAQ metadata search](https://i-pro.com/products_and_solutions/us-en/surveillance/faq-list/us-en124551) có luồng cấu hình metadata và tìm trong Smart Client | Kiểm tra model camera, AI app, firmware, driver và bật ghi metadata. Phân biệt luồng này với Active Guard. |
| LenelS2 OnGuard | [Manual](https://doc.milestonesys.com/int/pdf/latest/en-US/MilestoneXProtectAccessOnGuard_Manual_en-US.pdf), [compatibility matrix](https://download.milestonesys.com/lenels2xpa/OnGuard-XProtect-Access-Compatibility.pdf) | Door/cardholder/events liên kết camera; không mặc định connector đưa toàn bộ lịch sử ACS vào kho tìm kiếm. |
| Nedap AEOS | [Nedap integration](https://www.nedapsecurity.com/technology-partner/milestone/) | Cardholder, cửa và intrusion; cần manual/API/matrix tương ứng bản đang chạy. |
| Gallagher Command Centre | [Gallagher integration](https://products.security.gallagher.com/security/us/en_US/products/integrations/milestone-vms-integration/p/C12730) | Có tích hợp hai chiều được hãng mô tả; xác minh từng event type và quyền truy cập dữ liệu. |
| Parking: XProtect LPR | [LPR admin 2025 R1](https://doc.milestonesys.com/addons/pdf/2025r1/en-US/MilestoneXProtectLPR_AdministratorManual_en-US.pdf) | Nguồn biển số; cần ghép thêm session vào/ra, ticket và vị trí đỗ nếu truy vấn nghiệp vụ yêu cầu. |
| Parking: IPsens FuseParking | [Integration manual](https://www.milestonesys.com/globalassets/marketplace/uploaded-assets/0013x00002hmrm1qam/milestone-integration-user-manual-1.pdf) | Có mapping parking event với camera; lấy danh sách event và lịch sử qua interface hãng cung cấp. |
| Parking: Parquery | [Parquery integration](https://parquery.com/milestone-integration/) | Có occupancy và duration trong live/playback; dashboard riêng chứa thêm dữ liệu nghiệp vụ. |
| BMS/BACnet | [Milestone BACnet guide](https://www.milestonesys.com/globalassets/marketplace/uploaded-assets/0012000000c2oxaaab/bacnetintegrationuserguide.pdf) | Object/property mapping; cần biết chiều truyền và points được cấu hình. BACnet không tự bảo đảm tương thích mọi hệ BMS. |
| Fire/BACnet | [Radinium](https://radinium.com/products/fire-integration-series-bacnet/) | Hãng mô tả tích hợp fire events với camera/maps; trang đang ghi verification in progress. |
| BriefCam/Rapid REVIEW | [Deployment guide](https://doc.milestonesys.com/xprr/pdf/2024m1/en-US/MilestoneXProtectRapidReview_InstallationGuide_en-US.pdf) | Tham khảo analytics/search và deployment; chốt sản phẩm, license, API thực tế. |
| Vaidio/App-Techs | [Vaidio case study](https://www.vaidio.ai/blog/customer-success-story-app-techs) | Có tích hợp qua BTX; xin schema kết quả và query API, không suy ra chỉ cần XProtect API. |

Các tên trên là ví dụ có nguồn xác nhận, không phải inventory thiết bị tại dự án. Chưa xác minh một connector cụ thể cho Schneider EcoStruxure, Siemens Desigo, Honeywell hoặc Johnson Controls; cần biết hãng và phiên bản để tìm đúng tài liệu.

## Những kết luận quan trọng cho phạm vi dự án

**Phải phân biệt object observation, event, alarm và incident project.** Alarms API mô tả alarm với trạng thái, độ ưu tiên và người được giao. Incident Manager có workflow hồ sơ riêng. Trong lượt rà soát này chưa tìm được public API contract đủ rõ để cam kết đọc/ghi toàn bộ Incident Manager; cần xác nhận với Milestone trước khi thiết kế connector. Không coi việc biết tên database nội bộ là API được hỗ trợ. Nguồn: [Alarms API](https://doc.developer.milestonesys.com/mipvmsapi/api/alarms-rest/v1/), [Incident Manager manual](https://doc.milestonesys.com/addons/pdf/2023r2/en-US/MilestoneXProtectIncidentManager_AdministratorManual_en-US.pdf).

**Search Agent là điểm mở rộng UI phù hợp**, cho phép đưa category/filter và thuật toán tìm kiếm vào Smart Client. Theo guide, license hỗ trợ khác nhau giữa các thế hệ: từ 2022 R3 có Express+, Professional+, Expert, Corporate; bản trước cần Expert hoặc Corporate. Vẫn phải kiểm tra edition/release đích. Nguồn: [Search Agent](https://doc.developer.milestonesys.com/mipsdk/gettingstarted/intro_searchagent.html).

**Không đặt kế hoạch dựa trên tính năng mới chỉ được công bố.** Milestone thông báo AI Search dùng ngôn ngữ tự nhiên dự kiến cuối 2026; đó chưa phải bằng chứng có installer/API sẵn trên hệ hiện tại. Portal App Platform cũng ghi early access cho pilot partners. Nguồn: [AI announcement](https://www.milestonesys.com/company/news/press-releases/ai-built-for-security-operations/), [App Platform portal](https://portal.developer.milestonesys.com/).

**Tài liệu và video là hai corpus khác nhau.** RAG trên manual giúp trả lời cách cấu hình/tích hợp. Để tìm “người áo đỏ đi vào cổng”, hệ thống phải có metadata hoặc chỉ mục được tạo từ video thực tế. Không thể huấn luyện khả năng tìm video chỉ từ manual.

## Hồ sơ còn cần thu thập từ hệ đích

1. XProtect edition/release/build, Smart Client, MIP SDK, API Gateway và license add-on.
2. Danh sách camera, firmware, Device Pack, AI app và metadata channels đang ghi.
3. FaceMe: ba tài liệu được nêu trong verification, API query lịch sử, event payload, retention và matrix mới.
4. i-PRO: Active Guard server/plugin/AI app matrix, API/SDK quyền truy xuất best-shot/attributes và hướng dẫn HA/retention.
5. ACS/parking/BMS: hãng, version, connector, API/manual, danh mục event/point, camera mapping và khả năng backfill.
6. Incident Manager: interface được hãng hỗ trợ để query/export hồ sơ, quyền, notes, attachments, timeline và update history.
7. Mẫu dữ liệu thực: metadata XML, event/alarm JSON, incident mẫu, đoạn video gắn timestamp và 30–50 câu hỏi vận hành.
8. Quy mô và ràng buộc: số camera, ngày lưu, GPU, on-prem/cloud, tiếng Việt/Anh, độ trễ mong muốn, phạm vi người dùng.

Không cần chờ đủ mọi hãng mới làm PoC: bắt đầu bằng inventory + event/alarm + metadata playback của một site, rồi thêm nguồn vendor có API rõ ràng. Phần này là đề xuất triển khai, chưa phải kết quả đã được kiểm thử.
