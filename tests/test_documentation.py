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
