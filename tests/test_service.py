from search_engine.service import CLI_COMMANDS, parse


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
