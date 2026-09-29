"""Windows service host: one executable installs, runs and manages the backend service.

Running the executable without arguments (double-click) elevates, copies itself to Program Files,
creates configuration on first install, registers the service and starts it. Re-running it upgrades
the binary and refreshes service settings; configuration and data are kept.
"""
import argparse
import ctypes
import json
import logging
import logging.config
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SERVICE_NAME = 'MilestoneSearchBackend'
DISPLAY_NAME = 'Milestone Search Backend'
DESCRIPTION = 'Milestone Search API, ingestion, indexing and reconciliation.'
EXE_NAME = 'MilestoneSearch.Backend.exe'
INSTALL_DIR = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'MilestoneSearch' / 'Backend'
DATA_DIR = Path(os.environ.get('ProgramData', r'C:\ProgramData')) / 'MilestoneSearch' / 'backend'
CONFIG = DATA_DIR / 'config.json'
LOG_DIR = DATA_DIR / 'logs'
COLLECTOR_TOKEN = DATA_DIR.parent / 'collector.token'
FIREWALL_RULE = 'Milestone Search Backend'
ADMIN_COMMANDS = ('install', 'uninstall', 'start', 'stop', 'restart')
CLI_COMMANDS = ('init', 'seed', 'import', 'backfill', 'discover', 'openapi', 'iag-probe')


def parse(argv):
    parser = argparse.ArgumentParser(prog=EXE_NAME, description='Milestone Search backend service. '
                                     'Without arguments: install or upgrade, then start the service.')
    parser.add_argument('command', nargs='?', default='install',
                        choices=ADMIN_COMMANDS + ('status', 'console', 'run-service') + CLI_COMMANDS)
    parser.add_argument('--config', help='Configuration to copy on first install, or to use with console/CLI commands')
    parser.add_argument('--pause', action='store_true', help=argparse.SUPPRESS)
    args, rest = parser.parse_known_args(argv)
    if args.command not in CLI_COMMANDS and rest:
        parser.error('unrecognized arguments: ' + ' '.join(rest))
    args.rest = rest
    args.pause = args.pause or not argv
    return args


def log_config():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return {'version': 1, 'disable_existing_loggers': False,
            'formatters': {'default': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
            'handlers': {'file': {'class': 'logging.handlers.RotatingFileHandler', 'formatter': 'default',
                                  'filename': str(LOG_DIR / 'backend.log'), 'maxBytes': 10 * 1024 * 1024,
                                  'backupCount': 5, 'encoding': 'utf8'}},
            'root': {'handlers': ['file'], 'level': 'INFO'}}


def wait_for_app(config, timeout=300, interval=5):
    """PostgreSQL crash recovery after an unclean shutdown can take a while on a slow disk; retry the
    connection here instead of crashing once and leaving it to the service's failure-action restarts."""
    from .api import create_app
    deadline = time.time() + timeout
    attempt = 0
    while True:
        try:
            return create_app(config)
        except Exception as exc:
            attempt += 1
            if time.time() >= deadline:
                raise
            logging.warning('Backend not ready (attempt %d): %s; retrying in %ds', attempt, exc, interval)
            time.sleep(interval)


def build_server(config_path, log=None):
    import uvicorn
    config = json.loads(Path(config_path).read_text(encoding='utf8'))
    app = wait_for_app(config)
    options = {'log_config': None} if log is None else {}
    return uvicorn.Server(uvicorn.Config(app, host=config.get('host', '127.0.0.1'), port=config.get('port', 8765),
                                         workers=1, **options))


def service_class():
    import win32service
    import win32serviceutil

    class BackendService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = DESCRIPTION

        def __init__(self, args):
            super().__init__(args)
            self.server = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self.server:
                self.server.should_exit = True

        def SvcDoRun(self):
            # Services have no console; keep prints and tracebacks in a file next to the rotating log.
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stdout = LOG_DIR / 'stdout.log'
            if stdout.exists() and stdout.stat().st_size > 10 * 1024 * 1024:
                stdout.unlink()
            sys.stdout = sys.stderr = open(stdout, 'a', buffering=1, encoding='utf8')
            logging.config.dictConfig(log_config())
            try:
                logging.info('Starting with configuration %s', CONFIG)
                try:
                    share_collector_token(read_config())
                except Exception:
                    logging.exception('Collector token file not written; the Event Server plugin needs MILESTONE_SEARCH_COLLECTOR_TOKEN')
                self.server = build_server(CONFIG)
                self.server.run()
                if not self.server.should_exit:
                    raise RuntimeError('Server stopped without a stop request')
            except BaseException:
                logging.exception('Backend service failed')
                logging.shutdown()
                os._exit(1)  # Unexpected exit lets the SCM recovery actions restart the service.
            logging.info('Stopped')

    return BackendService


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate(argv):
    params = subprocess.list2cmdline(argv + ['--pause'])
    if ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable, params, None, 1) <= 32:
        raise SystemExit('Administrator rights are required.')


def ensure_config(source):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        print(f'Configuration kept: {CONFIG}')
        return
    candidates = [Path(source)] if source else [Path(sys.executable).parent / n for n in ('config.json', 'config.local.json')]
    found = next((p for p in candidates if p.is_file()), None)
    if source and not found:
        raise SystemExit(f'Configuration not found: {source}')
    if found:
        json.loads(found.read_text(encoding='utf8'))
        shutil.copyfile(found, CONFIG)
        print(f'Configuration copied from {found} to {CONFIG}')
    else:
        from .cli import initialize
        initialize(CONFIG)
        print('New configuration uses SQLite. Edit "database" to a PostgreSQL DSN for production, then run this program again.')
    # Tokens and DSN are secrets: only SYSTEM and Administrators may read the data directory.
    subprocess.run(['icacls', str(DATA_DIR), '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F',
                    '*S-1-5-32-544:(OI)(CI)F'], check=True, stdout=subprocess.DEVNULL)


def read_config():
    return json.loads(CONFIG.read_text(encoding='utf8'))


def event_server_account():
    import win32service
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
    try:
        for name, display, _ in win32service.EnumServicesStatus(scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL):
            if 'milestone' in display.lower() and 'event server' in display.lower():
                handle = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_CONFIG)
                try:
                    return win32service.QueryServiceConfig(handle)[7]
                finally:
                    win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)
    return None


def share_collector_token(config):
    """Let the Event Server plugin on this machine authenticate without manual token setup."""
    token = next((p['token'] for p in config.get('principals', []) if 'collector' in p.get('roles', [])), None)
    if not token:
        return
    COLLECTOR_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    if not COLLECTOR_TOKEN.exists() or COLLECTOR_TOKEN.read_text(encoding='utf8').strip() != token:
        COLLECTOR_TOKEN.write_text(token, encoding='utf8')
    readers = ['*S-1-5-18:F', '*S-1-5-32-544:F', '*S-1-5-20:R', '*S-1-5-19:R']
    account = event_server_account()
    if account and account.lower() not in ('localsystem', 'nt authority\\networkservice', 'nt authority\\localservice'):
        readers.append(account + ':R')
    subprocess.run(['icacls', str(COLLECTOR_TOKEN), '/inheritance:r', '/grant:r', *readers], check=True, stdout=subprocess.DEVNULL)


def open_firewall(config):
    """Clients on the local network reach the console; nothing outside the subnet does."""
    if config.get('host', '127.0.0.1') in ('127.0.0.1', 'localhost', '::1'):
        return
    port = str(config.get('port', 8765))
    subprocess.run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', f'name={FIREWALL_RULE}'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['netsh', 'advfirewall', 'firewall', 'add', 'rule', f'name={FIREWALL_RULE}', 'dir=in', 'action=allow',
                    'protocol=TCP', f'localport={port}', 'remoteip=localsubnet', 'profile=any'], check=True, stdout=subprocess.DEVNULL)
    print(f'Firewall: TCP {port} open to the local subnet.')


def local_postgres_services(scm, config):
    """Informational only: naming a Windows service dependency proved unreliable (SCM's start-wait can time out
    on a slow crash-recovery while the database process keeps running fine, leaving the service marked Stopped
    even though it is not). The backend instead retries its own connection at startup; see wait_for_app."""
    import win32service
    database = str(config.get('database', ''))
    if not database.startswith(('postgresql:', 'postgres:')) or not any(h in database for h in ('@localhost', '@127.0.0.1', '@[::1]')):
        return []
    services = win32service.EnumServicesStatus(scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL)
    return [name for name, _, _ in services if name.lower().startswith('postgresql')]


def wait_state(handle, state, timeout=60):
    import win32service
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = win32service.QueryServiceStatus(handle)[1]
        if current == state:
            return True
        time.sleep(0.5)
    return False


def open_service(scm, access=None):
    import pywintypes
    import win32service
    try:
        return win32service.OpenService(scm, SERVICE_NAME, access or win32service.SERVICE_ALL_ACCESS)
    except pywintypes.error as error:
        if error.winerror == 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
            return None
        raise


def stop(scm):
    import win32service
    handle = open_service(scm)
    if handle is None:
        return
    try:
        if win32service.QueryServiceStatus(handle)[1] != win32service.SERVICE_STOPPED:
            win32service.ControlService(handle, win32service.SERVICE_CONTROL_STOP)
            if not wait_state(handle, win32service.SERVICE_STOPPED):
                raise SystemExit('Service did not stop within 60 seconds.')
        print('Service stopped.')
    finally:
        win32service.CloseServiceHandle(handle)


def start(scm):
    import win32service
    handle = open_service(scm)
    if handle is None:
        raise SystemExit('Service is not installed.')
    try:
        if win32service.QueryServiceStatus(handle)[1] != win32service.SERVICE_RUNNING:
            win32service.StartService(handle, None)
            if not wait_state(handle, win32service.SERVICE_RUNNING):
                raise SystemExit(f'Service did not start. See {LOG_DIR}.')
    finally:
        win32service.CloseServiceHandle(handle)
    probe(read_config())


def probe(config):
    host = config.get('host', '127.0.0.1')
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::', '') else host}:{config.get('port', 8765)}/"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                print(f'Service running: {url} (HTTP {response.status})')
                return True
        except Exception:
            time.sleep(1)
    print(f'Service started but {url} does not answer yet. Last log lines:')
    log = LOG_DIR / 'backend.log'
    if log.exists():
        print(''.join(log.read_text(encoding='utf8', errors='replace').splitlines(True)[-20:]))
    return False


def install(args):
    import win32service
    if not getattr(sys, 'frozen', False):
        raise SystemExit('Install from the built executable (scripts/build-backend.ps1).')
    ensure_config(args.config)
    config = read_config()
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        target = INSTALL_DIR / EXE_NAME
        if Path(sys.executable).resolve() != target.resolve():
            stop(scm)  # Release the running binary before replacing it.
            INSTALL_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sys.executable, target)
            print(f'Executable installed: {target}')
        command = f'"{target}" run-service'
        # No SCM-level dependency: on a slow crash-recovery, SCM's dependency-start wait can time out and mark
        # PostgreSQL Stopped while the process keeps running, which then makes OUR start fail too (error 1068).
        # The backend instead retries its own database connection at startup (see wait_for_app).
        found = local_postgres_services(scm, config)
        handle = open_service(scm)
        if handle is None:
            handle = win32service.CreateService(
                scm, SERVICE_NAME, DISPLAY_NAME, win32service.SERVICE_ALL_ACCESS, win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START, win32service.SERVICE_ERROR_NORMAL, command, None, 0, None, None, None)
            print(f'Service created: {SERVICE_NAME}')
        else:
            win32service.ChangeServiceConfig(
                handle, win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL, command, None, 0, [], None, None, DISPLAY_NAME)
            print(f'Service updated: {SERVICE_NAME}')
        try:
            win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_DESCRIPTION, DESCRIPTION)
            win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_FAILURE_ACTIONS, {
                'ResetPeriod': 86400, 'RebootMsg': None, 'Command': None,
                'Actions': [(win32service.SC_ACTION_RESTART, 5000), (win32service.SC_ACTION_RESTART, 15000),
                            (win32service.SC_ACTION_RESTART, 60000)]})
        finally:
            win32service.CloseServiceHandle(handle)
        if found:
            print('PostgreSQL cùng máy: ' + ', '.join(found) + '. Backend tự thử kết nối lại nếu chưa sẵn sàng, không đợi Windows.')
        share_collector_token(config)
        open_firewall(config)
        start(scm)
    finally:
        win32service.CloseServiceHandle(scm)
    print(f'Configuration: {CONFIG}\nLogs: {LOG_DIR}')
    print('Sign in to the console with a Milestone account. Smart Client users are signed in automatically.')


def uninstall():
    import win32service
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        stop(scm)
        handle = open_service(scm)
        if handle is None:
            print('Service is not installed.')
            return
        win32service.DeleteService(handle)
        win32service.CloseServiceHandle(handle)
        subprocess.run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', f'name={FIREWALL_RULE}'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f'Service removed. Configuration and data kept in {DATA_DIR}; executable in {INSTALL_DIR}.')
    finally:
        win32service.CloseServiceHandle(scm)


def status():
    import win32service
    names = {win32service.SERVICE_STOPPED: 'Stopped', win32service.SERVICE_START_PENDING: 'Starting',
             win32service.SERVICE_STOP_PENDING: 'Stopping', win32service.SERVICE_RUNNING: 'Running'}
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        handle = open_service(scm, win32service.SERVICE_QUERY_STATUS)
        if handle is None:
            print('Service: not installed')
        else:
            print('Service: ' + names.get(win32service.QueryServiceStatus(handle)[1], 'Unknown'))
            win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)
    print(f'Configuration: {CONFIG}\nLogs: {LOG_DIR}')


def run(args):
    if args.command == 'run-service':
        import servicemanager
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(service_class())
        servicemanager.StartServiceCtrlDispatcher()
    elif args.command == 'console':
        build_server(args.config or CONFIG, log=True).run()
    elif args.command in CLI_COMMANDS:
        from . import cli
        sys.argv = [EXE_NAME, args.command, '--config', str(args.config or CONFIG)] + args.rest
        cli.main()
    elif args.command == 'status':
        status()
    elif args.command == 'install':
        install(args)
    elif args.command == 'uninstall':
        uninstall()
    else:
        import win32service
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            if args.command in ('stop', 'restart'):
                stop(scm)
            if args.command in ('start', 'restart'):
                start(scm)
        finally:
            win32service.CloseServiceHandle(scm)


def utf8_console():
    """Vietnamese messages must print on any console code page instead of crashing."""
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main(argv=None):
    utf8_console()
    argv = sys.argv[1:] if argv is None else argv
    args = parse(argv)
    if args.command in ADMIN_COMMANDS and not is_admin():
        elevate([a for a in argv if a != '--pause'] or ['install'])
        return
    code = 0
    try:
        run(args)
    except SystemExit as exit:
        if exit.code not in (None, 0):
            print(exit.code if isinstance(exit.code, str) else f'Exit code {exit.code}')
            code = 1
    except Exception as error:
        print(f'ERROR: {error}')
        code = 1
    if args.pause:
        input('Press Enter to close...')
    sys.exit(code)


if __name__ == '__main__':
    main()
