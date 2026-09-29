import importlib.util
from search_engine.store import Engine
from test_engine import event


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
