import json
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

from search_engine.api import create_app
from test_engine import event

CAMERA, HARDWARE, OTHER = 'cam-1', 'hw-1', 'cam-2'


def milestone(request):
    path, auth = request.url.path, request.headers.get('authorization', '')
    if path == '/api/.well-known/uris':
        return httpx.Response(404)
    if path == '/API/IDP/connect/token':
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get('password') != 'right':
            return httpx.Response(400, json={'error': 'invalid_grant'})
        return httpx.Response(200, json={'access_token': 'tok-' + form['username'], 'expires_in': 3600})
    if not auth.startswith('Bearer tok-'):
        return httpx.Response(401)
    user = auth[len('Bearer tok-'):]
    if path == '/api/rest/v1/cameras':
        return httpx.Response(200, json={'array': [{'id': CAMERA, 'relations': {'parent': {'type': 'hardware', 'id': HARDWARE}}}]})
    if path == '/api/rest/v1/roles':
        return httpx.Response(200 if user == 'boss' else 403, json={'array': []})
    return httpx.Response(404)


def client(tmp_path):
    config = {'database': str(tmp_path / 'id.db'), 'milestone': {'lab': {'url': 'https://vms.example'}},
              'auto_detect_milestone': False, 'full_access': False,
              'principals': [{'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['*']}]}
    return TestClient(create_app(config, transport=httpx.MockTransport(milestone)))


def ingest(c, source, **extra):
    data = {**event(), 'site_id': 'lab', 'source_id': source, 'source_guid': source, **extra}
    assert c.post('/api/ingest', json=data, headers={'Authorization': 'Bearer ' + 'c' * 32}).status_code == 202


def sign_in(c, username, password='right'):
    return c.post('/api/session', json={'username': username, 'password': password})


def test_milestone_permissions_define_search_scope(tmp_path):
    with client(tmp_path) as c:
        for source in (CAMERA, HARDWARE, OTHER):
            ingest(c, source, payload={'header': {'Name': 'Motion Detected', 'Source': {'Name': 'Gate ' + source}}})
        c.app.state.engine.process_pending()
        assert sign_in(c, 'guard', 'wrong').status_code == 401
        operator = sign_in(c, 'guard').json()
        assert operator['roles'] == ['reader']
        h = {'Authorization': 'Bearer ' + operator['token']}
        assert c.post('/api/search', json={}, headers=h).json()['total'] == 2
        assert c.get('/api/catalog', headers=h).status_code == 403
        names = {s['name'] for s in c.get('/api/sources', headers=h).json()['sources']}
        assert names == {'Gate cam-1', 'Gate hw-1'}
        found = c.post('/api/search', json={'query': 'cam'}, headers=h).json()['items']
        assert {i['source_name'] for i in found} == {'Gate cam-1'} and found[0]['event_name'] == 'Motion Detected'
        admin = sign_in(c, 'boss').json()
        assert 'admin' in admin['roles']
        assert c.post('/api/search', json={}, headers={'Authorization': 'Bearer ' + admin['token']}).json()['total'] == 3
        c.delete('/api/session', headers=h)
        assert c.post('/api/search', json={}, headers=h).status_code == 401


def test_smart_client_token_signs_in_without_password(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/session', json={'token': 'forged'}).status_code == 401
        result = c.post('/api/session', json={'token': 'tok-guard'})
        assert result.status_code == 200 and result.json()['roles'] == ['reader']


def test_service_account_is_protected_and_used_for_reconciliation(tmp_path):
    with client(tmp_path) as c:
        admin = {'Authorization': 'Bearer ' + sign_in(c, 'boss').json()['token']}
        body = {'site_id': 'lab', 'url': 'https://vms.example', 'username': 'svc', 'password': 'wrong'}
        assert c.put('/api/settings/milestone', headers=admin, json=body).status_code == 400
        ok = c.put('/api/settings/milestone', headers=admin, json={**body, 'password': 'right'})
        assert ok.status_code == 200 and ok.json()['has_credentials'] and ok.json()['origin'] == 'console'
        stored = c.app.state.engine.checkpoint('milestone:lab')
        assert 'right' not in stored and json.loads(stored)['username'] == 'svc'
        assert c.app.state.milestone.service_token('lab') == 'tok-svc'


def test_full_access_default_opens_everything(tmp_path):
    config = {'database': str(tmp_path / 'open.db'), 'milestone': {'lab': {'url': 'https://vms.example'}}, 'auto_detect_milestone': False,
              'principals': [{'name': 'collector', 'token': 'c' * 32, 'roles': ['collector'], 'sites': ['*']},
                             {'name': 'old', 'token': 'o' * 32, 'roles': ['reader'], 'grants': [['demo', 'x']]}]}
    with TestClient(create_app(config, transport=httpx.MockTransport(milestone))) as c:
        for source in (CAMERA, OTHER):
            ingest(c, source)
        c.app.state.engine.process_pending()
        guard = sign_in(c, 'guard').json()
        assert 'admin' in guard['roles']
        for token in (guard['token'], 'o' * 32):
            h = {'Authorization': 'Bearer ' + token}
            assert c.post('/api/search', json={}, headers=h).json()['total'] == 2
            assert c.get('/api/catalog', headers=h).status_code == 200
        assert sign_in(c, 'guard', 'wrong').status_code == 401
        assert c.post('/api/search', json={}, headers={'Authorization': 'Bearer ' + 'c' * 32}).status_code == 403


def test_wildcard_grants():
    from search_engine.store import allowed, grant_clause
    assert grant_clause([['*', '*']]) == ('1=1', [])
    assert grant_clause([]) == ('1=0', [])
    clause, args = grant_clause([['a', '*'], ['a', 'x'], ['b', 'y'], ['b', 'z']])
    assert args == ['a', 'b', 'y', 'z'] and 'IN' in clause
    assert allowed({'site_id': 'b', 'source_id': 'y'}, [['b', 'y']])
    assert not allowed({'site_id': 'b', 'source_id': 'q'}, [['b', 'y']])
