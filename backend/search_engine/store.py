"""Single-node durable index. Writes share a lock and explicit transactions; reads use pooled connections (see Engine.reading)."""
import hashlib
import json
import re
import sqlite3
import contextlib
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

from .contracts import Envelope, Profile, SearchRequest
from .profiles import matches, normalize
from .database import connect, integrity_errors
from .query import fold


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def display_names(data):
    """Human-readable event and source names carried by Milestone SDK or REST payloads."""
    payload = data.get('payload') or {}
    header = payload.get('header') or payload.get('eventHeader') or (payload.get('event') or {}).get('EventHeader') or {}
    source = header.get('Source') or header.get('source') or {}
    event_name = header.get('Name') or header.get('name') or data.get('message') or ''
    source_name = (source.get('Name') or source.get('name') or '') if isinstance(source, dict) else ''
    return str(event_name)[:256], str(source_name)[:512]


def record_key(site_id, kind, source_guid):
    return hashlib.sha256(dumps([site_id, kind, source_guid]).encode()).hexdigest()


def type_key(data):
    """Integrations often share one Milestone event id and tell kinds apart by header Type (Checkin, FACEME.PERSON...)."""
    payload = data.get('payload') or {}
    header = payload.get('header') or payload.get('eventHeader') or {}
    kind = str(header.get('Type') or header.get('type') or '').strip()
    return data['event_type'] if not kind or kind == data['event_type'] else f"{data['event_type']}|{kind}"[:256]


def grant_clause(grants, prefix=''):
    """SQL filter for [site, source] grants; '*' matches any site or source. No grants means no access."""
    grants = [list(g) for g in grants or []]
    if any(g == ['*', '*'] for g in grants):
        return '1=1', []
    clauses, args = [], []
    whole_sites = sorted({g[0] for g in grants if g[1] == '*'})
    any_site_sources = sorted({g[1] for g in grants if g[0] == '*' and g[1] != '*'})
    if whole_sites:
        clauses.append(f"{prefix}site_id IN ({','.join('?' * len(whole_sites))})")
        args += whole_sites
    if any_site_sources:
        clauses.append(f"{prefix}source_id IN ({','.join('?' * len(any_site_sources))})")
        args += any_site_sources
    by_site = {}
    for site, source in grants:
        if site != '*' and source != '*' and site not in whole_sites:
            by_site.setdefault(site, set()).add(source)
    for site, sources in sorted(by_site.items()):
        clauses.append(f"({prefix}site_id=? AND {prefix}source_id IN ({','.join('?' * len(sources))}))")
        args += [site, *sorted(sources)]
    return ('(' + ' OR '.join(clauses) + ')' if clauses else '1=0'), args


def allowed(item, grants):
    return any(site in ('*', item['site_id']) and source in ('*', item['source_id']) for site, source in grants or [])


def embedding_text(item):
    return ' '.join([item['message'], item['description'], item['family'], dumps(item['attributes'])])


class Engine:
    SCHEMA_VERSION = 1
    READERS = 8     # concurrent read connections (PostgreSQL); searches, pictures and status never queue behind writes
    def __init__(self, path):
        # self.lock serialises writers on self.db; readers use their own connections (see reading()).
        self.lock = threading.RLock()
        self.dialect, self.db = connect(path)
        self._target, self._readers, self._reader_count, self._reader_guard = path, queue.LifoQueue(), 0, threading.Lock()
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS inbox(
          key TEXT PRIMARY KEY, body TEXT NOT NULL, revision TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', error TEXT, received TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(
          key TEXT PRIMARY KEY, site_id TEXT, source_id TEXT, kind TEXT, family TEXT,
          state TEXT, occurred_at TEXT, updated_at TEXT, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS record_scope ON records(site_id,source_id,occurred_at);
        CREATE VIRTUAL TABLE IF NOT EXISTS record_fts USING fts5(key UNINDEXED, content, tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS profiles(
          id TEXT, version INTEGER, status TEXT NOT NULL, body TEXT NOT NULL,
          created TEXT NOT NULL, activated TEXT, PRIMARY KEY(id,version));
        CREATE TABLE IF NOT EXISTS catalog(
          site_id TEXT, event_type TEXT, source_id TEXT, first_seen TEXT, last_seen TEXT,
          fields TEXT, PRIMARY KEY(site_id,event_type,source_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time TEXT, actor TEXT, action TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints(name TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY, model TEXT, content_hash TEXT, vector TEXT);
        CREATE TABLE IF NOT EXISTS reconciliation(key TEXT PRIMARY KEY,body TEXT,status TEXT,error TEXT,received TEXT);
        CREATE TABLE IF NOT EXISTS contexts(site_id TEXT,version TEXT,body TEXT,created TEXT,PRIMARY KEY(site_id,version));
        CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS names(site_id TEXT, kind TEXT, id TEXT, name TEXT NOT NULL, PRIMARY KEY(site_id,kind,id));
        ''')
        row = self.db.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        if row and int(row[0]) != self.SCHEMA_VERSION:
            self.db.close()
            raise RuntimeError(f'Unsupported database schema version {row[0]}; expected {self.SCHEMA_VERSION}')
        if not row:
            self.db.execute("INSERT INTO schema_meta VALUES('version',?)", (str(self.SCHEMA_VERSION),))
            if self.dialect == 'sqlite':
                self.db.commit()
        self._names = {}
        from .enrich import SCHEMA
        self.db.executescript(SCHEMA)
        if self.dialect == 'sqlite':
            self.db.commit()
        self.episode_gap, self.episode_span = 60, 600
        # pgvector only serves the embedding index; text and voice search do not need it. Without it (e.g. PostgreSQL
        # on Windows, where pgvector is not bundled) semantic search falls back to re-ranking text-search candidates.
        self.pgvector = False
        if self.dialect == 'postgresql':
            try:
                self.db.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
                self.db.execute('CREATE TABLE IF NOT EXISTS embeddings(key TEXT PRIMARY KEY REFERENCES records(key) ON DELETE CASCADE, model TEXT NOT NULL, content_hash TEXT NOT NULL, embedding public.vector NOT NULL)')
                self.pgvector = True
            except Exception as exc:
                import logging
                logging.warning('PostgreSQL extension "vector" unavailable (%s); semantic index off, text and voice search unaffected',
                                str(exc).splitlines()[0])
        self._search_schema()

    def _search_schema(self):
        """Columns and indexes search relies on at scale; added in place to databases created by earlier versions."""
        import logging
        columns = ({r[1] for r in self.db.execute('PRAGMA table_info(records)')} if self.dialect == 'sqlite' else
                   {r[0] for r in self.db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='records' AND table_schema=current_schema()")})
        added = [c for c in ('event_type', 'event_family') if c not in columns]
        for column in added:
            # Real columns: filters, the event-type facet and rule learning otherwise parse every record's JSON body.
            self.db.execute(f'ALTER TABLE records ADD COLUMN {column} TEXT')
        filled = 0
        with self.lock, self.db:
            for column in ('event_type', 'event_family'):
                filled += self.db.execute(f"UPDATE records SET {column}=json_extract(body,'$.{column}') WHERE {column} IS NULL").rowcount
        if filled:
            logging.info('records.event_type filled for %d existing records', filled)
        for statement in ('CREATE INDEX IF NOT EXISTS record_time ON records(occurred_at)',
                          'CREATE INDEX IF NOT EXISTS record_event ON records(event_type, occurred_at)',
                          'CREATE INDEX IF NOT EXISTS record_family ON records(event_family, occurred_at)',
                          'CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(status, received)',
                          'CREATE INDEX IF NOT EXISTS inbox_received ON inbox(received)'):
            self.db.execute(statement)
        if self.dialect == 'postgresql':
            # Attribute (role, value) pairs are strongly correlated ("gender"/"male" is common, not rare); without these
            # statistics the planner expects a few hundred rows and walks tens of thousands through nested loops.
            self.db.execute('CREATE STATISTICS IF NOT EXISTS attributes_role_folded (ndistinct, dependencies, mcv) ON role, folded FROM attributes')
            if filled or added:
                # Filling the new columns rewrote every row: reclaim the old versions now, or every search and status
                # call visits them until autovacuum gets round to it (measured: status 37 ms -> 327 ms on 60k records).
                logging.info('Vacuuming records after the one-time column fill')
                self.db.execute('VACUUM (ANALYZE) records'); self.db.execute('ANALYZE attributes')
        else:
            self.db.commit()

    def close(self):
        # Background workers run in threads; wait for the one holding the connection before closing it.
        with self.lock:
            self.closed = True
            self.db.close()
        while True:
            try:
                self._readers.get_nowait().close()
            except queue.Empty:
                break

    @contextlib.contextmanager
    def reading(self):
        """A connection for reads only. On PostgreSQL it is one of up to READERS pooled connections in a REPEATABLE READ
        transaction: it never waits for the writer (MVCC) and count, page and facets of one search see the same snapshot.
        SQLite has a single connection, so there reads still take the writer lock."""
        if self.dialect != 'postgresql':
            with self.lock:
                yield self.db
            return
        conn = self._take_reader()
        try:
            with conn.connection.transaction():
                yield conn
        finally:
            if conn.connection.closed or conn.connection.broken:
                with self._reader_guard:
                    self._reader_count -= 1
            else:
                self._readers.put(conn)

    def _take_reader(self):
        try:
            return self._readers.get_nowait()
        except queue.Empty:
            pass
        with self._reader_guard:
            create = self._reader_count < self.READERS
            if create:
                self._reader_count += 1
        if not create:
            return self._readers.get(timeout=120)
        try:
            from .database import PostgresConnection
            import psycopg
            conn = PostgresConnection(self._target)
            conn.connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            return conn
        except Exception:
            with self._reader_guard:
                self._reader_count -= 1
            raise

    def save_embedding(self, key, model, content_hash, vector, expected_body=None):
        import math
        if not vector or any(not math.isfinite(x) for x in vector) or not any(vector):
            raise ValueError('Embedding must be finite and nonzero')
        with self.lock, self.db:
            row = self.db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
            if not row or (expected_body is not None and row['body'] != expected_body):
                return False
            self.db.execute('INSERT INTO embeddings VALUES(?,?,?,?::public.vector) ON CONFLICT(key) DO UPDATE SET model=excluded.model,content_hash=excluded.content_hash,embedding=excluded.embedding',
                            (key, model, content_hash, dumps(vector)))
        return True

    def vector_search(self, vector, model, grants, request):
        clause, args = grant_clause(grants, 'r.')
        clauses = [clause]
        for name in ('site_id', 'source_id', 'kind', 'family', 'state'):
            value = getattr(request, name)
            if value is not None:
                clauses.append(f'r.{name}=?'); args.append(value)
        if request.event_type:
            clauses.append('r.event_type=?'); args.append(request.event_type)
        for column, values in (('r.event_type', request.event_types), ('r.source_id', request.source_ids)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' * len(values))})"); args.extend(values)
        for name, sign in [('start', '>='), ('end', '<=')]:
            value = getattr(request, name)
            if value:
                clauses.append(f'r.occurred_at{sign}?'); args.append(value.isoformat(timespec='microseconds').replace('+00:00','Z'))
        where = ' AND '.join(clauses)
        with self.reading() as db:
            available = db.execute(f'SELECT count(*) FROM records r WHERE {where}', args).fetchone()[0]
            join = f'FROM records r JOIN embeddings e ON e.key=r.key WHERE {where} AND e.model=? AND public.vector_dims(e.embedding)=?'
            params = [*args, model, len(vector)]
            count = db.execute('SELECT count(*) '+join, params).fetchone()[0]
            rows = db.execute('SELECT r.body,1-(e.embedding OPERATOR(public.<=>) ?::public.vector) AS score '+join+
                                   ' ORDER BY score DESC,r.key LIMIT ? OFFSET ?', [dumps(vector),*params,request.limit,request.offset]).fetchall()
        return {'items': [{**json.loads(r['body']), 'score': r['score']} for r in rows], 'total': count,
                'unindexed': available-count, 'mode': 'semantic', 'limit': request.limit, 'offset': request.offset}

    def queue_reconciliation(self, data):
        import uuid
        key = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO reconciliation VALUES(?,?,?,?,?)', (key, dumps(data), 'pending', None, now()))
        return key

    def context_snapshot(self, site_id, data):
        body = dumps(data)
        version = hashlib.sha256(body.encode()).hexdigest()
        with self.lock, self.db:
            self.db.execute('INSERT INTO contexts VALUES(?,?,?,?) ON CONFLICT(site_id,version) DO NOTHING',
                            (site_id, version, body, now()))
        return version

    def latest_context(self, site_id):
        with self.reading() as db:
            row = db.execute(
                'SELECT site_id,version,body,created FROM contexts WHERE site_id=? ORDER BY created DESC LIMIT 1',
                (site_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['data'] = json.loads(result.pop('body'))
        return result

    def audit(self, actor, action, detail):
        self.db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
                        (now(), actor, action, dumps(detail)))

    def ingest(self, data):
        data = Envelope.model_validate(data).model_dump(mode='json')
        body = dumps(data)
        if len(body.encode()) > 1024 * 1024:
            raise ValueError('Envelope exceeds 1 MiB')
        key = record_key(data['site_id'], data['kind'], data['source_guid'])
        with self.lock, self.db:
            old = self.db.execute('SELECT revision,body FROM inbox WHERE key=?', (key,)).fetchone()
            if old and old['revision'] >= data['updated_at']:
                # Same source revision is immutable; duplicate notifications are harmless.
                return key
            self.db.execute('''INSERT INTO inbox(key,body,revision,received) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET body=excluded.body,revision=excluded.revision,
                received=excluded.received,status='pending',error=NULL WHERE excluded.revision > inbox.revision''', (key, body, data['updated_at'], now()))
        return key

    def save_profile(self, data, actor='system'):
        p = Profile.model_validate(data)
        with self.lock, self.db:
            try:
                self.db.execute('INSERT INTO profiles VALUES(?,?,?,?,?,NULL)',
                                (p.id, p.version, 'draft', p.model_dump_json(), now()))
            except integrity_errors as exc:
                raise ValueError('Profile version is immutable; create a new version') from exc
            self.audit(actor, 'profile.create', {'id': p.id, 'version': p.version})
        return p.model_dump()

    def _profile(self, id, version):
        row = self.db.execute('SELECT * FROM profiles WHERE id=? AND version=?', (id, version)).fetchone()
        if not row:
            raise ValueError('Profile not found')
        return row

    def validate_profile(self, id, version, samples, actor='system'):
        with self.lock, self.db:
            row = self._profile(id, version)
            p = Profile.model_validate_json(row['body'])
            results = []
            for sample in samples[:100]:
                try:
                    data = Envelope.model_validate(sample).model_dump(mode='json')
                    if not matches(p, data):
                        raise ValueError('Sample does not match profile')
                    results.append({'valid': True, 'attributes': normalize(p, data)})
                except (ValueError, TypeError, OverflowError) as exc:
                    results.append({'valid': False, 'error': str(exc)})
            valid = bool(results) and all(r['valid'] for r in results)
            if row['status'] not in ('active', 'retired'):
                self.db.execute('UPDATE profiles SET status=? WHERE id=? AND version=?',
                                ('validated' if valid else 'draft', id, version))
            self.audit(actor, 'profile.validate', {'id': id, 'version': version, 'valid': valid})
            return {'valid': valid, 'results': results}

    def activate_profile(self, id, version, actor='system'):
        with self.lock, self.db:
            row = self._profile(id, version)
            if row['status'] not in ('validated', 'retired', 'active'):
                raise ValueError('Profile must be validated before activation')
            self.db.execute("UPDATE profiles SET status='retired' WHERE id=? AND status='active'", (id,))
            self.db.execute("UPDATE profiles SET status='active',activated=? WHERE id=? AND version=?", (now(), id, version))
            self.audit(actor, 'profile.activate', {'id': id, 'version': version})

    def list_profiles(self):
        with self.reading() as db:
            return [{**json.loads(r['body']), 'status': r['status'], 'activated': r['activated']}
                    for r in db.execute('SELECT * FROM profiles ORDER BY id,version DESC')]

    def process_pending(self, limit=100):
        with self.lock, self.db:
            rows = self.db.execute("SELECT * FROM inbox WHERE status='pending' ORDER BY received LIMIT ?", (limit,)).fetchall()
            profiles = [Profile.model_validate_json(r['body']) for r in self.db.execute("SELECT body FROM profiles WHERE status='active'")]
            for row in rows:
                data = json.loads(row['body'])
                key = row['key']
                self.db.execute('DELETE FROM record_fts WHERE key=?', (key,))
                if data['deleted']:
                    for table in ('attributes', 'record_links', 'media'):
                        self.db.execute(f'DELETE FROM {table} WHERE key=?', (key,))
                    self.db.execute('DELETE FROM vectors WHERE key=?', (key,))
                    self.db.execute('DELETE FROM records WHERE key=?', (key,))
                    # Retain only a minimal tombstone; do not retain deleted payload.
                    tombstone = {**data, 'message': '', 'description': '', 'payload': {}, 'location': ''}
                    self.db.execute("UPDATE inbox SET body=?,status='deleted',error=NULL WHERE key=?", (dumps(tombstone), key))
                    continue
                candidates = sorted((p for p in profiles if matches(p, data)), key=lambda p: p.priority, reverse=True)
                result = {**data, 'key': key, 'family': 'unclassified', 'attributes': {},
                          'analysis_status': 'unclassified', 'profile': None}
                error = None
                if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
                    result['analysis_status'] = 'conflict'
                    error = 'Multiple profiles have equal priority'
                elif candidates:
                    p = candidates[0]
                    try:
                        result.update(attributes=normalize(p, data), family=p.family, context=p.context,
                                      profile={'id': p.id, 'version': p.version}, analysis_status='ready')
                    except (ValueError, TypeError, OverflowError) as exc:
                        result['analysis_status'] = 'failed'
                        error = str(exc)
                result['analysis_error'] = error
                result['event_name'], result['source_name'] = display_names(data)
                result['event_family'], result['event_type'] = data['event_type'], type_key(data)
                from .enrich import enrich
                extracted = enrich(self, key, data, result)
                for kind, id, name in (('event_type', result['event_type'], result['event_label']),
                                       ('source', data['source_id'], result['source_name'])):
                    if name and self._names.get((data['site_id'], kind, id)) != name:
                        self.db.execute('INSERT INTO names VALUES(?,?,?,?) ON CONFLICT(site_id,kind,id) DO UPDATE SET name=excluded.name',
                                        (data['site_id'], kind, id, name))
                        self._names[(data['site_id'], kind, id)] = name
                old = self.db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
                if old is None or embedding_text(json.loads(old['body'])) != embedding_text(result):
                    self.db.execute('DELETE FROM vectors WHERE key=?', (key,))
                    if self.pgvector:
                        self.db.execute('DELETE FROM embeddings WHERE key=?', (key,))
                self.db.execute('''INSERT INTO records(key,site_id,source_id,kind,family,state,occurred_at,updated_at,body,event_type,event_family)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET site_id=excluded.site_id,source_id=excluded.source_id,
                    kind=excluded.kind,family=excluded.family,state=excluded.state,occurred_at=excluded.occurred_at,
                    updated_at=excluded.updated_at,body=excluded.body,event_type=excluded.event_type,event_family=excluded.event_family''',
                    (key, data['site_id'], data['source_id'], data['kind'], result['family'], data['state'],
                     data['occurred_at'], data['updated_at'], dumps(result), result['event_type'], result['event_family']))
                content = ' '.join([data['message'], data['description'], data['event_type'], data['location'],
                                    result['event_name'], result['source_name'], data['state'], data['priority'],
                                    result['family'], dumps(result['attributes']), extracted])
                # Folded copy: accents, 'đ' and word order do not matter when searching.
                self.db.execute('INSERT INTO record_fts VALUES(?,?)', (key, content + ' ' + fold(content)))
                self.db.execute('UPDATE inbox SET status=?,error=? WHERE key=?', ('failed' if error else 'done', error, key))
                fields = sorted(data['payload'])
                self.db.execute('''INSERT INTO catalog VALUES(?,?,?,?,?,?) ON CONFLICT(site_id,event_type,source_id)
                    DO UPDATE SET fields=excluded.fields,
                    first_seen=CASE WHEN excluded.first_seen < catalog.first_seen THEN excluded.first_seen ELSE catalog.first_seen END,
                    last_seen=CASE WHEN excluded.last_seen > catalog.last_seen THEN excluded.last_seen ELSE catalog.last_seen END''',
                    (data['site_id'], result['event_type'], data['source_id'], data['occurred_at'], data['occurred_at'], dumps(fields)))
            return len(rows)

    def search(self, query, grants, facets=False, **filters):
        request = SearchRequest(query=query, **filters)
        clause, args = grant_clause(grants)
        clauses = [clause]
        for name in ('site_id', 'source_id', 'kind', 'family', 'state'):
            value = getattr(request, name)
            if value is not None:
                clauses.append(f'{name}=?')
                args.append(value)
        if request.event_type:
            clauses.append('event_type=?')
            args.append(request.event_type)
        for column, values in (('event_type', request.event_types), ('source_id', request.source_ids)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' * len(values))})")
                args.extend(values)
        for attr, sign in [('start', '>='), ('end', '<=')]:
            value = getattr(request, attr)
            if value:
                clauses.append(f'occurred_at {sign} ?')
                args.append(value.isoformat(timespec='microseconds').replace('+00:00', 'Z'))
        if request.entities:
            clauses.append(f"key IN (SELECT key FROM attributes WHERE role IN ('person','plate','watchlist') AND folded IN ({','.join('?' * len(request.entities))}))")
            args.extend(request.entities)
        if request.facts:
            # One set operation over the attribute index instead of a nested IN per fact.
            clauses.append('key IN (' + ' INTERSECT '.join('SELECT key FROM attributes WHERE role=? AND folded=?' for _ in request.facts) + ')')
            for role, value in request.facts:
                args.extend([role, fold(value)])
        tokens = re.findall(r'\w+', fold(query), flags=re.UNICODE)
        if tokens:
            if self.dialect == 'postgresql':
                clauses.append("key IN (SELECT key FROM record_fts WHERE to_tsvector('simple',content) @@ plainto_tsquery('simple',?))")
                args.append(' '.join(tokens[:64]))
            else:
                clauses.append('key IN (SELECT key FROM record_fts WHERE record_fts MATCH ?)')
                args.append(' AND '.join('"' + token + '"' for token in tokens[:64]))
        where = ' AND '.join(clauses)
        # The matching set is computed once into a temporary table; count, page and facets all read it instead of each
        # re-running the whole filter (and, collapsed, the occurrence grouping) over every match.
        if request.latest_per_source:
            # Only the newest match of each camera: "where was this person last seen", at most one row per camera.
            fill = (f'SELECT key FROM (SELECT key, row_number() OVER (PARTITION BY site_id, source_id ORDER BY occurred_at DESC, key DESC) '
                    f'AS newest FROM records WHERE {where}) ranked WHERE newest=1')
        elif request.collapse:
            # One row per occurrence: an event, its alarm and repeats at the same camera count once.
            fill = f'SELECT DISTINCT l.head AS key FROM record_links l WHERE l.key IN (SELECT key FROM records WHERE {where})'
        else:
            fill = f'SELECT key FROM records WHERE {where}'
        postgres = self.dialect == 'postgresql'
        with self.reading() as db:
            if not postgres:
                db.execute('DROP TABLE IF EXISTS temp.search_hits')
            try:
                db.execute(('CREATE TEMP TABLE search_hits ON COMMIT DROP AS ' if postgres else 'CREATE TEMP TABLE search_hits AS ') + fill, args)
                total = db.execute('SELECT count(*) FROM search_hits').fetchone()[0]
                # Order and page on (time, key) alone; only the requested page's bodies are read.
                rows = db.execute(
                    'SELECT r.body FROM records r JOIN (SELECT r2.key FROM records r2 JOIN search_hits h ON h.key=r2.key '
                    'ORDER BY r2.occurred_at DESC, r2.key LIMIT ? OFFSET ?) page ON page.key=r.key ORDER BY r.occurred_at DESC, r.key',
                    (request.limit, request.offset)).fetchall()
                items = [json.loads(r['body']) for r in rows]
                if request.collapse or request.latest_per_source:
                    from .enrich import episode_stats
                    keys = [i['key'] for i in items]
                    # Per-camera rows are the newest record of their occurrence, not its head: report the occurrence.
                    heads = dict(db.execute(f"SELECT key, head FROM record_links WHERE key IN ({','.join('?' * len(keys))})", keys).fetchall()) if keys else {}
                    stats = episode_stats(self, sorted(set(heads.values()) | set(keys)), db)
                    for i in items:
                        i['episode'] = stats.get(heads.get(i['key'], i['key']))
                result = {'total': total, 'items': items, 'limit': request.limit, 'offset': request.offset}
                if facets and total:
                    people = db.execute(
                        "SELECT a.folded, max(a.value) AS name, count(DISTINCT a.key) AS n FROM attributes a JOIN search_hits h "
                        "ON h.key=a.key WHERE a.role='person' GROUP BY a.folded ORDER BY n DESC LIMIT 8").fetchall()
                    result['facets'] = {
                        'people': [{'id': r[0], 'name': self._entity_name(db, r[0], r[1]), 'count': r[2]} for r in people],
                        'event_types': self._facet(db, 'SELECT r.site_id, r.event_type AS id, count(*) AS n FROM records r JOIN search_hits h '
                                                       'ON h.key=r.key GROUP BY r.site_id, r.event_type ORDER BY n DESC LIMIT 8', 'event_type'),
                        'sources': self._facet(db, 'SELECT r.site_id, r.source_id AS id, count(*) AS n FROM records r JOIN search_hits h '
                                                   'ON h.key=r.key GROUP BY r.site_id, r.source_id ORDER BY n DESC LIMIT 8', 'source')}
                return result
            finally:
                if not postgres:
                    db.execute('DROP TABLE IF EXISTS temp.search_hits')

    def _entity_name(self, db, folded, fallback):
        row = db.execute("SELECT name FROM names WHERE kind='person' AND id=?", (folded,)).fetchone()
        return row[0] if row else fallback

    def _facet(self, db, query, kind):
        rows = db.execute(query).fetchall()
        out = []
        for r in rows:
            name = db.execute('SELECT name FROM names WHERE site_id=? AND kind=? AND id=?', (r['site_id'], kind, r['id'])).fetchone()
            out.append({'id': r['id'], 'name': name[0] if name else r['id'], 'count': r['n']})
        return out

    def has_key(self, key):
        with self.reading() as db:
            return db.execute('SELECT 1 FROM inbox WHERE key=?', (key,)).fetchone() is not None

    def records_by_keys(self, keys):
        """Processed records in the order requested; keys still waiting in the inbox are skipped."""
        out = []
        with self.reading() as db:
            for key in keys:
                row = db.execute('SELECT body FROM records WHERE key=?', (key,)).fetchone()
                if row:
                    out.append(json.loads(row['body']))
        return out

    def replay(self, site_id, actor='system'):
        with self.lock, self.db:
            count = self.db.execute("UPDATE inbox SET status='pending',error=NULL WHERE json_extract(body,'$.site_id')=? AND status!='deleted'", (site_id,)).rowcount
            self.audit(actor, 'replay', {'site_id': site_id, 'count': count})
            return count

    def status(self):
        with self.reading() as db:
            states = dict(db.execute('SELECT status,count(*) FROM inbox GROUP BY status').fetchall())
            return {**{'pending': 0, 'done': 0, 'failed': 0, 'deleted': 0}, **states,
                    'records': db.execute('SELECT count(*) FROM records').fetchone()[0],
                    'last_received': db.execute('SELECT max(received) FROM inbox').fetchone()[0]}

    INDEX_VERSION = 4

    def upgrade_index(self):
        """Re-process stored envelopes once when the searchable content format changes."""
        if int(self.checkpoint('index_version') or 1) >= self.INDEX_VERSION:
            return 0
        with self.lock, self.db:
            count = self.db.execute("UPDATE inbox SET status='pending',error=NULL WHERE status!='deleted'").rowcount
        self.checkpoint('index_version', str(self.INDEX_VERSION))
        return count

    def catalog(self):
        with self.reading() as db:
            rows = db.execute('''SELECT c.*, e.name AS event_name, s.name AS source_name FROM catalog c
                LEFT JOIN names e ON e.site_id=c.site_id AND e.kind='event_type' AND e.id=c.event_type
                LEFT JOIN names s ON s.site_id=c.site_id AND s.kind='source' AND s.id=c.source_id
                ORDER BY c.last_seen DESC''').fetchall()
            return [{**dict(zip(r.keys(), r)), 'fields': json.loads(r['fields'])} for r in rows]

    def sources(self, grants):
        """Sources and event types the caller may search, with display names."""
        clause, args = grant_clause(grants, 'c.')
        with self.reading() as db:
            rows = db.execute(f'''SELECT c.site_id, c.source_id, c.event_type, max(c.last_seen) AS last_seen,
                max(e.name) AS event_name, max(s.name) AS source_name FROM catalog c
                LEFT JOIN names e ON e.site_id=c.site_id AND e.kind='event_type' AND e.id=c.event_type
                LEFT JOIN names s ON s.site_id=c.site_id AND s.kind='source' AND s.id=c.source_id
                WHERE {clause} GROUP BY c.site_id, c.source_id, c.event_type''', args).fetchall()
        sources, events = {}, {}
        for r in rows:
            sources.setdefault((r['site_id'], r['source_id']), r['source_name'] or r['source_id'])
            events.setdefault(r['event_type'], r['event_name'] or r['event_type'])
        sites = sorted({site for site, _ in sources})
        people = {}
        if sites:
            with self.reading() as db:
                for kind, id, name in db.execute(
                        f"SELECT kind, id, name FROM names WHERE kind IN ('person','plate','watchlist') AND site_id IN ({','.join('?' * len(sites))})", sites):
                    people.setdefault((kind, id), name)
        return {'sites': sites,
                'entities': [{'kind': k, 'id': i, 'name': n} for (k, i), n in sorted(people.items(), key=lambda kv: kv[1].lower())],
                'sources': sorted(({'site_id': k[0], 'source_id': k[1], 'name': v} for k, v in sources.items()), key=lambda x: x['name'].lower()),
                'event_types': sorted(({'id': k, 'name': v} for k, v in events.items()), key=lambda x: x['name'].lower())}

    def checkpoint(self, name, value=None):
        with self.lock, self.db:
            if value is not None:
                self.db.execute('INSERT INTO checkpoints VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value', (name, value))
            row = self.db.execute('SELECT value FROM checkpoints WHERE name=?', (name,)).fetchone()
            return row[0] if row else None

    def data_span(self):
        """How much is stored, for the retention setting: oldest and newest record, count and database size."""
        with self.reading() as db:
            oldest, newest, count = db.execute('SELECT min(occurred_at), max(occurred_at), count(*) FROM records').fetchone()
            pictures = db.execute('SELECT count(*) FROM media').fetchone()[0]
            if self.dialect == 'postgresql':
                size = db.execute('SELECT pg_database_size(current_database())').fetchone()[0]
            else:
                size = db.execute('SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()').fetchone()[0]
        return {'oldest': oldest, 'newest': newest, 'records': count, 'pictures': pictures, 'size_bytes': size}

    CONTENT_TABLES = ('records', 'record_fts', 'vectors', 'attributes', 'record_links', 'media')

    def purge(self, before, actor='system', batch=2000):
        """Delete content older than `before` but keep identity/revision tombstones against replay.

        Expired records are found through the occurred_at index and removed a batch at a time, so the writer lock is
        held only briefly and ingestion keeps flowing; records already purged are never visited again."""
        cutoff = Envelope.timezone_required(datetime.fromisoformat(before.replace('Z', '+00:00'))).isoformat(timespec='microseconds').replace('+00:00', 'Z')
        total = 0
        while True:
            with self.reading() as db:
                keys = [r[0] for r in db.execute('SELECT key FROM records WHERE occurred_at<? ORDER BY occurred_at LIMIT ?', (cutoff, batch))]
                if not keys:
                    # Envelopes that never became records (failed processing) still hold payload and pictures.
                    keys = [r[0] for r in db.execute("SELECT key FROM inbox WHERE status='failed' AND received<? LIMIT ?", (cutoff, batch))]
            if not keys:
                break
            marks = ','.join('?' * len(keys))
            with self.lock, self.db:
                bodies = self.db.execute(f'SELECT key,body FROM inbox WHERE key IN ({marks})', keys).fetchall()
                for table in self.CONTENT_TABLES:
                    self.db.execute(f'DELETE FROM {table} WHERE key IN ({marks})', keys)
                for row in bodies:
                    data = json.loads(row['body'])
                    data.update(deleted=True, message='', description='', payload={}, location='')
                    self.db.execute("UPDATE inbox SET body=?,status='deleted',error=NULL WHERE key=?", (dumps(data), row['key']))
            total += len(keys)
        with self.lock, self.db:
            self.audit(actor, 'retention.purge', {'before': cutoff, 'count': total})
        return total

    def wipe(self, actor):
        """Delete every event, alarm, picture and derived index. Settings (Milestone and Active Guard connections,
        profiles, learned rules), the audit log and the Active Guard import cursors are kept, so nothing old is
        imported again; only data arriving from now on is stored."""
        tables = ['inbox', *self.CONTENT_TABLES, 'catalog', 'names', 'reconciliation'] + (['embeddings'] if self.pgvector else [])
        with self.lock, self.db:
            counts = {'records': self.db.execute('SELECT count(*) FROM records').fetchone()[0],
                      'pictures': self.db.execute('SELECT count(*) FROM media').fetchone()[0]}
            if self.dialect == 'postgresql':
                self.db.execute('TRUNCATE ' + ', '.join(tables))
            else:
                for table in tables:
                    self.db.execute(f'DELETE FROM {table}')
            for name in [r[0] for r in self.db.execute('SELECT name FROM checkpoints WHERE name LIKE ? OR name LIKE ?',
                                                                         ('activeguard:%:imported', 'activeguard:%:face_photos'))]:
                self.db.execute('UPDATE checkpoints SET value=? WHERE name=?', ('0', name))
            self._names = {}
            self.audit(actor, 'data.wipe', counts)
        if self.dialect == 'sqlite':
            self.db.execute('VACUUM')      # give the file space back; PostgreSQL TRUNCATE already did
        return counts
