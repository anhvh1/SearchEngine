"""Turns a free Vietnamese/English sentence into search filters without a language model.

Recognises time phrases, event kinds and camera names/IP addresses. Anything unrecognised becomes
keywords, which are dropped again if they would make the result empty.
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone


def fold(text):
    """Lower-case and strip Vietnamese diacritics so typed or spoken text matches either way."""
    text = unicodedata.normalize('NFD', text.lower().replace('đ', 'd').replace('Đ', 'd'))
    return re.sub(r'\s+', ' ', ''.join(c for c in text if unicodedata.category(c) != 'Mn')).strip()


# phrase (folded) -> (label, regex over folded event names, kind)
EVENTS = [
    (('het chuyen dong', 'ngung chuyen dong', 'motion stopped'), 'Hết chuyển động', r'motion stopped', None),
    (('chuyen dong', 'motion', 'di chuyen'), 'Chuyển động', r'motion detected|motion started', None),
    (('mat ket noi', 'khong phan hoi', 'mat tin hieu', 'ngat ket noi', 'offline', 'not responding', 'mat mang'),
     'Mất kết nối', r'not responding|disconnected|communication error', None),
    (('ket noi lai', 'co ket noi', 'phuc hoi ket noi', 'online', 'da phan hoi'), 'Kết nối lại', r'^(server )?responding$', None),
    (('xam nhap', 'dot nhap', 'intruder', 'intrusion', 'vuot rao'), 'Xâm nhập', r'intru|xam nhap', None),
    (('khuon mat', 'nhan dien', 'guong mat', 'face'), 'Nhận diện khuôn mặt', r'face|khuon mat', None),
    (('bien so', 'lpr', 'license plate'), 'Biển số', r'licen|plate|lpr|bien so', None),
    (('xe', 'phuong tien', 'vehicle', 'o to'), 'Phương tiện', r'vehicle|xe |bien so|licen|plate', None),
    (('o cung', 'day o', 'het dung luong', 'xoa ban ghi', 'luu tru', 'dung luong'), 'Lưu trữ', r'recording|disk|storage|database|archive', None),
    (('may chu', 'server'), 'Máy chủ', r'server', None),
    (('canh bao', 'bao dong', 'alarm'), 'Alarm', None, 'alarm'),
]

# phrase (folded) -> (label, attribute role, folded value) produced by extraction
COLORS = [('xanh la', 'green'), ('xanh duong', 'blue'), ('xanh', 'blue'), ('do', 'red'), ('den', 'black'), ('trang', 'white'),
          ('xam', 'gray'), ('vang', 'yellow'), ('cam', 'orange'), ('tim', 'purple'), ('hong', 'pink'), ('nau', 'brown')]
COLOR_RE = '|'.join(c for c, _ in COLORS)
COLOR_LABEL = {'xanh la': 'xanh lá', 'xanh duong': 'xanh dương', 'xanh': 'xanh', 'do': 'đỏ', 'den': 'đen', 'trang': 'trắng', 'xam': 'xám',
               'vang': 'vàng', 'cam': 'cam', 'tim': 'tím', 'hong': 'hồng', 'nau': 'nâu'}
# noun before a colour -> attribute role (extraction assigns the same roles to Active Guard best shots)
COLOR_NOUNS = [(r'ao(?: khoac| thun| so mi)?', 'upper_color', 'Áo'), (r'quan(?: dai| ngan| jean)?', 'lower_color', 'Quần'),
               (r'toc', 'hair_color', 'Tóc'), (r'(?:tui|ba lo|cap)', 'bag_color', 'Túi'), (r'giay', 'shoes_color', 'Giày'),
               (r'(?:xe|o to|xe hoi)', 'vehicle_color', 'Xe')]
ATTRIBUTES = [
    (('nam', 'nam gioi', 'dan ong', 'con trai'), 'Nam', 'gender', 'male'), (('nu', 'nu gioi', 'phu nu', 'con gai', 'ba'), 'Nữ', 'gender', 'female'),
    (('tre em', 'be trai', 'be gai', 'em be'), 'Trẻ em', 'age', '0-10'), (('thieu nien', 'hoc sinh'), 'Thiếu niên', 'age', '11-20'),
    (('nguoi lon', 'trung nien'), 'Người lớn', 'age', '21-60'), (('nguoi gia', 'cao tuoi', 'nguoi cao tuoi', 'ong gia', 'ba gia'), 'Người cao tuổi', 'age', '61+'),
    (('toc dai',), 'Tóc dài', 'hair_style', 'long-hair'), (('toc ngan',), 'Tóc ngắn', 'hair_style', 'short-hair'),
    (('doi mu', 'deo mu', 'mu bao hiem', 'co mu'), 'Đội mũ', 'hair_style', 'hat'),
    (('deo kinh', 'kinh ram', 'kinh mat', 'deo kinh ram'), 'Đeo kính', 'sunglasses', 'yes'),
    (('khau trang', 'deo khau trang'), 'Khẩu trang', 'face_mask', 'yes'), (('co rau', 'rau'), 'Có râu', 'beard', 'yes'),
    (('mang tui', 'deo tui', 'xach tui', 'deo ba lo', 'co tui', 'ba lo'), 'Có túi', 'bag', 'yes'),
    (('ao dai tay',), 'Áo dài tay', 'upper_garment', 'long-sleeves'), (('ao ngan tay', 'ao coc tay', 'ao phong'), 'Áo ngắn tay', 'upper_garment', 'short-sleeves'),
    (('quan dai',), 'Quần dài', 'lower_garment', 'long'), (('quan ngan', 'quan dui', 'quan short'), 'Quần ngắn', 'lower_garment', 'short'),
    (('xe tai',), 'Xe tải', 'vehicle_type', 'truck'), (('xe buyt', 'xe khach'), 'Xe buýt', 'vehicle_type', 'bus'), (('suv',), 'SUV', 'vehicle_type', 'suv'),
    (('xe van',), 'Xe van', 'vehicle_type', 'van'), (('sedan', 'xe con'), 'Xe sedan', 'vehicle_type', 'sedan'),
    (('ban tai', 'xe ban tai'), 'Xe bán tải', 'vehicle_type', 'pickup'), (('xe may', 'xe hai banh', 'mo to'), 'Xe hai bánh', 'vehicle_type', 'two-wheels'),
]

FACTS = [
    (('nguoi la', 'stranger', 'unknown person', 'khong xac dinh'), 'Người lạ', 'identity_status', 'unknown'),
    (('nguoi quen', 'da dang ky', 'registered person', 'nhan vien'), 'Người đã đăng ký', 'identity_status', 'known'),
    (('ve som',), 'Về sớm', 'action', 've som'), (('di muon', 'den muon', 'di tre'), 'Đi muộn', 'action', 'di muon'),
    (('check in', 'checkin', 'vao ca', 'cham cong vao'), 'Check-in', 'action', 'check-in'),
    (('check out', 'checkout', 'ra ca', 'cham cong ra'), 'Check-out', 'action', 'check-out'),
    (('tu choi', 'khong co quyen', 'access denied', 'bi chan'), 'Bị từ chối', 'action', 'tu choi'),
]

STOP = set('''nguoi co khong cac nhung nao o tai trong luc vao cua la bi da duoc cho toi xem tim kiem hay voi va hoac su kien event
    events camera cam thiet nguon gi bao nhieu lan the khi ai nhu the nao dau khu vuc tu den gio h ngay tat ca moi
    show find me the at in on of and or any all hien thi liet ke danh sach ra nhe a oi di duoc khong vong
    xuat khoang buoi sang trua chieu dem khuya'''.split())

DAY = timedelta(days=1)


def _day(value):
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _times(n, now):
    """Return (start, end, label, consumed spans) for the first time expression found."""
    today = _day(now)
    fixed = [
        (r'\btoi qua\b|\bdem qua\b', today - DAY + timedelta(hours=18), today + timedelta(hours=6), 'Tối qua'),
        (r'\bhom qua\b', today - DAY, today, 'Hôm qua'),
        (r'\bhom kia\b', today - 2 * DAY, today - DAY, 'Hôm kia'),
        (r'\bsang nay\b', today, today + timedelta(hours=12), 'Sáng nay'),
        (r'\bchieu nay\b', today + timedelta(hours=12), today + timedelta(hours=18), 'Chiều nay'),
        (r'\btoi nay\b|\bdem nay\b', today + timedelta(hours=18), today + DAY, 'Tối nay'),
        (r'\btuan nay\b', today - timedelta(days=now.weekday()), now, 'Tuần này'),
        (r'\btuan truoc\b', today - timedelta(days=now.weekday() + 7), today - timedelta(days=now.weekday()), 'Tuần trước'),
        (r'\bthang nay\b', today.replace(day=1), now, 'Tháng này'),
        (r'\bhom nay\b', today, now, 'Hôm nay'),
        (r'\bgan day\b|\bmoi day\b|\bvua roi\b', now - DAY, now, '24 giờ qua'),
    ]
    # "8 giờ trước" is 8 hours ago, but in "sau 8h trước 10h" the "trước" belongs to the next clock time.
    m = re.search(r'(?<!sau )(?<!tu )(?<!luc )(?<!khoang )\b(\d{1,3}) ?(phut|gio|tieng|h|ngay|tuan|thang)( qua| truoc(?! \d)| gan day| vua qua)\b', n)
    if m:
        amount, unit = int(m.group(1)), m.group(2)
        delta = {'phut': timedelta(minutes=1), 'gio': timedelta(hours=1), 'tieng': timedelta(hours=1), 'h': timedelta(hours=1),
                 'ngay': DAY, 'tuan': 7 * DAY, 'thang': 30 * DAY}[unit]
        label = {'phut': 'phút', 'gio': 'giờ', 'tieng': 'giờ', 'h': 'giờ', 'ngay': 'ngày', 'tuan': 'tuần', 'thang': 'tháng'}[unit]
        return now - amount * delta, now, f'{amount} {label} qua', [m.span()]
    m = re.search(r'\b(?:ngay )?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b', n)
    if m:
        try:
            start = _day(now).replace(year=int(m.group(3) or now.year), month=int(m.group(2)), day=int(m.group(1)))
            return start, start + DAY, f'Ngày {start:%d/%m/%Y}', [m.span()]
        except ValueError:
            pass
    for pattern, start, end, label in fixed:
        m = re.search(pattern, n)
        if m:
            return start, end, label, [m.span()]
    return None, None, None, []


APPROX = timedelta(minutes=15)      # "lúc 5 giờ chiều": a single clock time means this much either side
# A clock time needs h/giờ or ':mm', so plain numbers (camera 5, 10 phút) are never read as hours.
_CLOCK = r'(\d{1,2})(?: ?(?:h|gio)(?: ?(\d{1,2})(?: ?phut)?)?|:(\d{2}))'
# Part of the day after a clock time; "toi" followed by another time is "tới" (to), not "tối" (evening).
_PART = r'(?: (?:buoi )?(sang|trua|chieu|toi|dem|khuya)(?! ?\d))?'
_RANGE = re.compile(r'\b(?:(?:tu|khoang|tam|vao|luc) )?' + _CLOCK + _PART + r'(?: ?(?:den|toi|cho den|-|~) ?| )(?:khoang )?' + _CLOCK + _PART + r'\b')
_SINGLE = re.compile(r'\b(?:(luc|khoang|tam|vao luc|vao|chung|gan|sau|tu|truoc) )?' + _CLOCK + _PART + r'\b')
_BEFORE = re.compile(r'\btruoc ' + _CLOCK + _PART + r'\b')
_ONLY_PART = re.compile(r'\b(?:buoi )?(sang|trua|chieu|toi|dem|khuya)(?= hom| ngay| qua|$)')
PARTS = {'sang': (0, 12, 'Buổi sáng'), 'trua': (11, 14, 'Buổi trưa'), 'chieu': (12, 18, 'Buổi chiều'),
         'toi': (18, 24, 'Buổi tối'), 'dem': (18, 30, 'Ban đêm'), 'khuya': (22, 30, 'Đêm khuya')}


def _clock(hour, minute, part):
    """24-hour (hour, minute) for '5 giờ 15 chiều' style times."""
    hour, minute = int(hour), int(minute or 0)
    if part in ('chieu', 'toi') and hour < 12 or part == 'trua' and hour <= 5 or part in ('dem', 'khuya') and 7 <= hour < 12:
        hour += 12
    elif part in ('dem', 'khuya') and hour == 12:
        hour = 0
    return hour % 24, min(minute, 59)


def _hours(n, start, end, now, used=()):
    """Narrow the day found (or today) to clock times: 'từ 17h đến 17h15', 'khoảng 5 giờ đến 5 giờ 15 chiều',
    '17h-17h15', 'lúc 5 giờ chiều' (±APPROX), 'sau 22h', 'trước 8 giờ', or just a part of the day ('chiều hôm qua')."""
    base = start if start is not None and end - start <= DAY + timedelta(hours=12) else _day(now)
    # Words already read as the day ("sáng nay", "24 giờ qua") are blanked so they are not read again as clock times.
    original, chars = n, list(n)
    for a, b in used:
        chars[a:b] = ' ' * (b - a)
    n = ''.join(chars)
    # Inside a part of the day ("chiều nay", "tối qua") a bare "5 giờ" means the one that falls in it: 17:00, not 05:00.
    window = (start, end) if start is not None and end - start < DAY else None

    def at(hour, minute, part):
        h, mi = _clock(hour, minute, part)
        moment = base.replace(hour=h, minute=mi)
        if window and part is None:
            moment = next((t for t in (moment, moment + timedelta(hours=12), moment + DAY) if window[0] <= t < window[1]), moment)
        return moment

    m = _RANGE.search(n)
    if m:
        h1, m1, part1, h2, m2, part2 = m.group(1), m.group(2) or m.group(3), m.group(4), m.group(5), m.group(6) or m.group(7), m.group(8)
        part1 = part1 or (part2 if int(h1) <= 12 else None)    # "5 giờ đến 5 giờ 15 chiều": the afternoon covers both
        s, e = at(h1, m1, part1), at(h2, m2, part2 or part1)
        if e <= s:
            e += DAY
        return s, e, f'{s:%H:%M}–{e:%H:%M}', [m.span()]

    m = _SINGLE.search(n)
    if m:
        word, moment = m.group(1), at(m.group(2), m.group(3) or m.group(4), m.group(5))
        if word in ('sau', 'tu'):
            later = _BEFORE.search(n, m.end())                                  # "sau 8h trước 10h"
            if later:
                until = at(later.group(1), later.group(2) or later.group(3), later.group(4))
                if until > moment:
                    return moment, until, f'{moment:%H:%M}–{until:%H:%M}', [m.span(), later.span()]
            return moment, end if end is not None and end > moment else base + DAY, f'sau {moment:%H:%M}', [m.span()]
        if word == 'truoc':
            return start if start is not None and start < moment else base, moment, f'trước {moment:%H:%M}', [m.span()]
        return moment - APPROX, moment + APPROX, f'khoảng {moment:%H:%M}', [m.span()]

    m = _ONLY_PART.search(original)     # needs the day words it qualifies ("chiều hôm qua"), so read the unblanked text
    if m and start is not None and not any(a < m.end() and m.start() < b for a, b in used):
        first, last, label = PARTS[m.group(1)]
        return base + timedelta(hours=first), base + timedelta(hours=last), label, [m.span()]
    return start, end, None, []


def interpret(text, catalog, now=None):
    """catalog: {'sources': [{site_id, source_id, name}], 'event_types': [{id, name}]} visible to the caller."""
    now = now or datetime.now(timezone.utc)
    n = fold(text)
    chips, used = [], []
    start, end, label, spans = _times(n, now)
    used += spans
    h_start, h_end, h_label, h_spans = _hours(n, start, end, now, used)
    if h_spans:
        start, end, used = h_start, h_end, used + h_spans
        label = f'{label}, {h_label}' if label else h_label
    if label:
        chips.append({'type': 'time', 'label': label})

    kind, event_ids = None, set()
    names = [(e['id'], fold(e['name'])) for e in catalog.get('event_types', [])]
    for phrases, label, pattern, event_kind in EVENTS:
        for phrase in phrases:
            m = re.search(r'\b' + re.escape(phrase) + r'\b', n)
            if not m or any(a <= m.start() < b for a, b in used):
                continue
            if event_kind:
                used.append(m.span())
                kind = event_kind
                chips.append({'type': 'kind', 'label': label})
            else:
                matched = {i for i, name in names if re.search(pattern, name)}
                if not matched:
                    continue   # this kind of event has not been seen: leave the words for other rules
                used.append(m.span())
                event_ids |= matched
                chips.append({'type': 'event', 'label': label})
            break

    facts = []
    for noun, role, label in COLOR_NOUNS:
        for m in re.finditer(r'\b' + noun + r'(?: mau)? (' + COLOR_RE + r')\b', n):
            if any(a <= m.start() < b for a, b in used):
                continue
            color = dict(COLORS)[m.group(1)]
            used.append(m.span())
            facts.append([role, color])
            chips.append({'type': 'fact', 'label': f"{label} {COLOR_LABEL[m.group(1)]}"})
    for phrases, label, role, value in ATTRIBUTES:
        for phrase in sorted(phrases, key=len, reverse=True):
            m = re.search(r'\b' + re.escape(phrase) + r'\b', n)
            if m and not any(a <= m.start() < b for a, b in used):
                used.append(m.span())
                facts.append([role, value])
                chips.append({'type': 'fact', 'label': label})
                break
    if any(f[0] == 'vehicle_type' for f in facts):   # "xe tải màu trắng": the colour follows the type word
        for m in re.finditer(r'\bmau (' + COLOR_RE + r')\b', n):
            if not any(a <= m.start() < b for a, b in used):
                used.append(m.span())
                facts.append(['vehicle_color', dict(COLORS)[m.group(1)]])
                chips.append({'type': 'fact', 'label': f'Màu {COLOR_LABEL[m.group(1)]}'})
    for phrases, label, role, value in FACTS:
        for phrase in phrases:
            m = re.search(r'\b' + re.escape(phrase) + r'\b', n)
            if m and not any(a <= m.start() < b for a, b in used):
                used.append(m.span())
                facts.append([role, value])
                chips.append({'type': 'fact', 'label': label})
                break

    latest = re.search(r'\b(?:cho )?(?:moi|tung) (?:camera|cam)\b', n)
    if latest and not any(a <= latest.start() < b for a, b in used):
        used.append(latest.span())
        chips.append({'type': 'mode', 'label': 'Mỗi camera 1 kết quả gần nhất'})

    sources = [(s['source_id'], fold(s['name'])) for s in catalog.get('sources', [])]
    source_ids = set()
    for m in re.finditer(r'(?<![\d.])((?:\d{1,3}\.){1,3}\d{1,3}|\.\d{1,3})(?![\d.])', n):
        ip = m.group(1)
        pattern = (r'\d' if ip.startswith('.') else r'(?<![\d.])') + re.escape(ip) + r'(?![\d])'
        hits = {i for i, name in sources if re.search(pattern, name)}
        if hits:
            source_ids |= hits
            used.append(m.span())
            chips.append({'type': 'source', 'label': 'Camera ' + ip})

    # People, plates and watchlists seen in the data: order and accents do not matter ("Phi Ngo Van" = "Ngô Văn Phi").
    entity_ids = []
    free = [m for m in re.finditer(r'[a-z0-9]+', n) if not any(a <= m.start() < b for a, b in used)]
    present = {m.group() for m in free}
    for e in sorted(catalog.get('entities', []), key=lambda e: -len(e['id'].split())):
        parts = e['id'].split()
        if len(parts) >= 2 and set(parts) <= present or e['kind'] == 'plate' and re.sub(r'[^a-z0-9]', '', e['id']) in re.sub(r'[^a-z0-9]', '', n):
            entity_ids.append(e['id'])
            present -= set(parts)
            for m in free:
                if m.group() in parts:
                    used.append(m.span())
            chips.append({'type': 'entity', 'label': e['name']})
    words = []
    for m in re.finditer(r'[a-z0-9]+', n):
        if any(a <= m.start() < b for a, b in used) or m.group() in STOP or len(m.group()) < 2:
            continue
        words.append(m.group())
    name_words = [w for w in words if not w.isdigit() and any(re.search(r'\b' + w, name) for _, name in sources)]
    if name_words:
        hits = {i for i, name in sources if all(re.search(r'\b' + w, name) for w in name_words)}
        if hits:
            source_ids = (source_ids & hits) if source_ids else hits
            chips.append({'type': 'source', 'label': ' '.join(name_words)})
            words = [w for w in words if w not in name_words]
    filters = {'start': start, 'end': end, 'kind': kind, 'entities': entity_ids or None, 'facts': facts or None,
               'event_types': sorted(event_ids) or None, 'source_ids': sorted(source_ids) or None,
               'latest_per_source': True if latest and latest.span() in used else None}
    return {'filters': filters, 'keywords': ' '.join(words), 'chips': chips}
