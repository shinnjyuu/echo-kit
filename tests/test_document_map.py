import json
from pathlib import Path

import pytest

from echo_kit import document_map as dm, project_docs
from echo_kit.cli import main
from echo_kit.core import Workspace, init_workspace
from test_project_docs import git, ws


def put(ws, path, body='', tracked=True):
    file = ws.root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body, encoding='utf-8')
    if tracked:
        git(ws.root, 'add', '--', path)
    return file


def test_map_whole_workspace_no_bodies(ws, monkeypatch):
    put(ws, 'AGENTS.md', 'ROOT')
    put(ws, '后端/订单/AGENTS.md', 'ORDERS')
    put(ws, '后端/订单/退款 说明.md', 'SECRET BODY')
    put(ws, 'frontend/README.md', 'UI')
    put(ws, '.echo-kit/private/AGENTS.md', 'PRIVATE', False)
    monkeypatch.setattr(project_docs, 'read_document', lambda *args: pytest.fail('body read'))
    result = dm.document_map(ws)
    assert [r['path'] for r in result['items']] == ['frontend', '后端', 'AGENTS.md']
    found = dm.document_map(ws, query='退款')
    assert [r['path'] for r in found['items']] == ['后端/订单/退款 说明.md']
    assert 'SECRET BODY' not in json.dumps(found)
    assert dm.document_map(ws, '后端/订单')['items'][0]['scope'] == '后端/订单'


def test_directory_chain_and_references_not_expanded(ws, monkeypatch):
    put(ws, 'AGENTS.md', '# Team\n[Shared](common.md)')
    put(ws, 'common.md', 'UNRELATED COMMON BODY')
    put(ws, 'backend/AGENTS.md', 'Backend rules')
    put(ws, 'backend/orders/AGENTS.md', '[Refund](docs/refund.md)')
    put(ws, 'backend/orders/docs/refund.md', 'UNRELATED REFUND BODY')
    put(ws, 'backend/orders/README.md', 'UNRELATED README BODY')
    put(ws, 'frontend/AGENTS.md', 'UNRELATED UI BODY')
    put(ws, 'backend/orders/source.py', '')
    original = project_docs.read_document
    def read(path):
        assert path.name == 'AGENTS.md' and 'frontend' not in path.parts
        return original(path)
    monkeypatch.setattr(project_docs, 'read_document', read)
    result = dm.context(ws, 'backend/orders/source.py')
    assert result['status'] == 'passed'
    assert [r['path'] for r in result['rules']] == ['AGENTS.md', 'backend/AGENTS.md', 'backend/orders/AGENTS.md']
    assert {r['path'] for r in result['references']} == {'common.md', 'backend/orders/docs/refund.md', 'backend/orders/README.md'}
    assert 'UNRELATED' not in json.dumps(result)


def test_read_pagination_and_byte_budget(ws):
    put(ws, 'long.md', ''.join(f'第{i}行\n' for i in range(450)))
    first = dm.read_document(ws, 'long.md')
    assert first['status'] == 'unverified' and first['document']['next_line'] == 201
    last = dm.read_document(ws, 'long.md', 401)
    assert last['complete'] and last['document']['end_line'] == 450
    put(ws, 'huge.md', ('中' * 300 + '\n') * 200)
    clipped = dm.read_document(ws, 'huge.md')['document']
    assert clipped['bytes'] <= dm.OUTPUT_BYTES and clipped['next_line'] < 200
    put(ws, 'single.md', '中' * dm.OUTPUT_BYTES)
    single = dm.read_document(ws, 'single.md')
    assert single['status'] == 'unverified' and not single['document']['continue_command']


def test_context_total_budget_and_candidates(ws):
    put(ws, 'AGENTS.md', ('r' * 1000 + '\n') * 60)
    put(ws, 'sub/AGENTS.md', ('s' * 1000 + '\n') * 40)
    put(ws, 'sub/AGENTS.override.md', 'OVERRIDE NOT APPLIED')
    result = dm.context(ws, 'sub')
    assert result['status'] == 'unverified'
    assert sum(len(r['content'].encode()) for r in result['rules']) <= dm.OUTPUT_BYTES
    assert result['rules'][1]['continue_command']
    assert result['candidates'][0]['path'] == 'sub/AGENTS.override.md'
    assert 'OVERRIDE NOT APPLIED' not in json.dumps(result)


def test_rule_checks_direct_only_and_no_setup(ws):
    put(ws, 'AGENTS.md', '[valid](guide.md)\n[bad](missing.md)\n[site](https://example.com)\n[anchor](#ok)')
    put(ws, 'guide.md', '[not checked](absent.md)')
    result = dm.check_rules(ws)
    assert result['checked_rules'] == ['AGENTS.md']
    assert [(i['code'], i.get('line')) for i in result['issues']] == [('shared_missing_reference', 2)]
    assert not (ws.state / 'local.toml').exists()
    assert project_docs.check(ws)['reason'] == 'not_configured'


@pytest.mark.parametrize('target', ['C:/private/a.md', '//server/share/a.md', '/home/me/a.md',
                                   'file:///tmp/a.md', '../outside.md', '.echo-kit/private.md'])
def test_bad_reference_never_reads_target(ws, target, monkeypatch):
    put(ws, 'AGENTS.md', f'[bad]({target})')
    original = project_docs.read_document
    monkeypatch.setattr(project_docs, 'read_document', lambda p: original(p) if p.name == 'AGENTS.md' else pytest.fail('target body'))
    assert dm.check_rules(ws)['status'] == 'failed'


def test_custom_private_and_ignored_tracked(ws):
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate="notes/private.md"')
    put(ws, 'notes/private.md', 'PRIVATE BODY', False)
    put(ws, 'ignored/AGENTS.md', 'IGNORED BODY')
    (ws.root / '.gitignore').write_text('.echo-kit/\nignored/\n')
    put(ws, 'AGENTS.md', '[private](notes/private.md)\n[ignored](ignored/AGENTS.md)')
    assert dm.document_map(ws, query='private')['items'] == []
    assert dm.document_map(ws, query='ignored')['items'] == []
    for path in ['notes/private.md', 'ignored/AGENTS.md']:
        assert dm.read_document(ws, path)['status'] == 'failed'
    result = dm.check_rules(ws)
    assert result['status'] == 'failed' and 'PRIVATE BODY' not in json.dumps(result)


def test_nested_repository_and_symlink_boundaries(ws, tmp_path):
    nested = ws.root / 'nested'
    nested.mkdir()
    git(nested, 'init')
    (nested / 'AGENTS.md').write_text('NESTED')
    assert not dm.document_map(ws, query='nested')['items']
    assert dm.context(ws, 'nested')['status'] == 'unverified'
    target = ws.root.parent / 'outside.md'
    target.write_text('OUTSIDE BODY')
    link = ws.root / 'link.md'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('Symlink creation unavailable')
    put(ws, 'AGENTS.md', '[bad](link.md)')
    assert dm.check_rules(ws)['status'] == 'failed'
    assert dm.read_document(ws, 'link.md')['document'] is None


def test_pagination_and_scan_limit(ws, monkeypatch):
    for i in range(26):
        put(ws, f'{i:02}.md', 'BODY', False)
    first = dm.document_map(ws)
    assert len(first['items']) == 20 and first['total'] == 26 and first['next_offset'] == 20
    assert len(dm.document_map(ws, offset=20)['items']) == 6
    monkeypatch.setattr(dm, 'MAX_FILES', 5)
    result = dm.document_map(ws)
    assert result['status'] == 'unverified' and not result['total_is_complete']


def test_no_git_and_cli_json(tmp_path_factory, ws, capsys, monkeypatch):
    plain = tmp_path_factory.mktemp('no-git')
    init_workspace(plain)
    assert dm.document_map(Workspace(plain))['status'] == 'unverified'
    put(ws, 'AGENTS.md', '# Root rules')
    for command in [('map',), ('context', '.'), ('read', 'AGENTS.md'), ('check', '--rules')]:
        assert main(['--offline', '--workspace', str(ws.root), '--json', 'docs', *command]) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'passed'
    monkeypatch.setattr(dm.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    assert dm.document_map(ws)['status'] == 'unverified'


def test_parent_relative_reference_within_workspace(ws):
    put(ws, 'AGENTS.md', 'root')
    put(ws, 'common.md', 'common')
    put(ws, 'module/AGENTS.md', '[shared](../common.md)')
    assert dm.check_rules(ws)['status'] == 'passed'
    assert dm.context(ws, 'module')['references'][0]['path'] == 'common.md'


def test_submodule_without_git_marker_excluded(ws):
    sub = ws.root / 'submodule'
    sub.mkdir()
    (sub / 'AGENTS.md').write_text('SUBMODULE PRIVATE')
    git(ws.root, 'update-index', '--add', '--cacheinfo', '160000', '1' * 40, 'submodule')
    assert dm.document_map(ws, query='submodule')['items'] == []
    assert dm.context(ws, 'submodule')['status'] == 'unverified'
    assert dm.read_document(ws, 'submodule/AGENTS.md')['document'] is None


def test_directory_symlink_not_followed_even_inside_workspace(ws):
    put(ws, 'actual/AGENTS.md', 'ACTUAL')
    try:
        (ws.root / 'alias').symlink_to(ws.root / 'actual', target_is_directory=True)
    except OSError:
        pytest.skip('Directory symlink creation unavailable')
    assert dm.context(ws, 'alias')['status'] == 'unverified'
    assert dm.read_document(ws, 'alias/AGENTS.md')['document'] is None
    assert dm.document_map(ws, query='alias')['items'] == []


def test_nonstandard_and_untracked_rules_are_unverified(ws):
    put(ws, 'agents.md', 'lowercase rules', False)
    assert dm.context(ws, '.')['candidates'][0]['path'] == 'agents.md'
    codes = {i['code'] for i in dm.check_rules(ws)['issues']}
    assert codes == {'nonstandard_rule_name', 'rule_untracked'}


def test_scan_size_missing_and_no_mutation(ws):
    file = put(ws, 'AGENTS.md', 'a' * (project_docs.MAX_BYTES + 1))
    assert dm.read_document(ws, 'AGENTS.md')['status'] == 'unverified'
    assert dm.check_rules(ws)['status'] == 'unverified'
    file.unlink()
    assert dm.check_rules(ws)['issues'][0]['code'] == 'rule_missing_file'
    assert not (ws.state / 'local.toml').exists()
    assert not (ws.root / 'echo/README.md').exists()


def test_many_unrelated_bodies_do_not_expand_context(ws, monkeypatch):
    put(ws, 'AGENTS.md', 'Global rule')
    put(ws, 'module/AGENTS.md', 'Local rule')
    for i in range(510):
        put(ws, f'unrelated/{i:04}.md', f'UNRELATED_{i}\n' * 100, False)
    original = project_docs.read_document
    def guarded(path):
        assert path.name == 'AGENTS.md'
        return original(path)
    monkeypatch.setattr(project_docs, 'read_document', guarded)
    result = dm.context(ws, 'module')
    assert result['status'] == 'passed' and len(result['rules']) == 2
    assert 'UNRELATED_' not in json.dumps(result)
    mapped = dm.document_map(ws)
    assert mapped['status'] == 'unverified' and mapped['scanned_documents'] == 500
    assert 'UNRELATED_' not in json.dumps(mapped)
    assert dm.document_map(ws, 'module')['status'] == 'passed'


def test_setup_template_new_only_and_rule_check_does_not_execute(ws):
    project_docs.setup(ws)
    text = (ws.root / 'echo/README.md').read_text()
    assert 'docs map' in text and 'docs context' in text and 'docs check --rules' in text
    put(ws, 'AGENTS.md', '```sh\ntouch SHOULD_NOT_EXIST\n```\n```md\n[not a reference](absent.md)\n```')
    assert dm.check_rules(ws)['status'] == 'passed'
    assert not (ws.root / 'SHOULD_NOT_EXIST').exists()
