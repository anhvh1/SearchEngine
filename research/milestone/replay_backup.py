"""Acceptance on real data: replay alarms/events from restored Milestone backups through the search engine.

Builds envelopes in the same shape the Event Server plugin sends, ingests them into a scratch database and
checks that every person can be found however the name is typed. Real names stay on this machine.

    python research/milestone/replay_backup.py --db scratch.db
"""
import argparse
import collections
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pyodbc

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from search_engine.enrich import learn_rules  # noqa: E402
from search_engine.extract import _xml, entity_key, split_code  # noqa: E402
from search_engine.query import fold  # noqa: E402
from search_engine.store import Engine  # noqa: E402

DATABASES = ('Surveillance_Analysis', 'MilestoneResearch_20260915')


def iso(value):
    return value.isoformat(timespec='microseconds') + 'Z'


def alarms(cursor):
    rows = cursor.execute('''SELECT a.Alarm_T001_ID, a.Description, a.Location, a.StateName, a.CategoryName, a.AssignedTo,
        h.ID, h.Name, h.Message, h.Type, h.CustomTag, h.MessageId, h.Priority, h.PriorityName, h.Timestamp,
        s.Name AS SourceName, f.ObjectId, f.Kind, f.ParentId
        FROM Central.Alarm_T001 a JOIN Central.T001_EventHeader_T002 h ON h.Alarm_T001_ID=a.Alarm_T001_ID
        LEFT JOIN Central.T002_Source_T003 s ON s.T001_EventHeader_T002_ID=h.T001_EventHeader_T002_ID
        LEFT JOIN Central.T003_FQID_T004 f ON f.T002_Source_T003_ID=s.T002_Source_T003_ID''').fetchall()
    objects = collections.defaultdict(list)
    for r in cursor.execute('''SELECT l.Alarm_T001_ID, o.Name, o.Type, o.Value, o.Confidence, o.Color FROM Central.T014_Object_T015 o
        JOIN Central.T001_ObjectList_T014 l ON l.T001_ObjectList_T014_ID=o.T001_ObjectList_T014_ID'''):
        objects[r[0]].append({k: v for k, v in zip(('Name', 'Type', 'Value', 'Confidence', 'Color'), r[1:]) if v is not None})
    rules = collections.defaultdict(list)
    for r in cursor.execute('''SELECT l.Alarm_T001_ID, r.ID, r.Name, r.Type FROM Central.T006_Rule_T007 r
        JOIN Central.T001_RuleList_T006 l ON l.T001_RuleList_T006_ID=r.T001_RuleList_T006_ID'''):
        rules[r[0]].append({'ID': r[1], 'Name': r[2], 'Type': r[3]})
    for r in rows:
        header = {'ID': r.ID, 'Name': r.Name, 'Message': r.Message, 'Type': r.Type, 'CustomTag': r.CustomTag,
                  'MessageId': r.MessageId, 'Priority': r.Priority, 'PriorityName': r.PriorityName,
                  'Source': {'Name': r.SourceName, 'FQID': {'ObjectId': r.ObjectId, 'Kind': r.Kind, 'ParentId': r.ParentId}}}
        payload = {'header': header, 'category': r.CategoryName, 'assignedTo': r.AssignedTo}
        if objects[r.Alarm_T001_ID]:
            payload['objects'] = objects[r.Alarm_T001_ID]
        if rules[r.Alarm_T001_ID]:
            payload['rules'] = rules[r.Alarm_T001_ID]
        yield {'site_id': 'main', 'kind': 'alarm', 'source_guid': r.ID, 'source_id': r.ObjectId or 'unknown',
               'event_type': r.MessageId or r.Type or 'alarm', 'occurred_at': iso(r.Timestamp), 'updated_at': iso(r.Timestamp),
               'message': r.Message or '', 'description': r.Description or '', 'state': r.StateName or '',
               'priority': r.PriorityName or '', 'location': r.Location or '',
               'camera_id': r.ObjectId if (r.Kind or '').lower() == '5135ba21-f1dc-4321-806a-6ce2017343c0' else None,
               'payload': payload}


def events(cursor):
    for (xml,) in cursor.execute('SELECT Data FROM Central.Event_Inactive_Data UNION ALL SELECT Data FROM Central.Event_Active_Data'):
        (_, event), = _xml(ET.fromstring(xml)).items()
        header = event.get('EventHeader') or {}
        fqid = (header.get('Source') or {}).get('FQID') or {}
        yield {'site_id': 'main', 'kind': 'event', 'source_guid': header['ID'], 'source_id': fqid.get('ObjectId') or 'unknown',
               'event_type': header.get('MessageId') or header.get('Type') or 'event',
               'occurred_at': header['Timestamp'], 'updated_at': header['Timestamp'], 'message': header.get('Message') or '',
               'description': event.get('Description') or '' if isinstance(event.get('Description'), str) else '',
               'camera_id': fqid.get('ObjectId') if (fqid.get('Kind') or '').lower() == '5135ba21-f1dc-4321-806a-6ce2017343c0' else None,
               'payload': {'header': header, 'event': event}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True, help='Scratch SQLite file or PostgreSQL DSN (not the production database)')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    engine = Engine(args.db)
    expected = collections.Counter()   # person entity -> alarm count (SQL ground truth)
    spellings = collections.defaultdict(set)
    total = 0
    for name in DATABASES:
        cursor = pyodbc.connect(f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER=.;DATABASE={name};Trusted_Connection=yes').cursor()
        for env in list(alarms(cursor)) + list(events(cursor)):
            engine.ingest(env)
            total += 1
        for (value,) in cursor.execute("SELECT ObjectValue FROM Central.Alarms WHERE ObjectValue IS NOT NULL AND ObjectValue<>''"):
            expected[entity_key(value)] += 1
            spellings[entity_key(value)].add(split_code(value)[0])
        for (text,) in cursor.execute("SELECT Name FROM Central.Alarms WHERE Name LIKE N'Phát hiện % tại %' AND Name NOT LIKE N'%người lạ%'"):
            person = re.match(r'Phát hiện (.+?) tại', text).group(1)
            expected[entity_key(person)] += 1
            spellings[entity_key(person)].add(person)
    while engine.process_pending(500):
        pass
    learned = learn_rules(engine)
    while engine.process_pending(500):
        pass
    print(f'Ingested {total} envelopes; learned rules for {len(learned)} event types.')

    failures, rows = 0, []
    for key, count in sorted(expected.items(), key=lambda kv: -kv[1]):
        name = max(spellings[key], key=lambda s: (fold(s) != s, len(s)))
        variants = {'as written': name, 'no accents': fold(name), 'reversed': ' '.join(reversed(name.split()))}
        found = {label: engine.search(q, [['*', '*']], kind='alarm', limit=1)['total'] for label, q in variants.items()}
        by_entity = engine.search('', [['*', '*']], kind='alarm', entities=[key], limit=1)['total']
        ok = all(v >= count for v in found.values()) and by_entity == count
        failures += not ok
        rows.append((name, count, found, by_entity, 'OK' if ok else 'FAIL'))
    for name, count, found, by_entity, status in rows:
        print(f'{status:4} {name:28} expected {count:4}  text {found}  entity {by_entity}')
    faces = engine.search('', [['*', '*']], limit=1)['total']
    occurrences = engine.search('', [['*', '*']], limit=1, collapse=True)['total']
    print(f'Records {faces}; occurrences after merging event+alarm and repeats: {occurrences}')
    print('PASS' if not failures else f'{failures} FAILED')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
