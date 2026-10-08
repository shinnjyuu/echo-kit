"""Compatibility entry for migrated projects. No project version state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    command = shutil.which('echo-kit')
    if not command:
        raise ValueError('Install Echo Kit first: uv tool install shinnjyuu-echo-kit')
    env = dict(os.environ)
    for key in ('ECHO_KIT_EXPECTED_VERSION', 'ECHO_KIT_FORWARDED_VERSION'):
        env.pop(key, None)
    version = subprocess.run([command, '--version'], env=env, capture_output=True, text=True)
    try:
        numbers = tuple(int(n) for n in version.stdout.strip().split('.')[:3])
    except ValueError:
        numbers = ()
    if version.returncode or numbers < (0, 4, 0):
        raise ValueError('Install Echo Kit >= 0.4.0: uv tool install --upgrade shinnjyuu-echo-kit')
    if any(arg == '--workspace' or arg.startswith('--workspace=') for arg in sys.argv[1:]):
        raise ValueError('This entry belongs to its project; do not pass --workspace')
    return subprocess.call([command, '--workspace', str(Path(__file__).resolve().parent)] + sys.argv[1:], env=env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}), file=sys.stderr)
        raise SystemExit(2)
