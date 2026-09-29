import json
import uuid
from pathlib import Path
import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_engine import event, profile


@pytest.fixture
def pg_dsn():
    path = Path('data/postgres-test.json')
    if not path.exists():
        pytest.skip('Local PostgreSQL test configuration not provided')
    dsn = json.loads(path.read_text())['dsn']
    schema = 'test_' + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    yield make_conninfo(dsn, options=f'-c search_path={schema}')
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_postgres_full_lifecycle(pg_dsn):
    from search_engine.store import Engine
    e = Engine(pg_dsn)
    try:
        assert getattr(e, 'dialect', None) == 'postgresql', 'Must use PostgreSQL, not a file named after the DSN'
        e.save_profile(profile())
        e.validate_profile('face', 1, [event()])
        e.activate_profile('face', 1)
        e.ingest(event()); e.ingest(event())
        e.process_pending()
        assert e.search('người lạ', [('lab', 'camera-1')])['total'] == 1
        assert e.search('', [])['total'] == 0
        assert e.search('', [('lab', 'camera-2')])['total'] == 0
        assert e.status()['done'] == 1
        assert e.replay('lab') == 1
        e.process_pending()
        assert e.search('', [('lab', 'camera-1')])['items'][0]['attributes'] == {'zone': 'Cổng'}
        assert e.purge('2027-01-01T00:00:00Z') == 1
        assert e.search('', [('lab', 'camera-1')])['total'] == 0
    finally:
        e.close()


def test_native_vector_search_filters_before_ranking(pg_dsn):
    from search_engine.store import Engine
    from search_engine.contracts import SearchRequest
    e = Engine(pg_dsn)
    try:
        first = e.ingest(event('allowed'))
        second = e.ingest({**event('hidden'), 'source_id': 'camera-2'})
        e.process_pending()
        assert hasattr(e, 'save_embedding'), 'Native PostgreSQL vector store is missing'
        e.save_embedding(first, 'test', 'hash', [0.8, 0.2, 0.0])
        e.save_embedding(second, 'test', 'hash', [1.0, 0.0, 0.0])
        result = e.vector_search([1.0, 0.0, 0.0], 'test', [('lab', 'camera-1')], SearchRequest(query='door'))
        assert result['total'] == 1
        assert result['items'][0]['source_guid'] == 'allowed'
    finally:
        e.close()


def test_update_invalidates_vector_and_worker_rebuilds(pg_dsn, monkeypatch):
    from search_engine.store import Engine
    from search_engine import ai
    from search_engine.contracts import SearchRequest
    e = Engine(pg_dsn)
    try:
        e.ingest(event()); e.process_pending()
        assert hasattr(ai, 'index_pending'), 'Background embedding indexer is missing'
        monkeypatch.setattr(ai, 'embed', lambda config, texts: [[1.0, 0.0] for _ in texts])
        assert ai.index_pending(e, {'embedding_model': 'test'}) == 1
        assert ai.index_pending(e, {'embedding_model': 'test'}) == 0
        e.ingest({**event(), 'updated_at': '2026-09-17T00:00:00Z', 'message': 'changed'})
        e.process_pending()
        assert e.vector_search([1.0, 0.0], 'test', [('lab', 'camera-1')], SearchRequest())['unindexed'] == 1
        assert ai.index_pending(e, {'embedding_model': 'test'}) == 1
    finally:
        e.close()


def test_state_only_update_preserves_embedding(pg_dsn):
    from search_engine.store import Engine
    from search_engine.contracts import SearchRequest
    e = Engine(pg_dsn)
    try:
        key = e.ingest(event()); e.process_pending()
        e.save_embedding(key, 'test', 'hash', [1.0, 0.0])
        e.ingest({**event(), 'state':'Closed', 'updated_at':'2026-09-17T00:00:00Z'})
        e.process_pending()
        result = e.vector_search([1.0,0.0], 'test', [('lab','camera-1')], SearchRequest(state='Closed'))
        assert result['total'] == 1
        assert result['items'][0]['state'] == 'Closed'
    finally:
        e.close()


def test_postgres_milestone_identity_and_names(pg_dsn):
    import httpx
    from fastapi.testclient import TestClient
    from search_engine.api import create_app
    from test_identity import milestone, CAMERA
    config = {'database': pg_dsn, 'milestone': {'lab': {'url': 'https://vms.example'}}, 'auto_detect_milestone': False, 'full_access': False,
              'principals': [{'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['*']}]}
    with TestClient(create_app(config, transport=httpx.MockTransport(milestone))) as c:
        data = {**event(), 'site_id': 'lab', 'source_id': CAMERA, 'payload': {'header': {'Name': 'Motion Detected', 'Source': {'Name': 'Camera 7'}}}}
        c.post('/api/ingest', json=data, headers={'Authorization': 'Bearer ' + 'c' * 32})
        c.app.state.engine.process_pending()
        assert c.app.state.milestone.site_ids() == ['lab']
        session = c.post('/api/session', json={'username': 'guard', 'password': 'right'}).json()['token']
        h = {'Authorization': 'Bearer ' + session}
        assert c.post('/api/search', json={'query': 'Camera 7', 'event_type': data['event_type']}, headers=h).json()['total'] == 1
        assert c.get('/api/sources', headers=h).json()['sources'][0]['name'] == 'Camera 7'
        admin = {'Authorization': 'Bearer ' + c.post('/api/session', json={'username': 'boss', 'password': 'right'}).json()['token']}
        assert c.get('/api/catalog', headers=admin).json()[0]['source_name'] == 'Camera 7'


def test_postgres_extraction_templates_and_occurrences(pg_dsn):
    from search_engine.enrich import learn_rules
    from search_engine.store import Engine
    from test_extract import active_guard, faceme
    e = Engine(pg_dsn)
    try:
        for env in [active_guard(1, 'Trần Thị Bích Ngọc', 0), active_guard(1, 'Trần Thị Bích Ngọc', -1, 'event', image=True),
                    faceme(1, 'Checkin', 'Phát hiện Ngoc Tran Thi Bich tại Camera 83', 'Ngoc Tran Thi Bich đã đi muộn 12 phút', 900),
                    faceme(2, 'Checkin', 'Phát hiện Le Van Hai tại Camera 83', 'Le Van Hai đã đi muộn 3 phút', 1000)]:
            e.ingest(env)
        while e.process_pending():
            pass
        learn_rules(e)
        while e.process_pending():
            pass
        assert e.search('ngoc bich tran', [['*', '*']])['total'] == 3
        assert e.search('', [['*', '*']], entities=['bich ngoc thi tran'], collapse=True)['total'] == 2
        late = e.search('', [['*', '*']], facts=[['action', 'di muon']], facets=True)
        assert late['total'] == 2 and {p['name'] for p in late['facets']['people']} == {'Trần Thị Bích Ngọc', 'Le Van Hai'}
    finally:
        e.close()
