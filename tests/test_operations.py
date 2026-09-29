import importlib.util
from search_engine.store import Engine
from test_engine import event


def test_scheduler_does_not_duplicate_pending_jobs_and_respects_interval(tmp_path):
    assert importlib.util.find_spec('search_engine.operations'), 'Scheduler is missing'
    from search_engine.operations import schedule
    e = Engine(tmp_path / 'db.sqlite')
    config = {'milestone': {'lab': {'url': 'https://vms.example', 'sync_interval_seconds': 60}}}
    assert schedule(e, config, 1000) == 1
    assert schedule(e, config, 1030) == 0
    assert schedule(e, config, 1061) == 0
    with e.lock, e.db:
        e.db.execute("UPDATE reconciliation SET status='done'")
    assert schedule(e, config, 1062) == 1
    assert e.db.execute('SELECT count(*) FROM reconciliation').fetchone()[0] == 2
    e.close()


def test_retention_only_runs_when_explicitly_enabled(tmp_path):
    assert importlib.util.find_spec('search_engine.operations'), 'Scheduler is missing'
    from search_engine.operations import schedule
    e = Engine(tmp_path / 'db.sqlite')
    e.ingest(event()); e.process_pending()
    schedule(e, {}, 2000000000)
    assert e.status()['records'] == 1
    schedule(e, {'retention': {'enabled': True, 'days': 30}}, 2000000000)
    assert e.status()['records'] == 0
    e.close()
