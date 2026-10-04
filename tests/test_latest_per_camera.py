"""One result per camera: the most recent match on each camera, newest first ("where was this person last seen")."""
from datetime import datetime, timedelta, timezone

from search_engine.query import interpret
from search_engine.store import Engine
from test_engine import event

ALL = [['*', '*']]


def seen(id, camera, when, message='Người lạ tại cổng', event_type='FACEME.UNKNOWN_PERSON'):
    data = event(id)
    data.update(source_id=camera, occurred_at=when, updated_at=when, message=message, event_type=event_type)
    return data


def engine_with(tmp_path, envelopes):
    e = Engine(tmp_path / 'db.sqlite')
    for data in envelopes:
        e.ingest(data)
    while e.process_pending():
        pass
    return e


def test_keeps_only_the_newest_match_of_each_camera_newest_first(tmp_path):
    e = engine_with(tmp_path, [
        seen('a1', 'cam-1', '2026-10-04T09:51:00Z'), seen('a2', 'cam-1', '2026-10-04T09:58:00Z'), seen('a3', 'cam-1', '2026-10-04T09:55:00Z'),
        seen('b1', 'cam-2', '2026-10-04T09:57:00Z'),
        seen('c1', 'cam-3', '2026-10-04T09:52:00Z'), seen('c2', 'cam-3', '2026-10-04T09:53:00Z'),
        seen('d1', 'cam-4', '2026-10-04T09:59:00Z', message='Cửa mở', event_type='Door Open'),           # newest overall, but not a match
        seen('e1', 'cam-5', '2026-10-04T09:30:00Z'),                           # a match, but outside the time window
    ])
    start = datetime(2026, 10, 4, 9, 50, tzinfo=timezone.utc)
    found = e.search('nguoi la', ALL, latest_per_source=True, start=start, end=start + timedelta(minutes=10))
    assert [(i['source_id'], i['source_guid']) for i in found['items']] == [('cam-1', 'a2'), ('cam-2', 'b1'), ('cam-3', 'c2')]
    assert found['total'] == 3
    everything = e.search('nguoi la', ALL, start=start, end=start + timedelta(minutes=10))
    assert everything['total'] == 6                                              # the normal search still lists every match
    e.close()


def test_the_question_itself_can_ask_for_one_result_per_camera():
    now = datetime(2026, 10, 4, 10, 0, tzinfo=timezone(timedelta(hours=7)))
    catalog = {'sources': [], 'event_types': [], 'entities': [], 'sites': []}
    m = interpret('nam giới áo trắng, đội mũ trong vòng 10 phút qua mỗi camera', catalog, now)
    assert m['filters']['latest_per_source'] is True
    assert sorted(map(tuple, m['filters']['facts'])) == [('gender', 'male'), ('hair_style', 'hat'), ('upper_color', 'white')]
    assert m['filters']['start'] == now - timedelta(minutes=10)
    assert m['keywords'] == ''                                                   # "giới" and "trong vòng" are not search words
    assert interpret('nam áo trắng', catalog, now)['filters']['latest_per_source'] is None


def test_ask_api_option(tmp_path):
    from fastapi.testclient import TestClient
    from search_engine.api import create_app
    token = 'r' * 32
    app = create_app({'database': str(tmp_path / 'api.db'), 'auto_detect_milestone': False, 'ai': {},
                      'principals': [{'name': 'reader', 'token': token, 'roles': ['reader'], 'grants': ALL}]})
    with TestClient(app) as c:
        engine = c.app.state.engine
        for data in (seen('a1', 'cam-1', '2026-10-04T09:51:00Z'), seen('a2', 'cam-1', '2026-10-04T09:58:00Z'), seen('b1', 'cam-2', '2026-10-04T09:57:00Z')):
            engine.ingest(data)
        while engine.process_pending():
            pass
        h = {'Authorization': 'Bearer ' + token}
        latest = c.post('/api/ask', json={'text': 'người lạ', 'latest_per_source': True}, headers=h).json()
        assert latest['latest_per_source'] and [i['source_guid'] for i in latest['items']] == ['a2', 'b1']
        assert any(chip['type'] == 'mode' for chip in latest['understood'])
        assert c.post('/api/ask', json={'text': 'người lạ'}, headers=h).json()['latest_per_source'] is False
