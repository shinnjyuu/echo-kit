import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from echo_kit import __version__, project_docs as docs, tasks
from echo_kit.cli import main
from echo_kit.core import Workspace, init_workspace


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


@pytest.fixture
def ws(tmp_path):
    git(tmp_path, 'init')
    init_workspace(tmp_path)
    return Workspace(tmp_path)


def ready(ws):
    docs.setup(ws)
    git(ws.root, 'add', 'echo-kit.toml', '.gitignore', 'echo')


def shared(ws, text):
    (ws.root / 'echo/README.md').write_text(text, encoding='utf-8')


def codes(result):
    return {i['code'] for i in result['issues']}


def test_opt_in_and_preserving_setup(ws):
    assert docs.catalog(ws)['reason'] == 'not_configured'
    assert docs.check(ws)['reason'] == 'not_configured'
    original = ws.root / 'echo/README.md'
    original.write_text('# Existing team rules\n', encoding='utf-8')
    config = ws.root / 'echo-kit.toml'
    config.write_text(config.read_text() + '\n# Keep my comment\n', encoding='utf-8')
    first = docs.setup(ws)
    assert first['status'] == 'unverified'
    assert first['reused'] == ['echo/README.md']
    assert original.read_text() == '# Existing team rules\n'
    assert '# Keep my comment' in config.read_text()
    before = {p: p.read_bytes() for p in (config, ws.state / 'local.toml', original, ws.root / '.gitignore')}
    assert docs.setup(ws)['created'] == []
    assert all(p.read_bytes() == data for p, data in before.items())
    git(ws.root, 'add', 'echo', 'echo-kit.toml', '.gitignore')
    assert docs.check(ws)['status'] == 'passed'
    assert docs.catalog(ws)['documents'][1]['git']['ignored']


@pytest.mark.parametrize('filename,block,expected', [
    ('echo-kit.toml', '[documents]\nprivate="secret.md"', 'invalid_document_source'),
    ('.echo-kit/local.toml', '[documents]\nshared="README.md"', 'invalid_document_source'),
    ('echo-kit.toml', '[environments.qa.documents]\nshared="README.md"', 'environment_documents_forbidden'),
    ('.echo-kit/local.toml', '[environments.qa.documents]\nprivate="x.md"', 'environment_documents_forbidden'),
    ('echo-kit.toml', '[documents]\nshared="../outside.md"', 'shared_outside_or_private'),
    ('echo-kit.toml', '[documents]\nshared="C:/personal/file.md"', 'shared_absolute_path'),
])
def test_source_validation_before_merge(ws, filename, block, expected):
    p = ws.root / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a', encoding='utf-8') as stream:
        stream.write('\n' + block + '\n')
    assert expected in codes(docs.check(ws))
    assert docs.setup(ws)['created'] == []


def test_git_ignoring_does_not_untrack_private_docs(ws):
    ready(ws)
    private = ws.state / 'private/README.md'
    private.write_text('SECRET SHOULD NOT APPEAR', encoding='utf-8')
    git(ws.root, 'add', '-f', '.echo-kit/private/README.md')
    checked = docs.check(ws)
    assert 'private_tracked' in codes(checked)
    assert checked['status'] == 'failed'
    assert 'SECRET SHOULD NOT APPEAR' not in json.dumps(checked)
    assert docs.setup(ws)['status'] == 'failed'
    assert git(ws.root, 'ls-files', '.echo-kit/private/README.md').stdout.strip()
    private.unlink()
    assert 'private_tracked' in codes(docs.check(ws))


def test_private_ignore_and_optional_missing(ws):
    ready(ws)
    (ws.root / '.gitignore').write_text('', encoding='utf-8')
    assert 'private_not_ignored' in codes(docs.check(ws))
    (ws.root / '.gitignore').write_text('.echo-kit/\n', encoding='utf-8')
    (ws.state / 'private/README.md').unlink()
    assert docs.check(ws)['status'] == 'passed'
    assert docs.show(ws, 'private')['exists'] is False


def test_external_private_and_explicit_read(ws, tmp_path):
    external = tmp_path.parent / (tmp_path.name + '-personal.md')
    external.write_text('PERSONAL BODY', encoding='utf-8')
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate=' + json.dumps(str(external)), encoding='utf-8')
    ready(ws)
    assert docs.check(ws)['status'] == 'passed'
    assert docs.show(ws, 'private')['content'] == 'PERSONAL BODY'
    assert 'PERSONAL BODY' not in json.dumps(docs.catalog(ws))
    assert 'PERSONAL BODY' not in (ws.root / 'echo/README.md').read_text()
    shared(ws, '[bad](<' + str(external) + '>)')
    assert docs.check(ws)['status'] == 'failed'


@pytest.mark.parametrize('target', ['C:/Users/someone/file.md', '//server/share/file.md',
                                   '/home/someone/file.md', 'file:///C:/private.md', '../.. /file.md'])
def test_nonportable_links(ws, target):
    ready(ws)
    shared(ws, '[bad](<' + target + '>)')
    assert docs.check(ws)['status'] == 'failed'


def test_recursive_links_reference_images_html_unicode_and_cycles(ws):
    ready(ws)
    (ws.root / 'echo/中文 空格.md').write_text('[back](README.md)\n[missing](lost.md)', encoding='utf-8')
    shared(ws, '[next][ref]\n\n[ref]: <中文 空格.md>\n\n![img](missing.png)\n<a href="absent.md">more</a>')
    git(ws.root, 'add', 'echo')
    checked = docs.check(ws)
    assert checked['checked_files'] == 3  # Two shared docs and optional personal entry.
    assert sum(i['code'] == 'shared_missing_reference' for i in checked['issues']) == 3
    assert all(i.get('line') for i in checked['issues'])


def test_shared_never_reads_private_targets(ws, monkeypatch):
    ready(ws)
    private = ws.state / 'private/README.md'
    private.write_text('SENSITIVE BODY', encoding='utf-8')
    shared(ws, '[private](../.echo-kit/private/README.md)')
    original = docs.read_document
    def read(path):
        if path == private:
            raise AssertionError('must not read private body during shared traversal')
        return original(path)
    # Remove private registration: shared traversal still recognizes all local state.
    (ws.state / 'local.toml').write_text('', encoding='utf-8')
    monkeypatch.setattr(docs, 'read_document', read)
    checked = docs.check(ws)
    assert 'shared_private_reference' in codes(checked)
    assert 'SENSITIVE' not in json.dumps(checked)


def test_plain_paths_warn_urls_and_anchors_pass(ws):
    ready(ws)
    shared(ws, '# Hello\n[web](https://example.com/doc) [mail](mailto:a@example.com) [anchor](#hello)\n')
    assert docs.check(ws)['status'] == 'passed'
    shared(ws, '```sh\nC:\\Users\\someone\\tool.exe\n```\n')
    checked = docs.check(ws)
    assert checked['status'] == 'unverified'
    assert checked['issues'] == [docs.issue('possible_personal_path', 'echo/README.md', 'unverified', 2)]


def test_no_git_and_missing_git_are_unverified(tmp_path, monkeypatch):
    init_workspace(tmp_path)
    ws = Workspace(tmp_path)
    assert docs.setup(ws)['status'] == 'unverified'
    git(tmp_path, 'init')
    git(tmp_path, 'add', 'echo')
    monkeypatch.setenv('PATH', '')
    assert 'git_unavailable' in codes(docs.check(ws))


def test_symlink_outside_is_not_read(ws, tmp_path):
    ready(ws)
    outside = tmp_path.parent / (tmp_path.name + '-outside.md')
    outside.write_text('PRIVATE SENTINEL', encoding='utf-8')
    link = ws.root / 'echo/link.md'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable on this host')
    shared(ws, '[link](link.md)')
    checked = docs.check(ws)
    assert 'shared_reference_outside' in codes(checked)
    assert 'PRIVATE SENTINEL' not in json.dumps(checked)


def test_cli_task_entry_and_advisory_workspace_check(ws, capsys):
    ready(ws)
    tasks.setup(ws)
    task = tasks.start(ws, 'document check', no_network=True)
    output = subprocess.run([sys.executable, str(ws.root / 'echo-kit.py'), '--task', task['id'],
                             '--offline', '--json', 'docs', 'show', 'shared'], capture_output=True, text=True)
    assert output.returncode == 0, output.stderr + output.stdout
    assert json.loads(output.stdout)['scope'] == 'shared'
    assert task['version'] == __version__
    shared(ws, '[bad](/home/user/private.md)')
    assert main(['--workspace', str(ws.root), '--offline', '--json', 'workspace', 'check']) == 0
    assert json.loads(capsys.readouterr().out)['documents']['configured']
    assert main(['--workspace', str(ws.root), '--json', 'docs', 'check']) == 1
    capsys.readouterr()
    tasks.finish(ws, task['id'])


def test_custom_entries_and_private_graph(ws):
    team = ws.root / 'docs/团队 #1.md'
    team.parent.mkdir()
    team.write_text('[second](second.md)', encoding='utf-8')
    (team.parent / 'second.md').write_text('# Shared second', encoding='utf-8')
    config = ws.root / 'echo-kit.toml'
    with config.open('a', encoding='utf-8') as stream:
        stream.write('\n[documents]\nshared="docs/团队 #1.md"\n')
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate="personal/notes [1].md"\n', encoding='utf-8')
    docs.setup(ws)
    private = ws.root / 'personal/notes [1].md'
    assert private.exists() and docs.Git().state(private)['ignored']
    private.write_text('[team](../docs/团队%20%231.md)\n[detail](detail.md)', encoding='utf-8')
    detail = private.parent / 'detail.md'
    detail.write_text('DO NOT LEAK THIS LINE', encoding='utf-8')
    git(ws.root, 'add', 'docs', 'personal/detail.md')
    checked = docs.check(ws)
    assert 'private_tracked' in codes(checked)
    assert 'DO NOT LEAK' not in json.dumps(checked)
    assert docs.show(ws, 'shared')['content'] == '[second](second.md)'


def test_external_private_in_another_git_repository(ws, tmp_path):
    external = tmp_path.parent / (tmp_path.name + '-repo')
    external.mkdir()
    git(external, 'init')
    note = external / 'personal.md'
    note.write_text('PRIVATE', encoding='utf-8')
    git(external, 'add', 'personal.md')
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate=' + json.dumps(str(note)), encoding='utf-8')
    ready(ws)
    assert 'private_tracked' in codes(docs.check(ws))


def test_private_absolute_references_are_checked_without_directory_scan(ws, tmp_path):
    ready(ws)
    external = tmp_path.parent / (tmp_path.name + '-evidence')
    external.mkdir()
    git(external, 'init')
    note = external / '原始 #1.md'
    note.write_text('EXTERNAL PRIVATE BODY', encoding='utf-8')
    git(external, 'add', '.')
    (external / 'unreferenced.md').write_text('must not be scanned', encoding='utf-8')
    entry = ws.state / 'private/README.md'
    entry.write_text('[external](' + note.as_uri() + ')', encoding='utf-8')
    checked = docs.check(ws)
    assert 'private_tracked' in codes(checked)
    assert checked['checked_files'] == 3
    assert 'EXTERNAL PRIVATE BODY' not in json.dumps(checked)


def test_encoded_path_and_markdown_code_examples(ws):
    ready(ws)
    (ws.root / 'echo/a #b.md').write_text('# Safe', encoding='utf-8')
    git(ws.root, 'add', 'echo')
    shared(ws, '[valid](a%20%23b.md)\n\n```md\n[example](missing.md)\n```')
    assert docs.check(ws)['status'] == 'passed'
    shared(ws, '[absolute](%2Fhome%2Fuser%2Fprivate.md)')
    assert 'shared_absolute_reference' in codes(docs.check(ws))


def test_recursive_scan_limits_do_not_claim_success(ws, monkeypatch):
    ready(ws)
    shared(ws, '[next](next.md)')
    (ws.root / 'echo/next.md').write_text('# Next', encoding='utf-8')
    git(ws.root, 'add', 'echo')
    monkeypatch.setattr(docs, 'MAX_FILES', 1)
    assert 'scan_limit' in codes(docs.check(ws))
    monkeypatch.setattr(docs, 'MAX_FILES', 500)
    monkeypatch.setattr(docs, 'MAX_BYTES', 1)
    assert docs.check(ws)['status'] == 'unverified'


def test_private_configuration_cannot_be_tracked(ws):
    ready(ws)
    git(ws.root, 'add', '-f', '.echo-kit/local.toml')
    assert 'private_config_tracked' in codes(docs.check(ws))


def test_private_missing_reference_and_directory_entry(ws):
    ready(ws)
    entry = ws.state / 'private/README.md'
    entry.write_text('[gone](gone.md)', encoding='utf-8')
    assert 'private_missing_reference' in codes(docs.check(ws))
    entry.unlink()
    assert docs.check(ws)['status'] == 'passed'
    entry.mkdir()
    assert 'document_not_file' in codes(docs.check(ws))
    assert docs.show(ws, 'private')['status'] == 'failed'


def test_missing_external_private_setup_does_not_change_configuration(ws, tmp_path):
    external = tmp_path.parent / (tmp_path.name + '-missing.md')
    ws.state.mkdir(exist_ok=True)
    (ws.state / 'local.toml').write_text('[documents]\nprivate=' + json.dumps(str(external)), encoding='utf-8')
    before = (ws.root / 'echo-kit.toml').read_bytes()
    assert 'external_private_missing' in codes(docs.setup(ws))
    assert (ws.root / 'echo-kit.toml').read_bytes() == before
    assert not external.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction check')
def test_windows_junction_escape(ws, tmp_path):
    ready(ws)
    external = tmp_path.parent / (tmp_path.name + '-junction-target')
    external.mkdir()
    (external / 'private.md').write_text('PRIVATE JUNCTION BODY', encoding='utf-8')
    junction = ws.root / 'echo/linked'
    # Directory junctions do not require Windows symlink privileges.
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(external)], capture_output=True)
    assert made.returncode == 0, made.stderr
    shared(ws, '[outside](linked/private.md)')
    checked = docs.check(ws)
    assert 'shared_reference_outside' in codes(checked)
    assert 'PRIVATE JUNCTION BODY' not in json.dumps(checked)
