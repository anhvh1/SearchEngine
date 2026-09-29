# Phân tích payload alarm/event AI và phương án trích xuất phục vụ tìm kiếm

Ngày: 2026-09-28. Nguồn: hai bản backup database `Surveillance` restore chỉ đọc trên máy phát triển
(`Surveillance_Analysis` — bản 28/09, `MilestoneResearch_20260915` — bản 15/09). Dữ liệu chứa tên người
thật và ảnh khuôn mặt: chỉ dùng cục bộ, không đưa vào repository hay test fixture.

## 1. Dữ liệu đã đọc

| | Bản 28/09 | Bản 15/09 |
|---|---|---|
| Alarm | 525, 100% *Registered face detection* (i-PRO Active Guard, camera .62), 22–28/09 | 115, 8 kiểu: FaceMe người lạ / người quen / check-in / check-out, Intruder Human, xe không biển số, thẻ không có quyền |
| Event lưu kèm dữ liệu | 307: 123 nhận diện khuôn mặt, 184 *Database Deleting Recordings* | 333 *Database Deleting Recordings* |
| Lịch sử xử lý alarm | 525 bản ghi, tất cả *New*, không có người xử lý/ghi chú | — |
| Loại sự kiện khai báo | 530 | — |

530 loại sự kiện được khai báo cho biết phạm vi AI có thể xuất hiện:

| Nhóm | Số loại | Ví dụ |
|---|---|---|
| Analytics Events (MIP) | 28 | License plate detection, Intruder Human, Loitering, Crossline, Direction, Container/Code detection, AI Scene Change |
| i-PRO Maximizer Events | 27 | Non mask detection, Sound (gunshot, glass break, yell, horn), Impact, Occupancy, Casing open |
| Access Control / SALTO | 83 | Access granted/denied, Door forced/left open, Duress, Antipassback |
| Driver camera (ONVIF, dynamic) | 265 | Smoke and fire, Abandoned object, Glass break, `RuleEngine/FaceRecognitionDetector/ObjectIsRecognized` |
| Hệ thống / recorder / server | ~110 | CPU, dung lượng, mất kết nối, lưu trữ |

## 2. Dữ liệu nghiệp vụ nằm ở đâu — bốn kiểu thực tế

**A. Có cấu trúc trong `ObjectList`** — AI dùng chuẩn AnalyticsEvent của Milestone.
- i-PRO Active Guard: một `Object` chỉ có `Value` = tên người (`"Khuất Hoàng Chương "`); không có `Name`, `Type`, `Confidence`, khung hình.
- Sự kiện hệ thống: cặp `Name`/`Value` (`BankName=Local default`, `BankPath=D:\MediaDatabase`).
- Schema còn hỗ trợ `Type`, `Confidence`, `Color`, `Size`, `BoundingBox`, `Polygon`, `Motion/Speed`, `Data` — chưa có AI nào trong dữ liệu dùng tới.

**B. Nhúng trong chuỗi văn bản** — các tích hợp bên thứ ba (FaceMe, biển số, kiểm soát ra vào):
- `Name`: `"Phát hiện Phi Ngo Van tại Camera 62"`, `"[Checkin] Phát hiện người lạ tại Camera 54"`
- `Message`: `"Phi Ngo Van đã về sớm 472 phút"`, `"FACEME.UNKNOWN_PERSON"`
- `Type`: `"Checkout"`, `"VEHICLE_WITHOUT_LICENSE_PLATE"`, `"CARD_ACCESS_DENIED"`
- `CustomTag`: mã tham chiếu hệ ngoài (32 ký tự hex)

**C. Tối thiểu** — Intruder Human, Motion: chỉ có loại sự kiện và camera.

**D. Ảnh** — 519/525 alarm khuôn mặt có snapshot JPEG, trung bình 19 KB (tối đa 26 KB).

`Vendor/CustomData` và `ReferenceList` trống ở cả hai bản, nhưng schema hỗ trợ; AI sau này có thể đặt JSON/XML tự do vào đó.

## 3. Vấn đề chất lượng dữ liệu

1. **Cùng một người, nhiều cách viết**: thừa khoảng trắng cuối; tên kèm mã (`"Hồng Ánh 0132465"`); có dấu / không dấu (`"Khuất Hoàng Chương"` ↔ `"Khuat Hoang Chuong"`); đảo thứ tự (`"Phi Ngo Van"` ↔ `"Ngo Van Phi"`).
2. **Một lần nhận diện thành hai bản ghi**: 123/123 event khuôn mặt có alarm tương ứng cùng người, lệch dưới 3 giây, **khác ID**. Đếm thô sẽ gấp đôi.
3. **Lặp liên tục**: 235/525 alarm (45%) lặp lại cùng người trong 60 giây — một lần đứng trước camera sinh nhiều alarm.
4. Cùng kiểu alarm nhưng `Type` khác nhau (`"Test Event"` và tên thật).
5. Hiện trạng hệ thống: plugin đã gửi `ObjectList` trong payload nhưng backend **chưa đưa vào chỉ mục** — tìm "Khuất Hoàng Chương" không ra. Kiểu B tìm được qua message nhưng phải gõ đúng dấu và thứ tự.

## 4. Phương án đề xuất: trích xuất tổng quát, vai trò tự suy luận, quy tắc có phiên bản

Không viết code riêng cho từng hãng, không bắt quản trị viên viết JSON, không gọi LLM cho từng sự kiện.

```
Envelope thô (giữ nguyên, bất biến)
  └─ L1 Làm phẳng tổng quát ──► mọi giá trị thành thuộc tính + chữ đã chuẩn hóa ──► tìm được ngay
       └─ L2 Gán vai trò ──► person / plate / action / zone / count ... (quy tắc có phiên bản)
            └─ L3 Thực thể & lần xảy ra ──► gộp biến thể tên, gộp event+alarm, gộp chuỗi lặp
                 └─ L4 Ảnh (sau) ──► thumbnail, embedding khuôn mặt/xe
```

**L1 – Làm phẳng tổng quát (không cần cấu hình).** Duyệt toàn bộ payload: mỗi `Object` thành thuộc tính (khóa = `Name` hoặc `Type` hoặc `value`); header `Name/Message/Type/CustomTag/Description`; `Vendor.CustomData` được parse nếu là JSON/XML; rule, reference. Chuẩn hóa: trim, Unicode NFC, bản không dấu. GUID/base64 giữ làm thuộc tính nhưng loại khỏi chỉ mục chữ. Chỉ mục full-text lưu cả bản có dấu và không dấu. Kết quả: mọi AI mới tìm được theo nội dung **ngay từ sự kiện đầu tiên**.

**L2 – Gán vai trò (tự động, có phiên bản).** Từ vựng vai trò nhỏ, cố định: `person`, `person_code`, `identity_status` (quen/lạ), `plate`, `vehicle_attr`, `zone`, `action` (vào/ra/về sớm/đi muộn), `object_class`, `count`, `duration`, `confidence`. Nguồn suy luận theo thứ tự:
1. Từ điển tên khóa (`LicensePlate`, `Plate`, `PersonName`, `Zone`…);
2. Mẫu giá trị (biển số Việt Nam, mã số, tên người);
3. **Khuôn văn bản tự học theo từng loại sự kiện**: so nhiều mẫu cùng loại để tách phần cố định và phần thay đổi — `"Phát hiện {X} tại {camera}"`, `"{X} đã về sớm {N} phút"` — rồi gán vai trò cho `{X}`, `{N}`;
4. LLM chỉ **đề xuất** quy tắc một lần cho loại sự kiện mới hoặc khó, không chạy trên từng sự kiện. Kết quả lưu thành quy tắc tất định.

Quy tắc lưu có phiên bản (`extraction_rules`: loại sự kiện, khuôn, vai trò, nguồn auto/llm/người sửa, trạng thái). Độ tin cậy cao thì tự áp dụng; thấp thì hiện trên trang trạng thái dạng câu dễ hiểu — *"Loại mới: Checkout (FaceMe). Hệ thống hiểu: người = Phi Ngo Van, hành động = về sớm, số phút = 472. [Đúng] [Sửa]"* — không có JSON.

**L3 – Thực thể và lần xảy ra.**
- Thực thể người/xe với khóa chuẩn = tập từ không dấu (xử lý đảo thứ tự và dấu); mã nhân viên tách riêng làm bí danh.
- Gộp event + alarm cùng camera, cùng thực thể, lệch ≤ 3 giây thành **một lần xảy ra**.
- Gộp chuỗi lặp cùng người/camera trong N giây (mặc định 60) thành **một lần xuất hiện** có số lần, giờ đầu, giờ cuối.
- Câu hỏi đếm ("bao nhiêu lần", "ai đến muộn") trả lời theo lần xuất hiện, không theo alarm thô.

**L4 – Ảnh (giai đoạn sau).** Lưu thumbnail cho mỗi lần xuất hiện (~20 KB; 525 alarm ≈ 10 MB) để hiện trong kết quả; sau đó embedding khuôn mặt/xe cho "tìm người giống ảnh này".

**Lưu trữ bổ sung** (PostgreSQL): `attributes(record_key, role, key, value, folded, number)` có index theo `(role, folded)`; `entities`, `entity_aliases`; `occurrences`; `extraction_rules`; `records.extractor_version`. Đổi quy tắc thì chạy lại trích xuất trên envelope thô bằng cơ chế replay sẵn có.

**Tìm kiếm dùng kết quả trích xuất.** Bộ hiểu câu hỏi lấy từ vựng từ `attributes`/`entities`: tên người (khớp không dấu, không phụ thuộc thứ tự), biển số, hành động, trạng thái quen/lạ. Ví dụ: *"Khuất Hoàng Chương tuần này"*, *"người lạ ở camera 54 hôm qua"*, *"xe không biển số"*, *"ai về sớm hôm nay"*. Kết quả có thống kê theo người.

**Khi thêm hoặc thay đổi AI** — không sửa plugin, không phát hành bản mới:
1. Loại sự kiện mới đến → L1 làm cho nó tìm được ngay.
2. Sau khoảng 20 mẫu → L2 tự học khuôn và vai trò, đề xuất hoặc tự áp dụng quy tắc.
3. Hãng đổi định dạng (thêm trường, đổi câu chữ) → phát hiện qua thay đổi tập đường dẫn/khuôn → quy tắc phiên bản mới; dữ liệu cũ giữ nguyên hoặc chạy lại có kiểm soát.

**Vì sao không chọn cách khác**

| Cách | Lý do không chọn |
|---|---|
| Code riêng cho từng hãng AI | Mỗi AI mới phải phát hành lại; không theo kịp khi thêm AI |
| Hồ sơ JSON do quản trị viên viết | Quá phức tạp cho người dùng (phản hồi trực tiếp) |
| LLM đọc từng sự kiện | Chậm trên CPU, kết quả không ổn định, chi phí tăng theo số sự kiện |
| Chỉ full-text | Không lọc/đếm theo người, không gộp trùng và chuỗi lặp, không xử lý biến thể tên |

## 5. Lộ trình và tiêu chí nghiệm thu

| Giai đoạn | Nội dung | Nghiệm thu |
|---|---|---|
| P1 | L1 + chỉ mục không dấu + chạy lại dữ liệu cũ | Tìm được 100% alarm của từng người trong 14 tên thật, gõ có dấu, không dấu, đảo thứ tự |
| P2 | L3: thực thể, gộp event+alarm, gộp chuỗi lặp | 123 event + 525 alarm khuôn mặt → 525 lần xảy ra, rồi gộp chuỗi lặp; đếm theo người khớp đối chiếu SQL |
| P3 | L2: khuôn tự học, vai trò, xác nhận trên trang trạng thái | Kiểu B của bản 15/09 (FaceMe, check-in/out, biển số, thẻ) tự tách đúng người/hành động/số phút |
| P4 | LLM đề xuất quy tắc; thumbnail và embedding ảnh | Loại AI mới đạt L2 không cần người viết quy tắc |

Test dùng dữ liệu tổng hợp mô phỏng đúng cấu trúc ở mục 2; không dùng tên hay ảnh thật.

## 6. i-PRO Active Guard: dữ liệu về người gửi sang Milestone

Theo tài liệu *i-PRO Active Guard Setup Instructions for XProtect* (bản 2025-10) và trang hướng dẫn tích hợp của i-PRO:

- Camera AI gửi video vào XProtect, còn **ảnh best shot và metadata gửi vào Active Guard server**. Metadata lưu trong SQL Server riêng của Active Guard: các database `ai_db`, `aicam`, `bi`, `support_db`, mặc định cổng TCP 1435. Server `.4` đang mở cổng 1435.
- Sang Milestone, Active Guard chỉ gửi **Analytics Event khi khớp watchlist**: `Registered face detection`, `Registered people detection`, `Registered vehicle detection`, `Registered/Unregistered license plate detection`, code, container, cùng Intruder/Loitering/Direction/Crossline/Occupancy từ AI Processing relay app, và `i-PRO Active Guard system notification`.
- Cột **Object** chứa **tên mục watchlist**. Với khuôn mặt đó là tên người; với người và xe đó là tên watchlist do người vận hành đặt (ví dụ "Nam áo đỏ quần xanh"). **Đặc điểm như giới tính, tuổi, màu áo, túi, kính không nằm trong alarm.**
- Khuôn mặt không có trong watchlist **không bao giờ được gửi**. Chỉ biển số, code và container có tùy chọn "Notify all detected objects".
- Ảnh gửi kèm alarm cấu hình được: không ảnh, ảnh phát hiện, hoặc ảnh phát hiện kèm ảnh đăng ký.

**Hệ quả cho tìm kiếm:**
1. Người đã khai báo trong watchlist của Active Guard → tìm được theo tên watchlist, đã hỗ trợ: giá trị Object được gán vai trò `watchlist`.
2. Muốn tìm theo mọi người đi qua camera (kể cả người lạ, theo màu áo, giới tính…) → cần **connector đọc trực tiếp database Active Guard**, chỉ đọc, qua cổng 1435. Việc này cần: (a) một tài khoản SQL chỉ đọc do quản trị Active Guard cấp; (b) khảo sát schema `ai_db` trên máy thật, vì i-PRO không công bố schema; (c) xác nhận điều khoản hỗ trợ của i-PRO khi đọc database. Connector sẽ đưa mỗi best shot vào hệ thống dưới dạng envelope `kind=event` với thuộc tính giới tính, tuổi, màu, hướng, kèm ảnh; phần L1–L3 hiện có xử lý tiếp mà không cần sửa.

## 7. Trạng thái triển khai (2026-09-28)

| Giai đoạn | Mã nguồn | Kết quả nghiệm thu |
|---|---|---|
| P1 | `extract.py` (L1), `store.py` (chỉ mục kèm bản không dấu) | Dữ liệu thật hai bản backup, 1.278 envelope: **16/16 người** tìm đủ 100% alarm theo 3 cách gõ (có dấu, không dấu, đảo thứ tự), cả theo chữ lẫn theo thực thể. `research/milestone/replay_backup.py` → `PASS` |
| P2 | `enrich.py` (thực thể, `record_links`) | 123/123 event khuôn mặt gộp với alarm tương ứng; 648 bản ghi khuôn mặt → 290 lần xuất hiện; toàn bộ 1.278 → 616 |
| P3 | `enrich.learn_rules`, `extract.learn_template/slot_roles` | Tự học 7 khuôn: `{người} đã đi muộn {số phút} phút`, `{người} đã về sớm {số phút} phút`, `Phát hiện {người} tại Camera {số}`, `Phát hiện người lạ tại {vị trí}`… Người FaceMe không dấu được gộp với người Active Guard có dấu |
| P4 | `ai.suggest_rules`, `ai.describe_image`, `/api/ask/image`, ảnh chụp trong kết quả | Với `qwen3:1.7b`: đề xuất đúng khuôn cho 5 loại, một số vai trò sai → đề xuất luôn chờ duyệt. Tìm theo ảnh cần `ai.vision_model` (model thị giác Ollama); chưa cài trên máy nào |

**Còn thiếu:**
- Tìm người giống ảnh theo đặc trưng khuôn mặt (face embedding): cần model nhận dạng khuôn mặt và ảnh best shot đầy đủ từ Active Guard.
- Connector database Active Guard (mục 6).
- Model thị giác và model ngôn ngữ lớn hơn trên server để đề xuất vai trò chính xác hơn.
