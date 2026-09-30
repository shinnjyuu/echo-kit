import argparse
import json
import shutil
import sys
from importlib.resources import files
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
    p.add_argument('--json', action='store_true')
    top = p.add_subparsers(dest='module', required=True)
    for module, actions in {'workspace': ['init', 'check'], 'services': ['up', 'status', 'down', 'restart', 'logs'],
                            'auth': ['login', 'check', 'logout'], 'browser': ['up', 'status', 'down'],
                            'lab': ['run', 'compare'], 'verify': ['run'],
                            'operations': ['list', 'show', 'resolve'],
                            'runs': ['list', 'show', 'compare', 'report', 'cleanup'], 'skills': ['export']}.items():
        group = top.add_parser(module)
        sub = group.add_subparsers(dest='action', required=True)
        for action in actions:
            cmd = sub.add_parser(action)
            if module == 'operations' and action != 'list':
                cmd.add_argument('id')
                if action == 'resolve':
                    cmd.add_argument('--note', required=True)
            elif module == 'services':
                cmd.add_argument('names', nargs='*')
            elif module == 'auth':
                cmd.add_argument('name')
            elif module in ('lab', 'verify'):
                cmd.add_argument('case')
                cmd.add_argument('--repeat', type=int, default=1)
                cmd.add_argument('--variants', nargs='+', default=['baseline'])
            elif module == 'runs' and action != 'list':
                cmd.add_argument('ids', nargs='+' if action == 'compare' else 1)
            elif module == 'skills':
                cmd.add_argument('destination')
    top.add_parser('doctor')
    return p


def dispatch(args):
    if args.module == 'workspace' and args.action == 'init':
        return init_workspace(args.workspace)
    if args.module == 'skills':
        dest = Path(args.destination).resolve()
        source = files('echo_kit').joinpath('skills')
        if dest.exists() and any(dest.iterdir()):
            raise KitError('Skill destination is not empty; not overwriting')
        shutil.copytree(str(source), dest, dirs_exist_ok=True)
        return {'status': 'passed', 'destination': str(dest)}
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
        return {'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'unverified', 'checks': checks,
                'unfinished_runs': [p.parent.name for p in ws.runs.glob('*/active.json')]}
    if args.module == 'runs':
        from .runs import locate, compare, report
        if args.action == 'list':
            return [read(p) for p in sorted(ws.runs.glob('*/run.json'))]
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
        names = args.names or list(ws.cfg.get('services', {}))
        return getattr(service, args.action)(names)
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
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        code = exit_code(result)
    except (KitError, Timeout, OSError, ValueError, KeyError) as error:
        result, code = {'status': 'blocked', 'error': str(error), **getattr(error, 'details', {})}, 2
    except KeyboardInterrupt:
        result, code = {'status': 'interrupted'}, 130
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif isinstance(result, list):
        for item in result:
            print(f"{item.get('id', '')} {item.get('case', '')}: {item.get('status', '')}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
