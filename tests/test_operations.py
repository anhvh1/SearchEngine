import importlib.util
from search_engine.store import Engine
from test_engine import event


def test_retention_only_runs_when_explicitly_enabled(tmp_path):
    assert importlib.util.find_spec('search_engine.operations'), 'Scheduler is missing'
    from search_engine.operations import schedule
    e = Engine(tmp_path / 'db.sqlite')
    e.ingest(event()); e.process_pending()
    schedule(e, {}, 2000000000)
    assert e.status()['records'] == 1
    schedule(e, {'retention': {'enabled': True, 'days': 30}}, 2000000000)
    assert e.status()['records'] == 0
    e.close()


def stamped(id, when):
    data = event(id)
    data.update(occurred_at=when, updated_at=when)
    return data


def test_purge_removes_only_expired_records_in_batches_and_keeps_tombstones(tmp_path):
    e = Engine(tmp_path / 'db.sqlite')
    for i in range(5):
        e.ingest(stamped(f'old{i}', f'2026-01-0{i + 1}T08:00:00Z'))
    for i in range(2):
        e.ingest(stamped(f'new{i}', f'2026-09-1{i + 1}T08:00:00Z'))
    while e.process_pending():
        pass
    assert e.purge('2026-06-01T00:00:00Z', batch=2) == 5          # several small batches, not one long transaction
    assert e.status()['records'] == 2 and e.status()['deleted'] == 5
    e.ingest(stamped('old0', '2026-01-01T08:00:00Z'))              # a replayed notification does not bring it back
    e.process_pending()
    assert e.status()['records'] == 2
    assert e.purge('2026-06-01T00:00:00Z') == 0                     # already purged records are not visited again
    e.close()


def test_retention_days_and_wipe_from_the_administration_page(tmp_path):
    from fastapi.testclient import TestClient
    from search_engine.api import create_app
    admin = 'a' * 32
    app = create_app({'database': str(tmp_path / 'api.db'), 'auto_detect_milestone': False, 'ai': {},
                      'principals': [{'name': 'admin', 'token': admin, 'roles': ['admin', 'reader'], 'grants': [['*', '*']]}]})
    h = {'Authorization': 'Bearer ' + admin}
    with TestClient(app) as c:
        engine = c.app.state.engine
        engine.ingest(stamped('old', '2020-01-01T08:00:00Z')); engine.ingest(stamped('recent', '2099-01-01T08:00:00Z'))
        engine.checkpoint('activeguard:servers', '[{"id": "ag"}]'); engine.checkpoint('activeguard:ag:imported', '42')
        while engine.process_pending():
            pass
        status = c.get('/api/settings/retention', headers=h).json()
        assert status['days'] == 0 and status['source'] == 'default' and status['records'] == 2 and status['oldest'].startswith('2020')
        assert c.put('/api/settings/retention', json={'days': 30}, headers=h).json()['days'] == 30
        assert c.put('/api/settings/retention', json={'days': -1}, headers=h).status_code == 422
        from search_engine.operations import schedule
        import time
        schedule(engine, {}, time.time())                                # the saved setting applies on the next pass
        assert engine.status()['records'] == 1
        assert c.post('/api/operations/wipe', json={'confirm': 'xoa'}, headers=h).status_code == 400
        deleted = c.post('/api/operations/wipe', json={'confirm': 'xóa'}, headers=h).json()['deleted']
        assert deleted['records'] == 1
        assert engine.status()['records'] == 0 and engine.status()['pending'] == 0
        assert engine.checkpoint('activeguard:servers') == '[{"id": "ag"}]'     # connections are settings, not data
        assert engine.checkpoint('activeguard:ag:imported') == '0'
        assert c.get('/api/settings/retention', headers=h).json()['days'] == 30
        engine.ingest(stamped('after', '2099-02-01T08:00:00Z')); engine.process_pending()
        assert engine.status()['records'] == 1                           # new data keeps arriving after a wipe
