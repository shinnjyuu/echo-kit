"""Echo Kit project entrypoint, launcher protocol 1. No business dependencies."""
import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument('--task')
    options.add_argument('--workspace')
    known, _ = options.parse_known_args()
    if known.workspace:
        raise ValueError('This entrypoint belongs to its project; do not pass --workspace')
    selection = json.loads((root / 'echo-kit.lock.json').read_text(encoding='utf-8'))
    if selection.get('schema_version') != 1 or selection.get('package') != 'shinnjyuu-echo-kit':
        raise ValueError('Unknown project version file')
    if known.task:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', known.task):
            raise ValueError('Invalid task ID')
        selection = json.loads((root / '.echo-kit/tasks' / (known.task + '.json')).read_text(encoding='utf-8'))
        if selection.get('state') != 'open':
            raise ValueError('Task is finished; start a new task')
    version = selection['version']
    # Exact release only, never a requirement supplied as an option or URL.
    if not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+(?:\.post[0-9]+)?', version):
        raise ValueError('Invalid pinned release version')
    try:
        installed = importlib.metadata.version('shinnjyuu-echo-kit') == version
    except importlib.metadata.PackageNotFoundError:
        installed = False
    if installed:
        command = [sys.executable, '-I', '-m', 'echo_kit']
    else:
        uv = shutil.which('uv')
        if uv is None:
            raise ValueError('Install uv to run the project-selected Echo Kit version')
        command = [uv, 'tool', 'run', '--no-config', '--default-index', 'https://pypi.org/simple',
                   '--python', sys.executable, '--from', 'shinnjyuu-echo-kit==' + version]
        if '--offline' in sys.argv[1:] or any(os.environ.get(key, '').lower() in ('1', 'true', 'yes')
                                              for key in ('ECHO_KIT_OFFLINE', 'UV_OFFLINE')):
            command.append('--offline')
        command.append('echo-kit')
    env = dict(os.environ)
    for name in ('PYTHONPATH', 'UV_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL', 'UV_DEFAULT_INDEX',
                 'UV_FIND_LINKS', 'UV_NO_INDEX', 'UV_CONSTRAINT', 'UV_OVERRIDE', 'UV_CONFIG_FILE'):
        env.pop(name, None)
    env['ECHO_KIT_EXPECTED_VERSION'] = version
    return subprocess.call(command + ['--workspace', str(root)] + sys.argv[1:], cwd=root, env=env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}), file=sys.stderr)
        raise SystemExit(2)
