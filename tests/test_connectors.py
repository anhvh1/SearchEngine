import importlib.util
import httpx
import pytest

from search_engine.store import Engine


def connector():
    assert importlib.util.find_spec('search_engine.connectors'), 'Milestone REST connector is missing'
    from search_engine.connectors import MilestoneRest
    return MilestoneRest


def test_backfill_commit_only_after_successful_page(tmp_path):
    cls = connector()
    calls = []
    def handler(req):
        calls.append(str(req.url))
        if req.url.params.get('page') == '1':
            return httpx.Response(503)
        return httpx.Response(200, json={'array': [{'id': 'a1', 'eventHeader': {
            'id': 'a1', 'timestamp': '2026-09-16T08:00:00Z', 'message': 'Stranger',
            'source': {'fqid': {'objectId': 'camera-1'}}, 'type': 'FaceMe'},
            'lastUpdatedTime': '2026-09-16T08:01:00Z'}], 'paging': {'next': '/api/rest/v1/alarms?page=1'}})
    e = Engine(tmp_path / 'c.db')
    c = cls('https://vms.example', 'token', 'lab', e, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        c.backfill()
    assert e.checkpoint('lab:alarms:cursor') == '/api/rest/v1/alarms?page=1'
    e.process_pending()
    assert e.search('', [('lab', 'camera-1')])['total'] == 1


def test_connector_refuses_cross_origin_pagination(tmp_path):
    cls = connector()
    e = Engine(tmp_path / 'c.db')
    c = cls('https://vms.example', 'token', 'lab', e,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'array': [], 'paging': {'next': 'https://attacker.example/'}})))
    with pytest.raises(ValueError, match='origin'):
        c.backfill()


def test_official_rest_schema_and_links(tmp_path):
    cls = connector()
    e = Engine(tmp_path / 'official.db')
    alarm = {'id': 'alarm-guid', 'localId': 4, 'source': 'cameras/camera-1',
             'time': '2026-09-16T08:00:00Z', 'lastUpdatedTime': '2026-09-16T08:00:00.123Z',
             'name': 'Unknown person', 'message': 'FACEME.UNKNOWN_PERSON', 'legacyType': 'Analytics',
             'priority': {'level': 1, 'name': 'High'}, 'state': {'level': 1, 'name': 'New'},
             'cameraId': 'camera-1', 'data': {'description': 'Unknown at gate', 'location': 'Gate', 'objects': []}}
    def handler(req):
        if req.url.params.get('page') == '2':
            return httpx.Response(200, json={'array': [], '_links': {}})
        return httpx.Response(200, json={'array': [alarm], '_links': {'next': '/api/rest/v1/alarms?page=2'}})
    c = cls('https://vms.example', 'token', 'lab', e, transport=httpx.MockTransport(handler))
    assert c.backfill() == 1
    e.process_pending()
    item = e.search('', [('lab', 'camera-1')])['items'][0]
    assert item['state'] == 'New'
    assert item['location'] == 'Gate'


def test_password_auth_uses_idp_and_never_returns_credentials():
    cls = connector()
    seen = {}
    def handler(req):
        seen['body'] = req.content.decode()
        return httpx.Response(200, json={'access_token': 'short-lived-token', 'expires_in': 300})
    token = cls.password_token('https://vms.example', 'admin', 'secret-value', transport=httpx.MockTransport(handler))
    assert token == 'short-lived-token'
    assert 'grant_type=password' in seen['body']
    assert 'client_id=GrantValidatorClient' in seen['body']
    assert 'secret-value' in seen['body']


def test_password_auth_requires_https_except_loopback():
    cls = connector()
    with pytest.raises(ValueError, match='HTTPS'):
        cls.password_token('http://vms.example', 'admin', 'secret')


def test_rest_connector_passes_explicit_certificate_policy(tmp_path, monkeypatch):
    cls = connector()
    observed = []
    real = httpx.Client
    class RecordingClient:
        def __init__(self, **kwargs):
            observed.append(kwargs)
            self.inner = real(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[])))
        def __getattr__(self, name): return getattr(self.inner, name)
    monkeypatch.setattr(httpx, 'Client', RecordingClient)
    c = cls('https://vms.example', 'token', 'lab', Engine(tmp_path/'db'), verify=False)
    c.close()
    assert observed[0]['verify'] is False
