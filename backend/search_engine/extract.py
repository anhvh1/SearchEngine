"""Vendor-independent extraction from Milestone event/alarm payloads.

L1 flattens every payload into (path, key, value) attributes. L2 assigns a small set of roles (person,
plate, place, action, ...) from key names, value shapes and text templates learned per event type.
Nothing here is specific to one AI vendor; adding a new AI needs no code change.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from .query import fold

GUID = re.compile(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$|^[0-9a-fA-F]{32}$')
BASE64 = re.compile(r'^[A-Za-z0-9+/=\s]{120,}$')
# Structure, identity and binary keys: kept out of text and roles.
SKIP_KEYS = {'fqid', 'serverid', 'objectid', 'parentid', 'kind', 'foldertype', 'id', 'image', 'mask', 'snapshotlist',
             'snapshot', 'boundingbox', 'polygon', 'polygonlist', 'path', 'timeoffset', 'hostname', 'port', 'scheme',
             'version', 'messageid', 'timestamp', 'starttime', 'endtime', 'priority', 'priorityname', 'haslayout',
             'hasoverlay', 'width', 'height', 'sizeinbytes', 'extensiondata', 'removed', 'alarmtrigger', 'count', 'state'}
# Key name fragments (folded, no separators) that reveal what a value is.
KEY_ROLES = [
    (r'licen[cs]eplate|plate|bienso|lpr', 'plate'),
    (r'watchlist', 'watchlist'),
    (r'person|people|fullname|employee|staff|visitor|nhanvien|hoten', 'person'),
    (r'code$|ocr|barcode|qrcode', 'code'),
    (r'container', 'container'),
    (r'gender|gioitinh', 'gender'), (r'^age$|agegroup|dotuoi', 'age'),
    (r'colou?r|^mau', 'color'), (r'vehicle|^xe$|loaixe', 'vehicle'),
    (r'zone|area|region|location|place|khuvuc', 'place'),
    (r'direction|huong', 'direction'), (r'confidence|score', 'confidence'),
    (r'card|credential|^the$|sothe', 'card'), (r'door|^cua$', 'door'),
]
PLATE = re.compile(r'^\d{2}[A-Z]{1,2}\d?[-\s.]?\d{3}\.?\d{1,2}$')
class _NameToken:
    """Capitalised word such as 'Ngọc' or 'Đức' (Unicode-aware; 'đã' is not one)."""
    @staticmethod
    def match(token):
        body = token.replace("'", '')
        return bool(body) and body[0].isupper() and body.isalpha() and (len(body) == 1 or body[1:].islower())


NAME_TOKEN = _NameToken()
UNKNOWN_WORDS = ('nguoi la', 'unknown', 'stranger', 'unregistered', 'khong xac dinh')
KNOWN_WORDS = ('registered', 'da dang ky', 'faceme.person', 'nhan vien')
ACTIONS = [('checkin', 'check-in'), ('check in', 'check-in'), ('checkout', 'check-out'), ('check out', 'check-out'),
           ('ve som', 'về sớm'), ('di muon', 'đi muộn'), ('den muon', 'đi muộn'), ('access denied', 'từ chối'),
           ('khong co quyen', 'từ chối'), ('access granted', 'cho phép'), ('forced', 'mở cưỡng bức')]


def clean(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', str(value))).strip()


def _compact(key):
    return re.sub(r'[^a-z0-9]', '', fold(key))


def _textual(value):
    return isinstance(value, str) and value.strip() and not GUID.match(value.strip()) and not BASE64.match(value)


def flatten(data):
    """Every meaningful scalar in the envelope as {path, key, value}."""
    out = []

    def add(path, key, value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            out.append({'path': path, 'key': key, 'value': value})
        elif _textual(value):
            out.append({'path': path, 'key': key, 'value': clean(value)})

    def walk(node, path, key):
        if isinstance(node, dict):
            # Analytics objects: the object's own Name/Type labels its Value ("BankName" = "Local default").
            if 'Value' in node or 'value' in node:
                label = node.get('Name') or node.get('name') or node.get('Type') or node.get('type') or key
                add(path + '.Value', str(label), node.get('Value', node.get('value')))
                for k, v in node.items():
                    if k.lower() not in ('value', 'name', 'type'):
                        walk(v, f'{path}.{k}', k)
                return
            for k, v in node.items():
                if _compact(k) in SKIP_KEYS:
                    continue
                walk(v, f'{path}.{k}' if path else k, k)
        elif isinstance(node, list):
            for i, v in enumerate(node[:200]):
                walk(v, f'{path}[{i}]', key)
        elif isinstance(node, str) and node.strip()[:1] in '{[<' and len(node) < 65536:
            parsed = _parse_embedded(node)
            if parsed is not None:
                walk(parsed, path, key)
            else:
                add(path, key, node)
        else:
            add(path, key, node)

    for field in ('message', 'description', 'location', 'state'):
        add(field, field, data.get(field))
    walk(data.get('payload') or {}, '', 'payload')
    seen, unique = set(), []
    for a in out:
        mark = (a['key'], a['value'])
        if mark not in seen:
            seen.add(mark)
            unique.append(a)
    return unique


def _parse_embedded(text):
    """Vendor CustomData is often JSON or XML inside a string."""
    import json
    try:
        return json.loads(text)
    except ValueError:
        pass
    if text.lstrip().startswith('<'):
        import xml.etree.ElementTree as ET
        try:
            return _xml(ET.fromstring(text))
        except ET.ParseError:
            return None
    return None


def _xml(element):
    local = lambda name: name.split('}')[-1]
    node = {local(k): v for k, v in element.attrib.items()}
    children = list(element)
    if not children:
        text = (element.text or '').strip()
        return {local(element.tag): {**node, 'text': text} if node else text}
    for child in children:
        (k, v), = _xml(child).items()
        if k in node:
            node[k] = (node[k] if isinstance(node[k], list) else [node[k]]) + [v]
        else:
            node[k] = v
    return {local(element.tag): node}


def looks_like_person(value):
    parts = str(value).split()
    return 2 <= len(parts) <= 6 and sum(bool(NAME_TOKEN.match(p)) for p in parts) >= max(2, len(parts) - 1)


def split_code(value):
    """'Hồng Ánh 0132465' -> ('Hồng Ánh', '0132465')."""
    m = re.match(r'^(.*?)[\s,;:-]+(\d{3,})$', clean(value))
    return (clean(m.group(1)), m.group(2)) if m and m.group(1) else (clean(value), None)


def entity_key(name):
    """Order- and accent-independent identity of a name: 'Phi Ngo Van' == 'Ngô Văn Phi'."""
    return ' '.join(sorted(fold(split_code(name)[0]).split()))


def event_context(data, event_name):
    text = fold(' '.join(str(x) for x in (event_name, data.get('event_type'), data.get('message'))))
    return {'face': any(w in text for w in ('face', 'khuon mat', 'faceme', 'nhan dien', 'checkin', 'checkout')),
            'people': 'people' in text, 'vehicle': any(w in text for w in ('vehicle', 'xe ', 'license', 'plate', 'bien so')),
            'text': text}


def classify(attrs, data, event_name):
    """Assign roles to flattened attributes; returns (attributes, facts)."""
    ctx = event_context(data, event_name)
    for a in attrs:
        key, value = _compact(a['key']), a['value']
        role = next((r for pattern, r in KEY_ROLES if re.search(pattern, key)), None)
        if isinstance(value, str):
            if role is None and PLATE.match(value.replace(' ', '')):
                role = 'plate'
            # Analytics objects without a label: meaning comes from the event (face -> person name, people -> watchlist).
            if role is None and a['path'].endswith('.Value') and key in ('value', 'payload', 'objects', 'object', 'objectlist'):
                role = 'plate' if ctx['vehicle'] and PLATE.match(value.replace(' ', '')) else \
                       'watchlist' if ctx['people'] or ctx['vehicle'] else 'person' if ctx['face'] or looks_like_person(value) else 'value'
        a['role'] = role
    facts = {}
    status = 'unknown' if any(w in ctx['text'] for w in UNKNOWN_WORDS) else 'known' if any(w in ctx['text'] for w in KNOWN_WORDS) else None
    if status:
        facts['identity_status'] = status
    text = ctx['text'] + ' ' + fold(' '.join(str(a['value']) for a in attrs if isinstance(a['value'], str)))
    actions = list(dict.fromkeys(label for word, label in ACTIONS if word in text))
    if actions:
        facts['action'] = actions
    return attrs, facts


# ---------- templates learned per event type ----------
TOKEN = re.compile(r'\S+')


def learn_template(texts, min_samples=2):
    """Induce 'constant {slot} constant' from differing texts of one event type, or None."""
    samples = [clean(t) for t in dict.fromkeys(texts) if t and clean(t)]
    if len(samples) < min_samples:
        return None
    base = TOKEN.findall(samples[0])
    constant = [True] * len(base)
    for other in samples[1:]:
        tokens = TOKEN.findall(other)
        keep = [False] * len(base)
        for block in SequenceMatcher(None, base, tokens, autojunk=False).get_matching_blocks():
            for i in range(block.a, block.a + block.size):
                keep[i] = True
        constant = [c and k for c, k in zip(constant, keep)]
    parts, slot = [], False
    for token, fixed in zip(base, constant):
        if fixed:
            parts.append(token)
            slot = False
        elif not slot:
            parts.append('{}')
            slot = True
    template = ' '.join(parts)
    slots = template.count('{}')
    fixed_tokens = sum(constant)
    if not slots or fixed_tokens == 0 or slots > 4:
        return None
    # A proper name left in the constant part means the samples shared a person, not a format.
    capitals = 0
    for token in template.split():
        capitals = capitals + 1 if token != '{}' and NAME_TOKEN.match(token) and not token.isupper() else 0
        if capitals >= 2:
            return None
    return template


def template_regex(template):
    pieces = [re.escape(p) for p in template.split('{}')]
    return re.compile('^' + '(.+?)'.join(pieces) + '$')


def apply_template(template, text):
    m = template_regex(template).match(clean(text or ''))
    return [clean(g) for g in m.groups()] if m else None


def slot_roles(template, samples):
    """Role of each slot from its values and the words around it."""
    pieces = template.split('{}')
    rows = [v for v in (apply_template(template, s) for s in samples) if v]
    roles = []
    for i in range(template.count('{}')):
        values = [r[i] for r in rows]
        before, after = fold(pieces[i]).split()[-1:] or [''], fold(pieces[i + 1]).split()[:1] or ['']
        before, after = before[0], after[0]
        if values and all(re.fullmatch(r'\d+(?:[.,]\d+)?', v) for v in values):
            role = 'duration_minutes' if after in ('phut', 'minutes', 'min') else 'number'
        elif values and all(PLATE.match(v.replace(' ', '')) for v in values):
            role = 'plate'
        elif before in ('tai', 'o', 'at', 'in', 'camera', 'cam') or all(re.match(r'(?i)camera\s*\d+', v) for v in values):
            role = 'place'
        elif values and sum(looks_like_person(split_code(v)[0]) for v in values) >= max(1, len(values) * 0.6):
            role = 'person'
        else:
            role = 'value'
        roles.append(role)
    return roles
