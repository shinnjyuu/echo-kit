"""Controlled release discovery, real 0.15.0 startup/probe/forwarding and 1.0.0 execution."""
import json
import os
import subprocess
import sys
from pathlib import Path

from echo_kit import __version__


def main():
    assert __version__ == '0.15.0', 'Run this helper using the separately installed official old package'
    candidate = str(Path(sys.argv[1]).resolve())
    root = Path(sys.argv[2]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if key not in ('ECHO_KIT_OFFLINE', 'UV_OFFLINE', 'ECHO_KIT_EXPECTED_VERSION',
                          'ECHO_KIT_FORWARDED_VERSION', 'ECHO_KIT_SOURCE_VERSION', 'ECHO_KIT_UPDATE_ROUTE')}
    env.update(ECHO_KIT_DATA_HOME=str(root / 'data'), ECHO_KIT_TRAY='0', PYTHONIOENCODING='utf-8')
    assert Path(candidate).is_file(), 'Candidate must be the separately installed 1.0.0 interpreter'
    commands = [(['--version'], 'version'),
                (['--json', 'guide', 'echo-workbench'], 'compact-guide'),
                (['--json', 'guide', 'echo-workbench', '--topic', 'visual'], 'visual-guide'),
                (['browser', 'observe', '--reference', 'prototype.html', '--spec', 'spec.md', '--help'], 'observe-help'),
                (['browser', 'up', '--backend', 'agent-browser', '--url', 'http://localhost:5173',
                  '--session', '0' * 32, '--help'], 'new-startup-help'),
                (['browser', 'exec', '--session', '0' * 32, '--page', 'reference', '--help'], 'new-command-help')]
    verified = []
    for args, name in commands:
        # Only discovery/preparation location is controlled because 1.0.0 is not yet public.
        # startup(), the runtime probe, CLI parsing and child business execution are real.
        script = ("from echo_kit import __version__,updates; from echo_kit.cli import main; "
                  "assert __version__=='0.15.0'; "
                  "updates.fetch_releases=lambda:{'1.0.0':['>=3.11']}; "
                  "updates.command_prefix=lambda version,*a,**k:" + repr([candidate, '-I', '-m', 'echo_kit']) + '; '
                  'raise SystemExit(main(' + repr(args) + '))')
        result = subprocess.run([sys.executable, '-I', '-c', script], cwd=root, env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=180)
        (root / (name + '.stdout')).write_text(result.stdout, encoding='utf-8')
        (root / (name + '.stderr')).write_text(result.stderr, encoding='utf-8')
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        if name == 'version':
            assert result.stdout.strip() == '1.0.0'
        elif name in ('compact-guide', 'visual-guide'):
            guide = json.loads(result.stdout)
            assert guide['version'] == '1.0.0'
            if name == 'compact-guide':
                assert guide['mode'] == 'compact' and guide['topic'] is None
                assert 'headed local Chrome' in guide['skill']['content']
                assert not guide['protocol']['references']
                assert {'browser', 'visual'} <= {topic['id'] for topic in guide['topics']}
            else:
                assert guide['topic'] == 'visual'
                assert any(ref['path'] == 'references/visual-comparison.md' for ref in guide['protocol']['references'])
                assert '--page reference' in guide['protocol']['references'][0]['content']
        elif name == 'observe-help':
            assert '--reference-root' in result.stdout and '--spec' in result.stdout
        elif name == 'new-startup-help':
            assert all(flag in result.stdout for flag in ('--backend', '--url', '--service', '--auth', '--session'))
        else:
            assert all(flag in result.stdout for flag in ('--session', '--page', '--stdin'))
            assert 'reference' in result.stdout and 'target' in result.stdout
        verified.append(name)
    assert not (root / 'echo-kit.toml').exists()
    result = {'installed_entry': __version__, 'candidate_execution': '1.0.0',
                      'real_probe_and_forward': 'passed', 'new_parameters_before_old_parser': 'passed',
                      'native_command_and_reference_help': 'passed', 'verified_commands': verified,
                      'discovery': 'controlled candidate metadata, not a claim of PyPI publication'}
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
