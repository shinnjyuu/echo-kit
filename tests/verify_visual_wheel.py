"""Outside-repository installed-wheel checks; real browsers are separate evidence."""
import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath
from unittest.mock import patch
from urllib.parse import urlsplit

import echo_kit
from echo_kit import __version__, documentation
from echo_kit.core import KitError, Workspace, init_workspace
from echo_kit.visual import configuration


def check_native_resources():
    native = json.loads(files('echo_kit').joinpath('resources/native-browser.json').read_text(encoding='utf-8'))
    assert native['schema_version'] == 1
    assert native['agent_browser_version'] == '0.43.0'
    assert native['chrome_version'] == '157.0.8103.78'
    assert set(native['platforms']) == {'windows-x64', 'linux-x64'}
    for platform, values in native['platforms'].items():
        assert set(values) == {'agent_browser', 'chrome'}
        for component, asset in values.items():
            url = urlsplit(asset['url'])
            assert url.scheme == 'https' and not url.username and not url.password
            assert re.fullmatch('[0-9a-f]{64}', asset['sha256'])
            assert isinstance(asset['size'], int) and 0 < asset['size'] < 512 * 1024**2
            executable = PurePosixPath(asset['executable'])
            assert not executable.is_absolute() and '..' not in executable.parts
            assert ':' not in asset['executable'] and '\\' not in asset['executable']
            if component == 'agent_browser':
                assert url.netloc == 'github.com'
                assert url.path.startswith('/vercel-labs/agent-browser/releases/download/v0.43.0/')
                assert 'archive' not in asset
                assert executable.name == ('agent-browser.exe' if platform == 'windows-x64' else 'agent-browser')
            else:
                assert url.netloc == 'storage.googleapis.com'
                assert url.path.startswith('/chrome-for-testing-public/157.0.8103.78/')
                assert asset['archive'] == 'zip'
                assert executable.name == ('chrome.exe' if platform == 'windows-x64' else 'chrome')
    # Import and missing-cache diagnostics must not require a global Node/npm executable.
    original_which = shutil.which
    def no_global_node(command, *args, **kwargs):
        if Path(command).stem.lower() in ('node', 'npm', 'npx'):
            return None
        return original_which(command, *args, **kwargs)
    with patch('shutil.which', side_effect=no_global_node):
        modules = [importlib.import_module('echo_kit.' + name) for name in
                   ('native_runtime', 'native_transport', 'native_visual', 'native_sessions')]
        assert callable(modules[2].NativeVisual.observe) and callable(modules[3].worker)
        assert modules[1].translate(['snapshot', '-c']) == {'action': 'snapshot', 'compact': True}
        try:
            modules[0].prepare(offline=True)
        except KitError as error:
            assert 'offline' in str(error).lower(), str(error)
        else:
            raise AssertionError('A fresh offline wheel check must not invent a prepared native runtime')
    return native


def main():
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    assert __version__ == '1.0.0'
    installed = importlib.metadata.distribution('shinnjyuu-echo-kit').locate_file('echo_kit/__init__.py')
    assert Path(echo_kit.__file__).resolve() == installed.resolve(), 'Run against an installed wheel, not editable source'
    os.environ['ECHO_KIT_DATA_HOME'] = str(root / 'data')
    os.environ['ECHO_KIT_TRAY'] = '0'
    os.environ['ECHO_KIT_OFFLINE'] = '1'
    def cli(*args, code=0):
        result = subprocess.run([sys.executable, '-I', '-m', 'echo_kit', '--offline', *args],
                                cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == code, (args, result.stdout, result.stderr)
        return result.stdout
    assert cli('--version').strip() == '1.0.0'
    help_text = cli('browser', 'observe', '--help')
    assert all(flag in help_text for flag in ('--reference', '--reference-root', '--spec'))
    assert all(flag in cli('browser', 'up', '--help') for flag in ('--url', '--service', '--auth', '--backend', '--session'))
    exec_help = cli('browser', 'exec', '--help')
    assert all(flag in exec_help for flag in ('--session', '--page', '--stdin'))
    assert 'reference' in exec_help and 'target' in exec_help
    for name in ('echo-init', 'echo-lab', 'echo-workbench'):
        compact = json.loads(cli('--json', 'guide', name))
        topic = json.loads(cli('--json', 'guide', name, '--topic', 'visual'))
        browser = json.loads(cli('--json', 'guide', name, '--topic', 'browser'))
        full = json.loads(cli('--json', 'guide', name, '--full'))
        assert compact['version'] == topic['version'] == browser['version'] == full['version'] == '1.0.0'
        assert compact['mode'] == 'compact' and not compact['protocol']['references']
        assert compact['protocol']['content'].startswith('## Core rules')
        assert {'visual', 'browser'} <= {item['id'] for item in compact['topics']}
        assert 'headed local Chrome' in compact['skill']['content']
        assert topic['topic'] == 'visual' and topic['protocol']['scope'] == 'topic'
        assert any(item['path'] == 'references/visual-comparison.md' for item in topic['protocol']['references'])
        assert 'exec --session SESSION_ID --page reference' in documentation.render_guide(topic)
        assert 'backend="playwright"' in documentation.render_guide(browser)
        assert '## Visible browser' in full['protocol']['content']
    destination = root / 'exported'
    documentation.export(destination)
    assert (destination / 'references/visual-comparison.md').is_file()
    assert (destination / 'references/protocol.md').read_text(encoding='utf-8') == documentation.show()['content']
    assert 'reference: options' in (destination / 'references/observe-client.mjs').read_text(encoding='utf-8')
    rejected = cli('--json', 'skills', 'export', str(destination), code=2)
    assert 'not overwriting' in rejected
    # Compatibility assets stay distributable without executing their Node bridge.
    data = files('echo_kit').joinpath('resources/pixelmatch')
    provenance = json.loads(data.joinpath('provenance.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(data.joinpath('index.mjs').read_bytes()).hexdigest() == provenance['sha256']
    assert data.joinpath('bridge.cjs').is_file() and data.joinpath('LICENSE').is_file()
    assert files('echo_kit').joinpath('resources/visual-inspect.js').is_file()
    native = check_native_resources()
    init_workspace(root / 'workspace')
    workspace = root / 'workspace'
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    (workspace / 'prototype.html').write_text('<h1>Controlled prototype</h1>', encoding='utf-8')
    (workspace / 'spec.md').write_text('# Requirement\nHeader is 62px high.', encoding='utf-8')
    ws = Workspace(workspace)
    from echo_kit.browser import selected_backend
    assert selected_backend(ws) == 'agent-browser'
    metadata = configuration(ws, 'prototype.html', spec='spec.md')
    assert metadata['spec']['lines'] == 2 and 'body' not in metadata['spec']
    notes = json.loads(cli('--json', 'updates', 'notes'))
    assert notes['releases'][0]['version'] == '1.0.0'
    assert 'agent-browser' in notes['releases'][0]['changes']
    result = {'version': __version__, 'python': sys.executable,
                      'installed_wheel': 'verified', 'resources': 'verified', 'guides': 3,
                      'runtime_manifest': {'agent_browser': native['agent_browser_version'], 'chrome': native['chrome_version'],
                                           'official_urls_sha256_and_executable_layout': 'verified'},
                      'global_node_requirement': 'none for native import, CLI and offline preparation diagnostics',
                      'compatibility_pixelmatch': 'bundled hash/resources verified; bridge not executed',
                      'native_browser_execution': 'separate real-browser contract evidence',
                      'export_overwrite': 'rejected'}
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
