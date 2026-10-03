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
    p.add_argument('--task', help='Use the version and environment pinned by task start')
    p.add_argument('--offline', action='store_true', help='Use cached update information and runtimes only')
    p.add_argument('--json', action='store_true')
    top = p.add_subparsers(dest='module', required=True)
    for module, actions in {'workspace': ['init', 'check'], 'services': ['up', 'status', 'down', 'restart', 'logs'],
                            'auth': ['login', 'check', 'logout'], 'browser': ['up', 'status', 'down'],
                            'lab': ['run', 'compare'], 'verify': ['run'],
                            'operations': ['list', 'show', 'resolve'],
                            'runs': ['list', 'show', 'compare', 'report', 'cleanup'],
                            'skills': ['list', 'show', 'export', 'status'], 'protocol': ['show'],
                            'runtime': ['info'], 'updates': ['check', 'setup', 'apply'],
                            'task': ['start', 'list', 'show', 'finish']}.items():
        group = top.add_parser(module)
        sub = group.add_subparsers(dest='action', required=True)
        for action in actions:
            cmd = sub.add_parser(action)
            if module == 'updates':
                if action == 'check':
                    cmd.add_argument('--refresh', action='store_true', help='Bypass the release-check cache')
                elif action == 'setup':
                    cmd.add_argument('--policy', choices=['manual', 'patch', 'latest'])
                elif action == 'apply':
                    cmd.add_argument('--version', dest='target_version', help='Explicit stable release, including rollback')
            elif module == 'task':
                if action == 'start':
                    cmd.add_argument('--label', help='What this task is doing')
                elif action in ('show', 'finish'):
                    cmd.add_argument('id')
            elif module == 'operations' and action != 'list':
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
                if action == 'show':
                    cmd.add_argument('name')
                elif action in ('export', 'status'):
                    cmd.add_argument('destination')
    top.add_parser('doctor')
    return p


def dispatch(args):
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
        from .tasks import CURRENT_TASK, project_version
        selection = project_version(Workspace(args.workspace), required=False) if (
            Path(args.workspace) / 'echo-kit.toml').exists() else None
        current = (CURRENT_TASK.get() or selection or {}).get('version')
        return {**check(current, (selection or {}).get('policy', 'manual'), args.refresh, args.offline),
                'project_version': (selection or {}).get('version')}
    if args.module == 'operations':
        from .operations import Journal
        journal = Journal()
        if args.action == 'list':
            return {'operations': journal.list()}
        if args.action == 'show':
            return journal.show(args.id)
        return journal.resolve(args.id, args.note)
    ws = Workspace(args.workspace, args.environment)
    ws.actor = args.actor or ws.actor
    if args.module == 'updates':
        from . import tasks
        if args.action == 'setup':
            return tasks.setup(ws, args.policy)
        return tasks.apply(ws, args.target_version, args.offline)
    if args.module == 'task':
        from . import tasks
        if args.action == 'start':
            return tasks.start(ws, args.label, args.offline)
        if args.action == 'list':
            return {'status': 'passed', 'tasks': tasks.list_tasks(ws)}
        if args.action == 'show':
            return {'status': 'passed', **tasks.load_task(ws, args.id)}
        return tasks.finish(ws, args.id)
    if args.module in ('workspace', 'doctor', 'lab', 'verify') and not ws.tool_update:
        from .updates import check
        ws.tool_update = check(no_network=args.offline)
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
        return {'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'unverified', 'checks': checks,
                'tool': tool_info(), 'update': ws.tool_update, 'task_id': ws.task_id,
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
    raw = list(sys.argv[1:] if argv is None else argv)
    args = None
    try:
        from . import __version__
        expected = os.environ.get('ECHO_KIT_EXPECTED_VERSION')
        if expected and expected != __version__:
            raise KitError('Resolved tool version differs from the requested task version')
        # Route before the full parser so an older installed launcher can pass
        # commands introduced by a newer task runtime without interpreting them.
        route_parser = argparse.ArgumentParser(add_help=False)
        route_parser.add_argument('--workspace', default='.')
        route_parser.add_argument('--environment')
        route_parser.add_argument('--task')
        route_parser.add_argument('--offline', action='store_true')
        route, _ = route_parser.parse_known_args(raw)
        task_ws = None
        if route.task:
            from . import tasks, updates
            task_ws = Workspace(route.workspace, route.environment)
            task = tasks.load_task(task_ws, route.task, open_only=True)
            if task['version'] != __version__:
                env = updates.child_environment()
                env['ECHO_KIT_EXPECTED_VERSION'] = task['version']
                return subprocess.call(updates.command_prefix(task['version'], route.offline) + raw, env=env)
            if route.environment is None and task_ws.environment != task['environment']:
                task_ws = Workspace(route.workspace, task['environment'])
        args = parser().parse_args(raw)
        if args.task:
            if args.module == 'task' or (args.module == 'updates' and args.action != 'check') or (
                    args.module == 'workspace' and args.action == 'init'):
                raise KitError('Manage task lifetimes and update policy outside --task')
            if args.environment is None and task_ws.environment != Workspace(args.workspace).environment:
                args.environment = task_ws.environment
            with tasks.bind(task_ws, args.task):
                result = dispatch(args)
        else:
            result = dispatch(args)
        code = exit_code(result)
    except (KitError, Timeout, OSError, ValueError, KeyError) as error:
        result, code = {'status': 'blocked', 'error': str(error), **getattr(error, 'details', {})}, 2
    except KeyboardInterrupt:
        result, code = {'status': 'interrupted'}, 130
    if args is not None and not args.json:
        records = result if isinstance(result, list) else [result]
        notices = [r.get('update') for r in records if isinstance(r, dict) and isinstance(r.get('update'), dict)]
        if notices and notices[0].get('update_available'):
            print(f"Echo Kit {notices[0]['latest_version']} is available. Start a new task to apply project policy; "
                  'the current task keeps its version.', file=sys.stderr)
    if (args is not None and args.json) or (args is None and '--json' in raw):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args is not None and args.module in ('skills', 'protocol') and args.action == 'show' and code == 0:
        print(f"Echo Kit {result['version']} · {result['path']}\n")
        print(result['content'], end='' if result['content'].endswith('\n') else '\n')
    elif isinstance(result, list):
        for item in result:
            print(f"{item.get('id', '')} {item.get('case', '')}: {item.get('status', '')}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
