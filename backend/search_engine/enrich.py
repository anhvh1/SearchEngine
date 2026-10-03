"""Persist extraction results: attributes, entities, episodes, media and learned rules.

All functions run inside the caller's transaction on Engine.db.
"""
import json
from datetime import datetime, timedelta

from .extract import (apply_template, classify, clean, entity_key, flatten, learn_template, looks_like_person,
                      slot_roles, split_code)
from difflib import SequenceMatcher
from .query import fold
from .store import now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS attributes(key TEXT NOT NULL, role TEXT, name TEXT, value TEXT, folded TEXT, number REAL);
CREATE INDEX IF NOT EXISTS attributes_key ON attributes(key);
CREATE INDEX IF NOT EXISTS attributes_role ON attributes(role, folded);
CREATE TABLE IF NOT EXISTS record_links(key TEXT PRIMARY KEY, site_id TEXT, source_id TEXT, signature TEXT,
  occurred_at TEXT, kind TEXT, head TEXT);
CREATE INDEX IF NOT EXISTS record_links_chain ON record_links(site_id, source_id, signature, occurred_at);
CREATE INDEX IF NOT EXISTS record_links_head ON record_links(head);
CREATE TABLE IF NOT EXISTS media(key TEXT PRIMARY KEY, mime TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS rules(site_id TEXT, event_type TEXT, field TEXT, version INTEGER, template TEXT, roles TEXT,
  source TEXT, status TEXT, samples TEXT, created TEXT, PRIMARY KEY(site_id, event_type, field, version));
'''
ENTITY_ROLES = ('person', 'plate', 'watchlist')
FIELDS = {'name': lambda d, name: name, 'message': lambda d, name: d.get('message'), 'description': lambda d, name: d.get('description')}


def active_rules(engine, site, scopes):
    marks = ','.join('?' * len(scopes))
    return [dict(zip(r.keys(), r)) for r in engine.db.execute(
        f"SELECT * FROM rules WHERE site_id=? AND event_type IN ({marks}) AND status='active'", (site, *scopes))]


def best_match(rules, field, text):
    """Most specific matching template (most constant words) for one field."""
    best = None
    for rule in rules:
        if rule['field'] != field:
            continue
        values = apply_template(rule['template'], text)
        if values is not None:
            weight = len(rule['template'].replace('{}', ' ').split())
            if best is None or weight > best[0]:
                best = (weight, rule, values)
    return best


def pop_images(node, found):
    """Remove base64 snapshots from the payload copy kept in records; keep the first for display."""
    if isinstance(node, dict):
        for k in list(node):
            if k.lower() == 'image' and isinstance(node[k], str) and len(node[k]) > 200:
                found.append(node.pop(k))
            else:
                pop_images(node[k], found)
    elif isinstance(node, list):
        for v in node:
            pop_images(v, found)


def enrich(engine, key, data, result):
    """Extract, store and return extra searchable text for one processed record."""
    db = engine.db
    images = []
    pop_images(result['payload'], images)
    db.execute('DELETE FROM media WHERE key=?', (key,))
    if images:
        db.execute('INSERT INTO media VALUES(?,?,?)', (key, 'image/jpeg', images[0]))
    result['has_image'] = bool(images)  # updated for the whole occurrence in episode_stats

    attrs, facts = classify(flatten(data), data, result['event_name'])
    label = result['event_name']
    rules = active_rules(engine, data['site_id'], (result['event_type'], data['event_type']))
    for field in FIELDS:
        text = FIELDS[field](data, result['event_name'])
        match = best_match(rules, field, text) if text else None
        if not match:
            continue
        _, rule, values = match
        for i, (value, role) in enumerate(zip(values, json.loads(rule['roles']))):
            if role == 'person' and not looks_like_person(split_code(value)[0]):
                role = 'value'  # "người lạ" in a name slot is not a person
            attrs.append({'path': f'template.{field}.{i}', 'key': role, 'value': value, 'role': role})
        if field == 'name':
            label = rule['template'].replace('{}', '…')
    result['event_label'] = label

    db.execute('DELETE FROM attributes WHERE key=?', (key,))
    entities = {}
    for a in attrs:
        value, role = a['value'], a.get('role')
        number = value if isinstance(value, (int, float)) else None
        text = clean(value) if not isinstance(value, (int, float)) else str(value)
        if role == 'person':
            text, code = split_code(text)
            if code:
                db.execute('INSERT INTO attributes VALUES(?,?,?,?,?,?)', (key, 'person_code', a['key'], code, code, None))
        folded = entity_key(text) if role == 'person' else fold(text)
        db.execute('INSERT INTO attributes VALUES(?,?,?,?,?,?)', (key, role, str(a['key'])[:128], text[:1000], folded[:1000], number))
        if role in ENTITY_ROLES and folded:
            entities.setdefault(role, {})[folded] = _remember_entity(engine, data['site_id'], role, folded, text)
    for name, value in facts.items():
        for v in value if isinstance(value, list) else [value]:
            db.execute('INSERT INTO attributes VALUES(?,?,?,?,?,?)', (key, name, name, v, fold(v), None))
    result['facts'] = {**facts, **{role + 's': sorted(v.values()) for role, v in entities.items()}}

    signature = result['event_type'] + '|' + ';'.join(sorted(k for v in entities.values() for k in v))
    if (data.get('payload') or {}).get('activeguard', {}).get('type') in ('people', 'vehicle', 'face'):
        signature += '|' + key   # each best shot is a different person or vehicle: never merge them
    link(engine, key, data, signature)
    words = [str(a['value']) for a in attrs if not isinstance(a['value'], (int, float))]
    return ' '.join(words + [label, *facts.get('action', []), 'nguoi la' if facts.get('identity_status') == 'unknown' else ''])


def _remember_entity(engine, site, role, folded, display):
    """Prefer the spelling with Vietnamese diacritics as the display name."""
    cache = engine._names
    current = cache.get((site, role, folded))
    if current is None:
        row = engine.db.execute('SELECT name FROM names WHERE site_id=? AND kind=? AND id=?', (site, role, folded)).fetchone()
        current = row[0] if row else None
    better = current is None or (fold(display) != display and fold(current) == current)
    if better and current != display:
        engine.db.execute('INSERT INTO names VALUES(?,?,?,?) ON CONFLICT(site_id,kind,id) DO UPDATE SET name=excluded.name',
                          (site, role, folded, display))
        current = display
    cache[(site, role, folded)] = current
    return current


# ---------- episodes: one occurrence = event + alarm + repeats of the same thing at the same camera ----------
def _t(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def link(engine, key, data, signature):
    db, gap, span = engine.db, engine.episode_gap, engine.episode_span
    db.execute('DELETE FROM record_links WHERE key=?', (key,))
    db.execute('INSERT INTO record_links VALUES(?,?,?,?,?,?,?)',
               (key, data['site_id'], data['source_id'], signature, data['occurred_at'], data['kind'], key))
    t = _t(data['occurred_at'])
    low = (t - timedelta(seconds=3 * span)).isoformat().replace('+00:00', 'Z')
    high = (t + timedelta(seconds=3 * span)).isoformat().replace('+00:00', 'Z')
    rows = db.execute('''SELECT key, occurred_at, head FROM record_links WHERE site_id=? AND source_id=? AND signature=?
        AND occurred_at>=? AND occurred_at<=? ORDER BY occurred_at, key''',
                      (data['site_id'], data['source_id'], signature, low, high)).fetchall()
    rows = [(r[0], _t(r[1]), r[2]) for r in rows]
    if not rows:
        return
    # Regroup the chain around the new record; the window's first record keeps its existing head.
    first_head = rows[0][2]
    head_row = db.execute('SELECT occurred_at FROM record_links WHERE key=?', (first_head,)).fetchone()
    head, head_time = first_head, _t(head_row[0]) if head_row else rows[0][1]
    previous = rows[0][1]
    updates = []
    for i, (k, when, old) in enumerate(rows):
        if i and ((when - previous).total_seconds() > gap or (when - head_time).total_seconds() > span):
            head, head_time = k, when
        previous = when
        if old != head:
            updates.append((head, k))
    for h, k in updates:
        db.execute('UPDATE record_links SET head=? WHERE key=?', (h, k))


def episode_stats(engine, heads, db=None):
    if not heads:
        return {}
    db = db or engine.db
    marks = ','.join('?' * len(heads))
    rows = db.execute(f'''SELECT head, count(*) AS n, min(occurred_at) AS first, max(occurred_at) AS last,
        sum(CASE WHEN kind='alarm' THEN 1 ELSE 0 END) AS alarms FROM record_links WHERE head IN ({marks}) GROUP BY head''', heads).fetchall()
    pictures = {r[0] for r in db.execute(f'''SELECT DISTINCT l.head FROM record_links l JOIN media m ON m.key=l.key
        WHERE l.head IN ({marks})''', heads)}
    return {r[0]: {'count': r[1], 'first': r[2], 'last': r[3], 'alarms': r[4], 'image': r[0] in pictures} for r in rows}


def image(engine, key, db=None):
    """Snapshot of the record or of any record in the same occurrence (the event usually carries it)."""
    db = db or engine.db
    row = db.execute('SELECT mime, data FROM media WHERE key=?', (key,)).fetchone() or db.execute(
        '''SELECT m.mime, m.data FROM media m JOIN record_links l ON l.key=m.key
           WHERE l.head=(SELECT head FROM record_links WHERE key=?) ORDER BY l.occurred_at LIMIT 1''', (key,)).fetchone()
    return (row[0], row[1]) if row else None


# ---------- rules learned per event type ----------
def cluster_templates(texts, min_samples=2):
    """Group similar texts and learn one template per group: 'Phát hiện {} tại Camera {}', 'Phát hiện người lạ tại {}'."""
    distinct = list(dict.fromkeys(clean(t) for t in texts if t and clean(t)))
    templates = []
    for text in distinct:
        if any(apply_template(t, text) is not None for t in templates):
            continue
        words = text.split()
        similar = [o for o in distinct if o != text and SequenceMatcher(None, words, o.split(), autojunk=False).ratio() >= 0.5
                   and len(o.split()) >= 2]
        best = None
        for other in similar:
            template = learn_template([text, other], min_samples)
            if not template:
                continue
            members = [o for o in distinct if apply_template(template, o) is not None]
            fixed = len(template.replace('{}', ' ').split())
            score = (len(members), fixed)
            if len(members) >= min_samples and (best is None or score > best[0]):
                best = (score, template)
        if best:
            templates.append(best[1])
    return templates


def learn_rules(engine, sample_size=300, min_samples=2):
    """Learn text templates per event kind and per shared event id; returns the scopes that changed.

    Sampling and clustering run on a read connection without the writer lock (ingestion keeps flowing); only the
    new rules and the re-index request are written under it."""
    proposals = []
    with engine.reading() as db:
        scopes = [(r[0], r[1], 'event_type') for r in db.execute('SELECT DISTINCT site_id, event_type FROM catalog')]
        scopes += [(r[0], r[1], 'event_family') for r in db.execute(
            'SELECT DISTINCT site_id, event_family FROM records WHERE event_family IS NOT NULL') if r[1]]
        for site, scope, column in scopes:
            rows = db.execute(f'SELECT body FROM records WHERE site_id=? AND {column}=? ORDER BY occurred_at DESC LIMIT ?',
                              (site, scope, sample_size)).fetchall()
            bodies = [json.loads(r[0]) for r in rows]
            for field in FIELDS:
                texts = [t for t in (FIELDS[field](b, _raw_name(b)) for b in bodies) if t]
                known = {r[0] for r in db.execute('SELECT template FROM rules WHERE site_id=? AND event_type=? AND field=?', (site, scope, field))}
                for template in cluster_templates(texts, min_samples):
                    if template not in known:
                        matched = [t for t in dict.fromkeys(texts) if apply_template(template, t) is not None]
                        proposals.append((site, scope, column, field, template, matched))
    changed = []
    if not proposals:
        return changed
    with engine.lock, engine.db:
        for site, scope, column, field, template, matched in proposals:
            if engine.db.execute('SELECT 1 FROM rules WHERE site_id=? AND event_type=? AND field=? AND template=?',
                                 (site, scope, field, template)).fetchone():
                continue    # learned meanwhile by another run
            version = engine.db.execute('SELECT coalesce(max(version),0)+1 FROM rules WHERE site_id=? AND event_type=? AND field=?',
                                        (site, scope, field)).fetchone()[0]
            engine.db.execute('INSERT INTO rules VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (site, scope, field, version, template, json.dumps(slot_roles(template, matched)), 'auto', 'active',
                               json.dumps(matched[:5], ensure_ascii=False), now()))
            engine.audit('system', 'rule.learned', {'site_id': site, 'event_type': scope, 'field': field, 'template': template})
            changed.append((site, scope, column))
        for site, scope, column in dict.fromkeys(changed):
            engine.db.execute(f"UPDATE inbox SET status='pending',error=NULL WHERE status!='deleted' AND key IN (SELECT key FROM records WHERE site_id=? AND {column}=?)",
                              (site, scope))
    return [(site, scope) for site, scope, _ in dict.fromkeys(changed)]


def _raw_name(body):
    header = (body.get('payload') or {}).get('header') or (body.get('payload') or {}).get('eventHeader') or {}
    return header.get('Name') or header.get('name')


def list_rules(engine):
    """One entry per distinct template; 'members' are the stored rules it stands for."""
    with engine.reading() as db:
        rows = [dict(zip(r.keys(), r)) for r in db.execute("SELECT * FROM rules WHERE status!='retired' ORDER BY created DESC")]
    grouped = {}
    for r in rows:
        entry = grouped.setdefault((r['template'], r['status']), {**r, 'roles': json.loads(r['roles']),
                                                                   'samples': json.loads(r['samples'] or '[]'), 'members': []})
        entry['members'].append({k: r[k] for k in ('site_id', 'event_type', 'field', 'version')})
    return list(grouped.values())


def set_rule_status(engine, site, event_type, field, version, status, actor):
    if status not in ('active', 'disabled'):
        raise ValueError('Status must be active or disabled')
    with engine.lock, engine.db:
        count = engine.db.execute('UPDATE rules SET status=? WHERE site_id=? AND event_type=? AND field=? AND version=?',
                                  (status, site, event_type, field, version)).rowcount
        if not count:
            raise ValueError('Rule not found')
        engine.audit(actor, 'rule.' + status, {'site_id': site, 'event_type': event_type, 'field': field, 'version': version})
        engine.db.execute("""UPDATE inbox SET status='pending',error=NULL WHERE status!='deleted' AND key IN (SELECT key FROM records
            WHERE site_id=? AND (event_type=? OR event_family=?))""", (site, event_type, event_type))
