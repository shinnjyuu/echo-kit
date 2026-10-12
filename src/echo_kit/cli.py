import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from filelock import Timeout

from .core import KitError, Workspace, init_workspace, read, safe_name


def parser():
    p = argparse.ArgumentParser(prog='echo-kit')
    from . import __version__
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--workspace', default='.')
    p.add_argument('--environment')
    p.add_argument('--actor')
    p.add_argument('--offline', action='store_true', help='Use cached update information and runtimes only')
    p.add_argument('--json', action='store_true')
    top = p.add_subparsers(dest='module', required=True)
    for module, actions in {'workspace': ['init', 'check'], 'services': ['up', 'status', 'down', 'restart', 'logs'],
                            'auth': ['login', 'check', 'logout'], 'browser': ['up', 'status', 'down'],
                            'cases': ['list', 'show'], 'lab': ['run', 'compare'], 'verify': ['run'],
                            'operations': ['list', 'show', 'resolve'],
                            'runs': ['list', 'show', 'compare', 'report', 'cleanup'],
                            'skills': ['list', 'show', 'export', 'status'], 'protocol': ['show'],
                            'docs': ['setup', 'list', 'show', 'check', 'map', 'context', 'read'],
                            'tray': ['start', 'status', 'stop'], 'runtime': ['info'], 'updates': ['check', 'setup', 'notes']}.items():
        group = top.add_parser(module, **({'help': 'Discover and check shared/private project documents'}
                                         if module == 'docs' else {'help': 'Windows viewing tray; never stops business resources'}
                                         if module == 'tray' else {}))
        sub = group.add_subparsers(dest='action', required=True)
        for action in actions:
            doc_help = {'setup': 'Create missing entries and ignore rules without overwriting documents',
                        'list': 'List configured entries and Git status without document bodies',
                        'show': 'Read one explicit project document entry',
                        'check': 'Check paths and Git ownership without changing files',
                        'map': 'Discover Markdown across the workspace without reading bodies',
                        'context': 'Read the directory AGENTS.md chain; not a client instruction resolver',
                        'read': 'Read an explicit shared Markdown file in bounded line ranges'}
            cmd = sub.add_parser(action, **({'help': doc_help[action]} if module == 'docs' else
                {'description': {'start': 'Register this workspace and show the Windows tray if owned resources are running. Honors CI, desktop availability and ECHO_KIT_TRAY=0.',
                                 'status': 'Read tray and resource process state without a workspace; does not run application health commands.',
                                 'stop': 'Exit only the tray. Services, browser containers and experiments continue. No workspace required.'}[action]}
                if module == 'tray' else
                {'description': 'Managed Docker Chromium is headed by default with a local read-only viewer. '
                 'browser up opens it once in the system browser (disable with browser.auto_open=false; CI defaults off). '
                 'Set browser.headless=true for automation without a viewer. Existing containers require explicit down/up to change mode.'}
                if module == 'browser' else {}))
            if module in ('cases', 'runs') and action == 'list':
                cmd.add_argument('--limit', type=int, default=20, help='Page size (1..200; default 20)')
                cmd.add_argument('--offset', type=int, default=0, help='Nonnegative page offset')
                if module == 'cases':
                    cmd.add_argument('--category', help='Browse a classification subtree')
                    cmd.add_argument('--query', help='Search IDs, descriptions and tags without reading documents')
                else:
                    cmd.add_argument('--case', help='Filter by exact case ID')
                    cmd.add_argument('--status', help='Filter by exact recorded status')
            elif module == 'cases':
                cmd.add_argument('name')
            elif module == 'docs' and action == 'show':
                cmd.add_argument('scope', choices=['shared', 'private'])
            elif module == 'docs' and action == 'map':
                cmd.add_argument('--path', default='.')
                cmd.add_argument('--query')
                cmd.add_argument('--limit', type=int, default=20)
                cmd.add_argument('--offset', type=int, default=0)
            elif module == 'docs' and action in ('context', 'read'):
                cmd.add_argument('path')
                if action == 'read':
                    cmd.add_argument('--start-line', type=int, default=1)
                    cmd.add_argument('--limit', type=int, default=200, help='Lines per read (1..1000)')
            elif module == 'docs' and action == 'check':
                cmd.add_argument('--rules', action='store_true', help='Check workspace rule files and direct links independently of docs setup')
            elif module == 'updates':
                if action == 'notes':
                    cmd.add_argument('--since', help='Read bundled releases newer than this version')
                if action == 'check':
                    cmd.add_argument('--refresh', action='store_true', help='Bypass the release-check cache')
            elif module == 'operations' and action != 'list':
                cmd.add_argument('id')
                if action == 'resolve':
                    cmd.add_argument('--note', required=True)
            elif module == 'services':
                cmd.add_argument('names', nargs='*', help='Service names; down/restart without names select only instances started by this workspace/environment')
                if action in ('up', 'restart'):
                    cmd.add_argument('--jobs', type=int, help='Concurrent service starts (default: all selected services unless configured; 1 is serial)')
            elif module == 'auth':
                cmd.add_argument('name')
            elif module in ('lab', 'verify'):
                cmd.add_argument('case')
                cmd.add_argument('--repeat', type=int, default=1)
                cmd.add_argument('--variants', nargs='+', default=['baseline'])
            elif module == 'runs' and action != 'list':
                cmd.add_argument('ids', nargs='+' if action == 'compare' else 1)
            elif module == 'skills':
                if action == 'show':
                    cmd.add_argument('name')
                elif action in ('export', 'status'):
                    cmd.add_argument('destination')
    guide = top.add_parser('guide', help='Read compact rules and topic directory from this package')
    guide.add_argument('name')
    mode = guide.add_mutually_exclusive_group()
    mode.add_argument('--topic', help='Read one topic from the guide directory')
    mode.add_argument('--full', action='store_true', help='Read the complete protocol and reference documents')
    top.add_parser('doctor')
    return p


def dispatch(args):
    if args.module == 'tray':
        from . import tray
        if args.action == 'status':
            return tray.status()
        if sys.platform != 'win32':
            return {'status': 'unverified', 'reason': 'unsupported_platform', 'tray': 'unsupported'}
        if args.action == 'stop':
            return tray.stop()
        return tray.start(Workspace(args.workspace, args.environment))
    if args.module == 'guide':
        from .documentation import guide
        return guide(args.name, topic=args.topic, full=args.full)
    if args.module == 'updates' and args.action == 'notes':
        from .documentation import notes
        return notes(args.since)
    if args.module == 'workspace' and args.action == 'init':
        return init_workspace(args.workspace)
    if args.module == 'skills':
        from . import documentation
        if args.action == 'list':
            return documentation.catalog()
        if args.action == 'show':
            return documentation.show(args.name)
        if args.action == 'status':
            return documentation.export_status(args.destination)
        return documentation.export(args.destination)
    if args.module == 'protocol':
        from .documentation import show
        return show()
    if args.module == 'runtime':
        from .documentation import tool_info
        return {'status': 'passed', **tool_info()}
    if args.module == 'updates' and args.action == 'check':
        from .updates import check
        return check(refresh=args.refresh, no_network=args.offline)
    if args.module == 'operations':
        from .operations import Journal
        journal = Journal()
        if args.action == 'list':
            return {'operations': journal.list()}
        if args.action == 'show':
            return journal.show(args.id)
        return journal.resolve(args.id, args.note)
    ws = Workspace(args.workspace, args.environment)
    ws.actor = args.actor
    ws.startup_progress = not args.json
    ws.tool_update = getattr(args, "_update", None)
    if args.module == 'docs':
        from . import project_docs
        from . import document_map
        if args.action == 'map':
            return document_map.document_map(ws, args.path, args.query, args.limit, args.offset)
        if args.action == 'context':
            return document_map.context(ws, args.path)
        if args.action == 'read':
            return document_map.read_document(ws, args.path, args.start_line, args.limit)
        if args.action == 'check' and args.rules:
            return document_map.check_rules(ws)
        if args.action == 'show':
            return project_docs.show(ws, args.scope)
        return getattr(project_docs, 'catalog' if args.action == 'list' else args.action)(ws)
    if args.module == 'updates':
        from .migration import setup
        return setup(ws)
    if args.module in ('workspace', 'doctor'):
        checks = []
        for name in ws.cfg.get('projects', {}):
            ws.project(name)
            checks.append({'name': 'project:' + name, 'status': 'passed'})
        for name, service in ws.cfg.get('services', {}).items():
            if service.get('mode', 'managed') == 'managed':
                command = ws.command(service)
                available = Path(command[0]).is_file() or bool(shutil.which(command[0]))
                checks.append({'name': 'runtime:' + name, 'status': 'passed' if available else 'blocked'})
            if args.module == 'doctor':
                from .services import healthy
                checks.append({'name': 'ready:' + name, 'status': 'passed' if healthy(ws, service) else 'unverified'})
        from .documentation import tool_info
        from .project_docs import summary
        return {'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'unverified', 'checks': checks,
                'documents': summary(ws),
                'tool': tool_info(), 'update': ws.tool_update,
                'unfinished_runs': [p.parent.name for p in ws.runs.glob('*/active.json')]}
    if args.module == 'cases':
        from . import catalogs
        return (catalogs.cases(ws, args.category, args.query, args.limit, args.offset)
                if args.action == 'list' else catalogs.case(ws, args.name))
    if args.module == 'runs':
        from .runs import locate, compare, report
        if args.action == 'list':
            from .catalogs import runs
            return runs(ws, args.case, args.status, args.limit, args.offset)
        path = locate(ws, args.ids[0])
        if args.action == 'show':
            return read(path / 'run.json')
        if args.action == 'report':
            return {'report': report(path)}
        if args.action == 'compare':
            if len(args.ids) != 2:
                raise KitError('compare requires two run IDs')
            return compare(read(path / 'run.json'), read(locate(ws, args.ids[1]) / 'run.json'))
        from .runner import cleanup_run
        return cleanup_run(ws, path)
    if args.module == 'services' and args.action in ('status', 'logs'):
        from .services import Services
        service = Services(ws)
        if args.action == 'status':
            return service.status()
        if len(args.names) != 1:
            raise KitError('logs requires one service name')
        return service.logs(args.names[0])
    if args.module == 'services':
        from .services import Services
        service = Services(ws)
        names = args.names if args.action in ('down', 'restart') else args.names or list(ws.cfg.get('services', {}))
        return getattr(service, args.action)(names, jobs=args.jobs) if args.action in ('up', 'restart') else getattr(service, args.action)(names)
    if args.module == 'auth':
        from .auth import authenticate
        summary, _ = authenticate(ws, args.name, args.action)
        return summary
    if args.module == 'browser':
        from .browser import manage
        return manage(ws, args.action)
    from .runner import run
    result = []
    for variant in args.variants:
        result.extend(run(ws, args.case, args.module, variant, args.repeat))
    return result


def exit_code(result):
    records = result if isinstance(result, list) else [result]
    statuses = [r.get('status') for r in records if isinstance(r, dict)]
    if 'interrupted' in statuses:
        return 130
    if any(s in ('blocked', 'unverified') for s in statuses):
        return 2
    return 1 if 'failed' in statuses else 0


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    args = None
    try:
        from . import __version__
        expected = os.environ.pop('ECHO_KIT_EXPECTED_VERSION', None)
        if expected and expected != __version__:
            raise KitError('Resolved tool version differs from the prepared version')
        from . import updates
        route_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        for name in ('--workspace', '--environment', '--actor'):
            route_parser.add_argument(name)
        route_parser.add_argument('--offline', action='store_true')
        route_parser.add_argument('--json', action='store_true')
        route_parser.add_argument('module', nargs='?')
        route_parser.add_argument('action', nargs='?')
        route, _ = route_parser.parse_known_args(raw)
        if (any(x == '--task' or x.startswith('--task=') or x == '--policy' or x.startswith('--policy=') for x in raw)
                or route.module == 'task' or (route.module == 'updates' and route.action == 'apply')):
            raise KitError('Project version tasks and policies were removed. Use echo-kit directly; '
                           'run updates setup once to migrate an old project entry. Preserve historical records.')
        diagnostic = (('--version' in raw and route.module is None)
                      or (route.module is None and any(x in ('--help', '-h') for x in raw))
                      or (route.module, route.action) in (('runtime', 'info'), ('updates', 'check')))
        discovery = None
        if diagnostic:
            os.environ.pop(updates.FORWARDED, None)
            os.environ.pop('ECHO_KIT_SOURCE_VERSION', None)
            os.environ.pop('ECHO_KIT_UPDATE_ROUTE', None)
        if not diagnostic:
            child_code, discovery = updates.startup(raw, route.offline)
            if child_code is not None:
                return child_code
        try:
            args = parser().parse_args(raw)
        except SystemExit as error:
            if error.code and discovery and discovery.get('fallback_reason'):
                print(f"Echo Kit: parsing with {__version__}; update routing incomplete "
                      f"({discovery['fallback_reason']}). A newer runtime may support this command. "
                      "Upgrade the entry with: uv tool install --upgrade --index-url "
                      "https://pypi.org/simple shinnjyuu-echo-kit", file=sys.stderr)
            raise
        args._update = discovery
        result = dispatch(args)
        code = exit_code(result)
    except (KitError, Timeout, OSError, ValueError, KeyError) as error:
        result, code = {'status': 'blocked', 'error': str(error), **getattr(error, 'details', {})}, 2
        code = exit_code(result)
    except KeyboardInterrupt:
        result, code = {'status': 'interrupted'}, 130
    if (args is not None and args.json) or (args is None and '--json' in raw):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args is not None and args.module in ('skills', 'protocol') and args.action == 'show' and code == 0:
        print(f"Echo Kit {result['version']} · {result['path']}\n")
        from .documentation import render_document
        print(render_document(result))
    elif args is not None and code == 0 and args.module == 'guide':
        from .documentation import render_guide
        print(render_guide(result))
    elif args is not None and code == 0 and args.module == 'updates' and args.action == 'notes':
        from .documentation import render_notes
        print(f"Echo Kit {result['version']}\n\n" + render_notes(result))
    elif isinstance(result, list):
        for item in result:
            print(f"{item.get('id', '')} {item.get('case', '')}: {item.get('status', '')}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
