"""Read-only Milestone REST adapter. Cursor advances only after durable ingestion."""
from urllib.parse import urljoin, urlparse
import httpx
import json


class MilestoneRest:
    @staticmethod
    def password_token(base_url, username, password, transport=None, verify=True):
        parsed = urlparse(base_url)
        if parsed.scheme != 'https' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Milestone password authentication requires HTTPS')
        if not username or not password:
            raise ValueError('Milestone username and password are required')
        with httpx.Client(transport=transport, verify=verify, timeout=20, follow_redirects=False) as client:
            response = client.post(base_url.rstrip('/') + '/API/IDP/connect/token', data={
                'grant_type': 'password', 'username': username, 'password': password,
                'client_id': 'GrantValidatorClient'})
            response.raise_for_status()
            token = response.json().get('access_token')
            if not token:
                raise ValueError('Milestone identity provider did not return an access token')
            return token
    def __init__(self, base_url, token, site_id, engine, transport=None, verify=True):
        self.base = base_url.rstrip('/')
        self.site = site_id
        self.engine = engine
        self.client = httpx.Client(headers={'Authorization': 'Bearer ' + token},
                                   timeout=30, transport=transport, follow_redirects=False, verify=verify)

    def close(self):
        self.client.close()

    def safe_url(self, path):
        url = urljoin(self.base + '/', path)
        original, target = urlparse(self.base), urlparse(url)
        if (original.scheme, original.netloc) != (target.scheme, target.netloc):
            raise ValueError('Pagination origin differs from configured Milestone server')
        return url

    def get(self, path):
        response = self.client.get(self.safe_url(path))
        response.raise_for_status()
        return response.json()

    def discovery(self):
        def pages(path):
            values, seen = [], set()
            while path:
                if path in seen or len(seen) >= 1000:
                    raise ValueError('Discovery pagination loop or limit reached')
                seen.add(path)
                response = self.get(path)
                if isinstance(response, list):
                    values.extend(response); break
                values.extend(response.get('array', []))
                path = response.get('_links', {}).get('next')
            return values
        return {'event_types': pages('/api/rest/v1/eventTypes'),
                'alarm_messages': pages('/api/rest/v1/alarmMessages')}

    def normalize_alarm(self, alarm):
        h = alarm.get('eventHeader', {})
        source = h.get('source', {})
        timestamp = h.get('timestamp') or alarm.get('time') or alarm.get('timestamp')
        source_id = source.get('fqid', {}).get('objectId') or alarm.get('sourceId')
        if not source_id and isinstance(alarm.get('source'), str):
            source_id = alarm['source'].rstrip('/').rsplit('/', 1)[-1]
        if not source_id:
            raise ValueError('Alarm missing source identity; source mapping is required')
        detail = alarm.get('data') or {}
        def label(value):
            return str(value.get('name', value.get('level', ''))) if isinstance(value, dict) else str(value or '')
        return {'site_id': self.site, 'kind': 'alarm', 'source_guid': str(alarm.get('id') or h['id']),
                'source_id': str(source_id), 'event_type': h.get('type') or alarm.get('legacyType') or alarm.get('type') or 'alarm',
                'occurred_at': timestamp, 'updated_at': alarm.get('lastUpdatedTime') or timestamp,
                'message': h.get('message') or alarm.get('message') or '',
                'description': detail.get('description') or alarm.get('description') or '',
                'state': label(alarm.get('state')), 'priority': label(alarm.get('priority')),
                'location': detail.get('location') or '', 'camera_id': alarm.get('cameraId'), 'payload': alarm}

    def backfill(self, max_pages=1000):
        name = self.site + ':alarms:cursor'
        default_cursor = '/api/rest/v1/alarms'
        cursor = self.engine.checkpoint(name) or default_cursor
        count = 0
        seen = set()
        for _ in range(max_pages):
            if cursor in seen:
                raise ValueError('Pagination loop detected')
            seen.add(cursor)
            try:
                result = self.get(cursor)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404 and cursor != default_cursor:
                    # a server-returned "next" link can be malformed (e.g. missing the API prefix); a stuck
                    # checkpoint on it would 404 forever, so drop back to the default and retry next cycle
                    self.engine.checkpoint(name, default_cursor)
                raise
            rows = result if isinstance(result, list) else result.get('array', result.get('alarms', []))
            for alarm in rows:
                self.engine.ingest(self.normalize_alarm(alarm))
                count += 1
            next_page = None if isinstance(result, list) else result.get('_links', result.get('paging', {})).get('next')
            if not next_page:
                self.engine.checkpoint(name, '/api/rest/v1/alarms')
                return count
            self.safe_url(next_page)
            self.engine.checkpoint(name, next_page)
            cursor = next_page
        raise ValueError('Page limit reached; checkpoint saved for next invocation')


def process_reconciliation(engine, milestone):
    """Fetch current alarm state for queued changes; failures stay pending with a readable reason."""
    from .identity import LoginError, NotConfigured
    with engine.reading() as db:
        jobs = db.execute("SELECT key,body FROM reconciliation WHERE status='pending' ORDER BY received LIMIT 50").fetchall()
    tokens = {}
    for job in jobs:
        data = json.loads(job['body'])
        site = data['site_id']
        error, blocked, status = None, False, 'done'
        try:
            if site not in tokens:
                tokens[site] = milestone.service_token(site)
            settings = milestone.settings(site)
            connector = MilestoneRest(settings['url'], tokens[site], site, engine,
                                      verify=settings.get('verify_certificates', True), transport=milestone.transport)
            try:
                alarm_id = data.get('alarm_id')
                if alarm_id and alarm_id != '00000000-0000-0000-0000-000000000000':
                    from urllib.parse import quote
                    response = connector.get('/api/rest/v1/alarms/' + quote(alarm_id, safe=''))
                    engine.ingest(connector.normalize_alarm(response.get('data', response)))
                else:
                    if data['reason'] in ('configuration_changed', 'scheduled', 'startup'):
                        engine.context_snapshot(site, connector.discovery())
                    connector.backfill()
            finally:
                connector.close()
        except NotConfigured as exc:
            # Alarm changes arrive from the Event Server plugin directly; REST reconciliation is optional.
            error, status = str(exc), 'skipped'
        except LoginError as exc:
            error, blocked = str(exc), True
        except Exception as exc:
            # Preserve error class only; HTTP URLs and response payloads may contain identifiers.
            error = type(exc).__name__ + ': reconciliation failed; check connection and source API'
        with engine.lock, engine.db:
            from .store import now
            engine.db.execute('UPDATE reconciliation SET status=?,error=?,received=? WHERE key=?',
                              ('pending' if error and status != 'skipped' else status, error, now(), job['key']))
        if blocked:
            break  # Same cause applies to the rest of the queue; retry on the next cycle.
