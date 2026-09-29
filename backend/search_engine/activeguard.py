"""i-PRO Active Guard server WebAPI (v1.1): best shots of people, vehicles, plates and faces.

Active Guard stores every detection with attributes (gender, age, clothing colours, bag, hat ...) but only sends
watchlist matches to Milestone. This connector reads the best shots over the documented WebAPI (HTTP Digest,
default port 8090), turns each into an event envelope and lets the normal pipeline index it.
"""
import base64
import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from .identity import protect, unprotect

SRV = '9999'          # fixed by the API
TYPES = {'people': 'section_people', 'vehicle': 'section_vehicle', 'lpr': 'section_lpr', 'face': 'section_face'}
TITLES = {'people': 'Phát hiện người', 'vehicle': 'Phát hiện phương tiện', 'lpr': 'Nhận dạng biển số', 'face': 'Phát hiện khuôn mặt'}
CHECKPOINT = 'activeguard'

VI = {'male': 'nam', 'female': 'nữ', '0-10': 'trẻ em', '11-20': 'thiếu niên', '21-60': 'người lớn', '61+': 'người cao tuổi',
      'long-hair': 'tóc dài', 'short-hair': 'tóc ngắn', 'hat': 'đội mũ', 'long-sleeves': 'áo dài tay', 'short-sleeves': 'áo ngắn tay',
      'long': 'quần dài', 'short': 'quần ngắn',
      'black': 'đen', 'brown': 'nâu', 'white': 'trắng', 'gray': 'xám', 'green': 'xanh lá', 'red': 'đỏ', 'blue': 'xanh dương',
      'yellow': 'vàng', 'orange': 'cam', 'purple': 'tím', 'pink': 'hồng', 'gold': 'vàng kim', 'violet': 'tím', 'beige': 'be', 'golden': 'vàng kim',
      'truck': 'xe tải', 'bus': 'xe buýt', 'suv': 'SUV', 'van': 'xe van', 'sedan': 'xe sedan', 'pickup': 'xe bán tải',
      'two-wheels': 'xe hai bánh', 'car': 'ô tô', 'motorcycle': 'xe máy'}
PEOPLE_GROUPS = ('gender', 'age', 'hair_style', 'hair_color', 'upper_garment', 'upper_color', 'lower_garment', 'lower_color',
                 'sunglasses', 'face_mask', 'beard', 'bag', 'bag_color', 'shoes_color')


class ActiveGuardError(Exception):
    pass


def top_label(scores):
    """Highest-scoring label of one attribute group; the API returns a one-element list of {label: score}."""
    table = scores[0] if isinstance(scores, list) and scores else scores
    if not isinstance(table, dict) or not table:
        return None, 0.0
    label, score = max(table.items(), key=lambda kv: float(kv[1]))
    return label, float(score)


def attributes_from(kind, info, min_score):
    """Flat {attribute: label} kept only where the model is confident, plus the raw scores for review."""
    attrs, groups = {}, {}
    if kind == 'people':
        groups = {g: info.get(g) for g in PEOPLE_GROUPS}
    elif kind == 'vehicle':
        groups = {'vehicle_type': info.get('vehicle_type'), 'vehicle_color': info.get('vehicle_color')}
    for name, scores in groups.items():
        label, score = top_label(scores)
        if label and score >= min_score:
            attrs[name] = label
    if kind == 'lpr':
        for field, key in (('license_plate', 'license_plate_number'), ('country_code', 'license_plate_country'),
                           ('brand', 'vehicle_brand'), ('model', 'vehicle_model'), ('vehicle_type', 'vehicle_type'),
                           ('vehicle_color', 'vehicle_color')):
            value = info.get(key)
            value = next(iter(value.values())) if isinstance(value, dict) and value else value
            if isinstance(value, str) and value.strip():
                attrs[field] = value.strip()
    for extra in ('day_night', 'direction'):
        if isinstance(info.get(extra), str):
            attrs[extra] = info[extra]
    return attrs


def describe(kind, attrs):
    """One Vietnamese sentence, so the text index finds the record even without structured filters."""
    v = lambda x: VI.get(x, x)
    parts = []
    if kind == 'people':
        parts += [v(attrs[k]) for k in ('gender', 'age') if k in attrs]
        if 'hair_style' in attrs:
            parts.append(v(attrs['hair_style']) + (f" {v(attrs['hair_color'])}" if attrs.get('hair_color') and attrs['hair_style'] != 'hat' else ''))
        elif 'hair_color' in attrs:
            parts.append('tóc ' + v(attrs['hair_color']))
        for garment, color in (('upper_garment', 'upper_color'), ('lower_garment', 'lower_color')):
            noun = 'áo' if garment.startswith('upper') else 'quần'
            if garment in attrs or color in attrs:
                parts.append((v(attrs[garment]) if garment in attrs else noun) + (f" {v(attrs[color])}" if color in attrs else ''))
        for key, phrase in (('sunglasses', 'đeo kính râm'), ('face_mask', 'đeo khẩu trang'), ('beard', 'có râu')):
            if attrs.get(key) == 'yes':
                parts.append(phrase)
        if attrs.get('bag') == 'yes' or 'bag_color' in attrs:
            parts.append('mang túi' + (f" {v(attrs['bag_color'])}" if 'bag_color' in attrs else ''))
        if 'shoes_color' in attrs:
            parts.append('giày ' + v(attrs['shoes_color']))
        return ', '.join(parts).capitalize() if parts else 'Người'
    if kind == 'vehicle':
        text = v(attrs.get('vehicle_type', 'xe')) + (f" màu {v(attrs['vehicle_color'])}" if 'vehicle_color' in attrs else '')
        return text.capitalize()
    if kind == 'lpr':
        bits = [f"Biển số {attrs['license_plate']}"] if attrs.get('license_plate') else ['Biển số']
        bits += [b for b in (attrs.get('brand'), attrs.get('model'), v(attrs['vehicle_type']) if 'vehicle_type' in attrs else None,
                             f"màu {v(attrs['vehicle_color'])}" if 'vehicle_color' in attrs else None) if b]
        return ', '.join(bits)
    return 'Khuôn mặt'


def to_utc_iso(value):
    """'2026-09-29 01:02:03:456' (UTC) -> '2026-09-29T01:02:03.456000Z'."""
    text = str(value).strip()
    if text.count(':') == 3:
        head, ms = text.rsplit(':', 1)
        text = f'{head}.{ms.ljust(3, "0")[:3]}'
    return datetime.fromisoformat(text.replace(' ', 'T')).replace(tzinfo=timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def api_time(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


class ActiveGuard:
    def __init__(self, url, username, password, transport=None, verify=True):
        self.client = httpx.Client(base_url=url.rstrip('/'), auth=httpx.DigestAuth(username, password), timeout=60,
                                   transport=transport, verify=verify, follow_redirects=False)

    def close(self):
        self.client.close()

    def call(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ActiveGuardError('Không kết nối được Active Guard server') from exc
        if response.status_code in (401, 403):
            raise ActiveGuardError('Active Guard từ chối tài khoản hoặc mật khẩu')
        if response.status_code >= 400:
            raise ActiveGuardError(f'Active Guard trả lỗi HTTP {response.status_code}')
        try:
            body = response.json()
        except ValueError as exc:
            raise ActiveGuardError('Active Guard trả dữ liệu không phải JSON') from exc
        status = body.get('status_body') or {}
        if status.get('status') is False:
            detail = (status.get('details') or [{}])[0]
            if detail.get('code') == 'C0008':
                return {'result_body': {'search_result': [], 'result_count': 0}}
            raise ActiveGuardError(f"Active Guard: {detail.get('code', '')} {detail.get('message', '')}".strip())
        return body

    def system_info(self):
        return self.call('GET', '/ai/system/info').get('result_body', {})

    def cameras(self):
        rows = []
        body = self.call('GET', '/ai/v1.0/cameras/info').get('result_body', {})
        for system in body.get('system_type_list', []):
            for server in system.get('srv_id_list', []):
                for cam in server.get('cameras', []):
                    rows.append({'camera_id': str(cam.get('camera_id')), 'name': cam.get('camera_name') or cam.get('camera_ip') or '',
                                 'ip': cam.get('camera_ip'), 'model': cam.get('camera_model'),
                                 'capability': cam.get('ai_capability') or [], 'enabled': str(cam.get('is_enabled', 'yes')).lower() not in ('no', 'false', '0')})
        return rows

    def search(self, kind, section, start, end, newest_first=False):
        body = {'date_from': api_time(start), 'date_to': api_time(end),
                'sort': {'sort-item': 'shot-date-time', 'asc-desc': 'desc' if newest_first else 'asc'}, TYPES[kind]: section}
        return self.call('POST', '/ai/v1.0/thumbnail/search', json=body)['result_body']['search_session_id']

    def results(self, session, start=0, count=200):
        body = self.call('GET', f'/ai/v1.0/thumbnail/search/{session}', params={'result-from': start + 1, 'result-count': count})   # the API numbers results from 1
        result = body.get('result_body', {})
        return result.get('result_count', 0), result.get('search_result', [])

    def thumbnail(self, key, info=True):
        params = {'thumbnail-key': key}
        if info:
            params['info'] = 'on'
        return self.call('GET', '/ai/v1.0/thumbnail', params=params).get('result_body', {})


# ---------------- settings ----------------
def load_settings(engine, config):
    saved = engine.checkpoint(CHECKPOINT)
    merged = {**(config.get('activeguard') or {}), **(json.loads(saved) if saved else {})}
    merged.setdefault('site_id', 'activeguard')   # its cameras belong to the VMS Active Guard is registered to, which may not be ours
    merged.setdefault('min_score', 0.5)
    merged.setdefault('lookback_hours', 24)
    merged.setdefault('max_per_cycle', 300)
    merged.setdefault('interval_seconds', 60)
    merged.setdefault('types', ['face', 'people', 'vehicle', 'lpr'])   # only those the server and cameras actually offer are read
    merged.setdefault('verify_certificates', False)
    return merged


def credentials(settings):
    if settings.get('username') and settings.get('password_protected'):
        return settings['username'], unprotect(settings['password_protected'])
    import os
    user, password = settings.get('username'), os.environ.get(settings.get('password_env', ''))
    return (user, password) if user and password else (None, None)


def connect(engine, config, transport=None):
    settings = load_settings(engine, config)
    user, password = credentials(settings)
    if not settings.get('url') or not user:
        return None, settings
    return ActiveGuard(settings['url'], user, password, transport, settings['verify_certificates']), settings


def save_settings(engine, config, url, username, password, site_id, actor, transport=None):
    if not url.startswith(('http://', 'https://')):
        raise ValueError('Địa chỉ Active Guard phải bắt đầu bằng http:// hoặc https://')
    previous = engine.checkpoint(CHECKPOINT)
    previous = json.loads(previous) if previous else {}
    value = {'url': url.rstrip('/'), 'username': username or previous.get('username', ''), 'site_id': site_id or 'main'}
    if password:
        value['password_protected'] = protect(password)
    elif previous.get('password_protected') and value['username'] == previous.get('username'):
        value['password_protected'] = previous['password_protected']
    user, secret = credentials(value)
    if not user:
        raise ValueError('Nhập tài khoản và mật khẩu Active Guard')
    client = ActiveGuard(value['url'], user, secret, transport, load_settings(engine, config)['verify_certificates'])
    try:
        info = client.system_info()
    except ActiveGuardError as exc:
        raise ValueError(str(exc))
    finally:
        client.close()
    engine.checkpoint(CHECKPOINT, json.dumps(value))
    with engine.lock, engine.db:
        engine.audit(actor, 'activeguard.connection', {'url': value['url'], 'username': value['username']})
    return info


def describe_settings(engine, config):
    s = load_settings(engine, config)
    user, _ = credentials(s) if s.get('url') else (None, None)
    return {'url': s.get('url'), 'username': user or '', 'site_id': s['site_id'], 'has_credentials': bool(user),
            'types': s['types'], 'last_sync': engine.checkpoint('activeguard:last_sync'), 'last_error': engine.checkpoint('activeguard:last_error') or None,
            'imported': int(engine.checkpoint('activeguard:imported') or 0)}


# ---------------- import ----------------
def envelope(kind, hit, info, camera, settings):
    key = hit['thumbnail_key']
    attrs = attributes_from(kind, info, float(settings['min_score']))
    text = describe(kind, attrs)
    if kind == 'face':
        try:
            score = float(hit.get('degree_of_similarity') or 0)
        except ValueError:
            score = 0.0
        if score > 0:                     # only photo searches carry a similarity
            text += f' (giống {score:.0f}%)'
        elif isinstance(info.get('likelihood'), (int, float)):
            text += f" (độ tin cậy {float(info['likelihood']) * 100:.0f}%)"
    when = to_utc_iso(hit['shot_date_time'])
    name = camera['name'] if camera else str(hit.get('camera_id'))
    payload = {'header': {'ID': key, 'Name': TITLES[kind], 'Message': text, 'Type': f'activeguard:{kind}', 'Source': {'Name': name}},
               'activeguard': {'type': kind, 'thumbnail_key': key, 'attributes': attrs,
                               'similarity': hit.get('degree_of_similarity')},
               'scores': {k: v for k, v in info.items() if k in PEOPLE_GROUPS or k in ('vehicle_type', 'vehicle_color')}}
    if info.get('thumbnail_image'):
        payload['Snapshot'] = {'Image': info['thumbnail_image']}
    return {'site_id': settings['site_id'], 'kind': 'event', 'source_guid': f'ag-{key}', 'source_id': str(hit.get('camera_id') or 'unknown'),
            'event_type': f'activeguard:{kind}', 'occurred_at': when, 'updated_at': when, 'message': text, 'description': text,
            'camera_id': str(hit.get('camera_id') or '') or None, 'payload': payload}


def import_hit(engine, ag, kind, hit, cameras, settings):
    """Fetch one best shot with its attribute scores and ingest it; returns the record key."""
    from .store import record_key
    key = record_key(settings['site_id'], 'event', f"ag-{hit['thumbnail_key']}")
    if engine.has_key(key):
        return key, False
    info = ag.thumbnail(hit['thumbnail_key'], info=True)
    engine.ingest(envelope(kind, hit, info, cameras.get(str(hit.get('camera_id'))), settings))
    return key, True


def sync(engine, ag, settings, now=None):
    """Import new best shots since the last cursor; returns {type: count}. Safe to repeat: keys are idempotent."""
    now = now or datetime.now(timezone.utc)
    cameras = {c['camera_id']: c for c in ag.cameras() if c['enabled']}
    offered = {b.get('section') for b in ag.system_info().get('bestshot_type_list', []) if isinstance(b, dict)}
    done, budget = {}, int(settings['max_per_cycle'])
    for kind in settings['types']:
        if offered and kind not in offered:
            continue        # e.g. people/vehicle best shots need the AI People/Vehicle extension on the cameras
        eligible = [c for c in cameras.values() if kind in c['capability']]
        if not eligible or budget <= 0:
            continue
        cursor = engine.checkpoint(f'activeguard:cursor:{kind}')
        start = (datetime.fromisoformat(cursor) - timedelta(minutes=2)) if cursor else now - timedelta(hours=float(settings['lookback_hours']))
        session = ag.search(kind, {'cameras': [{'srv_id': SRV, 'camera_id': c['camera_id']} for c in eligible]}, start, now)
        offset, count, last = 0, 0, None
        while budget > 0:
            total, hits = ag.results(session, offset, min(100, budget))
            if not hits:
                break
            for hit in hits:
                _, fresh = import_hit(engine, ag, kind, hit, cameras, settings)
                count += fresh
                budget -= fresh
                last = hit['shot_date_time']
            offset += len(hits)
            if offset >= total:
                break
        finished = budget > 0  # ran out of results, not of budget
        engine.checkpoint(f'activeguard:cursor:{kind}', (datetime.fromisoformat(to_utc_iso(last).replace('Z', '+00:00')) if last and not finished else now).isoformat())
        done[kind] = count
    engine.checkpoint('activeguard:last_sync', now.isoformat())
    total = sum(done.values())
    if total:
        engine.checkpoint('activeguard:imported', str(int(engine.checkpoint('activeguard:imported') or 0) + total))
    engine.checkpoint('activeguard:last_error', '')
    return done


def face_photo_search(engine, ag, settings, content, days=7, similarity=70, limit=50):
    """Ask Active Guard itself for faces similar to an uploaded photo; returns [(record_key, similarity)]."""
    cameras = {c['camera_id']: c for c in ag.cameras() if c['enabled']}
    eligible = [c for c in cameras.values() if 'face' in c['capability']]
    if not eligible:
        raise ActiveGuardError('Chưa có camera nào bật nhận diện khuôn mặt trong Active Guard')
    now = datetime.now(timezone.utc)
    session = ag.search('face', {'similarity': str(similarity), 'search_thumbnail_1': base64.b64encode(content).decode('ascii'),
                                 'cameras': [{'srv_id': SRV, 'camera_id': c['camera_id']} for c in eligible]},
                        now - timedelta(days=days), now, newest_first=True)
    _, hits = ag.results(session, 0, limit)
    found = []
    for hit in hits:
        key, _ = import_hit(engine, ag, 'face', hit, cameras, settings)
        try:
            score = float(hit.get('degree_of_similarity') or 0)
        except ValueError:
            score = 0.0
        found.append((key, score))
    return sorted(found, key=lambda kv: -kv[1])


def probe(url, username, password, out_path, verify=False, hours=6):
    """Read-only diagnostic for the operator: writes a sanitized sample of what the server returns."""
    ag = ActiveGuard(url, username, password, verify=verify)
    report = {'time': datetime.now(timezone.utc).isoformat()}
    try:
        report['system_info'] = ag.system_info()
        cams = ag.cameras()
        report['cameras'] = cams
        now = datetime.now(timezone.utc)
        report['samples'] = {}
        for kind in ('people', 'vehicle', 'lpr', 'face'):
            eligible = [c for c in cams if kind in c['capability'] and c['enabled']]
            if not eligible:
                report['samples'][kind] = 'không camera nào hỗ trợ'
                continue
            session = ag.search(kind, {'cameras': [{'srv_id': SRV, 'camera_id': c['camera_id']} for c in eligible]},
                                now - timedelta(hours=hours), now, newest_first=True)
            total, hits = ag.results(session, 0, 3)
            samples = []
            for hit in hits:
                info = ag.thumbnail(hit['thumbnail_key'], info=True)
                image = info.pop('thumbnail_image', '') or ''
                samples.append({'hit': {k: (v if k != 'thumbnail_key' else v[:8] + '…') for k, v in hit.items()},
                                'info': info, 'image_base64_chars': len(image)})
            report['samples'][kind] = {'total_in_window': total, 'examples': samples}
    finally:
        ag.close()
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report
