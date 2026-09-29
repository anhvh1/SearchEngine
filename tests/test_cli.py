import importlib.util
import json


def test_bootstrap_unique_tokens_and_seed(tmp_path):
    assert importlib.util.find_spec('search_engine.cli'), 'CLI missing'
    from search_engine.cli import initialize, seed
    from search_engine.store import Engine
    path = tmp_path / 'config.json'
    initialize(path)
    config = json.loads(path.read_text())
    assert len(set(p['token'] for p in config['principals'])) == 2
    assert next(p for p in config['principals'] if p['name'] == 'collector')['sites'] == ['*']
    e = Engine(config['database'])
    seed(e)
    seed(e)
    e.process_pending()
    assert e.search('', [('demo', 'gate-01')])['total'] == 3
    assert e.search('', [('demo', 'door-02')])['total'] == 1
