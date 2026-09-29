import argparse
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import Engine


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # People sign in with their Milestone account; these tokens are for the collector and emergency administration.
    config = {'database': str(path.parent.resolve() / 'search.db'), 'host': '0.0.0.0', 'port': 8765,
              'principals': [
                  {'name': 'administrator', 'token': secrets.token_urlsafe(40), 'roles': ['admin', 'reader'], 'grants': [['*', '*']]},
                  {'name': 'collector', 'token': secrets.token_urlsafe(40), 'roles': ['collector'], 'sites': ['*']}],
              'ai': {'ollama_url': 'http://127.0.0.1:11434', 'chat_model': '', 'embedding_model': '', 'whisper_model': ''}}
    with path.open('x', encoding='utf8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f'Configuration created: {path}. Tokens are in this local file; do not share it.')


def seed(engine):
    examples = [
        ('unknown-1', 'gate-01', 'FACEME.UNKNOWN_PERSON', 'Phát hiện người lạ tại Cổng chính', 'intrusion', 10),
        ('unknown-2', 'gate-01', 'FACEME.UNKNOWN_PERSON', 'Phát hiện người lạ tại Cổng chính', 'intrusion', 25),
        ('known-1', 'gate-01', 'FACEME.PERSON', 'Nhân viên mẫu đã đăng ký tại Cổng chính', 'identity', 40),
        ('access-1', 'door-02', 'SIPASS.DOOR_FORCED', 'Cửa kho mở cưỡng bức', 'access', 55),
        ('fire-1', 'bms-01', 'BMS.FIRE', 'Báo cháy thử nghiệm tại Tầng 2', 'fire', 70)]
    for id, source, event_type, message, family, minutes in examples:
        stamp = (datetime(2026, 9, 16, 8, tzinfo=timezone.utc) - timedelta(minutes=minutes)).isoformat()
        data = {'site_id': 'demo', 'kind': 'alarm', 'source_guid': id, 'source_id': source,
                'event_type': event_type, 'occurred_at': stamp, 'updated_at': stamp, 'message': message,
                'state': 'New', 'location': 'Khu vực mô phỏng', 'camera_id': source if source == 'gate-01' else None,
                'payload': {'zone': 'Cổng chính' if source == 'gate-01' else 'Kho / Tầng 2', 'synthetic': True}}
        profile = {'id': event_type.replace('.', '-'), 'name': event_type, 'version': 1, 'priority': 10,
                   'match': {'site_id': 'demo', 'event_type': event_type}, 'family': family,
                   'mapping': {'zone': {'path': 'payload.zone', 'type': 'string', 'required': True}},
                   'context': 'Dữ liệu mô phỏng dùng kiểm thử; không phải cảnh báo thực.'}
        if not any(p['id'] == profile['id'] for p in engine.list_profiles()):
            engine.save_profile(profile)
            engine.validate_profile(profile['id'], 1, [data])
            engine.activate_profile(profile['id'], 1)
        engine.ingest(data)
    engine.process_pending()


def main():
    parser = argparse.ArgumentParser(description='Milestone event search service')
    parser.add_argument('command', choices=['init', 'serve', 'seed', 'import', 'backfill', 'discover', 'openapi', 'iag-probe'])
    parser.add_argument('--user', default='')
    parser.add_argument('--hours', type=int, default=6)
    parser.add_argument('--config', default='config.local.json')
    parser.add_argument('--file')
    parser.add_argument('--site', default='lab')
    parser.add_argument('--url')
    args = parser.parse_args()
    if args.command == 'init':
        initialize(args.config)
        return
    if args.command == 'iag-probe':
        # Read-only diagnostic run by the operator: the password never leaves this process and is not stored.
        import getpass
        from .activeguard import probe
        if not args.url or not args.user:
            parser.error('--url and --user are required')
        password = os.environ.get('IAG_PASSWORD') or getpass.getpass(f'Mật khẩu Active Guard của {args.user}: ')
        out = args.file or 'iag-probe.json'
        report = probe(args.url, args.user, password, out, hours=args.hours)
        print(f"Đã ghi {out}: {len(report.get('cameras', []))} camera; " +
              ', '.join(f"{k}: {v.get('total_in_window', v) if isinstance(v, dict) else v}" for k, v in report.get('samples', {}).items()))
        return
    config = json.loads(Path(args.config).read_text(encoding='utf8'))
    if args.command in ('serve', 'openapi'):
        from .api import create_app
        app = create_app(config)
        if args.command == 'openapi':
            Path(args.file or 'docs/openapi.json').write_text(json.dumps(app.openapi(), indent=2), encoding='utf8')
            app.state.engine.close()
        else:
            import uvicorn
            uvicorn.run(app, host=config.get('host', '127.0.0.1'), port=config.get('port', 8765), workers=1)
        return
    engine = Engine(config['database'])
    try:
        if args.command == 'seed':
            seed(engine)
            print('Seeded 5 synthetic alarms; repeat runs do not duplicate records.')
        elif args.command == 'import':
            count = 0
            with Path(args.file).open(encoding='utf8') as f:
                for line in f:
                    if line.strip():
                        engine.ingest(json.loads(line))
                        count += 1
            while engine.process_pending():
                pass
            print(f'Imported {count} envelopes.')
        else:
            from .connectors import MilestoneRest
            token = os.environ.get('MILESTONE_TOKEN')
            if not token or not args.url:
                parser.error('--url and MILESTONE_TOKEN environment variable are required')
            connector = MilestoneRest(args.url, token, args.site, engine)
            try:
                if args.command == 'backfill':
                    print(f'Backfilled {connector.backfill()} alarms.')
                else:
                    Path(args.file or 'data/discovery.json').write_text(json.dumps(connector.discovery(), ensure_ascii=False, indent=2), encoding='utf8')
            finally:
                connector.close()
    finally:
        engine.close()


if __name__ == '__main__':
    main()
