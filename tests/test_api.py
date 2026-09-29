import importlib.util

from fastapi.testclient import TestClient
from test_engine import event, profile


def client(tmp_path):
    assert importlib.util.find_spec('search_engine.api'), 'Authenticated API is missing'
    from search_engine.api import create_app
    config = {'database': str(tmp_path / 'api.db'), 'full_access': False, 'principals': [
        {'name': 'admin', 'token': 'a' * 32, 'roles': ['admin', 'reader'], 'grants': [['lab', 'camera-1']]},
        {'name': 'reader', 'token': 'r' * 32, 'roles': ['reader'], 'grants': [['lab', 'camera-2']]},
        {'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['lab']}
    ]}
    return TestClient(create_app(config))


def auth(char):
    return {'Authorization': 'Bearer ' + char * 32}


def test_auth_scope_and_restricted_ingest(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/search', json={}).status_code == 401
        assert c.post('/api/ingest', json=event(), headers=auth('r')).status_code == 403
        assert c.post('/api/ingest', json={**event(), 'site_id': 'other'}, headers=auth('c')).status_code == 403
        assert c.post('/api/ingest', json=event(), headers=auth('c')).status_code == 202
        c.post('/api/operations/process', headers=auth('a'))
        assert c.post('/api/search', json={}, headers=auth('r')).json()['total'] == 0
        result = c.post('/api/search', json={}, headers=auth('a')).json()
        assert result['total'] == 1
        key = result['items'][0]['key']
        assert c.get('/api/records/' + key, headers=auth('r')).status_code == 404
        assert c.post('/api/search', json={'grants': [['lab', 'camera-1']]}, headers=auth('r')).status_code == 422
        assert c.get('/api/catalog', headers=auth('r')).status_code == 403


def test_profile_api_and_grounded_answer(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/profiles', json=profile(), headers=auth('a')).status_code == 201
        assert c.post('/api/profiles/face/1/activate', headers=auth('a')).status_code == 400
        assert c.post('/api/profiles/face/1/validate', json=[event()], headers=auth('a')).json()['valid']
        assert c.post('/api/profiles/face/1/activate', headers=auth('a')).status_code == 200
        c.post('/api/ingest', json=event(), headers=auth('c'))
        c.post('/api/operations/process', headers=auth('a'))
        result = c.post('/api/answer', json={'query': 'người lạ'}, headers=auth('a')).json()
        assert len(result['citations']) == 1
        assert result['citations'][0]['source_guid'] == 'one'
        assert c.post('/api/answer', json={'query': 'người lạ'}, headers=auth('r')).json()['citations'] == []


def test_collector_reconciliation_is_durable_and_admin_samples_are_scoped(tmp_path):
    with client(tmp_path) as c:
        body = {'site_id': 'lab', 'reason': 'alarm_changed', 'alarm_id': 'a1'}
        assert c.post('/api/collector/reconcile', json=body, headers=auth('c')).status_code == 202
        assert c.post('/api/collector/reconcile', json={**body, 'site_id': 'other'}, headers=auth('c')).status_code == 403
        assert c.get('/api/operations', headers=auth('a')).json()['reconciliation_pending'] == 1
        c.post('/api/ingest', json=event(), headers=auth('c'))
        assert len(c.get('/api/samples?site_id=lab', headers=auth('a')).json()) == 1
        assert c.get('/api/samples?site_id=lab', headers=auth('r')).status_code == 403


def test_discovered_configuration_is_versioned_and_admin_only(tmp_path):
    with client(tmp_path) as c:
        engine = c.app.state.engine
        version = engine.context_snapshot('lab', {'event_types':[{'id':'e1','name':'Intrusion'}], 'alarm_messages':['Alarm']})
        result = c.get('/api/contexts/lab/latest', headers=auth('a'))
        assert result.status_code == 200
        assert result.json()['version'] == version
        assert result.json()['data']['event_types'][0]['name'] == 'Intrusion'
        assert c.get('/api/contexts/lab/latest', headers=auth('r')).status_code == 403
        assert c.get('/api/contexts/missing/latest', headers=auth('a')).status_code == 404
