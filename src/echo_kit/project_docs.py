"""Project-owned documentation; separate from the distribution's Skill resources."""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections import deque
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit

import tomlkit
from markdown_it import MarkdownIt

from .core import KitError

DEFAULTS = {'shared': 'echo/README.md', 'private': '.echo-kit/private/README.md'}
MAX_FILES = 500
MAX_BYTES = 2 * 1024 * 1024
PERSONAL_PATH = re.compile(r'(?<![\w/])(?:[A-Za-z]:[\\/]|\\\\[^\s\\]+\\|/(?:Users|home)/)')


def absolute(value):
    return value.startswith(('/', '\\')) or bool(PureWindowsPath(value).drive)


def inside(path, root):
    return path == root or path.is_relative_to(root)


def label(ws, path):
    return path.relative_to(ws.root).as_posix() if inside(path, ws.root) else str(path)


def result(issues, **fields):
    status = 'failed' if any(i['status'] == 'failed' for i in issues) else 'unverified' if issues else 'passed'
    return {'status': status, **fields, 'issues': issues}


def issue(code, path, status='failed', line=None):
    # Never include source lines or target contents in diagnostics.
    return {'code': code, 'path': str(path), 'status': status, **({'line': line} if line else {})}


def configuration(ws):
    entries, issues = {}, []
    for scope, filename in (('shared', 'echo-kit.toml'), ('private', '.echo-kit/local.toml')):
        path = ws.root / filename
        cfg = tomllib.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        docs = cfg.get('documents', {})
        if not isinstance(docs, dict) or set(docs) - {scope}:
            issues.append(issue('invalid_document_source', filename))
            continue
        envs = cfg.get('environments', {})
        if isinstance(envs, dict) and any(isinstance(v, dict) and 'documents' in v for v in envs.values()):
            issues.append(issue('environment_documents_forbidden', filename))
        value = docs.get(scope)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            issues.append(issue('invalid_document_path', filename))
            continue
        if scope == 'shared' and absolute(value):
            issues.append(issue('shared_absolute_path', filename))
            continue
        # A Windows path on POSIX must not be interpreted as a relative filename.
        if os.name != 'nt' and (PureWindowsPath(value).drive or value.startswith('\\')):
            issues.append(issue('foreign_private_path', filename, 'unverified'))
            continue
        try:
            target = (ws.root / value.replace('\\', '/')).resolve()
        except (OSError, RuntimeError, ValueError):
            issues.append(issue('invalid_document_path', filename))
            continue
        if scope == 'shared' and (not inside(target, ws.root) or inside(target, ws.state.resolve())):
            issues.append(issue('shared_outside_or_private', filename))
            continue
        entries[scope] = target
    if entries.get('private') == entries.get('shared') and entries:
        issues.append(issue('document_scope_collision', 'echo-kit.toml'))
    return entries, issues


class Git:
    def __init__(self):
        self.cache = {}

    def state(self, path):
        """Inspect the path's own repository, including repositories outside workspace."""
        if path in self.cache:
            return self.cache[path]
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        state = {'repository': None, 'tracked': None, 'ignored': None, 'available': True}
        try:
            def run(*args):
                return subprocess.run(['git', '-C', str(parent), *args], capture_output=True,
                                      text=True, encoding='utf-8', errors='replace', timeout=5)
            root = run('rev-parse', '--show-toplevel')
            if root.returncode == 0:
                repo = Path(root.stdout.strip()).resolve()
                tracked = run('--literal-pathspecs', 'ls-files', '--error-unmatch', '--', str(path))
                ignored = run('check-ignore', '--no-index', '-q', '--', str(path))
                if tracked.returncode not in (0, 1) or ignored.returncode not in (0, 1):
                    raise OSError('Git inspection failed')
                state = {'repository': str(repo), 'available': True, 'tracked': tracked.returncode == 0,
                         'ignored': ignored.returncode == 0}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            state['available'] = False
        self.cache[path] = state
        return state


def catalog(ws):
    entries, issues = configuration(ws)
    git = Git()
    if not entries and not issues:
        return {'status': 'unverified', 'configured': False, 'reason': 'not_configured',
                'documents': [], 'issues': [], 'next_step': 'docs setup'}
    return result(issues, configured='shared' in entries, next_step='docs check' if entries else 'docs setup',
                  documents=[{'scope': scope, 'path': label(ws, path), 'exists': path.is_file(),
                              'git': git.state(path)} for scope, path in entries.items()])


def summary(ws):
    # Advisory only: document failures must not block runtime compatibility probes.
    try:
        entries, issues = configuration(ws)
        return {'configured': 'shared' in entries, 'configuration_valid': not issues,
                'next_step': 'docs check' if entries or issues else 'docs setup'}
    except (OSError, ValueError):
        return {'configured': False, 'configuration_valid': False, 'next_step': 'docs check'}


def read_document(path):
    if path.stat().st_size > MAX_BYTES:
        raise KitError('Document exceeds 2 MiB limit')
    return path.read_text(encoding='utf-8-sig')


def show(ws, scope):
    entries, issues = configuration(ws)
    if issues:
        return result(issues)
    if scope not in entries:
        return {'status': 'unverified', 'reason': 'not_configured', 'next_step': 'docs setup'}
    path = entries[scope]
    if path.exists() and not path.is_file():
        return result([issue('document_not_file', label(ws, path))])
    if not path.is_file():
        return {'status': 'failed' if scope == 'shared' else 'passed', 'scope': scope,
                'path': label(ws, path), 'exists': False, 'reason': 'missing_document'}
    if scope == 'shared' and private_path(ws, path, entries):
        return result([issue('shared_private_reference', label(ws, path))])
    if scope == 'shared' and Git().state(path)['ignored']:
        return result([issue('shared_ignored', label(ws, path))])
    return {'status': 'passed', 'scope': scope, 'path': label(ws, path), 'exists': True,
            'content': read_document(path)}


def private_path(ws, path, entries):
    return inside(path, ws.state.resolve()) or path == entries.get('private')


class HTMLLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src') and value:
                self.links.append((value, self.getpos()[0]))


def links(body):
    parser = MarkdownIt('commonmark')
    # Inspect all link destinations, even protocols normally rejected for rendering.
    parser.validateLink = lambda value: True
    for token in parser.parse(body):
        line = token.map[0] + 1 if token.map else 1
        if token.type == 'html_block':
            html = HTMLLinks()
            html.feed(token.content)
            yield from ((href, line + offset - 1) for href, offset in html.links)
        if token.type != 'inline':
            continue
        current = line
        for child in token.children or []:
            if child.type in ('softbreak', 'hardbreak'):
                current += 1
            if child.type in ('link_open', 'image'):
                yield child.attrGet('href' if child.type == 'link_open' else 'src'), current
            elif child.type == 'html_inline':
                html = HTMLLinks()
                html.feed(child.content)
                yield from ((href, current + offset - 1) for href, offset in html.links)


def destination(href, parent):
    decoded = unquote(href)
    if not decoded or decoded.startswith('#'):
        return None, None
    if absolute(decoded) or decoded.lower().startswith('file:'):
        return None, 'shared_absolute_reference'
    split = urlsplit(href)
    if split.scheme:
        return None, None
    value = unquote(split.path).replace('\\', '/')
    return (parent / value).resolve(), None


def private_destination(href, parent):
    if not href or href.startswith('#'):
        return None
    decoded = unquote(href)
    if decoded.lower().startswith('file:'):
        parts = urlsplit(href)
        value = unquote(parts.path)
        if parts.netloc and parts.netloc != 'localhost':
            value = '//' + parts.netloc + value
        elif os.name == 'nt' and re.match(r'^/[A-Za-z]:', value):
            value = value[1:]
    elif absolute(decoded):
        value = unquote(href.split('#', 1)[0].split('?', 1)[0])
    else:
        parts = urlsplit(href)
        if parts.scheme:
            return None
        value = unquote(parts.path)
    if os.name != 'nt' and (PureWindowsPath(value).drive or value.startswith('\\')):
        raise ValueError('Foreign private reference')
    return (parent / value.replace('\\', '/')).resolve()


def check(ws):
    entries, issues = configuration(ws)
    if not entries and not issues:
        return {'status': 'unverified', 'reason': 'not_configured', 'issues': [], 'next_step': 'docs setup'}
    if issues:
        return result(issues)
    if 'shared' not in entries:
        issues.append(issue('shared_not_configured', 'echo-kit.toml', 'unverified'))
    git, visited = Git(), set()
    local = ws.state / 'local.toml'
    if 'private' in entries:
        local_state = git.state(local)
        if local_state['tracked']:
            issues.append(issue('private_config_tracked', '.echo-kit/local.toml'))
        elif local.exists() and local_state['repository'] and not local_state['ignored']:
            issues.append(issue('private_config_not_ignored', '.echo-kit/local.toml'))
    queue = deque([('shared', entries['shared'])] if 'shared' in entries else [])
    private_pending = entries.get('private')
    while queue or private_pending is not None:
        if not queue:
            queue.append(('private', private_pending))
            private_pending = None
        scope, path = queue.popleft()
        key = (scope, path)
        if key in visited:
            continue
        if len(visited) >= MAX_FILES:
            issues.append(issue('scan_limit', label(ws, path), 'unverified'))
            break
        visited.add(key)
        name = label(ws, path)
        state = git.state(path)
        if scope == 'shared':
            if private_path(ws, path, entries) or state['ignored']:
                issues.append(issue('shared_private_or_ignored', name))
                continue
            if not path.is_file():
                issues.append(issue('shared_missing_file', name))
                continue
            if state['tracked'] is not True:
                issues.append(issue('shared_untracked' if state['repository'] else 'git_unavailable', name, 'unverified'))
        else:
            if state['tracked']:
                issues.append(issue('private_tracked', name))
            elif path.is_file() and state['repository'] and not state['ignored']:
                issues.append(issue('private_not_ignored', name))
            elif path.is_file() and (not state['available'] or (inside(path, ws.root) and not state['repository'])):
                issues.append(issue('git_unavailable', name, 'unverified'))
            if not path.is_file():
                if path.exists():
                    issues.append(issue('document_not_file', name))
                elif path != entries.get('private'):
                    issues.append(issue('private_missing_reference', name, 'unverified'))
                continue  # Optional missing entry can still be tracked in the index.
        if path.suffix.lower() not in ('.md', '.markdown'):
            continue
        try:
            body = read_document(path)
        except (OSError, UnicodeError, KitError):
            issues.append(issue('document_unreadable_or_too_large', name, 'unverified'))
            continue
        if scope == 'shared':
            for line, text in enumerate(body.splitlines(), 1):
                if PERSONAL_PATH.search(text):
                    issues.append(issue('possible_personal_path', name, 'unverified', line))
        for href, line in links(body):
            if scope == 'shared':
                try:
                    target, error = destination(href, path.parent)
                except (OSError, RuntimeError, ValueError):
                    target, error = None, 'invalid_reference'
                if error:
                    issues.append(issue(error, name, line=line))
                    continue
                if target is None:
                    continue
                if not inside(target, ws.root):
                    issues.append(issue('shared_reference_outside', name, line=line))
                elif private_path(ws, target, entries) or git.state(target)['ignored']:
                    issues.append(issue('shared_private_reference', name, line=line))
                elif not target.is_file():
                    issues.append(issue('shared_missing_reference', name, line=line))
                else:
                    queue.append(('shared', target))
            else:
                # Follow explicit references only, never enumerate personal directories.
                # Documents discovered through the shared entry retain shared ownership.
                try:
                    target = private_destination(href, path.parent)
                    if target is not None and ('shared', target) not in visited:
                        queue.append(('private', target))
                except (OSError, RuntimeError, ValueError):
                    issues.append(issue('invalid_private_reference', name, 'unverified', line))
    return result(issues, checked_files=len(visited), next_step='Fix reported files; docs check never changes Git or documents.')


def setup(ws):
    from filelock import FileLock
    # A lock outside private document trees protects configuration updates.
    if ws.state.is_symlink() or not inside(ws.state.resolve(), ws.root):
        raise KitError('Refusing to write through a linked state directory')
    ws.state.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ws.state / 'documents.lock'), timeout=1):
        return _setup(ws)


def _setup(ws):
    entries, issues = configuration(ws)
    if issues:
        return result(issues, created=[], reused=[])
    paths = {scope: entries.get(scope, (ws.root / default).resolve()) for scope, default in DEFAULTS.items()}
    for filename in ('echo-kit.toml', '.echo-kit/local.toml', '.gitignore'):
        if (ws.root / filename).is_symlink():
            raise KitError('Refusing to modify symlinked document configuration')
    # Preflight all destinations before touching existing configuration.
    if paths['shared'] == paths['private'] or private_path(ws, paths['shared'], paths):
        return result([issue('document_scope_collision', 'echo-kit.toml')], created=[], reused=[])
    for scope, path in paths.items():
        if scope == 'shared' and not inside(path, ws.root):
            return result([issue('shared_outside_or_private', 'echo-kit.toml')], created=[], reused=[])
        if path.exists() and not path.is_file():
            return result([issue('document_not_file', label(ws, path))], created=[], reused=[])
        if scope == 'private' and not inside(path, ws.root) and not path.is_file():
            return result([issue('external_private_missing', '.echo-kit/local.toml', 'unverified')], created=[], reused=[])
    created, reused = [], []
    for scope, filename in (('shared', 'echo-kit.toml'), ('private', '.echo-kit/local.toml')):
        config = ws.root / filename
        if config.is_symlink():
            raise KitError('Refusing to modify symlinked document configuration')
        if scope not in entries:
            document = tomlkit.parse(config.read_text(encoding='utf-8-sig')) if config.exists() else tomlkit.document()
            if 'documents' not in document:
                document['documents'] = tomlkit.table()
            document['documents'][scope] = DEFAULTS[scope]
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(tomlkit.dumps(document), encoding='utf-8')
        path = paths[scope]
        if path.exists():
            reused.append(label(ws, path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ('# Team documentation\n\nRecord reusable workflows, architecture and verification conclusions here.\n'
                'Use relative file links. Track shared documents in Git. Do not depend on private evidence.\n'
                'Discover optional local notes with `echo-kit docs list`; read explicitly with `echo-kit docs show private`.\n'
                if scope == 'shared' else
                '# Personal documentation\n\nRecord local paths and personal environment notes here. Absolute paths are allowed.\n'
                'Keep this document out of Git. This is not encrypted storage; use the existing authentication mechanism for secrets.\n'
                'These notes supplement team guidance and do not grant permission or silently override team rules.\n')
        with path.open('x', encoding='utf-8') as stream:
            stream.write(text)
        created.append(label(ws, path))
    ignore = ws.root / '.gitignore'
    if ignore.is_symlink():
        raise KitError('Refusing to modify symlinked .gitignore')
    body = ignore.read_text(encoding='utf-8-sig') if ignore.exists() else ''
    rules = ['/.echo-kit/']
    if inside(paths['private'], ws.root) and not inside(paths['private'], ws.state.resolve()):
        # Escape glob metacharacters: this is one explicitly configured file.
        relative = paths['private'].relative_to(ws.root).as_posix()
        rules.append('/' + re.sub(r'([*?\[\]\\ ])', r'\\\1', relative))
    for rule in rules:
        if rule not in body.splitlines():
            body = body.rstrip('\n') + '\n' + rule + '\n'
    if not ignore.exists() or ignore.read_text(encoding='utf-8-sig') != body:
        ignore.write_text(body, encoding='utf-8')
    checked = check(ws)
    return {**checked, 'created': created, 'reused': reused,
            'next_step': 'Review shared files for Git; never add private files. Run docs check.'}
