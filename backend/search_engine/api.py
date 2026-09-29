import asyncio
import hmac
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from pydantic import BaseModel, Field

from .contracts import AskRequest, Envelope, Profile, SearchRequest, ReconcileRequest
from .identity import LoginError, Milestone
from .store import Engine, allowed


class SignIn(BaseModel):
    username: str = Field(default='', max_length=256)
    password: str = Field(default='', max_length=1024)
    token: str = Field(default='', max_length=16384)


class ActiveGuardConnection(BaseModel):
    url: str = Field(min_length=1, max_length=512)
    username: str = Field(default='', max_length=256)
    password: str = Field(default='', max_length=1024)
    site_id: str = Field(default='main', max_length=128)


class RuleStatus(BaseModel):
    site_id: str = Field(max_length=128)
    event_type: str = Field(max_length=256)
    field: str = Field(pattern='^(name|message|description)$')
    version: int
    status: str = Field(pattern='^(active|disabled)$')


class MilestoneConnection(BaseModel):
    site_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=512)
    username: str = Field(default='', max_length=256)
    password: str = Field(default='', max_length=1024)
    verify_certificates: bool = False


def create_app(config, transport=None):
    principals = config.get('principals', [])
    if not principals or any(len(p.get('token', '')) < 32 for p in principals):
        raise ValueError('Configure unique principal tokens with at least 32 characters')
    if len({p['token'] for p in principals}) != len(principals):
        raise ValueError('Principal tokens must be unique')
    engine = Engine(config.get('database', 'data/search.db'))
    engine.upgrade_index()
    milestone = Milestone(engine, config, transport)
    # Development default: every signed-in person is an administrator who sees all data.
    full_access = config.get('full_access', True) is not False
    worker_state = {'error': None}

    async def worker():
        while True:
            try:
                await asyncio.to_thread(engine.process_pending)
                worker_state['error'] = None
            except Exception:
                worker_state['error'] = 'Ingestion worker failed; inspect service logs'
                logging.exception('Ingestion worker failed')
            await asyncio.sleep(0.5)

    async def reconciliation_worker():
        from .connectors import process_reconciliation
        from .enrich import learn_rules
        from .operations import schedule
        import time
        learned_at = 0
        while True:
            if time.time() - learned_at > 600:
                learned_at = time.time()
                try:
                    await asyncio.to_thread(learn_rules, engine)
                except Exception:
                    logging.exception('Rule learning failed')
            try:
                await asyncio.to_thread(schedule, engine, config, time.time())
                await asyncio.to_thread(process_reconciliation, engine, milestone)
            except Exception:
                logging.exception('Reconciliation worker failed')
            await asyncio.sleep(10)

    async def activeguard_worker():
        from . import activeguard
        import time
        while True:
            delay = 60
            for ag, settings in await asyncio.to_thread(activeguard.connect_all, engine, config, transport):
                delay = max(15, int(settings.get('interval_seconds', 60)))
                try:
                    await asyncio.to_thread(activeguard.sync, engine, ag, settings)
                except Exception as exc:       # one unreachable server must not stop the others
                    logging.exception('Active Guard import failed for %s', settings['id'])
                    engine.checkpoint(f"activeguard:{settings['id']}:last_error", str(exc)[:300])
                finally:
                    ag.close()
            await asyncio.sleep(delay)

    async def embedding_worker():
        from .ai import index_pending
        while True:
            try:
                await asyncio.to_thread(index_pending, engine, config.get('ai', {}))
                worker_state['embedding_error'] = None
            except Exception:
                worker_state['embedding_error'] = 'Embedding service unavailable; semantic index is incomplete'
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(worker())
        reconcile_task = asyncio.create_task(reconciliation_worker())
        embedding_task = asyncio.create_task(embedding_worker())
        guard_task = asyncio.create_task(activeguard_worker())
        yield
        for job in (task, reconcile_task, embedding_task, guard_task):
            job.cancel()
            try:
                await job
            except asyncio.CancelledError:
                pass
        engine.close()

    app = FastAPI(title='Milestone Search', version='0.1.0', lifespan=lifespan)
    app.state.engine = engine
    app.state.milestone = milestone
    bearer = HTTPBearer(auto_error=False)

    @app.middleware('http')
    async def headers(request, call_next):
        length = request.headers.get('content-length')
        if length and (not length.isdigit() or int(length) > 12 * 1024 * 1024):
            return JSONResponse({'detail': 'Request too large'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' blob:; media-src 'self' blob:; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    def widen(p):
        if not full_access or p.get('roles') == ['collector']:
            return p
        return {**p, 'roles': ['admin', 'reader'], 'grants': [['*', '*']]}

    def principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials:
            for p in principals:
                if hmac.compare_digest(credentials.credentials, p['token']):
                    return widen(p)
            session = milestone.principal(credentials.credentials)
            if session:
                return widen(session)
        raise HTTPException(401, 'Authentication required', headers={'WWW-Authenticate': 'Bearer'})

    def require(role):
        def check(p=Depends(principal)):
            if role not in p.get('roles', []):
                raise HTTPException(403, f'{role} permission required')
            return p
        return check

    reader, admin, collector = require('reader'), require('admin'), require('collector')

    def sites_of(p):
        return p.get('sites', [])

    def collector_site(p, site):
        if site not in sites_of(p) and '*' not in sites_of(p):
            raise HTTPException(403, 'Collector is not authorized for this site')

    @app.post('/api/session')
    def sign_in(data: SignIn):
        try:
            if data.token:
                session, p = milestone.sign_in(token=data.token)
            elif data.username and data.password:
                session, p = milestone.sign_in(data.username, data.password)
            else:
                raise LoginError('Nhập tên đăng nhập và mật khẩu Milestone')
        except LoginError as exc:
            raise HTTPException(401, str(exc))
        return {'token': session, 'name': p['name'], 'roles': ['admin', 'reader'] if full_access else p['roles']}

    @app.delete('/api/session')
    def sign_out(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials:
            milestone.sign_out(credentials.credentials)
        return {'signed_out': True}

    @app.get('/api/me')
    def me(p=Depends(principal)):
        return {'name': p.get('name'), 'roles': p.get('roles', []), 'grants': p.get('grants', []),
                'identity': p.get('identity', 'token')}

    @app.post('/api/ask')
    def ask(data: AskRequest, p=Depends(reader)):
        """One text box: understand the sentence, search, and explain what was understood."""
        from datetime import datetime, timedelta, timezone
        from .query import interpret
        grants = p.get('grants', [])
        now = datetime.now(timezone(timedelta(minutes=data.tz_offset_minutes)))
        meaning = interpret(data.text, engine.sources(grants), now)
        f = meaning['filters']
        params = {k: v for k, v in f.items() if v is not None}
        common = dict(facets=True, collapse=True, limit=data.limit, offset=data.offset, **params)
        result = engine.search(meaning['keywords'], grants, **common)
        dropped = None
        if not result['total'] and meaning['keywords']:
            dropped = meaning['keywords']
            result = engine.search('', grants, **common)
        elif meaning['keywords']:
            meaning['chips'].append({'type': 'text', 'label': '“' + meaning['keywords'] + '”'})
        return {**result, 'understood': meaning['chips'], 'ignored_words': dropped,
                'range': {k: f[k].isoformat() if f[k] else None for k in ('start', 'end')}}

    def activeguard_configured():
        from . import activeguard
        return [s for s in activeguard.load_settings(engine, config)['servers'] if s.get('url')]

    @app.get('/api/settings/activeguard')
    def activeguard_settings(p=Depends(admin)):
        from . import activeguard
        return activeguard.describe_settings(engine, config)

    @app.put('/api/settings/activeguard')
    def save_activeguard(data: ActiveGuardConnection, p=Depends(admin)):
        from . import activeguard
        info = activeguard.save_settings(engine, config, data.url, data.username, data.password, data.site_id, p['name'], transport)
        return {**activeguard.describe_settings(engine, config), 'server_version': info.get('multi_ai_soft_version')}

    @app.delete('/api/settings/activeguard/{server}')
    def remove_activeguard(server: str, p=Depends(admin)):
        from . import activeguard
        activeguard.remove_server(engine, server, p['name'])
        return activeguard.describe_settings(engine, config)

    @app.post('/api/activeguard/sync')
    def activeguard_sync(p=Depends(admin)):
        from . import activeguard
        servers = activeguard.connect_all(engine, config, transport)
        if not servers:
            raise HTTPException(400, 'Chưa kết nối Active Guard')
        done, errors = {}, {}
        for ag, settings in servers:
            try:
                done[settings['id']] = activeguard.sync(engine, ag, settings)
            except activeguard.ActiveGuardError as exc:
                errors[settings['id']] = str(exc)
                engine.checkpoint(f"activeguard:{settings['id']}:last_error", str(exc)[:300])
            finally:
                ag.close()
        engine.process_pending(500)
        if errors and not done:
            raise HTTPException(502, '; '.join(f'{k}: {v}' for k, v in errors.items()))
        return {'imported': done, 'errors': errors}

    @app.post('/api/ask/image')
    async def ask_image(file: UploadFile = File(...), tz_offset_minutes: int = 420, p=Depends(reader)):
        """Photo search: faces are matched by Active Guard itself; a full-body photo is described by the vision model."""
        from . import activeguard
        from .ai import describe_image
        content = await file.read(8 * 1024 * 1024 + 1)
        if len(content) > 8 * 1024 * 1024:
            raise HTTPException(413, 'Ảnh vượt quá 8 MiB')
        matches, text, problems = [], None, []
        for ag, settings in await asyncio.to_thread(activeguard.connect_all, engine, config, transport):
            try:
                matches += await asyncio.to_thread(activeguard.face_photo_search, engine, ag, settings, content)
            except activeguard.ActiveGuardError as exc:
                problems.append(f"{settings['id']}: {exc}")
            finally:
                ag.close()
        matches.sort(key=lambda kv: -kv[1])
        if not matches and config.get('ai', {}).get('vision_model'):
            try:
                text = await asyncio.to_thread(describe_image, content, config.get('ai', {}))
            except HTTPException as exc:
                problems.append(str(exc.detail))
        if matches:
            await asyncio.to_thread(engine.process_pending, 500)
            items = engine.records_by_keys([key for key, _ in matches])
            allowed_items = [i for i in items if allowed(i, p.get('grants', []))]
            return {'total': len(allowed_items), 'items': allowed_items, 'limit': len(allowed_items), 'offset': 0,
                    'understood': [{'type': 'photo', 'label': 'Khuôn mặt giống ảnh'}], 'ignored_words': None, 'described': None,
                    'similarity': {key: score for key, score in matches}}
        if text:
            return {**ask(AskRequest(text=text, tz_offset_minutes=tz_offset_minutes), p), 'described': text}
        raise HTTPException(503, problems[0] if problems else 'Chưa kết nối Active Guard hoặc cài model thị giác để tìm theo ảnh')

    @app.get('/api/records/{key}/image')
    def record_image(key: str, p=Depends(reader)):
        import base64
        from fastapi import Response
        from .enrich import image
        with engine.lock:
            row = engine.db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
            found = image(engine, key) if row else None
        import json as _json
        if not row or not found or not allowed(_json.loads(row['body']), p.get('grants', [])):
            raise HTTPException(404, 'Image not found')
        return Response(base64.b64decode(found[1]), media_type=found[0], headers={'Cache-Control': 'private, max-age=3600'})

    @app.get('/api/rules')
    def rules(p=Depends(admin)):
        from .enrich import list_rules
        return list_rules(engine)

    @app.post('/api/rules/status')
    def rule_status(data: RuleStatus, p=Depends(admin)):
        from .enrich import set_rule_status
        set_rule_status(engine, data.site_id, data.event_type, data.field, data.version, data.status, p['name'])
        return {'status': data.status}

    @app.post('/api/rules/learn')
    def learn(p=Depends(admin)):
        from .enrich import learn_rules
        return {'changed': [list(x) for x in learn_rules(engine)]}

    @app.post('/api/rules/suggest')
    def suggest(p=Depends(admin)):
        from .ai import suggest_rules
        return {'proposals': suggest_rules(engine, config.get('ai', {}))}

    @app.get('/api/summary')
    def summary(p=Depends(reader)):
        status = engine.status()
        return {'records': status['records'], 'last_received': status['last_received']}

    @app.get('/api/sources')
    def sources(p=Depends(reader)):
        return engine.sources(p.get('grants', []))

    @app.get('/api/settings/milestone')
    def milestone_settings(p=Depends(admin)):
        return [milestone.describe(site) for site in milestone.site_ids() or ['main']]

    @app.put('/api/settings/milestone')
    def save_milestone(data: MilestoneConnection, p=Depends(admin)):
        result = milestone.save(data.site_id, data.url, data.username, data.password, data.verify_certificates, p['name'])
        engine.queue_reconciliation({'site_id': data.site_id, 'reason': 'configuration_changed', 'alarm_id': None})
        return result

    @app.get('/health')
    def health():
        return {'status': 'degraded' if worker_state['error'] else 'ok'}

    @app.post('/api/ingest', status_code=202)
    def ingest(data: Envelope, p=Depends(collector)):
        collector_site(p, data.site_id)
        return {'key': engine.ingest(data.model_dump(mode='json')), 'accepted': True}

    @app.post('/api/search')
    def search(data: SearchRequest, p=Depends(reader)):
        return engine.search(grants=p.get('grants', []), **data.model_dump())

    @app.post('/api/collector/reconcile', status_code=202)
    def reconcile(data: ReconcileRequest, p=Depends(collector)):
        collector_site(p, data.site_id)
        return {'key': engine.queue_reconciliation(data.model_dump())}

    @app.get('/api/samples')
    def samples(site_id: str, event_type: str | None = None, p=Depends(admin)):
        import json
        with engine.lock:
            query = "SELECT body FROM inbox WHERE json_extract(body,'$.site_id')=? AND status!='deleted'"
            args = [site_id]
            if event_type:
                query += " AND json_extract(body,'$.event_type')=?"
                args.append(event_type)
            rows = engine.db.execute(query+' ORDER BY received DESC LIMIT 10', args).fetchall()
        return [json.loads(r['body']) for r in rows]

    @app.get('/api/contexts')
    def contexts(p=Depends(admin)):
        with engine.lock:
            return [dict(r) for r in engine.db.execute('SELECT site_id,version,created FROM contexts ORDER BY created DESC')]

    @app.get('/api/contexts/{site_id}/latest')
    def latest_context(site_id: str, p=Depends(admin)):
        result = engine.latest_context(site_id)
        if result is None:
            raise HTTPException(404, 'No discovered configuration for this site')
        return result

    @app.get('/api/records/{key}')
    def record(key: str, p=Depends(reader)):
        with engine.lock:
            row = engine.db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
        import json
        item = json.loads(row['body']) if row else None
        if item is None or not allowed(item, p.get('grants', [])):
            raise HTTPException(404, 'Record not found')
        return item

    @app.post('/api/answer')
    def answer(data: SearchRequest, p=Depends(reader)):
        from .ai import grounded_answer
        result = engine.search(grants=p.get('grants', []), **data.model_dump())
        return grounded_answer(data.query, result, config.get('ai', {}))

    @app.post('/api/plan')
    def plan(data: SearchRequest, p=Depends(reader)):
        from .ai import plan_query
        return plan_query(data.query, config.get('ai', {}))

    @app.post('/api/semantic-search')
    def semantic_search(data: SearchRequest, p=Depends(reader)):
        from .ai import semantic_search
        return semantic_search(engine, data, p.get('grants', []), config.get('ai', {}))

    @app.get('/api/capabilities')
    def capabilities(p=Depends(principal)):
        ai = config.get('ai', {})
        return {'chat': bool(ai.get('chat_model')), 'embedding': bool(ai.get('embedding_model')), 'vision': bool(ai.get('vision_model')),
                'activeguard': bool(activeguard_configured()),
                'voice': bool(ai.get('whisper_model')), 'video_playback': False,
                'identity': p.get('identity', 'token')}

    @app.post('/api/transcribe')
    async def transcribe(file: UploadFile = File(...), p=Depends(reader)):
        from .ai import transcribe_audio
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, 'Audio exceeds 10 MiB')
        return await asyncio.to_thread(transcribe_audio, content, config.get('ai', {}))

    @app.get('/api/profiles')
    def profiles(p=Depends(admin)):
        return engine.list_profiles()

    @app.post('/api/profiles', status_code=201)
    def save(data: Profile, p=Depends(admin)):
        return engine.save_profile(data.model_dump(), p['name'])

    @app.post('/api/profiles/{id}/{version}/validate')
    def validate(id: str, version: int, samples: list[Envelope], p=Depends(admin)):
        return engine.validate_profile(id, version, [x.model_dump(mode='json') for x in samples], p['name'])

    @app.post('/api/profiles/{id}/{version}/activate')
    def activate(id: str, version: int, p=Depends(admin)):
        engine.activate_profile(id, version, p['name'])
        return {'active': True}

    @app.get('/api/catalog')
    def catalog(p=Depends(admin)):
        return engine.catalog()

    @app.get('/api/operations')
    def operations(p=Depends(admin)):
        with engine.lock:
            errors = [dict(r) for r in engine.db.execute("SELECT key,error,received FROM inbox WHERE status='failed' LIMIT 100")]
            audit = [dict(r) for r in engine.db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
            pending = engine.db.execute("SELECT count(*) FROM reconciliation WHERE status='pending'").fetchone()[0]
            reconciliation_errors = [dict(r) for r in engine.db.execute("SELECT key,error FROM reconciliation WHERE status='pending' AND error IS NOT NULL LIMIT 100")]
        return {**engine.status(), 'errors': errors, 'audit': audit, 'worker_error': worker_state['error'],
                'reconciliation_pending': pending, 'reconciliation_errors': reconciliation_errors,
                'database': engine.dialect, 'embedding_error': worker_state.get('embedding_error')}

    @app.post('/api/operations/process')
    def process(p=Depends(admin)):
        return {'processed': engine.process_pending()}

    @app.post('/api/operations/replay/{site_id}')
    def replay(site_id: str, p=Depends(admin)):
        return {'queued': engine.replay(site_id, p['name'])}

    @app.delete('/api/operations/retention')
    def purge(before: str, p=Depends(admin)):
        return {'purged': engine.purge(before, p['name'])}

    bundled = Path(getattr(sys, '_MEIPASS', '')) / 'web' if getattr(sys, 'frozen', False) else None
    web = Path(config.get('web_root', bundled or Path(__file__).resolve().parents[2] / 'web'))
    if web.exists():
        app.mount('/', StaticFiles(directory=web, html=True), name='console')
    return app
