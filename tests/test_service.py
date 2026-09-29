from search_engine.service import CLI_COMMANDS, parse, wait_for_app


def test_double_click_installs_and_pauses():
    args = parse([])
    assert args.command == 'install' and args.pause


def test_cli_commands_pass_through_extra_arguments():
    args = parse(['backfill', '--url', 'https://vms', '--site', 'main'])
    assert args.command in CLI_COMMANDS and args.rest == ['--url', 'https://vms', '--site', 'main'] and not args.pause


def test_service_commands_reject_unknown_arguments():
    import pytest
    with pytest.raises(SystemExit):
        parse(['status', '--bogus'])


def test_wait_for_app_retries_until_the_database_is_ready(tmp_path, monkeypatch):
    """A slow PostgreSQL crash-recovery must not fail the service's one connection attempt (regression: issue
    where a Windows service dependency on PostgreSQL timed out and left the database marked Stopped while it
    was actually running fine, which then made this service fail to start too)."""
    import search_engine.service as service
    attempts = []

    def flaky_create_app(config):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError('connection refused')
        return 'app-object'

    monkeypatch.setattr('search_engine.api.create_app', flaky_create_app)
    monkeypatch.setattr(service.time, 'sleep', lambda s: None)
    assert wait_for_app({'database': str(tmp_path / 'x.db')}, timeout=10, interval=0.01) == 'app-object'
    assert len(attempts) == 3


def test_wait_for_app_gives_up_after_the_timeout(monkeypatch):
    import pytest
    import search_engine.service as service
    clock = {'t': 0.0}

    def advance(config):
        clock['t'] += 1
        raise RuntimeError('connection refused')

    monkeypatch.setattr('search_engine.api.create_app', advance)
    monkeypatch.setattr(service.time, 'sleep', lambda s: None)
    monkeypatch.setattr(service.time, 'time', lambda: clock['t'])
    with pytest.raises(RuntimeError, match='connection refused'):
        wait_for_app({}, timeout=3, interval=0.01)
