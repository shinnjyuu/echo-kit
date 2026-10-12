import json
import subprocess
from pathlib import Path

import pytest

from echo_kit import catalogs, documentation
from echo_kit.cli import main
from echo_kit.core import KitError, Workspace, init_workspace, save


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    subprocess.run(['git', '-C', str(tmp_path), 'init'], check=True, capture_output=True)
    return Workspace(tmp_path)


def test_case_tree_search_and_paging(ws, monkeypatch):
    ws.cfg['cases'] = {'legacy': {'command': ['secret']},
                       'login': {'category': 'auth/login', 'description': '登录验证', 'tags': ['Tenant']},
                       'roles': {'category': 'auth', 'tags': ['roles']},
                       'upload': {'category': 'files/upload'}}
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: pytest.fail('list read a file'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('list ran a command'))
    root = catalogs.cases(ws)
    assert [(x['type'], x['id']) for x in root['items']] == [
        ('category', 'auth'), ('category', 'files'), ('case', 'legacy')]
    assert root['items'][-1]['missing_description']
    assert [x['id'] for x in catalogs.cases(ws, category='auth')['items']] == ['auth/login', 'roles']
    assert catalogs.cases(ws, query='登录')['items'][0]['id'] == 'login'
    assert catalogs.cases(ws, query='tenant')['items'][0]['id'] == 'login'
    assert catalogs.cases(ws, category='files', query='tenant')['total'] == 0
    assert catalogs.cases(ws, category='absent')['items'] == []
    first = catalogs.cases(ws, limit=2)
    assert first['total'] == 3 and first['next_offset'] == 2
    assert catalogs.cases(ws, limit=2, offset=2)['items'] == root['items'][2:]
    assert 'secret' not in json.dumps(root)


def test_empty_invalid_and_environment(ws, capsys):
    assert catalogs.cases(ws)['total'] == 0
    for limit, offset in [(0, 0), (201, 0), (20, -1)]:
        with pytest.raises(KitError):
            catalogs.cases(ws, limit=limit, offset=offset)
        with pytest.raises(KitError):
            catalogs.runs(ws, limit=limit, offset=offset)
    with pytest.raises(KitError):
        catalogs.cases(ws, category='../private')
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text(
        '[environments.qa.cases.login]\ndescription="local QA"\ncategory="auth"\n', encoding='utf-8')
    assert main(['--offline', '--json', '--workspace', str(ws.root), '--environment', 'qa',
                 'cases', 'list', '--category', 'auth']) == 0
    assert json.loads(capsys.readouterr().out)['items'][0]['description'] == 'local QA'
    assert main(['--offline', '--json', '--workspace', str(ws.root), 'cases', 'show', 'absent']) == 2


def test_show_only_selected_shared_document(ws, monkeypatch):
    doc = ws.root / '说明.md'
    doc.write_text('Selected explanation [link](other.md)', encoding='utf-8')
    (ws.root / 'other.md').write_text('UNRELATED BODY')
    ws.cfg['cases'] = {'login': {'description': 'Login', 'docs': '说明.md', 'services': ['api'],
                                'auth': 'tester', 'browser': True, 'command': ['SECRET COMMAND'],
                                'env': {'secret': 'SECRET ENV'}, 'inputs': {'token': 'SECRET INPUT'}}}
    original = Path.read_text
    def guarded(path, *a, **kw):
        assert path.name != 'other.md'
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', guarded)
    result = catalogs.case(ws, 'login')
    assert result['status'] == 'passed'
    assert result['document']['content'].startswith('Selected explanation')
    assert result['requirements'] == {'services': ['api'], 'service_access': 'shared', 'auth': 'tester', 'browser': True, 'verified': False}
    assert result['entrypoints'][1][-3:] == ['verify', 'run', 'login']
    assert '--environment' not in result['entrypoints'][0]
    assert 'SECRET' not in json.dumps(result)
    del ws.cfg['cases']['login']['docs']
    assert catalogs.case(ws, 'login')['document'] is None


def test_legacy_case_entrypoint_and_bad_metadata_do_not_block_execution(ws, capsys):
    with (ws.root / 'echo-kit.toml').open('a') as stream:
        stream.write('\n[cases.legacy]\ncommand=["{python}", "-c", "pass"]\n'
                     'protocol="command"\n')
    updated = Workspace(ws.root)
    args = catalogs.case(updated, 'legacy')['entrypoints'][0][1:]
    assert main(['--offline', '--json', *args]) == 0
    capsys.readouterr()
    with (ws.root / 'echo-kit.toml').open('a') as stream:
        stream.write('category=123\n')
    # Discovery metadata validation is independent of the existing execution path.
    assert main(['--offline', '--json', *args]) == 0
    capsys.readouterr()


@pytest.mark.parametrize('value,reason', [
    ('../outside.md', 'document_outside_or_private'),
    ('/etc/passwd', 'invalid_shared_document_path'),
    ('C:\\private.md', 'invalid_shared_document_path'),
    ('\\\\server\\private.md', 'invalid_shared_document_path'),
    ('.echo-kit/private/secret.md', 'document_outside_or_private'),
    ('ignored.md', 'document_ignored'),
    ('missing.md', 'document_missing')])
def test_rejected_docs_never_read(ws, monkeypatch, value, reason):
    (ws.root / '.gitignore').write_text('.echo-kit/\nignored.md\n')
    ws.cfg['cases'] = {'demo': {'docs': value}}
    monkeypatch.setattr(catalogs.project_docs, 'read_document', lambda p: pytest.fail('private read'))
    result = catalogs.case(ws, 'demo')
    assert result['status'] == 'blocked' and result['reason'] == reason
    assert result['document'] is None
    assert value not in json.dumps(result)


def test_custom_private_and_git_unknown(ws, monkeypatch):
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate="personal.md"\n')
    ws.cfg['cases'] = {'demo': {'docs': 'personal.md'}}
    monkeypatch.setattr(catalogs.project_docs, 'read_document', lambda p: pytest.fail('private read'))
    assert catalogs.case(ws, 'demo')['reason'] == 'document_outside_or_private'
    ws.cfg['cases']['demo']['docs'] = 'public.md'
    monkeypatch.setattr(catalogs.project_docs.Git, 'state', lambda *a: {
        'available': False, 'ignored': None, 'repository': None})
    assert catalogs.case(ws, 'demo')['reason'] == 'document_git_unverified'


def test_symlink_outside(ws, tmp_path, monkeypatch):
    target = tmp_path.parent / (tmp_path.name + '-secret.md')
    target.write_text('PRIVATE CONTENT')
    try:
        (ws.root / 'link.md').symlink_to(target)
    except OSError:
        pytest.skip('symlink privileges unavailable')
    ws.cfg['cases'] = {'demo': {'docs': 'link.md'}}
    monkeypatch.setattr(catalogs.project_docs, 'read_document', lambda p: pytest.fail('private read'))
    assert catalogs.case(ws, 'demo')['reason'] == 'document_outside_or_private'


def test_large_catalog_and_run_history_do_not_read_evidence(ws, monkeypatch):
    marker = 'UNRELATED_LARGE_BODY'
    (ws.root / 'large.md').write_text(marker * 10000)
    ws.cfg['cases'] = {f'case{i:04}': {'docs': 'large.md', 'description': 'short'} for i in range(250)}
    for i in range(250):
        save(ws.runs / f'run{i:04}' / 'run.json', {'case': 'demo', 'started': i, 'status': 'passed',
                                                'inputs': marker, 'checks': [{'detail': marker}]})
    save(ws.runs / 'old' / 'run.json', {})
    save(ws.runs / 'run0249' / 'active.json', {'secret': marker})
    for i in range(8):
        path = ws.runs / f'broken{i}' / 'run.json'
        path.parent.mkdir()
        path.write_text(marker)
    original = Path.read_text
    def guarded(path, *a, **kw):
        assert path.name not in ('large.md', 'active.json')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'read_text', guarded)
    listing = catalogs.cases(ws)
    assert len(listing['items']) == 20 and listing['total'] == 250
    history = catalogs.runs(ws)
    assert len(history['items']) == 20 and history['total'] == 251
    assert history['items'][0]['id'] == 'run0249' and history['items'][0]['needs_cleanup']
    assert history['issue_count'] == 8 and len(history['issues']) == 5 and history['issues_truncated']
    assert marker not in json.dumps([listing, history])
    assert catalogs.runs(ws, case='demo', status='failed')['total'] == 0
    assert catalogs.runs(ws, offset=250)['items'][0]['started'] is None
    assert catalogs.runs(ws, case='demo', offset=20)['items'][0]['id'] == 'run0229'
    assert len(json.dumps(listing)) < 6000 and len(json.dumps(history)) < 6000


def test_run_cli_keeps_show_complete(ws, capsys):
    save(ws.runs / 'one' / 'run.json', {'case': 'demo', 'inputs': {'detail': 'FULL_RECORD'}})
    prefix = ['--offline', '--json', '--workspace', str(ws.root)]
    assert main([*prefix, 'runs', 'list', '--case', 'demo']) == 0
    assert 'FULL_RECORD' not in capsys.readouterr().out
    assert main([*prefix, 'runs', 'show', 'one']) == 0
    assert 'FULL_RECORD' in capsys.readouterr().out


@pytest.mark.parametrize('name', ['echo-init', 'echo-lab', 'echo-workbench'])
def test_layered_guide_modes(name):
    compact = documentation.guide(name)
    full = documentation.guide(name, full=True)
    core = compact['protocol']['content']
    assert compact['mode'] == compact['protocol']['scope'] == 'compact'
    assert full['protocol']['content'] == documentation.show()['content']
    assert len(documentation.render_guide(compact)) < len(documentation.render_guide(full)) / 2
    for item in compact['topics']:
        topic = documentation.guide(name, topic=item['id'])
        assert core.strip() in topic['protocol']['content']
        assert topic['version'] == compact['version'] == full['version']
        assert topic['protocol']['scope'] == 'topic'
    assert compact['protocol']['references'] == []
    assert documentation.guide(name, topic='auth')['protocol']['references']
    assert documentation.guide(name, topic='configuration')['protocol']['references'] == []
    with pytest.raises(KitError):
        documentation.guide(name, topic='unknown')
    with pytest.raises(KitError):
        documentation.guide(name, topic='auth', full=True)
