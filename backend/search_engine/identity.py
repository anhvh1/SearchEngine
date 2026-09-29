"""Milestone identity: people sign in with their Milestone account and search what Milestone lets them see.

The background connection (discovery, alarm reconciliation) uses one service account per site that an
administrator enters once in the console; its password is protected with Windows DPAPI.
"""
import base64
import ctypes
import json
import secrets
import sys
import threading
import time
from urllib.parse import urlparse

import httpx

LOCAL_MILESTONE = 'http://localhost'


class LoginError(Exception):
    pass


class NotConfigured(LoginError):
    """No REST account: optional work is skipped, not reported as a failure."""


def protect(text):
    return base64.b64encode(_dpapi(text.encode('utf8'), encrypt=True)).decode('ascii')


def unprotect(value):
    return _dpapi(base64.b64decode(value), encrypt=False).decode('utf8')


def _dpapi(data, encrypt):
    if sys.platform != 'win32':
        raise RuntimeError('Stored Milestone credentials require Windows DPAPI')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data, len(data))
    source, target = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    flags = 0x1 | 0x4  # UI_FORBIDDEN | LOCAL_MACHINE: the service and an administrator console can both decrypt.
    call = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(target)):
        raise RuntimeError('DPAPI operation failed')
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def jwt_claims(token):
    """Read claims of a token the Milestone API has already accepted; never used to validate it."""
    try:
        part = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except Exception:
        return {}


class Milestone:
    def __init__(self, engine, config, transport=None):
        self.engine, self.config, self.transport = engine, config, transport
        self.sessions, self.tokens, self.lock = {}, {}, threading.Lock()
        self.hours = float(config.get('session_hours', 8))
        self.admin_users = {u.lower() for u in config.get('admin_users', [])}
        self.local, self.local_checked = None, 0.0

    # --- connection settings -------------------------------------------------------------
    def detect_local(self):
        """A Management Server on this machine answers on loopback; no certificate or URL setup is needed."""
        if self.config.get('auto_detect_milestone', True) is False:
            return None
        if time.time() - self.local_checked > 300:
            self.local_checked = time.time()
            try:
                with httpx.Client(transport=self.transport, timeout=3) as client:
                    response = client.get(LOCAL_MILESTONE + '/api/.well-known/uris')
                    self.local = LOCAL_MILESTONE if response.status_code == 200 and 'ProductVersion' in response.text else None
            except httpx.HTTPError:
                self.local = None
        return self.local

    def stored(self, site):
        value = self.engine.checkpoint('milestone:' + site)
        return json.loads(value) if value else {}

    def site_ids(self):
        with self.engine.lock:
            seen = {r[0] for r in self.engine.db.execute('SELECT DISTINCT site_id FROM catalog')}
            saved = {r[0][len('milestone:'):] for r in self.engine.db.execute("SELECT name FROM checkpoints WHERE name LIKE ?", ('milestone:%',))}
        return sorted(seen | saved | set(self.config.get('milestone', {})))

    def settings(self, site):
        configured, saved = self.config.get('milestone', {}).get(site, {}), self.stored(site)
        merged, origin = {}, None
        local = self.detect_local()
        if local:
            merged, origin = {'url': local, 'verify_certificates': False}, 'auto'
        if configured.get('url'):
            merged, origin = {**merged, **configured}, 'config'
        if saved.get('url'):
            merged, origin = {**merged, **saved}, 'console'
        merged['origin'] = origin
        return merged

    def credentials(self, settings):
        import os
        if settings.get('username') and settings.get('password_protected'):
            return settings['username'], unprotect(settings['password_protected'])
        username, password = os.environ.get(settings.get('username_env', '')), os.environ.get(settings.get('password_env', ''))
        return (username, password) if username and password else (None, None)

    def save(self, site, url, username, password, verify, actor):
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('Địa chỉ Milestone phải bắt đầu bằng http:// hoặc https://')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Máy chủ Milestone ở máy khác phải dùng https://')
        previous = self.stored(site)
        value = {'url': url.rstrip('/'), 'verify_certificates': bool(verify), 'username': username or previous.get('username', '')}
        if password:
            value['password_protected'] = protect(password)
        elif previous.get('password_protected') and value['username'] == previous.get('username'):
            value['password_protected'] = previous['password_protected']
        self.test(value)
        self.engine.checkpoint('milestone:' + site, json.dumps(value))
        with self.engine.lock, self.engine.db:
            self.engine.audit(actor, 'milestone.connection', {'site_id': site, 'url': value['url'], 'username': value['username']})
        with self.lock:
            self.tokens.pop(site, None)
        return self.describe(site)

    def test(self, settings):
        username, password = self.credentials(settings)
        if not username:
            raise ValueError('Nhập tên đăng nhập và mật khẩu Milestone')
        try:
            self.password_token(settings, username, password)
        except LoginError as exc:
            raise ValueError(str(exc))

    def describe(self, site):
        s = self.settings(site)
        username, _ = self.credentials(s) if s.get('url') else (None, None)
        return {'site_id': site, 'url': s.get('url'), 'origin': s.get('origin'), 'username': username or '',
                'has_credentials': bool(username), 'verify_certificates': s.get('verify_certificates', True)}

    # --- tokens --------------------------------------------------------------------------
    def client(self, settings, token=None):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        return httpx.Client(base_url=settings['url'].rstrip('/'), headers=headers, transport=self.transport,
                            verify=settings.get('verify_certificates', True), timeout=20, follow_redirects=False)

    def password_token(self, settings, username, password):
        parsed = urlparse(settings['url'])
        if parsed.scheme != 'https' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise LoginError('Đăng nhập bằng mật khẩu tới máy chủ khác cần HTTPS')
        try:
            with self.client(settings) as client:
                response = client.post('/API/IDP/connect/token', data={
                    'grant_type': 'password', 'username': username, 'password': password, 'client_id': 'GrantValidatorClient'})
        except httpx.HTTPError:
            raise LoginError('Không kết nối được máy chủ Milestone')
        if response.status_code in (400, 401):
            raise LoginError('Sai tên đăng nhập hoặc mật khẩu Milestone')
        if response.status_code != 200 or not response.json().get('access_token'):
            raise LoginError(f'Milestone từ chối đăng nhập (HTTP {response.status_code})')
        data = response.json()
        return data['access_token'], float(data.get('expires_in', 3600))

    def service_token(self, site):
        """Background token for discovery and reconciliation, renewed before it expires."""
        with self.lock:
            cached = self.tokens.get(site)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
        settings = self.settings(site)
        if not settings.get('url'):
            raise NotConfigured('Chưa biết địa chỉ máy chủ Milestone của site này')
        username, password = self.credentials(settings)
        if not username:
            import os
            token = os.environ.get(settings.get('token_env', 'MILESTONE_TOKEN'))
            if token:
                return token
            raise NotConfigured('Chưa có tài khoản đồng bộ Milestone')
        token, lifetime = self.password_token(settings, username, password)
        with self.lock:
            self.tokens[site] = (token, time.time() + lifetime)
        return token

    # --- people --------------------------------------------------------------------------
    def scope(self, site, settings, token):
        """Cameras (and their hardware) the token's user can see; administrators see the whole site."""
        with self.client(settings, token) as client:
            response = client.get('/api/rest/v1/cameras')
            if response.status_code == 401:
                raise LoginError('Phiên đăng nhập Milestone không hợp lệ hoặc đã hết hạn')
            cameras = response.json().get('array', []) if response.status_code == 200 else []
            admin = client.get('/api/rest/v1/roles').status_code == 200
        sources = set()
        for camera in cameras:
            if camera.get('id'):
                sources.add(camera['id'])
            parent = (camera.get('relations') or {}).get('parent') or {}
            if parent.get('id'):
                sources.add(parent['id'])
        return admin, sources

    def sign_in(self, username=None, password=None, token=None):
        sites = [(site, self.settings(site)) for site in self.site_ids() or ['main']]
        sites = [(site, s) for site, s in sites if s.get('url')]
        if not sites:
            raise LoginError('Chưa biết máy chủ Milestone; quản trị viên cần nhập địa chỉ trong tab Vận hành')
        grants, admin, name, error = [], False, username, None
        for site, settings in sites:
            try:
                user_token = token or self.password_token(settings, username, password)[0]
                site_admin, sources = self.scope(site, settings, user_token)
            except (LoginError, httpx.HTTPError, ValueError) as exc:
                error = exc
                continue
            if not name:
                claims = jwt_claims(user_token)
                name = claims.get('name') or claims.get('preferred_username') or claims.get('sub') or 'Milestone user'
            admin = admin or site_admin
            grants += [[site, '*']] if site_admin else [[site, s] for s in sorted(sources)]
        if error is not None and not grants and not admin:
            raise LoginError(str(error) if isinstance(error, LoginError) else 'Không kết nối được máy chủ Milestone')
        admin = admin or (name or '').lower() in self.admin_users
        session = secrets.token_urlsafe(32)
        principal = {'name': name, 'roles': ['reader', 'admin'] if admin else ['reader'], 'grants': grants,
                     'identity': 'milestone', 'expires': time.time() + self.hours * 3600}
        with self.lock:
            now = time.time()
            for key in [k for k, v in self.sessions.items() if v['expires'] < now]:
                del self.sessions[key]
            self.sessions[session] = principal
        return session, principal

    def principal(self, session):
        with self.lock:
            p = self.sessions.get(session)
            if p and p['expires'] < time.time():
                del self.sessions[session]
                return None
            return p

    def sign_out(self, session):
        with self.lock:
            self.sessions.pop(session, None)
