"""Download a sample of Active Guard people best shots with Active Guard's own attribute scores (the answer key).

Reads through the Search Engine backend API with a normal sign-in; nothing is changed on the server.
Images show real people: they stay in ./sample (git-ignored) and must not be shared or committed.

    python fetch_sample.py --backend http://192.168.100.4:8765 --count 1000
"""
import argparse
import getpass
import hashlib
import json
import random
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='http://192.168.100.4:8765')
    ap.add_argument('--count', type=int, default=1000)
    ap.add_argument('--pool', type=int, default=6000, help='records to list before sampling, so the sample spans days and cameras')
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()

    out = HERE / 'sample'
    (out / 'images').mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.backend.rstrip('/'), timeout=60) as http:
        user = input('Milestone username: ')
        password = getpass.getpass('Password: ')
        r = http.post('/api/session', json={'username': user, 'password': password})
        if r.status_code != 200:
            raise SystemExit(f'Sign-in failed: {r.status_code} {r.text[:200]}')
        http.headers['Authorization'] = 'Bearer ' + r.json()['token']
        try:
            keys, offset = [], 0
            while len(keys) < args.pool:
                page = http.post('/api/search', json={'event_type': 'activeguard:people', 'limit': 200, 'offset': offset}).json()
                items = page.get('items', [])
                keys += [i['key'] for i in items if i.get('has_image')]
                offset += len(items)
                if not items or offset >= page.get('total', 0):
                    break
            print(f'{len(keys)} people best shots with images listed; sampling {min(args.count, len(keys))}')
            random.Random(args.seed).shuffle(keys)
            done = {json.loads(line)['key'] for line in (out / 'labels.jsonl').open(encoding='utf-8')} if (out / 'labels.jsonl').exists() else set()
            with (out / 'labels.jsonl').open('a', encoding='utf-8') as labels:
                for n, key in enumerate(keys[:args.count], 1):
                    if key in done:
                        continue
                    record = http.get(f'/api/records/{key}').json()
                    payload = record.get('payload') or {}
                    image = http.get(f'/api/records/{key}/image')
                    if image.status_code != 200 or not payload.get('scores'):
                        continue
                    name = hashlib.sha1(key.encode()).hexdigest()[:16] + '.jpg'
                    (out / 'images' / name).write_bytes(image.content)
                    labels.write(json.dumps({'key': key, 'image': name, 'camera': record.get('source_id'),
                                             'occurred_at': record.get('occurred_at'), 'scores': payload['scores'],
                                             'attributes': (payload.get('activeguard') or {}).get('attributes', {})},
                                            ensure_ascii=False) + '\n')
                    if n % 50 == 0:
                        print(f'  {n}/{args.count}')
        finally:
            http.delete('/api/session')
    print('Saved to', out)


if __name__ == '__main__':
    main()
