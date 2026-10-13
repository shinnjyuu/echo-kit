"""Explicit shared TOML fragments and value-free configuration provenance."""
from pathlib import Path
import subprocess
import tomllib

from .core import KitError, merge
from .project_docs import Git, absolute, inside

ENTITIES = {'projects', 'services', 'auth', 'cases'}


def parse(path, name):
    try:
        return tomllib.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError):
        # TOML errors may contain source text, including credentials.
        raise KitError(f'Cannot read or parse configuration: {name}') from None


class Configuration:
    def __init__(self, root):
        self.root = root
        self.files = []
        self.origins = []
        self.git = Git()
        self.gitlinks = None
        self.boundary_unverified = False
        self.shared = parse(root / 'echo-kit.toml', 'echo-kit.toml')
        local_path = root / '.echo-kit/local.toml'
        self.local = parse(local_path, '.echo-kit/local.toml') if local_path.exists() else {}
        self.add('echo-kit.toml', 'shared', self.shared)
        includes = self.shared.get('includes', [])
        if not isinstance(includes, list) or not all(isinstance(v, str) and v for v in includes):
            raise KitError('includes must be an array of relative TOML paths')
        self.reject_includes(self.local, '.echo-kit/local.toml', private=True)
        seen = [root / 'echo-kit.toml']
        for name in includes:
            path = self.include_path(name)
            if any(path == other or path.samefile(other) for other in seen):
                raise KitError(f'Duplicate configuration file: {name}')
            seen.append(path)
            cfg = parse(path, name)
            if set(cfg) - ENTITIES - {'environments'}:
                raise KitError(f'Module configuration only accepts projects/services/auth/cases/environments: {name}')
            envs = cfg.get('environments', {})
            if not isinstance(envs, dict) or any(not isinstance(v, dict) or set(v) - ENTITIES for v in envs.values()):
                raise KitError(f'Module environments only accept entity tables: {name}')
            self.add(name.replace('\\', '/'), 'shared', cfg)
            self.shared = merge(self.shared, cfg)
        if local_path.exists():
            self.add('.echo-kit/local.toml', 'private', self.local)
        self.cfg = merge(self.shared, self.local)
        self.cfg.pop('includes', None)

    @staticmethod
    def reject_includes(cfg, name, private=False):
        envs = cfg.get('environments', {})
        if ((private and 'includes' in cfg) or
                (isinstance(envs, dict) and any(isinstance(v, dict) and 'includes' in v for v in envs.values()))):
            raise KitError(f'includes is only supported in the shared root: {name}')

    def add(self, name, scope, cfg):
        self.reject_includes(cfg, name)
        envs = cfg.get('environments', {})
        if not isinstance(envs, dict) or any(not isinstance(v, dict) for v in envs.values()):
            raise KitError(f'environments must contain tables: {name}')
        for environment, table in [(None, cfg), *envs.items()]:
            for kind in ENTITIES:
                values = table.get(kind, {})
                if not isinstance(values, dict) or any(not isinstance(v, dict) for v in values.values()):
                    raise KitError(f'Expected entity tables at {kind}: {name}')
                for entity in values:
                    key = f'{kind}.{entity}'
                    previous = next((r for r in self.origins if r['key'] == key and
                                     r['environment'] == environment and r['scope'] == scope == 'shared'), None)
                    if previous:
                        prefix = f'environments.{environment}.' if environment is not None else ''
                        raise KitError(f'Duplicate shared definition {prefix}{key}: {previous["path"]} and {name}')
                    self.origins.append({'path': name, 'scope': scope, 'key': key, 'environment': environment})
        self.files.append({'path': name, 'scope': scope})

    def include_path(self, name):
        if absolute(name) or any(c in name for c in '*?[]'):
            raise KitError('include must be a relative TOML path without wildcards')
        lexical = self.root / name.replace('\\', '/')
        try:
            path = lexical.resolve()
            if path.suffix.lower() != '.toml' or not inside(path, self.root):
                raise KitError('include must resolve to a TOML file inside the workspace')
            if inside(path, (self.root / '.echo-kit').resolve()) or '.echo-kit' in lexical.relative_to(self.root).parts:
                raise KitError('Shared include cannot reference private configuration')
            documents = self.local.get('documents', {})
            private = documents.get('private') if isinstance(documents, dict) else None
            if isinstance(private, str):
                private_path = (self.root / private.replace('\\', '/')).resolve()
                if inside(path, private_path) or (path.exists() and private_path.is_file() and path.samefile(private_path)):
                    raise KitError('Shared include cannot reference registered private material')
            for candidate in (lexical, path):
                for parent in (candidate, *candidate.parents):
                    if parent == self.root or parent.resolve() == self.root:
                        break
                    if parent.name == '.git' or (parent.is_dir() and (parent / '.git').exists()):
                        raise KitError('include cannot enter a nested repository')
            if self.gitlinks is None:
                self.gitlinks = []
                try:
                    result = subprocess.run(['git', '-C', str(self.root), 'ls-files', '--stage', '-z'],
                                            capture_output=True, timeout=5)
                    if result.returncode:
                        self.boundary_unverified = True
                    else:
                        self.gitlinks = [self.root / entry.split('\t', 1)[1]
                                         for entry in result.stdout.decode('utf-8').split('\0')
                                         if entry.startswith('160000 ') and '\t' in entry]
                except (OSError, UnicodeError, subprocess.TimeoutExpired):
                    self.boundary_unverified = True
            if any(inside(path, link) or inside(lexical, link) for link in self.gitlinks):
                raise KitError('include cannot enter a submodule')
            for candidate in (lexical, path):
                if self.git.state(candidate)['ignored']:
                    raise KitError('Shared include cannot reference Git-ignored material')
            if not path.is_file():
                raise KitError(f'Missing configuration file: {name}')
            return path
        except (OSError, RuntimeError, ValueError):
            raise KitError('Cannot verify include path') from None

    def sources(self, kind, name, environment):
        rows = [r for r in self.origins if r['key'] == f'{kind}.{name}' and
                (r['environment'] is None or r['environment'] == environment)]
        rows.sort(key=lambda r: (r['environment'] is not None, r['scope'] == 'private'))
        return [{**r, 'stage': 'environment' if r['environment'] is not None else
                 ('local' if r['scope'] == 'private' else 'base')} for r in rows]

    def report(self):
        files, issues = [], []
        for item in self.files:
            state = self.git.state(self.root / item['path'])
            reason = None
            if not state['available'] or not state['repository']:
                reason = 'git_unavailable'
            elif item['scope'] == 'shared' and self.boundary_unverified:
                reason = 'git_boundary_unverified'
            elif item['scope'] == 'shared' and (not state['tracked'] or state['ignored']):
                reason = 'shared_ignored' if state['ignored'] else 'shared_untracked'
            elif item['scope'] == 'private' and (state['tracked'] or not state['ignored']):
                reason = 'private_tracked' if state['tracked'] else 'private_not_ignored'
            if reason:
                issues.append({'path': item['path'], 'reason': reason, 'status': 'unverified'})
            files.append({**item, 'git': state, 'status': 'unverified' if reason else 'passed'})
        return {'status': 'unverified' if issues else 'passed', 'files': files, 'issues': issues}
