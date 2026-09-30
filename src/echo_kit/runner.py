import time

from .adapter import execute
from .auth import authenticate
from .browser import manage
from .core import KitError, event, merge, new_run, read, save
from .runs import report
from .services import Services


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


def run(ws, name, kind='lab', variant='baseline', repeat=1):
    if repeat < 1:
        raise KitError('repeat must be positive')
    cases = ws.cfg.get('cases', {})
    if name not in cases:
        raise KitError('Unknown case: ' + name)
    case = cases[name]
    if variant != 'baseline' and variant not in case.get('variants', {}):
        raise KitError('Unknown variant: ' + variant)
    if any(ws.runs.glob('*/active.json')):
        raise KitError('Unfinished run found; inspect runs and use its cleanup adapter')
    outputs = []
    for iteration in range(repeat):
        inputs = merge(case.get('inputs', {}), case.get('variants', {}).get(variant, {}))
        path, record = new_run(ws, name, kind, inputs)
        record.update(variant=variant, iteration=iteration + 1, mode=case.get('mode', 'unspecified'))
        state = None
        try:
            save(path / 'active.json', {'run_id': record['id'], 'phase': 'preparing'})
            browser = None
            if kind == 'verify':
                services = case.get('services', [])
                if services:
                    event(path, record, 'services', 'running')
                    snapshot = Services(ws).up(services)
                    save(path / 'services.json', snapshot)
                    event(path, record, 'services', 'passed')
                if case.get('auth'):
                    summary, state = authenticate(ws, case['auth'])
                    record['auth'] = summary
                    event(path, record, 'authentication', 'passed')
                if case.get('browser'):
                    browser = manage(ws, 'up')
                    event(path, record, 'browser', 'passed')
            save(path / 'active.json', {'run_id': record['id'], 'phase': 'executing'})
            request = {'protocol': 1, 'run_id': record['id'], 'inputs': inputs,
                       'environment': ws.environment, 'variant': variant, 'iteration': iteration + 1,
                       'browser': browser, 'active_file': str(path / 'active.json')}
            event(path, record, 'adapter', 'running')
            value = execute(ws, case, path / 'adapter', request, {'auth': state})
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
        except KeyboardInterrupt:
            record.update(status='interrupted', reason='Interrupted; inspect active state')
        except Exception as error:
            record.update(status='blocked', reason=str(error))
            # Preparation has not submitted a business request.
            if read(path / 'active.json', {}).get('phase') == 'preparing':
                (path / 'active.json').unlink(missing_ok=True)
        # All abnormal exits need a cleanup decision, including malformed results.
        if (path / 'active.json').exists() and record.get('cleanup') != 'unconfirmed':
            if case.get('cleanup'):
                try:
                    cleanup = execute(ws, case['cleanup'], path / 'cleanup-error',
                                      {'run_id': record['id'], 'active': read(path / 'active.json')}, {'auth': state})
                    confirmed = cleanup.get('status') == 'passed' and cleanup.get('terminal_confirmed') is True
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
        record['finished'] = time.time()
        save(path / 'run.json', record)
        report(path)
        outputs.append(record)
        if (path / 'active.json').exists():
            break
    return outputs
