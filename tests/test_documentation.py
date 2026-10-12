import json
from pathlib import Path

import pytest

from echo_kit import __version__, documentation
from echo_kit.cli import main
from echo_kit.core import KitError


def test_read_documents_without_workspace_or_network(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(['--json', 'skills', 'list']) == 0
    catalog = json.loads(capsys.readouterr().out)
    assert {s['name'] for s in catalog['skills']} == {'echo-init', 'echo-lab', 'echo-workbench'}
    for name in ('echo-init', 'echo-lab', 'echo-workbench'):
        assert main(['--json', 'skills', 'show', name]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['version'] == result['documentation_version'] == __version__
        assert result['content'].encode() == documentation.resources()[name + '/SKILL.md']
    assert main(['--json', 'protocol', 'show']) == 0
    protocol = json.loads(capsys.readouterr().out)
    assert protocol['content'].encode() == documentation.resources()['references/protocol.md']
    assert not (tmp_path / 'echo-kit.toml').exists()
    assert main(['--json', 'skills', 'show', '../outside']) == 2


def test_export_provenance_and_project_edits(tmp_path, monkeypatch):
    dest = tmp_path / 'skills'
    documentation.export(dest)
    baseline = (dest / documentation.MANIFEST).read_bytes()
    assert documentation.export_status(dest)['modified'] == []
    changed = dest / 'echo-init/SKILL.md'
    changed.write_text(changed.read_text(encoding='utf-8') + '\nProject-specific rule.\n', encoding='utf-8')
    monkeypatch.setattr(documentation, '__version__', '0.2.1')
    status = documentation.export_status(dest)
    assert status['update_available']
    assert status['exported_version'] == __version__
    assert status['modified'] == ['echo-init/SKILL.md']
    assert (dest / documentation.MANIFEST).read_bytes() == baseline
    with pytest.raises(KitError, match='not overwriting'):
        documentation.export(dest)
    assert 'Project-specific rule.' in changed.read_text(encoding='utf-8')


def test_unknown_and_escaping_export_manifests(tmp_path):
    assert documentation.export_status(tmp_path)['status'] == 'unverified'
    documentation.export(tmp_path)
    path = tmp_path / documentation.MANIFEST
    value = json.loads(path.read_text())
    value['files']['../outside'] = 'x'
    path.write_text(json.dumps(value))
    with pytest.raises(KitError, match='manifest path'):
        documentation.export_status(tmp_path)


def test_public_protocol_matches_bundled_copy():
    public = Path(__file__).resolve().parents[1] / 'docs/protocol.md'
    assert documentation.digest(public.read_bytes()) == documentation.digest(
        documentation.resources()['references/protocol.md'])


@pytest.mark.parametrize('name', ['echo-init', 'echo-lab', 'echo-workbench'])
def test_identity_reference_read_and_export_same_package(name, tmp_path, capsys):
    reference = 'references/test-identities.md'
    body = documentation.resources()[reference].decode('utf-8')
    assert main(['--offline', '--json', 'skills', 'show', name]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['references'] == []
    assert result['content'] == documentation.resources()[name + '/SKILL.md'].decode('utf-8')
    assert body not in documentation.render_guide(documentation.guide(name))
    for flags in (['--topic', 'auth'], ['--full']):
        assert main(['--offline', 'guide', name, *flags]) == 0
        assert body in capsys.readouterr().out
    documentation.export(tmp_path)
    exported = tmp_path / reference
    assert exported.read_bytes().decode('utf-8') == body
    exported.write_text('Project-owned edits', encoding='utf-8')
    with pytest.raises(KitError, match='not overwriting'):
        documentation.export(tmp_path)
    assert exported.read_text(encoding='utf-8') == 'Project-owned edits'


@pytest.mark.parametrize('name', ['echo-init', 'echo-lab', 'echo-workbench'])
def test_guide_same_package_offline(tmp_path, monkeypatch, capsys, name):
    from echo_kit import updates
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(updates, 'fetch_releases', lambda: pytest.fail('network'))
    assert main(['--offline', '--json', 'guide', name]) == 0
    result = json.loads(capsys.readouterr().out)
    for item in (result, result['skill'], result['protocol'], result['updates']):
        assert item['version'] == __version__
        assert item['documentation_digest'] == result['documentation_digest']
    assert result['skill']['content'] == documentation.show(name)['content']
    assert result['updates']['releases'][0]['version'] == __version__
    assert not list(tmp_path.iterdir())


def test_notes_ranges_and_export(tmp_path, capsys):
    assert [e['version'] for e in documentation.notes('0.5.0')['releases']] == ['0.6.0', '0.7.0', '0.8.0', '0.8.1', '0.8.2', '0.8.3', '0.8.4', '0.9.0', '0.10.0', '0.11.0', '0.11.1']
    assert documentation.notes('0.2.0')['warning']
    assert documentation.notes('9.0.0')['warning']
    assert documentation.notes(__version__)['releases'] == []
    with pytest.raises(KitError):
        documentation.notes('bad')
    documentation.export(tmp_path)
    assert json.loads((tmp_path / 'references/releases.json').read_text())
    assert main(['--offline', '--json', 'updates', 'notes', '--since', '0.5.0']) == 0
    assert len(json.loads(capsys.readouterr().out)['releases']) == 11
    assert main(['--offline', 'guide', 'echo-init']) == 0
    assert __version__ in capsys.readouterr().out
