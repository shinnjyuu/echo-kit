import time
import uuid
from contextlib import contextmanager

from . import launches
from .adapter import execute
from .auth import authenticate
from .browser import manage
from .core import KitError, event, merge, new_run, read, save
from .runs import report
from .services import Services
from .tray import startup_scope


@launches.guarded
def cleanup_run(ws, path):
    from filelock import FileLock
    from .operations import Journal, bind_operation
    from .storage_config import cleanup_lease
    with cleanup_lease(ws, path), launches.launch_lock(FileLock(str(path / 'cleanup.lock'), timeout=1)):
        record = read(path / 'run.json')
        if record['environment'] != ws.environment:
            raise KitError('Cleanup environment differs from the original run')
        cleanup = ws.cfg.get('cases', {}).get(record['case'], {}).get('cleanup')
        if not cleanup or not (path / 'active.json').exists():
            raise KitError('No pending run with configured cleanup adapter')
        from .service_registry import validate_run_instances
        from .operations import service_resources
        if record.get('service_access'):
            validate_run_instances(record)
            names = ws.cfg.get('cases', {}).get(record['case'], {}).get('services', [])
            if set(service_resources(ws, names, builds=False)) != set(record.get('service_resources', {})):
                raise KitError('Service configuration changed since this run; cleanup requires inspection')
            from .service_registry import Registry
            if record.get('cleanup_signature') != Registry().cleanup_signature(ws, cleanup):
                raise KitError('Cleanup configuration changed since this run; inspect the original target')
            for name, item in record.get('services', {}).items():
                if (item.get('instance') or {}).get('registry_id'):
                    Registry().compatible(ws, name, item['instance'])
        oid = record.get('operation_id')
        journal = Journal()
        if oid:
            journal.claim_cleanup(oid, ws, path)
        confirmed = False
        try:
            attempt = path / ('cleanup-' + uuid.uuid4().hex[:10])
            with bind_operation(oid):
                result = execute(ws, cleanup, attempt, {'run_id': record['id'], 'active': read(path / 'active.json')})
            launches.checkpoint()
            confirmed = result.get('status') == 'passed' and result.get('terminal_confirmed') is True
            if not confirmed:
                raise KitError('Cleanup not confirmed; active record retained')
            (path / 'active.json').unlink()
            record['cleanup'] = 'confirmed after run'
            save(path / 'run.json', record)
            report(path)
            return {'status': 'passed', 'cleanup': 'confirmed', 'original_status': record['status']}
        finally:
            if oid:
                journal.release(oid, 'cleanup_confirmed' if confirmed else 'cleanup_unconfirmed', retain=not confirmed)


def verdict(value, required):
    checks = value.get('checks', [])
    found = {c['name']: c['status'] for c in checks}
    for name in required:
        if name not in found:
            checks.append({'name': name, 'status': 'unverified', 'detail': 'Required evidence missing'})
    statuses = [c['status'] for c in checks]
    current = value.get('status', 'unverified')
    for status in ('interrupted', 'failed', 'blocked', 'unverified'):
        if current == status or status in statuses:
            return status
    if any(found.get(n) != 'passed' for n in required):
        return 'unverified'
    return 'passed'


@contextmanager
def interrupted_run(path, record, case):
    try:
        yield
    except launches.EmergencyInterrupted:
        active = path / 'active.json'
        phase = read(active, {}).get('phase')
        if phase == 'preparing' or (not case.get('creates_tasks') and not case.get('cleanup')):
            active.unlink(missing_ok=True)
        record.update(status='interrupted', reason='emergency_stop', finished=time.time())
        if active.exists():
            record['cleanup'] = 'unconfirmed'
        save(path / 'run.json', record)
        raise


@launches.guarded
@startup_scope
def run(ws, name, kind='lab', variant='baseline', repeat=1):
    if repeat < 1:
        raise KitError('repeat must be positive')
    cases = ws.cfg.get('cases', {})
    if name not in cases:
        raise KitError('Unknown case: ' + name)
    case = cases[name]
    browser_backend = None
    if case.get('browser'):
        from .browser import selected_backend
        browser_backend = selected_backend(ws, case.get('browser_backend'))
        if browser_backend == 'agent-browser' and case.get('browser_backend') != 'agent-browser':
            raise KitError('Browser cases must declare browser_backend="agent-browser" after adapting their interface, '
                           'or explicitly select playwright compatibility')
    if variant != 'baseline' and variant not in case.get('variants', {}):
        raise KitError('Unknown variant: ' + variant)
    from .operations import operation, service_resources, service_names, browser_resources
    access = case.get('service_access', 'shared')
    if access not in ('shared', 'exclusive'):
        raise KitError('service_access must be shared or exclusive')
    manager = Services(ws)
    names = case.get('services', [])
    if kind == 'verify':
        names, jobs = manager._plan(names, None)
    else:
        names = service_names(ws, names)
    resources = {}
    if case.get('browser'):
        if browser_backend == 'playwright':
            original_browser = ws.cfg.get('browser', {})
            ws.cfg['browser'] = {**original_browser, 'backend': 'playwright'}
            resources.update(browser_resources(ws))
            ws.cfg['browser'] = original_browser
    if case.get('creates_tasks'):
        resources['case:' + str(ws.root) + ':' + ws.environment + ':' + name] = 'exclusive'
    outputs = []
    for iteration in range(repeat):
        launches.checkpoint()
        inputs = merge(case.get('inputs', {}), case.get('variants', {}).get(variant, {}))
        path, record = new_run(ws, name, kind, inputs)
        record.update(variant=variant, iteration=iteration + 1, mode=case.get('mode', 'unspecified'))
        if kind == 'verify':
            reservation = manager.reservation(names, 'verify.run', access, record['id'], path, resources)
        else:
            @contextmanager
            def lab_reservation():
                with operation(ws, 'lab.run', {**service_resources(ws, names, builds=False, access=access), **resources}, record['id'], path) as oid:
                    yield oid, lambda: None
            reservation = lab_reservation()
        with reservation as (oid, prepared), interrupted_run(path, record, case):
            record['operation_id'] = oid
            from .storage_metadata import own
            own(ws, path, 'runs', oid)
            from .operations import Journal
            record['resources'] = Journal().show(oid)['held']
            record['service_access'] = access
            record['service_resources'] = service_resources(ws, names, builds=False, access=access)
            if case.get('cleanup'):
                from .service_registry import Registry
                record['cleanup_signature'] = Registry().cleanup_signature(ws, case['cleanup'])
            save(path / 'run.json', record)
            state = None
            try:
                save(path / 'active.json', {'run_id': record['id'], 'phase': 'preparing'})
                browser = None
                if kind == 'verify':
                    services = case.get('services', [])
                    if services:
                        event(path, record, 'services', 'running')
                        snapshot = {n: v for n, v in manager._up_all(names, jobs).items() if n in names}
                        save(path / 'services.json', snapshot)
                        record['services'] = snapshot
                        event(path, record, 'services', 'passed')
                    if case.get('auth'):
                        summary, state = authenticate(ws, case['auth'])
                        record['auth'] = summary
                        event(path, record, 'authentication', 'passed')
                    if case.get('browser'):
                        if browser_backend == 'agent-browser':
                            browser = manage(ws, 'up', backend=browser_backend, sid=uuid.uuid4().hex, auth_state=state)
                        else:
                            browser = manage(ws, 'up', backend=browser_backend)
                        record['browser'] = browser
                        save(path / 'browser.json', browser)
                        event(path, record, 'browser', 'passed')
                if kind != 'verify':
                    record['services'] = manager.status(probe=False, names=names)
                prepared()
                launches.checkpoint()
                record['resources'] = Journal().show(oid)['held']
                save(path / 'run.json', record)
                save(path / 'active.json', {'run_id': record['id'], 'phase': 'executing'})
                request = {'protocol': 1, 'run_id': record['id'], 'inputs': inputs,
                           'environment': ws.environment, 'variant': variant, 'iteration': iteration + 1,
                           'browser': browser, 'active_file': str(path / 'active.json')}
                event(path, record, 'adapter', 'running')
                value = execute(ws, case, path / 'adapter', request, {'auth': state})
                launches.checkpoint()
                record.update({k: v for k, v in value.items() if k in {'status', 'checks', 'metrics', 'duration', 'scope', 'reason', 'exit_code'}})
                record['artifacts'] = ['adapter/' + x for x in value.get('artifacts', [])]
                record['status'] = verdict(record, case.get('required_checks', []))
                event(path, record, 'adapter', record['status'])
                needs_cleanup = value.get('reason') == 'timeout' or value.get('status') == 'interrupted' or bool(case.get('cleanup'))
                if needs_cleanup:
                    if case.get('cleanup'):
                        cleanup = execute(ws, case['cleanup'], path / 'cleanup', {'run_id': record['id'], 'active': read(path / 'active.json')}, {'auth': state})
                        confirmed = cleanup.get('status') == 'passed' and cleanup.get('terminal_confirmed') is True
                        record['cleanup'] = 'confirmed' if confirmed else 'unconfirmed'
                    else:
                        confirmed = not case.get('creates_tasks', False)
                        record['cleanup'] = 'local_process_only' if confirmed else 'unconfirmed'
                    if not confirmed:
                        record['status'] = 'unverified'
                        record['reason'] = 'Business task cleanup not confirmed'
                    else:
                        (path / 'active.json').unlink(missing_ok=True)
                elif case.get('creates_tasks') and value.get('terminal_confirmed') is not True:
                    record['status'] = 'unverified'
                    record['reason'] = 'Business terminal state missing'
                else:
                    (path / 'active.json').unlink(missing_ok=True)
            except launches.EmergencyInterrupted:
                raise
            except KeyboardInterrupt:
                record.update(status='interrupted', reason='Interrupted; inspect active state')
            except Exception as error:
                record.update(status='blocked', reason=str(error))
                if hasattr(error, 'details'):
                    record.update(error.details)
                # Preparation has not submitted a business request.
                if read(path / 'active.json', {}).get('phase') == 'preparing':
                    (path / 'active.json').unlink(missing_ok=True)
            # All abnormal exits need a cleanup decision, including malformed results.
            launches.checkpoint()
            if (path / 'active.json').exists() and record.get('cleanup') != 'unconfirmed':
                if case.get('cleanup'):
                    try:
                        cleanup = execute(ws, case['cleanup'], path / 'cleanup-error',
                                          {'run_id': record['id'], 'active': read(path / 'active.json')}, {'auth': state})
                        confirmed = cleanup.get('status') == 'passed' and cleanup.get('terminal_confirmed') is True
                    except launches.EmergencyInterrupted:
                        raise
                    except Exception:
                        confirmed = False
                    record['cleanup'] = 'confirmed' if confirmed else 'unconfirmed'
                    if confirmed:
                        (path / 'active.json').unlink()
                elif not case.get('creates_tasks', False):
                    record['cleanup'] = 'local_process_only'
                    (path / 'active.json').unlink()
                else:
                    record['cleanup'] = 'unconfirmed'
            launches.checkpoint()
            if browser and browser.get('backend') == 'agent-browser':
                try:
                    manage(ws, 'down', backend='agent-browser', sid=browser['session_id'])
                except KitError:
                    record.update(status='unverified', reason='Native browser cleanup unconfirmed')
            record['finished'] = time.time()
            save(path / 'run.json', record)
            report(path)
            from .storage_metadata import own
            own(ws, path, 'runs', oid, record)
            outputs.append(record)
        if (path / 'active.json').exists():
            break
    return outputs
