from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from search_engine.query import fold, interpret
from test_engine import event

VN = timezone(timedelta(hours=7))
NOW = datetime(2026, 9, 28, 21, 30, tzinfo=VN)
CATALOG = {
    'sources': [{'site_id': 'main', 'source_id': 'cam62', 'name': 'i-PRO/Panasonic WV-X15300-V3L (192.168.100.62) - Camera 1'},
                {'site_id': 'main', 'source_id': 'hw62', 'name': 'i-PRO/Panasonic WV-X15300-V3L (192.168.100.62)'},
                {'site_id': 'main', 'source_id': 'cam37', 'name': 'UNIVIEW IPC2122LR3-PF40-E (192.168.100.37) - Camera 1'},
                {'site_id': 'main', 'source_id': 'srv', 'name': 'WIN-CKAE6IGEE1V'}],
    'event_types': [{'id': 'motion', 'name': 'Motion Detected'}, {'id': 'stop', 'name': 'Motion Stopped'},
                    {'id': 'down', 'name': 'Not Responding'}, {'id': 'up', 'name': 'Responding'},
                    {'id': 'srvdown', 'name': 'Server Not Responding'}, {'id': 'face', 'name': 'Registered face detection'},
                    {'id': 'intr', 'name': 'IntruderHuman'}, {'id': 'disk', 'name': 'Database Deleting Recordings Before Set Retention Size'}]}


def ask(text):
    return interpret(text, CATALOG, NOW)


def test_fold_matches_typed_without_diacritics():
    assert fold('Mất Kết Nối Đêm qua') == 'mat ket noi dem qua'


def test_camera_ip_event_and_evening():
    r = ask('camera .62 mất kết nối tối qua')
    f = r['filters']
    assert f['source_ids'] == ['cam62', 'hw62']
    assert f['event_types'] == ['down', 'srvdown']
    assert f['start'] == datetime(2026, 9, 27, 18, tzinfo=VN) and f['end'] == datetime(2026, 9, 28, 6, tzinfo=VN)
    assert r['keywords'] == ''


def test_motion_is_not_motion_stopped_and_hours():
    r = ask('có chuyển động sau 22h hôm qua không')
    assert r['filters']['event_types'] == ['motion']
    assert r['filters']['start'] == datetime(2026, 9, 27, 22, tzinfo=VN)
    assert r['filters']['end'] == datetime(2026, 9, 28, tzinfo=VN)
    assert ask('hết chuyển động')['filters']['event_types'] == ['stop']


def test_reconnect_does_not_include_disconnect():
    assert ask('camera kết nối lại tuần này')['filters']['event_types'] == ['up']


def test_brand_name_relative_time_and_alarm():
    r = ask('cảnh báo uniview 3 ngày qua')
    assert r['filters']['kind'] == 'alarm' and r['filters']['source_ids'] == ['cam37']
    assert r['filters']['start'] == NOW - timedelta(days=3)


def test_date_and_unknown_words_become_keywords():
    r = ask('khuôn mặt ngày 17/9 áo đỏ Hùng')
    assert r['filters']['event_types'] == ['face']
    assert r['filters']['start'] == datetime(2026, 9, 17, tzinfo=VN)
    assert r['keywords'] == 'ao do hung'


def test_ask_endpoint_explains_and_recovers_from_unknown_words(tmp_path):
    from search_engine.api import create_app
    config = {'database': str(tmp_path / 'ask.db'), 'auto_detect_milestone': False,
              'principals': [{'name': 'admin', 'token': 'a' * 32, 'roles': ['admin', 'reader']},
                             {'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['*']}]}
    with TestClient(create_app(config)) as c:
        stamp = datetime.now(timezone.utc).isoformat()
        for i, (name, source) in enumerate([('Motion Detected', 'Cam A (10.0.0.62)'), ('Not Responding', 'Cam A (10.0.0.62)'),
                                            ('Motion Detected', 'Cam B (10.0.0.9)')]):
            data = {**event(), 'site_id': 'main', 'source_guid': f'e{i}', 'source_id': source[:5], 'event_type': name,
                    'occurred_at': stamp, 'updated_at': stamp, 'message': name,
                    'payload': {'header': {'Name': name, 'Source': {'Name': source}}}}
            c.post('/api/ingest', json=data, headers={'Authorization': 'Bearer ' + 'c' * 32})
        c.app.state.engine.process_pending()
        h = {'Authorization': 'Bearer ' + 'a' * 32}
        r = c.post('/api/ask', json={'text': 'chuyển động camera .62 hôm nay'}, headers=h).json()
        assert r['total'] == 1 and [x['label'] for x in r['understood']] == ['Hôm nay', 'Chuyển động', 'Camera .62']
        assert r['facets']['sources'][0]['name'] == 'Cam A (10.0.0.62)'
        r = c.post('/api/ask', json={'text': 'chuyển động xyzzy'}, headers=h).json()
        assert r['total'] == 2 and r['ignored_words'] == 'xyzzy'
        assert c.post('/api/ask', json={'text': ''}, headers=h).json()['total'] == 3
