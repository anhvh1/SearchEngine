import importlib.util
from datetime import datetime, timezone

import pytest


def engine(tmp_path):
    assert importlib.util.find_spec('search_engine.store'), 'Durable engine must be implemented'
    from search_engine.store import Engine
    return Engine(tmp_path / 'test.db')


def event(id='one', **changes):
    return dict(site_id='lab', kind='alarm', source_id='camera-1', source_guid=id,
                event_type='FACEME.UNKNOWN_PERSON', occurred_at='2026-09-16T08:00:00Z',
                updated_at='2026-09-16T08:00:00Z', message='Người lạ tại cổng',
                payload={'zone': 'Cổng', 'count': 2}, **changes)


def profile(id='face', priority=1):
    return dict(id=id, name='FaceMe', version=1, priority=priority,
                match={'site_id': 'lab', 'event_type': 'FACEME.UNKNOWN_PERSON'},
                mapping={'zone': {'path': 'payload.zone', 'type': 'string', 'required': True}},
                family='intrusion', context='Phát hiện người chưa đăng ký')


def test_durable_dedup_and_acl(tmp_path):
    e = engine(tmp_path)
    assert e.ingest(event()) == e.ingest(event())
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['total'] == 1
    assert e.search('', [('lab', 'camera-2')])['total'] == 0
    e.close()
    e = engine(tmp_path)
    assert e.search('người lạ', [('lab', 'camera-1')])['total'] == 1


def test_identity_kind_and_out_of_order(tmp_path):
    e = engine(tmp_path)
    first = event()
    e.ingest(first)
    newer = {**first, 'updated_at': '2026-09-16T09:00:00Z', 'message': 'Đã xử lý'}
    e.ingest(newer)
    e.ingest({**first, 'kind': 'event'})
    e.process_pending()
    e.ingest(first)
    e.process_pending()
    result = e.search('', [('lab', 'camera-1')])
    assert result['total'] == 2
    assert next(x for x in result['items'] if x['kind'] == 'alarm')['message'] == 'Đã xử lý'


def test_profile_lifecycle_replay_and_conflict(tmp_path):
    e = engine(tmp_path)
    p = profile()
    e.save_profile(p)
    with pytest.raises(ValueError, match='validated'):
        e.activate_profile('face', 1)
    assert e.validate_profile('face', 1, [event()])['valid']
    e.activate_profile('face', 1)
    e.ingest(event())
    e.process_pending()
    item = e.search('', [('lab', 'camera-1')])['items'][0]
    assert item['attributes']['zone'] == 'Cổng'
    assert item['family'] == 'intrusion'
    assert e.replay('lab') == 1
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['total'] == 1
    e.save_profile(profile('other'))
    e.validate_profile('other', 1, [event()])
    e.activate_profile('other', 1)
    e.replay('lab')
    e.process_pending()
    assert e.status()['failed'] == 1
    assert e.search('', [('lab', 'camera-1')])['items'][0]['analysis_status'] == 'conflict'


def test_invalid_sample_cannot_activate_and_version_is_immutable(tmp_path):
    e = engine(tmp_path)
    e.save_profile(profile())
    bad = {**event(), 'payload': {}}
    assert not e.validate_profile('face', 1, [bad])['valid']
    with pytest.raises(ValueError):
        e.activate_profile('face', 1)
    with pytest.raises(ValueError):
        e.save_profile({**profile(), 'family': 'fire'})


def test_delete_cannot_be_resurrected_by_old_delivery(tmp_path):
    e = engine(tmp_path)
    e.ingest(event())
    e.process_pending()
    e.ingest({**event(), 'deleted': True, 'updated_at': '2026-09-16T10:00:00Z'})
    e.process_pending()
    e.ingest(event())
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['total'] == 0


def test_naive_timestamp_rejected(tmp_path):
    e = engine(tmp_path)
    with pytest.raises(ValueError):
        e.ingest({**event(), 'occurred_at': '2026-09-16T08:00:00'})


def test_fractional_update_is_newer_than_whole_second(tmp_path):
    e = engine(tmp_path)
    e.ingest(event())
    e.ingest({**event(), 'updated_at': '2026-09-16T08:00:00.100Z', 'message': 'updated'})
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['items'][0]['message'] == 'updated'


def test_profile_can_match_message_across_connector_formats(tmp_path):
    e = engine(tmp_path)
    p = {**profile(), 'match': {'site_id': 'lab', 'message': 'Người lạ tại cổng'}}
    e.save_profile(p)
    assert e.validate_profile('face', 1, [event()])['valid']
    e.activate_profile('face', 1)
    e.ingest({**event(), 'event_type': 'opaque-event-guid'})
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['items'][0]['family'] == 'intrusion'


def test_unknown_future_schema_is_rejected(tmp_path):
    e = engine(tmp_path)
    e.db.execute("UPDATE schema_meta SET value='999' WHERE key='version'")
    e.db.commit()
    e.close()
    from search_engine.store import Engine
    with pytest.raises(RuntimeError, match='schema version'):
        Engine(tmp_path / 'test.db')
