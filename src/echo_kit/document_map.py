"""Read-only workspace documentation discovery, not a client instruction resolver."""
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

from .core import KitError
from . import project_docs as docs
from .catalogs import page

OUTPUT_BYTES = 64 * 1024
MAX_FILES = docs.MAX_FILES
MARKDOWN = ('.md', '.markdown')
BOUNDARY_NOTE = ('Kit-discovered directory rule chain, not all instructions loaded by an AI client. '
                 'Ancestors outside this workspace and user-global rules are excluded. '
                 'CLI checks structure, not semantic conflicts or authorization.')


def kind(path):
    if path.name == 'AGENTS.md':
        return 'rules'
    if path.name.casefold() in ('agents.md', 'agents.override.md'):
        return 'rule_candidate'
    return 'readme' if path.name.casefold() == 'readme.md' else 'document'


class Discovery:
    def __init__(self, ws):
        self.ws = ws
        self.entries, self.issues = docs.configuration(ws)
        self.git = docs.Git()
        self.complete = True
        self.gitlinks = None

    def run_git(self, *args, data=None):
        return subprocess.run(['git', '-C', str(self.ws.root), *args], input=data,
                              capture_output=True, timeout=10)

    def boundary(self, path):
        """Check lexical and resolved paths before opening any document body."""
        try:
            resolved = path.resolve()
            if not docs.inside(resolved, self.ws.root) or not docs.inside(path, self.ws.root):
                return 'shared_reference_outside'
            private = self.entries.get('private')
            if (docs.private_path(self.ws, resolved, self.entries) or docs.inside(path, self.ws.state)
                    or (private and docs.inside(resolved, private))):
                return 'shared_private_reference'
            if self.gitlinks is None:
                stage = self.run_git('ls-files', '--stage', '-z')
                if stage.returncode:
                    return 'git_unavailable'
                self.gitlinks = [self.ws.root / item.split('\t', 1)[1]
                                 for item in stage.stdout.decode('utf-8').split('\0')
                                 if item.startswith('160000 ') and '\t' in item]
            if any(docs.inside(resolved, link) or docs.inside(path, link) for link in self.gitlinks):
                return 'repository_boundary'
            for parent in (path, *path.parents):
                if parent == self.ws.root:
                    break
                if parent.name == '.git':
                    return 'repository_boundary'
                if parent.is_dir() and (parent.is_symlink() or
                        getattr(parent, 'is_junction', lambda: False)()):
                    return 'directory_link_boundary'
                if parent.resolve() == self.ws.root:
                    break
                if parent.is_dir() and (parent / '.git').exists():
                    return 'repository_boundary'
            # A file symlink may point inside a nested repository or ignored tree.
            if resolved != path:
                for parent in resolved.parents:
                    if parent == self.ws.root:
                        break
                    if (parent / '.git').exists():
                        return 'repository_boundary'
            return None
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            return 'path_unverified'

    def allowed(self, path):
        error = self.boundary(path)
        if error:
            return error
        for value in {path, path.resolve()}:
            state = self.git.state(value / 'echo-kit.toml' if value == self.ws.root else value)
            if not state['available'] or not state['repository']:
                return 'git_unavailable'
            if state['ignored']:
                return 'shared_ignored_reference'
        return None

    def target(self, raw, directory=False):
        if not raw or docs.absolute(raw):
            raise KitError('Document path must be workspace-relative')
        path = self.ws.root / raw.replace('\\', '/')
        error = self.allowed(path)
        if error:
            self.issues.append(docs.issue(error, '.', 'unverified' if error in
                               ('git_unavailable', 'path_unverified', 'repository_boundary', 'directory_link_boundary') else 'failed'))
            return None
        if not path.exists() or (directory and not path.is_dir()):
            self.issues.append(docs.issue('missing_directory' if directory else 'missing_path', docs.label(self.ws, path)))
            return None
        return path

    def inventory(self, base=None):
        if self.issues:
            self.complete = False
            return []
        try:
            def names(*args):
                result = self.run_git(*args)
                if result.returncode:
                    raise OSError()
                return set(result.stdout.decode('utf-8').rstrip('\0').split('\0')) - {''}
            tracked = names('ls-files', '--cached', '-z')
            untracked = names('ls-files', '--others', '--exclude-standard', '-z')
            candidates = sorted(n for n in tracked | untracked if Path(n).suffix.lower() in MARKDOWN)
            if candidates:
                ignored = self.run_git('check-ignore', '--no-index', '-z', '--stdin',
                                       data=('\0'.join(candidates) + '\0').encode())
                if ignored.returncode not in (0, 1):
                    raise OSError()
                excluded = set(ignored.stdout.decode('utf-8').split('\0'))
            else:
                excluded = set()
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            self.issues.append(docs.issue('git_unavailable', '.', 'unverified'))
            self.complete = False
            return []
        rows = []
        for name in candidates:
            path = self.ws.root / name
            if base is not None and not path.is_relative_to(base):
                continue
            error = self.boundary(path)
            if name in excluded or error:
                if name not in excluded and error in ('git_unavailable', 'path_unverified'):
                    self.complete = False
                    self.issues.append(docs.issue(error, name, 'unverified'))
                elif name not in excluded and path.is_symlink() and kind(path) in ('rules', 'rule_candidate'):
                    self.issues.append(docs.issue(error, name))
                continue
            if path.is_symlink():
                error = self.allowed(path)
                if error:
                    if error in ('git_unavailable', 'path_unverified'):
                        self.complete = False
                        self.issues.append(docs.issue(error, name, 'unverified'))
                    elif kind(path) in ('rules', 'rule_candidate'):
                        self.issues.append(docs.issue(error, name))
                    continue
            if len(rows) >= MAX_FILES:
                self.complete = False
                self.issues.append(docs.issue('scan_limit', '.', 'unverified'))
                break
            row = {'path': name, 'type': kind(path), 'exists': path.is_file(),
                   'git': {'tracked': name in tracked, 'ignored': False}}
            if row['type'] in ('rules', 'rule_candidate'):
                row['scope'] = path.parent.relative_to(self.ws.root).as_posix()
            rows.append(row)
        return rows

    def references(self, body, source):
        found = []
        for href, line in docs.links(body):
            try:
                target, error = docs.destination(href, source.parent)
                if error is None and target is not None:
                    # Preserve lexical path for ignored aliases and directory links.
                    lexical = source.parent / unquote(urlsplit(href).path).replace('\\', '/')
                    error = self.allowed(lexical)
                    if error is None and not target.exists():
                        error = 'shared_missing_reference'
                if error:
                    status = 'unverified' if error in ('git_unavailable', 'path_unverified',
                              'repository_boundary', 'directory_link_boundary') else 'failed'
                    self.issues.append(docs.issue(error, docs.label(self.ws, source), status, line))
                elif target is not None and target.suffix.lower() in MARKDOWN:
                    found.append({'path': docs.label(self.ws, target), 'source': docs.label(self.ws, source),
                                  'line': line, 'command': ['echo-kit', 'docs', 'read', docs.label(self.ws, target)]})
            except (OSError, ValueError, RuntimeError):
                self.issues.append(docs.issue('invalid_reference', docs.label(self.ws, source), line=line))
        return found


def document_map(ws, path='.', query=None, limit=20, offset=0):
    page([], limit, offset)
    scan = Discovery(ws)
    base = scan.target(path, directory=True) if not scan.issues else None
    if base is None:
        return docs.result(scan.issues, **{k: v for k, v in page([], limit, offset).items() if k != 'status'}, complete=False)
    base = base.resolve()
    inventory = scan.inventory(base)
    rows, directories = [], set()
    for row in inventory:
        relative = (ws.root / row['path']).relative_to(base)
        if query is not None:
            if query.casefold() in row['path'].casefold():
                rows.append(row)
        elif len(relative.parts) == 1:
            rows.append(row)
        else:
            directories.add((base / relative.parts[0]).relative_to(ws.root).as_posix())
    rows += [{'path': name, 'type': 'directory'} for name in directories]
    rows.sort(key=lambda row: (row['type'] != 'directory', row['path']))
    paged = page(rows, limit, offset)
    paged.pop('status')
    if not scan.complete:
        paged['next_step'] = 'Discovery is incomplete; narrow with docs map --path DIR. For a known file use docs read PATH.'
    return docs.result(scan.issues, **paged, path=path, query=query, complete=scan.complete,
                       total_is_complete=scan.complete, scanned_documents=len(inventory))


def excerpt(body, start, limit, budget=OUTPUT_BYTES):
    lines = body.splitlines(keepends=True)
    selected, size = [], 0
    for text in lines[start - 1:start - 1 + limit]:
        if size + len(text.encode('utf-8')) > budget:
            break
        selected.append(text)
        size += len(text.encode('utf-8'))
    end = start + len(selected) - 1
    next_line = end + 1 if end < len(lines) else None
    return {'content': ''.join(selected), 'start_line': start, 'end_line': end if selected else None,
            'total_lines': len(lines), 'next_line': next_line, 'bytes': size}


def read_document(ws, path, start_line=1, limit=200):
    if type(start_line) is not int or start_line < 1 or type(limit) is not int or not 1 <= limit <= 1000:
        raise KitError('start-line must be positive; limit must be 1..1000')
    scan = Discovery(ws)
    target = scan.target(path) if not scan.issues else None
    if target is None:
        return docs.result(scan.issues, document=None)
    name = docs.label(ws, target)
    if target.suffix.lower() not in MARKDOWN or not target.is_file():
        return docs.result([docs.issue('not_markdown_file', name)], document=None)
    try:
        body = docs.read_document(target)
    except (OSError, UnicodeError, KitError):
        return docs.result([docs.issue('document_unreadable_or_too_large', name, 'unverified')], document=None)
    value = excerpt(body, start_line, limit)
    value['path'] = name
    if value['next_line'] is not None:
        scan.issues.append(docs.issue('body_incomplete', name, 'unverified', value['next_line']))
    value['continue_command'] = ['echo-kit', 'docs', 'read', name, '--start-line', str(value['next_line']),
                                 '--limit', str(limit)] if value['next_line'] is not None else None
    if value['next_line'] == start_line:
        value['continue_command'] = None
        value['next_step'] = 'This single line exceeds 64 KiB; inspect it explicitly with a local editor.'
    return docs.result(scan.issues, document=value, complete=value['next_line'] is None)


def context(ws, path):
    scan = Discovery(ws)
    target = scan.target(path) if not scan.issues else None
    if target is None:
        return docs.result(scan.issues, rules=[], references=[], note=BOUNDARY_NOTE, complete=False)
    directory = (target if target.is_dir() else target.parent).resolve()
    chain = [ws.root]
    chain.extend(reversed([p for p in (directory, *directory.parents) if p != ws.root and p.is_relative_to(ws.root)]))
    rules, candidates, references = [], [], []
    remaining = OUTPUT_BYTES
    for folder in chain:
        try:
            children = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            scan.issues.append(docs.issue('directory_unreadable', docs.label(ws, folder), 'unverified'))
            continue
        for file in children:
            role = kind(file)
            if role not in ('rules', 'rule_candidate') and not (folder == directory and role == 'readme'):
                continue
            name = docs.label(ws, file)
            error = scan.allowed(file)
            if error:
                scan.issues.append(docs.issue(error, name, 'unverified'))
                continue
            if role == 'rule_candidate':
                candidates.append({'path': name, 'scope': docs.label(ws, folder)})
                scan.issues.append(docs.issue('nonstandard_rule_name', name, 'unverified'))
                continue
            if role == 'readme':
                references.append({'path': name, 'command': ['echo-kit', 'docs', 'read', name]})
                continue
            if not scan.git.state(file)['tracked']:
                scan.issues.append(docs.issue('rule_untracked', name, 'unverified'))
            try:
                body = docs.read_document(file)
                value = excerpt(body, 1, len(body.splitlines()) or 1, remaining)
                remaining -= value['bytes']
                references.extend(scan.references(value['content'], file))
            except (OSError, UnicodeError, KitError):
                value = {'content': '', 'next_line': 1}
                scan.issues.append(docs.issue('document_unreadable_or_too_large', name, 'unverified'))
            value.update(path=name, scope=docs.label(ws, folder))
            if value['next_line'] is not None:
                scan.issues.append(docs.issue('body_incomplete', name, 'unverified', value['next_line']))
                value['continue_command'] = ['echo-kit', 'docs', 'read', name, '--start-line', str(value['next_line'])]
            rules.append(value)
    return docs.result(scan.issues, target=docs.label(ws, directory), rules=rules, candidates=candidates,
                       references=references, note=BOUNDARY_NOTE, complete=not scan.issues)


def check_rules(ws):
    scan = Discovery(ws)
    inventory = scan.inventory()
    checked = []
    for row in inventory:
        if row['type'] not in ('rules', 'rule_candidate'):
            continue
        name = row['path']
        checked.append(name)
        if not row['exists']:
            scan.issues.append(docs.issue('rule_missing_file', name))
            continue
        if row['type'] == 'rule_candidate':
            scan.issues.append(docs.issue('nonstandard_rule_name', name, 'unverified'))
        if not row['git']['tracked']:
            scan.issues.append(docs.issue('rule_untracked', name, 'unverified'))
        path = ws.root / name
        error = scan.allowed(path)
        if error:
            scan.issues.append(docs.issue(error, name, 'unverified'))
            continue
        try:
            body = docs.read_document(path)
            scan.references(body, path)
        except (OSError, UnicodeError, KitError):
            scan.issues.append(docs.issue('document_unreadable_or_too_large', name, 'unverified'))
    return docs.result(scan.issues, checked_rules=checked, scanned_documents=len(inventory),
                       complete=scan.complete and not scan.issues, note=BOUNDARY_NOTE)
