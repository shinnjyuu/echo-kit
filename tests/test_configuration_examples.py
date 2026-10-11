"""Exercise published examples through the actual workspace parser."""
import json
import re
import subprocess
import sys

from echo_kit.core import Workspace
from echo_kit.documentation import resources


def pairs():
    content = resources()['references/protocol.md'].decode().split('### Paired configuration examples')[1].split('\n## ')[0]
    blocks = re.findall(r'```toml\r?\n(.*?)```', content, re.S)
    assert len(blocks) == 8
    return list(zip(blocks[::2], blocks[1::2]))


def workspace(root, pair):
    root.mkdir(exist_ok=True)
    (root / '.echo-kit').mkdir(exist_ok=True)
    (root / 'echo-kit.toml').write_text(pair[0], encoding='utf-8')
    (root / '.echo-kit/local.toml').write_text(pair[1], encoding='utf-8')
    return Workspace(root)


def test_browser_and_environment_examples(tmp_path):
    examples = pairs()
    for index in (0, 2):
        ws = workspace(tmp_path / str(index), examples[index])
        assert ws.cfg['browser']['port'] == 19423
        assert ws.cfg['browser']['viewer_port'] == 19424
        (ws.state / 'local.toml').unlink()
        assert Workspace(ws.root).cfg['browser']['port'] == 19323
    ws = workspace(tmp_path / 'precedence', (examples[2][0], examples[0][1]))
    assert ws.cfg['browser']['port'] == 19323


def test_shared_command_reads_local_input(tmp_path):
    ws = workspace(tmp_path, pairs()[1])
    (tmp_path / 'echo').mkdir()
    (tmp_path / 'echo/start_web.py').write_text('import os; print(os.environ["WEB_PORT"])')
    spec = ws.cfg['services']['web']
    assert spec['port'] == 3100
    assert spec['ready']['url'] == 'http://127.0.0.1:3100/health'
    assert ws.command(spec) == [sys.executable, 'echo/start_web.py']
    result = subprocess.run(ws.command(spec), cwd=ws.project('web'), env=ws.env(spec),
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '3100'
    with (ws.state / 'local.toml').open('a') as stream:
        stream.write('\n[services.other]\ncommand = ["new"]\n')
    with (ws.root / 'echo-kit.toml').open('a') as stream:
        stream.write('\n[services.other]\ncommand = ["old", "argument"]\n')
    assert Workspace(tmp_path).cfg['services']['other']['command'] == ['new']


def test_absolute_source_override(tmp_path):
    shared, local = pairs()[3]
    source = tmp_path / '个人 source'
    source.mkdir()
    local = re.sub(r"path = .*", lambda _: 'path = ' + json.dumps(str(source)), local)
    ws = workspace(tmp_path / 'workspace', (shared, local))
    assert ws.project('api') == source.resolve()


def test_ignored_is_not_untracked(tmp_path):
    workspace(tmp_path, pairs()[0])
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], capture_output=True, check=True)
    git('init')
    git('add', '.echo-kit/local.toml')
    (tmp_path / '.gitignore').write_text('.echo-kit/\n')
    assert git('check-ignore', '--no-index', '.echo-kit/local.toml').stdout.strip()
    assert git('ls-files', '--', '.echo-kit/local.toml').stdout.strip()
