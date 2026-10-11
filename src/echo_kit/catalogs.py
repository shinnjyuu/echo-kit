"""Read-only, bounded discovery of project cases and run records."""
from __future__ import annotations

import math
from .core import KitError, read, safe_name
from . import project_docs


def page(items, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise KitError('limit must be 1..200 and offset must be nonnegative')
    total = len(items)
    next_offset = offset + limit if offset + limit < total else None
    return {'status': 'passed', 'items': items[offset:offset + limit], 'total': total,
            'limit': limit, 'offset': offset, 'next_offset': next_offset,
            'next_step': f'Repeat the same filters with --offset {next_offset}' if next_offset is not None else None}


def category_path(value):
    if not isinstance(value, str) or (value and any(
            not part.strip() or part in ('.', '..') or '\\' in part for part in value.split('/'))):
        raise KitError('category must be a slash-separated relative classification')
    return value


def metadata(name, spec):
    safe_name(name)
    if not isinstance(spec, dict):
        raise KitError('Invalid case table: ' + name)
    description = spec.get('description', '')
    tags = spec.get('tags', [])
    if not isinstance(description, str) or not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise KitError('Invalid description/tags for case: ' + name)
    return {'type': 'case', 'id': name, 'description': description,
            'category': category_path(spec.get('category', '')), 'tags': tags,
            'docs_registered': bool(spec.get('docs')),
            'missing_description': not bool(description.strip())}


def cases(ws, category=None, query=None, limit=20, offset=0):
    page([], limit, offset)
    selected = category_path(category or '')
    specs = ws.cfg.get('cases', {})
    if not isinstance(specs, dict):
        raise KitError('cases must be a table')
    rows, groups = [], set()
    for name, spec in specs.items():
        item = metadata(name, spec)
        cat = item['category']
        if selected and cat != selected and not cat.startswith(selected + '/'):
            continue
        if query is not None:
            if query.casefold() in '\n'.join([name, item['description'], *item['tags']]).casefold():
                rows.append(item)
        elif cat == selected:
            rows.append(item)
        else:
            child = cat[len(selected) + 1:] if selected else cat
            groups.add((selected + '/' if selected else '') + child.split('/')[0])
    rows += [{'type': 'category', 'id': group} for group in groups]
    rows.sort(key=lambda item: (item['type'] != 'category', item['id']))
    return {**page(rows, limit, offset), 'category': selected, 'query': query}


def case(ws, name):
    safe_name(name)
    spec = ws.cfg.get('cases', {}).get(name)
    if spec is None:
        raise KitError('Unknown case: ' + name)
    item = metadata(name, spec)
    services, auth, browser = spec.get('services', []), spec.get('auth'), spec.get('browser', False)
    if (not isinstance(services, list) or not all(isinstance(s, str) for s in services)
            or (auth is not None and not isinstance(auth, str)) or type(browser) is not bool):
        raise KitError('Invalid declared requirements for case: ' + name)
    prefix = ['echo-kit', '--workspace', str(ws.root)]
    if ws.environment in ws.cfg.get('environments', {}):
        prefix += ['--environment', ws.environment]
    result = {'status': 'passed', 'case': item,
              'entrypoints': [[*prefix, module, 'run', name] for module in ('lab', 'verify')],
              'requirements': {'services': services, 'auth': auth, 'browser': browser, 'verified': False},
              'note': 'Lab does not prepare dependencies; Verify prepares declared capabilities.',
              'document': None}
    value = spec.get('docs')
    if value is None:
        result['next_step'] = 'No experiment document registered; inspect the selected project entry as needed.'
        return result
    # Every case document is a shared reference, even if supplied by a local override.
    # Never echo a rejected path or parsing exception containing private source text.
    def reject(reason):
        result.update(status='blocked', reason=reason)
        return result
    if not isinstance(value, str) or not value.strip() or project_docs.absolute(value):
        return reject('invalid_shared_document_path')
    try:
        lexical = ws.root / value.replace('\\', '/')
        target = lexical.resolve()
        entries, issues = project_docs.configuration(ws)
        if issues:
            return reject('document_configuration_unverified')
        if (not target.is_relative_to(ws.root) or project_docs.private_path(ws, target, entries)
                or lexical.is_relative_to(ws.state)):
            return reject('document_outside_or_private')
        git = project_docs.Git()
        states = [git.state(path) for path in (lexical, target)]
        if any(s['ignored'] for s in states):
            return reject('document_ignored')
        if any(not s['available'] or s['repository'] is None for s in states):
            return reject('document_git_unverified')
        if not target.is_file():
            return reject('document_missing')
        content = project_docs.read_document(target)
        result['document'] = {'path': target.relative_to(ws.root).as_posix(), 'content': content}
        return result
    except (OSError, ValueError, RuntimeError, KitError):
        return reject('document_unreadable')


def runs(ws, case=None, status=None, limit=20, offset=0):
    page([], limit, offset)
    rows, issues, issue_count = [], [], 0
    for path in ws.runs.glob('*/run.json'):
        try:
            record = read(path)
            if not isinstance(record, dict):
                raise ValueError()
            # Legacy missing fields are fine, malformed summary fields are not.
            for key in ('case', 'environment', 'status'):
                if record.get(key) is not None and not isinstance(record[key], str):
                    raise ValueError()
            started = record.get('started')
            if started is not None and (type(started) not in (int, float) or not math.isfinite(started)):
                raise ValueError()
            if case is not None and record.get('case') != case:
                continue
            if status is not None and record.get('status') != status:
                continue
            rows.append({'id': path.parent.name, 'case': record.get('case'), 'started': started,
                         'environment': record.get('environment'), 'status': record.get('status'),
                         'needs_cleanup': (path.parent / 'active.json').exists()})
        except (OSError, ValueError):
            issue_count += 1
            if len(issues) < 5:
                issues.append({'id': path.parent.name, 'reason': 'unreadable_run_record'})
    rows.sort(key=lambda row: (row['started'] is not None, row['started'] or 0, row['id']), reverse=True)
    return {**page(rows, limit, offset), 'issues': issues, 'issue_count': issue_count,
            'issues_truncated': issue_count > len(issues)}
