"""The API pages (/docs, /redoc) work on a LAN without Internet and under the backend's Content-Security-Policy."""
import re

from fastapi.testclient import TestClient

from search_engine.api import create_app


def client(tmp_path):
    return TestClient(create_app({'database': str(tmp_path / 'x.db'), 'auto_detect_milestone': False, 'ai': {},
                                  'principals': [{'name': 'a', 'token': 'a' * 32, 'roles': ['admin', 'reader'], 'grants': [['*', '*']]}]}))


def test_api_pages_load_only_bundled_assets(tmp_path):
    with client(tmp_path) as c:
        for page in ('/docs', '/redoc'):
            response = c.get(page)
            html = response.text
            assert response.status_code == 200
            assert not re.findall(r'https?://', html), f'{page} must not depend on a CDN'
            for url in re.findall(r'(?:src|href)="(/[^"]+)"', html):
                assert c.get(url).status_code == 200, url
            assert "script-src 'self';" in response.headers['content-security-policy']
        # Scripts stay external files: the console's script-src 'self' would block an inline start-up script.
        assert '<script>' not in c.get('/docs').text
        assert c.get('/openapi.json').json()['info']['title'] == 'Search Engine API'


def test_relaxed_styles_apply_to_the_api_pages_only(tmp_path):
    with client(tmp_path) as c:
        assert "'unsafe-inline'" in c.get('/docs').headers['content-security-policy']
        assert "'unsafe-inline'" not in c.get('/').headers['content-security-policy']
