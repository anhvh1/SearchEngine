"""Explicitly configured schedules with persisted checkpoints and pending-job coalescing."""
from datetime import datetime, timezone, timedelta
from .store import dumps, now


def schedule(engine, config, timestamp, sites=None):
    count = 0
    for site, settings in (config.get('milestone', {}) if sites is None else sites).items():
        interval = max(30, int(settings.get('sync_interval_seconds', 300)))
        name = f'{site}:last_scheduled'
        previous = float(engine.checkpoint(name) or 0)
        if timestamp - previous < interval:
            continue
        with engine.lock, engine.db:
            pending = engine.db.execute("SELECT count(*) FROM reconciliation WHERE status='pending' AND json_extract(body,'$.site_id')=? AND json_extract(body,'$.reason')='scheduled'", (site,)).fetchone()[0]
            if not pending:
                engine.queue_reconciliation({'site_id': site, 'reason': 'scheduled', 'alarm_id': None})
                engine.checkpoint(name, str(timestamp))
                count += 1
    retention = config.get('retention', {})
    if retention.get('enabled') is True:
        days = int(retention.get('days', 30))
        if days < 1:
            raise ValueError('Retention days must be at least one')
        previous = float(engine.checkpoint('retention:last_run') or 0)
        if timestamp - previous >= 3600:
            cutoff = datetime.fromtimestamp(timestamp, timezone.utc) - timedelta(days=days)
            engine.purge(cutoff.isoformat(), actor='retention-scheduler')
            engine.checkpoint('retention:last_run', str(timestamp))
    return count
