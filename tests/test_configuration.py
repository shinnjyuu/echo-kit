import json
import subprocess
import sys
from pathlib import Path

import pytest

from echo_kit.core import Workspace, KitError, init_workspace
from echo_kit.catalogs import case, cases
from echo_kit.cli import main


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)


@pytest.fixture
def root(tmp_path):
    init_workspace(tmp_path)
    git(tmp_path, 'init')
    return tmp_path


def put(root, name, body):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    return path


def include(root, names, extra=''):
    put(root, 'echo-kit.toml', 'schema_version = 1\nincludes = ' + json.dumps(names) + '\n' + extra)


def test_module_overlay_and_sources(root):
    include(root, ['模块 空格/echo.toml'], 'default_environment="development"\n[browser]\nheadless=false\n')
    put(root, '模块 空格/echo.toml', '''[projects.orders]
path="模块 空格"
[cases.refund]
project="orders"
command=["{python}","script.py"]
tags=["base","second"]
[services.api]
command=["{python}","server.py"]
[auth.user]
command=["{python}","auth.py"]
[environments.development.cases.refund]
timeout=5
''')
    put(root, '.echo-kit/local.toml', '''[cases.refund]
tags=["local"]
timeout=2
[environments.development.cases.refund]
timeout=8
''')
    ws = Workspace(root)
    assert ws.cfg['cases']['refund']['tags'] == ['local']
    assert ws.cfg['cases']['refund']['timeout'] == 8
    assert ws.project('orders') == root / '模块 空格'
    assert ws.command(ws.cfg['cases']['refund']) == [sys.executable, 'script.py']
    sources = case(ws, 'refund')['configuration_sources']
    assert [s['stage'] for s in sources] == ['base', 'local', 'environment', 'environment']
    assert [s['scope'] for s in sources] == ['shared', 'private', 'shared', 'private']
    assert sources[0]['path'] == '模块 空格/echo.toml'
    assert 'command' not in json.dumps(sources)
    assert 'configuration_sources' not in json.dumps(cases(ws))


@pytest.mark.parametrize('extra,module', [
    ('[cases.a]\ncommand=["x"]', '[cases.a]\ntimeout=4'),
    ('[environments.dev.services.api]\nport=1', '[environments.dev.services.api]\nport=2'),
])
def test_duplicate_shared_definitions(root, extra, module):
    include(root, ['a.toml'], extra)
    put(root, 'a.toml', module)
    with pytest.raises(KitError, match='Duplicate shared definition') as error:
        Workspace(root)
    assert 'echo-kit.toml' in str(error.value) and 'a.toml' in str(error.value)


@pytest.mark.parametrize('body', ['includes=[]', '[browser]\nheadless=true', '[documents]\nshared="secret"',
                                  'schema_version=1', '[environments.dev.browser]\nheadless=true',
                                  '[environments.dev.documents]\nprivate="secret"', 'cases="SECRET"'])
def test_module_restricted_fields(root, body):
    include(root, ['a.toml']); put(root, 'a.toml', body)
    with pytest.raises(KitError) as error:
        Workspace(root)
    assert 'SECRET' not in str(error.value)


@pytest.mark.parametrize('name', ['../outside.toml', 'C:/secret.toml', '//server/share/a.toml',
                                 '/private.toml', '*.toml', 'a.json', '.echo-kit/local.toml'])
def test_invalid_include_paths(root, name):
    include(root, [name])
    with pytest.raises(KitError):
        Workspace(root)


def test_missing_duplicate_and_malformed(root):
    include(root, ['missing.toml'])
    with pytest.raises(KitError, match='Missing configuration'):
        Workspace(root)
    put(root, 'a.toml', '[cases.a]\ncommand=["x"]')
    include(root, ['a.toml', './a.toml'])
    with pytest.raises(KitError, match='Duplicate configuration'):
        Workspace(root)
    include(root, ['a.toml']); put(root, 'a.toml', 'password="SECRET\n')
    with pytest.raises(KitError) as error:
        Workspace(root)
    assert 'SECRET' not in str(error.value)


def test_private_and_ignored_not_read(root, monkeypatch):
    from echo_kit import configuration
    include(root, ['private.toml'])
    put(root, 'private.toml', 'password="DO NOT READ"')
    put(root, '.echo-kit/local.toml', '[documents]\nprivate="private.toml"')
    original = configuration.parse
    def parse(path, name):
        assert name != 'private.toml'
        return original(path, name)
    monkeypatch.setattr(configuration, 'parse', parse)
    with pytest.raises(KitError, match='private material'):
        Workspace(root)
    put(root, '.echo-kit/local.toml', '')
    put(root, '.gitignore', '.echo-kit/\nprivate.toml\n')
    git(root, 'add', '-f', 'private.toml')
    with pytest.raises(KitError, match='Git-ignored'):
        Workspace(root)


def test_nested_repository_and_submodule(root):
    include(root, ['nested/a.toml']); put(root, 'nested/a.toml', '')
    git(root / 'nested', 'init')
    with pytest.raises(KitError, match='nested repository'):
        Workspace(root)
    include(root, ['sub/a.toml']); put(root, 'sub/a.toml', '')
    git(root, 'update-index', '--add', '--cacheinfo', '160000,' + '1'*40 + ',sub')
    with pytest.raises(KitError, match='submodule'):
        Workspace(root)


def test_symlink_escape_and_duplicate(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp('outside') / 'outside.toml'; outside.write_text('')
    try:
        (root / 'link.toml').symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable')
    include(root, ['link.toml'])
    with pytest.raises(KitError, match='inside the workspace'):
        Workspace(root)
    put(root, 'a.toml', '')
    (root / 'alias.toml').symlink_to(root / 'a.toml')
    include(root, ['a.toml', 'alias.toml'])
    with pytest.raises(KitError, match='Duplicate configuration'):
        Workspace(root)


def test_git_report_and_missing_git(root, monkeypatch):
    include(root, ['module.toml']); put(root, 'module.toml', '[cases.a]\ncommand=["SECRET"]')
    report = Workspace(root).configuration.report()
    assert report['status'] == 'unverified'
    assert 'SECRET' not in json.dumps(report)
    git(root, 'add', '.')
    assert Workspace(root).configuration.report()['status'] == 'passed'
    from echo_kit import configuration
    def missing(*a, **kw): raise FileNotFoundError()
    monkeypatch.setattr(configuration.subprocess, 'run', missing)
    assert Workspace(root).configuration.report()['status'] == 'unverified'


def test_local_and_environment_includes_rejected(root):
    put(root, '.echo-kit/local.toml', 'includes=[]')
    with pytest.raises(KitError, match='shared root'): Workspace(root)
    put(root, '.echo-kit/local.toml', '')
    include(root, [], '[environments.dev]\nincludes=[]')
    with pytest.raises(KitError, match='shared root'): Workspace(root)


def test_cli_provenance_and_no_side_effects(root, capsys):
    include(root, ['a.toml']); put(root, 'a.toml', '[cases.a]\ncommand=["NO_EXECUTION"]')
    assert main(['--offline','--workspace',str(root),'--json','cases','show','a']) == 0
    assert json.loads(capsys.readouterr().out)['configuration_sources'][0]['path'] == 'a.toml'
    assert main(['--offline','--workspace',str(root),'--json','workspace','check']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['configuration']['status'] == 'unverified'
    assert 'NO_EXECUTION' not in json.dumps(result)
    assert not (root / '.echo-kit/runs').exists()


def test_split_equivalence_and_real_case(root):
    from echo_kit.runner import run
    module = '[projects.main]\npath="module"\n[cases.a]\nproject="main"\nprotocol="command"\ncommand=["{python}","check.py"]\n'
    put(root, 'module/check.py', 'from pathlib import Path\nassert Path("check.py").is_file()\n')
    put(root, 'echo-kit.toml', 'schema_version=1\n' + module)
    before = Workspace(root)
    include(root, ['module/echo.toml']); put(root, 'module/echo.toml', module)
    after = Workspace(root)
    assert before.cfg == after.cfg
    assert before.project('main') == after.project('main')
    records = run(after, 'a')
    assert records[0]['status'] == 'passed'


def test_module_service_management(root):
    import socket
    from echo_kit.services import Services
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    include(root, ['module/services.toml'])
    put(root, 'module/services.toml', '[services.web]\ncommand=' + json.dumps(
        ['{python}', '-m', 'http.server', str(port), '--bind', '127.0.0.1']) +
        f'\nport={port}\ntimeout=8\n[services.web.ready]\nport={port}\n')
    manager = Services(Workspace(root))
    try:
        first = manager.up(['web'])['web']
        assert first['ready']
        assert manager.up(['web'])['web']['instance']['identity'] == first['instance']['identity']
        assert manager.status()['web']['ready']
    finally:
        manager.down(['web'])


def test_non_git_workspace_and_untracked_local(tmp_path_factory):
    root = tmp_path_factory.mktemp('outside-repository')
    include(root, ['module.toml']); put(root, 'module.toml', '[cases.a]\ncommand=["x"]')
    assert Workspace(root).cfg['cases']['a']['command'] == ['x']
    assert Workspace(root).configuration.report()['status'] == 'unverified'
    git(root, 'init')
    put(root, '.echo-kit/local.toml', '[cases.a]\ntimeout=5')
    git(root, 'add', '.')
    report = Workspace(root).configuration.report()
    assert any(i['reason'] == 'private_tracked' for i in report['issues'])


def test_module_doc_link_stays_workspace_relative(root):
    include(root, ['module/config.toml'])
    put(root, 'module/config.toml', '[cases.a]\ncommand=["x"]\ndocs="docs/a.md"')
    put(root, 'docs/a.md', 'expected workspace document')
    put(root, 'module/docs/a.md', 'wrong module-relative document')
    git(root, 'add', '.')
    assert 'expected workspace document' in json.dumps(case(Workspace(root), 'a'))
    assert 'wrong module-relative document' not in json.dumps(case(Workspace(root), 'a'))


def test_failure_precedes_business_dispatch(root, monkeypatch, capsys):
    from echo_kit.services import Services
    include(root, ['missing.toml'])
    monkeypatch.setattr(Services, 'up', lambda *a: pytest.fail('business started'))
    assert main(['--offline', '--workspace', str(root), '--json', 'services', 'up']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'blocked'
