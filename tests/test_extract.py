"""Extraction, templates, entities and occurrences on synthetic data shaped like Active Guard and FaceMe payloads."""
import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from search_engine.enrich import learn_rules
from search_engine.extract import classify, flatten
from search_engine.store import Engine

T0 = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
SHARED = '90f8ced8-0000-0000-0000-000000000000'   # integrations reuse one event id and differ by header Type
FACE = 'cf5c45b2-0000-0000-0000-000000000000'


def at(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat()


def active_guard(i, person, seconds, kind='alarm', camera='cam-62', image=False):
    header = {'ID': f'ag-{kind}-{i}', 'Name': 'Registered face detection', 'Message': 'Registered face detection',
              'Type': 'Registered face detection', 'MessageId': FACE, 'Source': {'Name': 'Panasonic (10.0.0.62) - Camera 1'}}
    payload = {'header': header}
    if kind == 'alarm':
        payload['objects'] = [{'Value': person + ' '}]
    else:
        payload['event'] = {'EventHeader': header, 'ObjectList': {'Object': {'Value': person}},
                            'SnapshotList': {'Snapshot': {'Image': base64.b64encode(b'\xff\xd8jpeg' * 60).decode()}} if image else None}
    return {'site_id': 'main', 'kind': kind, 'source_guid': header['ID'], 'source_id': camera, 'event_type': FACE,
            'occurred_at': at(seconds), 'updated_at': at(seconds), 'message': 'Registered face detection',
            'description': 'Registered face detection from i-PRO Active Guard', 'camera_id': camera, 'payload': payload}


def faceme(i, kind, name, message, seconds, camera='cam-83'):
    header = {'ID': f'fm-{i}', 'Name': name, 'Message': message, 'Type': kind, 'MessageId': SHARED, 'Source': {'Name': 'Camera ' + camera}}
    return {'site_id': 'main', 'kind': 'alarm', 'source_guid': header['ID'], 'source_id': camera, 'event_type': SHARED,
            'occurred_at': at(seconds), 'updated_at': at(seconds), 'message': message, 'description': name, 'payload': {'header': header}}


def engine_with(tmp_path, envelopes):
    e = Engine(tmp_path / 'x.db')
    for env in envelopes:
        e.ingest(env)
    while e.process_pending():
        pass
    learn_rules(e)
    while e.process_pending():
        pass
    return e


ALL = [['*', '*']]


def test_flatten_reads_objects_and_vendor_data_without_config():
    data = {'message': 'LPR', 'payload': {'objects': [{'Name': 'Zone', 'Value': 'Gate A'}],
                                          'vendor': {'CustomData': '<d><Plate>30A12345</Plate><Colour>Red</Colour></d>'},
                                          'header': {'Source': {'FQID': {'ObjectId': 'skip-me'}}}}}
    attrs, _ = classify(flatten(data), data, 'License plate detection')
    roles = {a['value']: a['role'] for a in attrs}
    assert roles['Gate A'] == 'place' and roles['30A12345'] == 'plate' and roles['Red'] == 'color'
    assert 'skip-me' not in roles


def test_person_found_with_any_spelling_and_order(tmp_path):
    e = engine_with(tmp_path, [active_guard(1, 'Trần Thị Bích Ngọc', 0), active_guard(2, 'Trần Thị Bích Ngọc', 300),
                               faceme(1, 'Checkin', 'Phát hiện Ngoc Tran Thi Bich tại Camera 83', 'Ngoc Tran Thi Bich đã đi muộn 12 phút', 900),
                               faceme(2, 'Checkin', 'Phát hiện Le Van Hai tại Camera 83', 'Le Van Hai đã đi muộn 3 phút', 1000)])
    for query in ('Trần Thị Bích Ngọc', 'tran thi bich ngoc', 'Ngọc Bích Thị Trần'):
        assert e.search(query, ALL)['total'] == 3
    key = 'bich ngoc thi tran'
    assert e.search('', ALL, entities=[key])['total'] == 3          # Active Guard object + FaceMe text are one person
    assert e.search('', ALL, facts=[['action', 'đi muộn']])['total'] == 2
    late = e.search('', ALL, entities=[key], facts=[['action', 'di muon']])['items']
    assert len(late) == 1 and late[0]['facts']['persons'] == ['Trần Thị Bích Ngọc']   # accented spelling preferred


def test_templates_learned_per_kind_and_shared_id(tmp_path):
    e = engine_with(tmp_path, [faceme(1, 'Checkout', 'Phát hiện Le Van Hai tại Camera 62', 'Le Van Hai đã về sớm 40 phút', 0, 'cam-62'),
                               faceme(2, 'Checkout', 'Phát hiện Pham Minh Duc tại Camera 62', 'Pham Minh Duc đã về sớm 5 phút', 100, 'cam-62'),
                               faceme(3, 'FACEME.UNKNOWN_PERSON', 'Phát hiện người lạ tại Cổng số 1', 'FACEME.UNKNOWN_PERSON', 200),
                               faceme(4, 'FACEME.UNKNOWN_PERSON', 'Phát hiện người lạ tại Camera 54', 'FACEME.UNKNOWN_PERSON', 400)])
    rules = {(r[0], r[1]) for r in e.db.execute('SELECT field, template FROM rules')}
    assert ('message', '{} đã về sớm {} phút') in rules
    values = sorted(r[0] for r in e.db.execute("SELECT value FROM attributes WHERE role='duration_minutes'"))
    assert values == ['40', '5']
    strangers = e.search('', ALL, facts=[['identity_status', 'unknown']])
    assert strangers['total'] == 2 and not e.db.execute("SELECT 1 FROM attributes WHERE role='person' AND folded='la nguoi'").fetchone()


def test_event_alarm_and_repeats_become_one_occurrence(tmp_path):
    e = engine_with(tmp_path, [active_guard(1, 'Hoang Minh Khoa', 10, 'alarm'), active_guard(1, 'Hoang Minh Khoa', 9, 'event', image=True),
                               active_guard(2, 'Hoang Minh Khoa', 40, 'alarm'), active_guard(3, 'Hoang Minh Khoa', 500, 'alarm'),
                               active_guard(4, 'Other Person Name', 12, 'alarm')])
    grouped = e.search('', ALL, collapse=True, entities=['hoang khoa minh'])
    assert e.search('', ALL, entities=['hoang khoa minh'])['total'] == 4
    assert grouped['total'] == 2
    first = min(grouped['items'], key=lambda i: i['occurred_at'])
    assert first['episode']['count'] == 3 and first['episode']['alarms'] == 2 and first['episode']['image']


def test_out_of_order_arrival_still_merges(tmp_path):
    e = engine_with(tmp_path, [active_guard(3, 'Vo Thanh Tung', 60, 'alarm'), active_guard(1, 'Vo Thanh Tung', 0, 'alarm'),
                               active_guard(2, 'Vo Thanh Tung', 30, 'alarm')])
    assert e.search('', ALL, collapse=True)['total'] == 1


def api(tmp_path, ai=None):
    from search_engine.api import create_app
    config = {'database': str(tmp_path / 'api.db'), 'auto_detect_milestone': False, 'ai': ai or {},
              'principals': [{'name': 'admin', 'token': 'a' * 32, 'roles': ['admin', 'reader']},
                             {'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['*']}]}
    return TestClient(create_app(config)), {'Authorization': 'Bearer ' + 'a' * 32}


def post_all(c, envelopes):
    for env in envelopes:
        assert c.post('/api/ingest', json=env, headers={'Authorization': 'Bearer ' + 'c' * 32}).status_code == 202
    c.app.state.engine.process_pending()


def test_ask_counts_occurrences_and_serves_the_snapshot(tmp_path):
    c, h = api(tmp_path)
    with c:
        post_all(c, [active_guard(1, 'Hoang Minh Khoa', 10, 'alarm'), active_guard(1, 'Hoang Minh Khoa', 9, 'event', image=True)])
        r = c.post('/api/ask', json={'text': 'khoa minh hoang'}, headers=h).json()
        assert r['total'] == 1 and r['understood'][0]['label'] == 'Hoang Minh Khoa'
        key = r['items'][0]['key']
        picture = c.get(f'/api/records/{key}/image', headers=h)
        assert picture.status_code == 200 and picture.content.startswith(b'\xff\xd8')
        assert c.post('/api/ask/image', files={'file': ('a.jpg', b'x')}, headers=h).status_code == 503


def test_rule_admin_and_model_proposals(tmp_path, monkeypatch):
    import search_engine.ai as ai
    c, h = api(tmp_path, {'chat_model': 'test', 'vision_model': 'test'})
    with c:
        post_all(c, [faceme(1, 'VIP', 'Khach VIP Nguyen Van An den quay 3', 'VIP', 0)])
        monkeypatch.setattr(ai, 'chat', lambda *a, **k: json.dumps({'template': 'Khach VIP {} den quay {}', 'roles': ['person', 'number']}))
        proposals = c.post('/api/rules/suggest', headers=h).json()['proposals']
        assert any(p['template'] == 'Khach VIP {} den quay {}' for p in proposals)
        rule = next(r for r in c.get('/api/rules', headers=h).json() if r['source'] == 'llm')
        assert rule['status'] == 'proposed' and {m['field'] for m in rule['members']} == {'name', 'description'}
        for member in rule['members']:
            assert c.post('/api/rules/status', headers=h, json={**member, 'status': 'active'}).status_code == 200
        c.app.state.engine.process_pending()
        assert c.post('/api/ask', json={'text': 'Nguyen Van An'}, headers=h).json()['understood'][0]['label'] == 'Nguyen Van An'
        monkeypatch.setattr(ai, 'describe_image', lambda content, config: 'khach vip')
        assert c.post('/api/ask/image', files={'file': ('a.jpg', b'x')}, headers=h).json()['described'] == 'khach vip'


def test_vietnamese_lowercase_accents_are_not_capitals():
    from search_engine.extract import learn_template, looks_like_person
    assert looks_like_person('Đỗ Đức Tâm') and not looks_like_person('đã đi muộn') and not looks_like_person('người lạ')
    assert learn_template(['Le Van Hai đã đi muộn 5 phút', 'Pham Minh Duc đã đi muộn 9 phút']) == '{} đã đi muộn {} phút'
    assert learn_template(['Le Van Hai đã về sớm 5 phút', 'Le Van Hai đã đi muộn 9 phút']) is None   # shared person, not a format
