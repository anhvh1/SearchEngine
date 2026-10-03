"""Explicitly configured schedules with persisted checkpoints.

Alarms arrive by direct push from the Event Server plugin only; this no longer queues periodic Milestone REST
reconciliation (that polling duplicated pushed alarms and could get stuck on a malformed pagination link).
"""
from datetime import datetime, timezone, timedelta

MAX_RETENTION_DAYS = 3650


def retention_days(engine, config):
    """(days, source): the number set on the administration page wins over config.json; 0 keeps everything."""
    saved = engine.checkpoint('retention:days')
    if saved not in (None, ''):
        return int(saved), 'console'
    retention = config.get('retention', {})
    if retention.get('enabled') is True:
        days = int(retention.get('days', 30))
        if days < 1:
            raise ValueError('Retention days must be at least one')
        return days, 'config'
    return 0, 'default'


def schedule(engine, config, timestamp):
    days, _ = retention_days(engine, config)
    if days:
        previous = float(engine.checkpoint('retention:last_run') or 0)
        if timestamp - previous >= 3600:
            cutoff = datetime.fromtimestamp(timestamp, timezone.utc) - timedelta(days=days)
            engine.purge(cutoff.isoformat(), actor='retention-scheduler')
            engine.checkpoint('retention:last_run', str(timestamp))
