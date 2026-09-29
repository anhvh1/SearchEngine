import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_host_plugins_have_separate_packages_and_ids():
    expected = {
        'MilestoneSearch.EventServer': 'Service',
        'MilestoneSearch.Management': 'Administration',
        'MilestoneSearch.SmartClient': 'SmartClient',
    }
    for project, environment in expected.items():
        directory = ROOT / 'plugins' / project
        definition = (directory / 'plugin.def').read_text(encoding='utf8')
        assert f'<load env="{environment}"/>' in definition
        assert definition.count('<load env=') == 1
        assert f'name="{project}.dll"' in definition

    settings = (ROOT / 'plugins/MilestoneSearch/PluginSettings.cs').read_text(encoding='utf8')
    ids = re.findall(r'(?:ManagementPlugin|EventServerPlugin|SmartClientPlugin)=new Guid\("([^"]+)', settings)
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_event_collector_loads_persisted_settings_at_startup():
    source = (ROOT / 'plugins/MilestoneSearch/CollectorPlugin.cs').read_text(encoding='utf8')
    init = source[source.index('public override void Init()'):source.index('private object ConfigurationChanged')]
    assert 'ReloadSettings("startup")' in init
    assert 'PluginLog.Info("Collector Init entered' in init
    assert 'Notification queued:' in source
