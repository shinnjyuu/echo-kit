"""Persistent visual worker and the public entry point for project adapters."""

import hashlib
import json
import sys
import time
import uuid
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import __version__, browser, launches
from .core import KitError, save

MAX_LINE = 262144


def request_id(request):
    value = request.get('id') if isinstance(request, dict) else None
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise KitError('An HTTP(S) URL is required')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise KitError('Use an HTTP(S) URL without credentials')
    return value


def public_url(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '<redacted>' if parsed.query else '', ''))


def emit(value, stream):
    line = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(line.encode('utf-8')) > MAX_LINE:
        raise KitError('Observation response is too large')
    print(line, file=stream, flush=True)


def secret_strings(value):
    if isinstance(value, dict):
        return [text for item in value.values() for text in secret_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in secret_strings(item)]
    return [value] if isinstance(value, str) and len(value) >= 4 else []


def clean(value, secrets, max_bytes=32768):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode('utf-8')) > max_bytes:
        raise KitError('Observation metadata exceeds ' + str(max_bytes // 1024) + ' KiB; narrow the request')
    for secret in secrets:
        encoded = encoded.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[redacted]')
    return json.loads(encoded)


def validate(request):
    if request_id(request) is None:
        raise KitError('Each request requires a nonempty string id of at most 128 characters')
    op = request.get('op')
    if op not in ('observe', 'navigate', 'reload', 'close', 'inspect', 'reference', 'compare'):
        raise KitError('Supported operations are observe, navigate, reload, close, inspect, reference and compare')
    if op == 'navigate':
        url(request.get('url'))
    target = request.get('target')
    if target is not None and (not isinstance(target, str) or not target.strip() or len(target) > 512):
        raise KitError('target must be a nonempty CSS selector of at most 512 characters')
    if request.get('capture', 'viewport') not in ('viewport', 'element'):
        raise KitError('capture must be viewport or element')
    if request.get('capture') == 'element' and not target:
        raise KitError('Element capture requires target')
    if request.get('animations', 'allow') not in ('allow', 'disabled'):
        raise KitError('animations must be allow or disabled')
    views = request.get('viewports', [])
    if not isinstance(views, list) or len(views) > 4:
        raise KitError('viewports must contain at most four width/height objects')
    for view in views:
        if not isinstance(view, dict) or any(type(view.get(key)) is not int or not 240 <= view[key] <= 2560
                                             for key in ('width', 'height')):
            raise KitError('Viewport width and height must be integers between 240 and 2560')
    if op in ('inspect', 'reference', 'compare'):
        if 'reload' in request and (op != 'reference' or type(request['reload']) is not bool):
            raise KitError('Only reference accepts a boolean reload option; actual-page reload is a separate explicit operation')
        if request.get('page', 'target') not in ('target', 'reference'):
            raise KitError('page must be target or reference')
        if type(request.get('limit', 20)) is not int or not 1 <= request.get('limit', 20) <= 20:
            raise KitError('inspect limit must be an integer between 1 and 20; narrow the selector')
        if not isinstance(request.get('state', 'default'), str) or len(request.get('state', 'default')) > 128:
            raise KitError('state must be a label of at most 128 characters')
        for field in ('regions', 'exclude'):
            values = request.get(field, [])
            if not isinstance(values, list) or len(values) > 10:
                raise KitError(field + ' must contain at most ten items; narrow the request')
            identities = set()
            for item in values:
                if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                                      or len(item[k]) > 512 for k in ('reference', 'target')):
                    raise KitError(field + ' requires reference and target CSS selectors')
                if field == 'regions':
                    name = item.get('id')
                    if not isinstance(name, str) or not name or len(name) > 128 or name in identities:
                        raise KitError('Region ids must be unique nonempty labels of at most 128 characters')
                    identities.add(name)
        if op == 'compare':
            if not isinstance(request.get('baseline_id'), str) or not request['baseline_id']:
                raise KitError('compare requires baseline_id from an explicit reference request')
            if any(key in request for key in ('regions', 'exclude', 'viewports', 'animations')):
                raise KitError('compare uses fixed baseline regions, exclusions, viewports and animation settings')
            value = request.get('threshold', .1)
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise KitError('threshold must be a finite number between 0 and 1')
    return op


class Observer:
    def __init__(self, page, settings, ready=None, diagnose=None, annotations=None):
        self.page, self.settings = page, settings
        self.ready, self.diagnose = ready, diagnose
        self.output = Path(settings['output'])
        self.script = files('echo_kit').joinpath('resources/observe.js').read_text(encoding='utf-8')
        self.secrets = secret_strings(settings.get('auth'))
        self.annotations = clean(annotations or {}, self.secrets)
        self.errors = {'page_errors': 0, 'console_errors': 0, 'failed_requests': 0}
        self.visual = None
        page.on('pageerror', lambda *_: self.count('page_errors'))
        page.on('console', lambda item: self.count('console_errors') if item.type == 'error' else None)
        page.on('requestfailed', lambda *_: self.count('failed_requests'))
        page.on('response', lambda response: self.count('failed_requests') if response.status >= 400 else None)

    def count(self, key):
        self.errors[key] += 1

    def response(self, request):
        started = time.monotonic()
        observation_id = uuid.uuid4().hex
        result = {'protocol': 1, 'op': request.get('op') if isinstance(request, dict) else None,
                  'id': request_id(request),
                  'operation_id': self.settings['operation_id'], 'observation_id': observation_id,
                  'time': datetime.now(timezone.utc).isoformat(), 'status': 'error', 'images': [], 'captures': []}
        old_viewport = self.page.viewport_size
        try:
            op = validate(request)
            launches.checkpoint()
            if op == 'navigate':
                response = self.page.goto(request['url'], wait_until='domcontentloaded')
                if response and response.status >= 400:
                    raise KitError('Navigation returned an HTTP error')
            elif op == 'reload':
                response = self.page.reload(wait_until='domcontentloaded')
                if response and response.status >= 400:
                    raise KitError('Reload returned an HTTP error')
            if op == 'observe':
                captures = []
                for index, view in enumerate(request.get('viewports') or [old_viewport]):
                    launches.checkpoint()
                    if view != self.page.viewport_size:
                        self.page.set_viewport_size(view)
                    captures.append(self.capture(request, observation_id, index))
                result.update(captures=captures, images=[item['path'] for item in captures])
            elif op in ('inspect', 'reference', 'compare'):
                if self.visual is None:
                    from .visual import Visual
                    self.visual = Visual(self)
                result.update(clean(self.visual.dispatch(request, observation_id), self.secrets, MAX_LINE - 1024))
            result.update(status='ok', url=public_url(self.page.url), annotations=self.annotations)
            if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_LINE - 1024:
                raise KitError('Visual response exceeds 256 KiB; request fewer regions or viewports')
        except Exception as error:
            # Runtime exceptions can contain page data, URLs or adapter credentials.
            result.update(error={'code': 'observation_failed', 'type': type(error).__name__}, images=[], captures=[])
            if result['op'] in ('inspect', 'reference', 'compare'):
                result['verification'] = 'unverified'
            if isinstance(error, KitError):
                result['error']['message'] = clean(str(error), self.secrets)
        finally:
            try:
                if self.page.viewport_size != old_viewport:
                    self.page.set_viewport_size(old_viewport)
            except Exception:
                result.update(status='error', error={'code': 'viewport_restore_failed'}, images=[], captures=[])
        result['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        save(self.output / (observation_id + '.json'), result)
        return result

    def capture(self, request, observation_id, index):
        began = time.monotonic()
        deadline = began + self.settings['timeout_ms'] / 1000
        def remaining():
            value = (deadline - time.monotonic()) * 1000
            if value <= 0:
                raise KitError('Capture readiness timed out')
            return value
        if self.ready:
            self.ready(self.page, request)
        target = request.get('target')
        locator = self.page.locator(target) if target else None
        if locator is not None:
            if locator.count() != 1:
                raise KitError('target must match one element')
            locator.wait_for(state='visible', timeout=remaining())
            locator.scroll_into_view_if_needed(timeout=remaining())
        diagnostic = self.page.evaluate(self.script, {'target': target, 'timeoutMs': remaining()})
        diagnostic.update(self.errors)
        if self.diagnose:
            diagnostic['project'] = clean(self.diagnose(self.page, request), self.secrets)
        ready_ms = (time.monotonic() - began) * 1000
        path = self.output / f'{observation_id}-{index}.png'
        screenshot_started = time.monotonic()
        source = locator if request.get('capture') == 'element' else self.page
        source.screenshot(path=str(path), scale='css', animations=request.get('animations', 'allow'), timeout=remaining())
        screenshot_ms = (time.monotonic() - screenshot_started) * 1000
        read_started = time.monotonic()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'path': str(path), 'sha256': digest, 'viewport': self.page.viewport_size,
                'url': public_url(self.page.url), 'animations': request.get('animations', 'allow'),
                'diagnostics': diagnostic, 'timings_ms': {'ready': round(ready_ms, 2),
                'screenshot': round(screenshot_ms, 2), 'file_read': round((time.monotonic() - read_started) * 1000, 2)}}


def serve_observer(*, prepare=None, ready=None, diagnose=None, prepare_reference=None, ready_reference=None):
    """Run in the supervised worker. Hooks receive only this session's page.

    prepare(page, options, auth) runs once and may return descriptive annotations.
    ready(page, request) and diagnose(page, request) run before every capture.
    prepare_reference(page, options) and ready_reference(page, request) are optional;
    the reference context never inherits target authentication.
    Project adapters can mark pending business work using options['operation_id'].
    """
    wire, incoming = sys.stdout, sys.stdin
    settings = json.loads(incoming.readline(MAX_LINE + 1))
    from .operations import bind_operation
    with launches.scope(settings['generation']), bind_operation(settings['operation_id']), redirect_stdout(sys.stderr):
        try:
            storage = settings.get('auth')
            if isinstance(storage, dict) and 'storage_state' in storage:
                storage = storage['storage_state']
            if storage and not (isinstance(storage, dict) and ('cookies' in storage or 'origins' in storage)):
                if prepare is None:
                    raise KitError('This authentication state requires a project prepare hook')
                storage = None
            with ExitStack() as stack:
                context = stack.enter_context(browser.session(settings['endpoint'], settings['output'], storage))
                context.set_default_timeout(settings['timeout_ms'])
                context.set_default_navigation_timeout(settings['timeout_ms'])
                page = context.new_page()
                page.set_viewport_size({'width': 1440, 'height': 900})
                observer = Observer(page, settings, ready, diagnose)
                response = page.goto(settings['url'], wait_until='domcontentloaded')
                if response and response.status >= 400:
                    raise KitError('Initial URL returned an HTTP error')
                annotations = prepare(page, settings, settings.get('auth')) if prepare else None
                observer.annotations = clean(annotations or {}, observer.secrets)
                if settings.get('visual', {}).get('reference'):
                    from .visual import Visual, reference_context
                    reference, local = stack.enter_context(reference_context(context, page, settings, prepare_reference))
                    observer.visual = Visual(observer, reference, local, ready_reference, prepare_reference)
                emit({'protocol': 1, 'event': 'ready', 'status': 'ready', 'version': __version__,
                      'capabilities': ['observe', 'navigate', 'reload', 'close', 'inspect', 'reference', 'compare'],
                      'visual': {key: ({k: v for k, v in value.items() if k in ('kind', 'source')} if key == 'reference' else value)
                                 for key, value in settings.get('visual', {}).items() if key in ('reference', 'spec')},
                      'operation_id': settings['operation_id'], 'output_dir': settings['output'],
                      'reply_file': str(Path(settings['output']) / 'latest.json'),
                      'url': public_url(page.url), 'viewer_url': settings.get('viewer_url')}, wire)
                closing = None
                request = None
                for line in incoming:
                    launches.checkpoint()
                    request = None
                    try:
                        if len(line.encode('utf-8')) > MAX_LINE:
                            raise KitError('Request exceeds 256 KiB')
                        request = json.loads(line)
                        op = validate(request)
                        if op == 'close':
                            closing = request['id']
                            break
                        result = observer.response(request)
                    except (ValueError, KitError):
                        result = {'protocol': 1, 'status': 'error', 'id': request_id(request), 'images': [],
                                  'error': {'code': 'invalid_request'}}
                    emit(result, wire)
            emit({'protocol': 1, 'status': 'closed', 'op': 'close', 'id': closing,
                  'operation_id': settings['operation_id']}, wire)
        except BaseException:
            # Adapter exceptions may embed credentials. The parent retains the operation.
            raise KitError('Observation worker failed; inspect the session operation') from None


def read_reply(path, request_id, timeout_ms=30000):
    """Read only a matching atomic receipt, including from Windows PTY sessions."""
    from .core import read
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        from .storage_metadata import state
        if state(Path(path).parent)['state'] != 'not_reclaimed':
            raise KitError('Observation attachments were reclaimed or are unverified; start a new observation session')
        value = read(path, {})
        if value.get('id') == request_id:
            if any(not Path(i).is_file() for i in value.get('images', [])):
                raise KitError('Observation attachments are missing; request a new observation')
            return value
        time.sleep(.025)
    raise KitError('No receipt for the requested id')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        serve_observer()
    else:
        import argparse
        parser = argparse.ArgumentParser(description='Read a matching observation receipt')
        parser.add_argument('--read-reply', required=True)
        parser.add_argument('--request-id', required=True)
        parser.add_argument('--timeout-ms', type=int, default=30000)
        args = parser.parse_args()
        emit(read_reply(args.read_reply, args.request_id, args.timeout_ms), sys.stdout)
