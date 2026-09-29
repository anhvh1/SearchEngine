"""Active Guard connector against a stand-in server that follows the published WebAPI v1.1 (Digest auth, sessions, paging)."""
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from search_engine import activeguard
from search_engine.query import interpret
from search_engine.store import Engine

JPEG = base64.b64encode(b'\xff\xd8\xff\xe0' + b'x' * 300).decode()
NOW = datetime.now(timezone.utc)
ALL = [['*', '*']]


def stamp(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S:%f')[:23]


def scores(**picked):
    """One-element score lists like the API returns, with the picked label at 0.9."""
    groups = {'gender': ['male', 'female'], 'age': ['0-10', '11-20', '21-60', '61+'], 'hair_style': ['long-hair', 'short-hair', 'hat'],
              'hair_color': ['brown', 'black', 'gold', 'gray', 'white'], 'upper_garment': ['long-sleeves', 'short-sleeves'],
              'upper_color': ['black', 'white', 'red', 'blue'], 'lower_garment': ['long', 'short'], 'lower_color': ['black', 'white', 'blue'],
              'sunglasses': ['none', 'yes'], 'face_mask': ['none', 'yes'], 'beard': ['none', 'yes'], 'bag': ['none', 'yes'],
              'bag_color': ['black', 'red'], 'shoes_color': ['black', 'white']}
    out = {}
    for group, labels in groups.items():
        want = picked.get(group)
        out[group] = [{label: (0.9 if label == want else 0.05) for label in labels}] if want else [{label: 0.2 for label in labels}]
    return out


class Server:
    """Just enough of the Active Guard WebAPI for the connector."""
    def __init__(self):
        self.calls, self.sessions, self.shots = [], {}, {}
        self.cameras = [{'camera_id': 'cam-a', 'camera_ip': '10.0.0.62', 'camera_model': 'WV-X', 'camera_name': 'Cổng chính',
                         'ai_capability': ['face', 'people', 'vehicle'], 'is_enabled': 'yes'},
                        {'camera_id': 'cam-off', 'camera_ip': '10.0.0.9', 'camera_model': 'WV-Y', 'camera_name': 'Tắt',
                         'ai_capability': ['people'], 'is_enabled': 'no'}]

    def add(self, key, kind, camera, minutes_ago, info):
        self.shots[key] = {'kind': kind, 'camera': camera, 'when': stamp(minutes_ago), 'info': info}

    def __call__(self, request):
        path = request.url.path
        self.calls.append((request.method, path))
        if 'authorization' not in request.headers:
            return httpx.Response(401, headers={'WWW-Authenticate': 'Digest realm="ai", nonce="n0nce", qop="auth", opaque="op", algorithm=MD5'})
        ok = lambda body: httpx.Response(200, json={'status_body': {'status': True, 'details': [{'srv_id': '9999', 'code': '00000', 'message': 'OK'}]}, 'result_body': body})
        if path == '/ai/system/info':
            return ok({'multi_ai_soft_version': '2.00'})
        if path == '/ai/v1.0/cameras/info':
            return ok({'system_type_list': [{'system_type': 'multi-AI', 'srv_id_list': [{'srv_id': '9999', 'cameras': self.cameras}]}]})
        if path == '/ai/v1.0/thumbnail/search':
            body = json.loads(request.content)
            kind = next(k for k in ('face', 'people', 'vehicle', 'lpr') if f'section_{k}' in body)
            ids = [c['camera_id'] for c in body[f'section_{kind}']['cameras']]
            hits = sorted((k for k, s in self.shots.items() if s['kind'] == kind and s['camera'] in ids and body['date_from'] <= s['when'][:19]),
                          key=lambda k: self.shots[k]['when'], reverse=body['sort']['asc-desc'] == 'desc')
            session = f'session-{len(self.sessions)}'
            self.sessions[session] = [{'shot_date_time': self.shots[k]['when'], 'camera_id': self.shots[k]['camera'], 'system_type': 'multi-AI',
                                       'srv_id': '9999', 'source_type': kind, 'thumbnail_key': k,
                                       **({'degree_of_similarity': '88'} if kind == 'face' else {})} for k in hits]
            self.last_search = body
            return ok({'search_session_id': session})
        if path.startswith('/ai/v1.0/thumbnail/search/'):
            rows = self.sessions[path.rsplit('/', 1)[1]]
            first, count = int(request.url.params.get('result-from', 1)), int(request.url.params.get('result-count', len(rows)))
            if first < 1 or count < 1:   # the real server rejects these with C0005
                return httpx.Response(200, json={'status_body': {'status': False, 'details': [{'code': 'C0005', 'message': f'Validation Error [ResultFrom]=[{first}]'}]}})
            return ok({'search_session_id': 'x', 'result_count': len(rows), 'search_result': rows[first - 1:first - 1 + count]})
        if path == '/ai/v1.0/thumbnail':
            key = request.url.params['thumbnail-key']
            return ok({'thumbnail_image': JPEG, **self.shots[key]['info']})
        return httpx.Response(404)


def people_server():
    server = Server()
    server.add('p1', 'people', 'cam-a', 30, scores(gender='male', age='21-60', upper_color='red', lower_color='black', hair_style='hat', bag='yes', bag_color='black'))
    server.add('p2', 'people', 'cam-a', 20, scores(gender='female', age='21-60', upper_color='white', lower_color='blue', face_mask='yes'))
    server.add('p3', 'people', 'cam-a', 10, scores(gender='male', age='61+', upper_color='blue', lower_color='black', sunglasses='yes'))
    server.add('v1', 'vehicle', 'cam-a', 15, {'vehicle_type': [{'truck': 0.8, 'bus': 0.1}], 'vehicle_color': [{'white': 0.9, 'red': 0.05}], 'direction': 'middle-right'})
    server.add('f1', 'face', 'cam-a', 25, {'recommended_size': [{'recommended': 'yes'}]})
    server.add('off', 'people', 'cam-off', 5, scores(gender='male'))
    return server


def engine_and_client(tmp_path, server):
    engine = Engine(tmp_path / 'ag.db')
    settings = activeguard.load_settings(engine, {})
    ag = activeguard.ActiveGuard('http://ag.local:8090', 'operator', 'secret', transport=httpx.MockTransport(server))
    return engine, ag, settings


def test_import_turns_best_shots_into_searchable_people(tmp_path):
    server = people_server()
    engine, ag, settings = engine_and_client(tmp_path, server)
    done = activeguard.sync(engine, ag, {**settings, 'types': ['people', 'vehicle']})
    assert done == {'people': 3, 'vehicle': 1}                      # disabled camera skipped
    while engine.process_pending():
        pass
    catalog = engine.sources(ALL)
    assert any(s['name'] == 'Cổng chính' for s in catalog['sources'])
    red = engine.search('', ALL, facts=[['upper_color', 'red'], ['gender', 'male']])
    assert red['total'] == 1 and red['items'][0]['description'].startswith('Nam, người lớn, đội mũ')
    assert 'áo đỏ' in red['items'][0]['description'] and red['items'][0]['has_image']
    assert engine.search('', ALL, facts=[['face_mask', 'yes']])['total'] == 1
    assert engine.search('áo đỏ quần đen', ALL)['total'] == 1        # plain text also finds it
    assert engine.search('', ALL, facts=[['vehicle_type', 'truck'], ['vehicle_color', 'white']])['total'] == 1
    # every best shot stays its own occurrence: different people are never merged
    assert engine.search('', ALL, collapse=True, event_type='activeguard:people')['total'] == 3
    assert server.calls[0][1] == '/ai/system/info' or server.calls[0][1] == '/ai/v1.0/cameras/info'


def test_natural_language_reaches_the_attributes(tmp_path):
    server = people_server()
    engine, ag, settings = engine_and_client(tmp_path, server)
    activeguard.sync(engine, ag, {**settings, 'types': ['people', 'vehicle']})
    while engine.process_pending():
        pass
    meaning = interpret('nam áo đỏ quần đen đội mũ hôm nay', engine.sources(ALL))
    params = {k: v for k, v in meaning['filters'].items() if v is not None}
    found = engine.search(meaning['keywords'], ALL, collapse=True, **params)
    assert found['total'] == 1
    old = interpret('phụ nữ khẩu trang áo trắng', engine.sources(ALL))
    assert engine.search('', ALL, **{k: v for k, v in old['filters'].items() if v is not None})['total'] == 1


def test_sync_is_incremental_idempotent_and_bounded(tmp_path):
    server = people_server()
    engine, ag, settings = engine_and_client(tmp_path, server)
    first = activeguard.sync(engine, ag, {**settings, 'types': ['people'], 'max_per_cycle': 2})
    assert first == {'people': 2}
    assert engine.checkpoint('activeguard:cursor:people')          # resumes after the last imported shot
    second = activeguard.sync(engine, ag, {**settings, 'types': ['people'], 'max_per_cycle': 2})
    assert second == {'people': 1}
    assert activeguard.sync(engine, ag, {**settings, 'types': ['people']}) == {'people': 0}
    server.add('p4', 'people', 'cam-a', 1, scores(gender='female'))
    assert activeguard.sync(engine, ag, {**settings, 'types': ['people']}) == {'people': 1}
    while engine.process_pending():
        pass
    assert engine.search('', ALL, event_type='activeguard:people')['total'] == 4


def test_errors_are_reported_in_plain_language(tmp_path):
    engine = Engine(tmp_path / 'e.db')
    denied = activeguard.ActiveGuard('http://ag.local:8090', 'x', 'y', transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    try:
        denied.system_info()
        raise AssertionError('must fail')
    except activeguard.ActiveGuardError as error:
        assert 'từ chối' in str(error)
    empty = activeguard.ActiveGuard('http://ag.local:8090', 'x', 'y', transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={'status_body': {'status': False, 'details': [{'code': 'C0008', 'message': 'NotFound'}]}})))
    assert empty.results('s') == (0, [])


def api(tmp_path, server):
    from search_engine.api import create_app
    config = {'database': str(tmp_path / 'api.db'), 'auto_detect_milestone': False, 'ai': {},
              'principals': [{'name': 'admin', 'token': 'a' * 32, 'roles': ['admin', 'reader']}]}
    return TestClient(create_app(config, transport=httpx.MockTransport(server))), {'Authorization': 'Bearer ' + 'a' * 32}


def test_console_connects_syncs_and_searches_by_face_photo(tmp_path):
    server = people_server()
    c, h = api(tmp_path, server)
    with c:
        assert c.put('/api/settings/activeguard', headers=h, json={'url': 'ftp://x', 'username': 'u', 'password': 'p'}).status_code == 400
        saved = c.put('/api/settings/activeguard', headers=h, json={'url': 'http://ag.local:8090', 'username': 'operator', 'password': 'secret'}).json()
        assert saved['has_credentials'] and saved['server_version'] == '2.00'
        assert 'secret' not in json.dumps(c.app.state.engine.checkpoint('activeguard'))          # password is protected at rest
        assert c.post('/api/activeguard/sync', headers=h).json()['imported']['people'] == 3
        found = c.post('/api/ask', json={'text': 'người nam áo đỏ'}, headers=h).json()
        assert found['total'] == 1 and any(x['label'] == 'Áo đỏ' for x in found['understood'])
        assert c.get(f"/api/records/{found['items'][0]['key']}/image", headers=h).status_code == 200
        photo = c.post('/api/ask/image', files={'file': ('face.jpg', b'\xff\xd8face')}, headers=h).json()
        assert photo['total'] == 1 and photo['understood'][0]['label'] == 'Khuôn mặt giống ảnh'
        assert 'search_thumbnail_1' in server.last_search['section_face'] and server.last_search['sort']['asc-desc'] == 'desc'
        status = c.get('/api/settings/activeguard', headers=h).json()
        assert status['imported'] >= 3 and status['last_error'] is None
        assert c.get('/api/capabilities', headers=h).json()['activeguard']
