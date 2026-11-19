"""Prototype evidence using the pinned native browser, without project-specific logic."""

import hashlib
import json
import mimetypes
import secrets
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import launches
from .core import KitError, save
from .document_map import Discovery
from .observation import validate
from .page_readiness import public_address, viewport_issue
from .visual import MAX_PIXELS, ORIGIN, LocalReference, configuration, differences


class ReferenceServer:
    """A short-lived, read-only loopback asset server with a private capability path."""

    def __init__(self, settings):
        self.local = LocalReference(settings)
        self.token = secrets.token_urlsafe(24)
        self.server = None
        self.thread = None

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                prefix = '/' + owner.token + '/'
                if self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_port):
                    self.send_error(404)
                    return
                parsed = urlsplit(self.path)
                if parsed.path.startswith(prefix):
                    relative = parsed.path[len(prefix):]
                else:
                    # Root-relative CSS/module assets work after the explicit capability
                    # document has established an isolated, HttpOnly reference cookie.
                    cookie = SimpleCookie()
                    try:
                        cookie.load(self.headers.get('Cookie', ''))
                    except Exception:
                        cookie = SimpleCookie()
                    value = cookie.get('echo-reference')
                    if value is None or not secrets.compare_digest(value.value, owner.token):
                        self.send_error(404)
                        return
                    relative = parsed.path.lstrip('/')
                try:
                    # The shared-path gate is checked before body reads, on every request.
                    owner.local.discovery.git.cache.clear()
                    path = owner.local.asset(ORIGIN + '/' + relative)
                    if Discovery(owner.local.ws).allowed(path):
                        raise KitError('Reference ownership changed')
                    body = owner.local.read_asset(path)
                    owner.local.assets[path.relative_to(owner.local.root).as_posix()] = hashlib.sha256(body).hexdigest()
                    self.send_response(200)
                    self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Set-Cookie', 'echo-reference=' + owner.token + '; Path=/; HttpOnly; SameSite=Strict')
                    self.end_headers()
                    if self.command == 'GET':
                        self.wfile.write(body)
                except (KitError, OSError, ValueError):
                    owner.local.failures += 1
                    self.send_error(404)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        relative = Path(self.local.source['path']).relative_to(self.local.root).as_posix()
        self.url = f'http://127.0.0.1:{self.server.server_port}/{self.token}/' + quote(relative)
        return self

    def __exit__(self, *_):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)


def _data(result):
    if not isinstance(result, dict) or result.get('success') is False:
        raise KitError('Native visual command failed; inspect the session diagnostics')
    value = result.get('data', result)
    if not isinstance(value, dict):
        raise KitError('Native visual command returned invalid data')
    return value


def _pair(reference, actual, path):
    from PIL import Image
    with Image.open(reference) as a, Image.open(actual) as b:
        width, height = max(a.width, b.width), max(a.height, b.height)
        if width * height > MAX_PIXELS:
            raise KitError('Comparison region exceeds 2560×2560 pixels; select a smaller region')
        result = Image.new('RGB', (a.width + b.width, height), 'white')
        result.paste(a.convert('RGB'), (0, 0))
        result.paste(b.convert('RGB'), (a.width, 0))
        result.save(path)
        return a.size == b.size


class NativeVisual:
    def __init__(self, ws, output, target, reference_factory, timeout_ms=10000,
                 reference=None, reference_root=None, spec=None):
        self.ws, self.output, self.target = ws, Path(output), target
        self.output.mkdir(parents=True, exist_ok=True)
        self.reference_factory = reference_factory
        self.timeout_ms = timeout_ms
        self.sources = configuration(ws, reference, reference_root, spec)
        self.baselines = {}
        self.deadline = None
        self.kept_reference = None
        self.kept_context = None
        self.kept_server = None
        self.script = files('echo_kit').joinpath('resources/visual-inspect.js').read_text(encoding='utf-8')

    def close(self):
        try:
            if self.kept_context:
                self.kept_context.__exit__(None, None, None)
        finally:
            if self.kept_server:
                self.kept_server.__exit__(None, None, None)
            self.kept_reference = self.kept_context = self.kept_server = None

    def _checkpoint(self):
        launches.checkpoint()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise KitError('Visual request exceeded its time budget; use fewer regions or views')

    def _inspect(self, page, selector, limit=20):
        self._checkpoint()
        argument = json.dumps({'selector': selector, 'limit': limit, 'timeoutMs': self.timeout_ms})
        value = page.evaluate('/* echo-kit-visual-inspect */ (' + self.script + ')(' + argument + ')')
        if not isinstance(value, dict) or not isinstance(value.get('elements'), list):
            raise KitError('Native inspection returned invalid data')
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 64 * 1024:
            raise KitError('Inspection exceeds 64 KiB; narrow the selector')
        if value.get('truncated'):
            value['next_step'] = 'Narrow the selector; remaining elements have not been inspected'
        return value

    @contextmanager
    def _reference(self, request):
        descriptor = self.sources.get('reference')
        if not descriptor:
            raise KitError('Visual baselines require --reference; Markdown alone supplies requirements, not pixels')
        if request.get('reload'):
            self.close()
        if self.kept_reference:
            yield self.kept_reference, self.kept_server
            return
        server = ReferenceServer(self.sources) if descriptor['kind'] == 'local' else None
        if server:
            server.__enter__()
        context = None
        kept = False
        try:
            context = self.reference_factory(server.url if server else descriptor['url'])
            page = context.__enter__()
            if request.get('keep_reference', False):
                self.kept_context, self.kept_reference, self.kept_server = context, page, server
                kept = True
            yield page, server
        finally:
            if not kept:
                try:
                    if context:
                        context.__exit__(None, None, None)
                finally:
                    if server:
                        server.__exit__(None, None, None)

    @contextmanager
    def reference_page(self, keep=True):
        """Permit explicit reference interaction without borrowing target authentication."""
        with self._reference({'keep_reference': keep}) as (page, _):
            yield page

    @contextmanager
    def _capture_options(self, page, request, reference):
        selectors = [item['reference' if reference else 'target'] for item in request.get('exclude', [])]
        key = '__echo_visual_' + secrets.token_hex(12)
        options = {'key': key, 'selectors': selectors, 'animations': request.get('animations', 'allow')}
        script = '''/* echo-kit-visual-mask */ (options => {
          const records=[]; let style=null;
          for(const selector of options.selectors) {
            const nodes=[...document.querySelectorAll(selector)];
            if(nodes.length!==1) throw new Error('Excluded selector must match one element');
            const element=nodes[0],r=element.getBoundingClientRect();
            if(!r.width||!r.height) throw new Error('Excluded element must be visible');
            records.push({element,style:element.getAttribute('style'),box:{x:r.x,y:r.y,width:r.width,height:r.height}});
          }
          window[options.key]={records,style:null};
          try {
            for(const record of records) record.element.style.setProperty('visibility','hidden','important');
            if(options.animations==='disabled') {
              style=document.createElement('style');style.textContent='*,*::before,*::after{animation-play-state:paused!important;transition:none!important}';
              document.head.appendChild(style);window[options.key].style=style;
            }
            return records.map(record=>record.box);
          } catch(error) {
            for(const record of records) record.style===null?record.element.removeAttribute('style'):record.element.setAttribute('style',record.style);
            if(style)style.remove();delete window[options.key];throw error;
          }
        })(''' + json.dumps(options) + ')'
        boxes = page.evaluate(script)
        try:
            yield boxes or []
        finally:
            restored = page.evaluate('''/* echo-kit-visual-restore */ (key=>{
              const value=window[key];if(!value)return false;
              for(const record of value.records)record.style===null?record.element.removeAttribute('style'):record.element.setAttribute('style',record.style);
              if(value.style)value.style.remove();delete window[key];return true;
            })(''' + json.dumps(key) + ')')
            if restored is not True:
                raise KitError('Temporary visual exclusions could not be confirmed restored')

    def _owned_png(self, value):
        path = Path(value)
        if (not path.resolve().is_relative_to(self.output.resolve()) or not path.is_file()
                or path.suffix.lower() != '.png' or path.stat().st_size > 64 * 1024 * 1024):
            raise KitError('Visual image is missing or outside this observation session')
        return path

    def _sample(self, page, selector, request, prefix, reference=False, diff_baseline=None,
                inspect_selector=None, check_viewport=False):
        self._checkpoint()
        began = time.monotonic()
        readiness_deadline = began + self.timeout_ms / 1000
        check = None
        if inspect_selector is not None:
            check = page.check_page(inspect_selector, min(self.deadline, readiness_deadline))
            if check['status'] != 'ready':
                raise page.readiness_failure(check, self.output / (prefix + '-failure.png'))
        while True:
            measured = self._inspect(page, inspect_selector or selector or 'body', 1)
            if measured.get('matched', 0) > 1:
                raise KitError('Each capture selector must match exactly one visible element')
            if (measured.get('matched') == 1 and (not (selector or inspect_selector)
                    or measured['elements'][0].get('visible'))):
                break
            if time.monotonic() >= readiness_deadline:
                raise KitError('Capture target was not uniquely visible before the readiness timeout')
            self._checkpoint()
            time.sleep(.05)
        area = measured['elements'][0]['box'] if selector else measured['profile']['viewport']
        if area['width'] * area['height'] > MAX_PIXELS:
            raise KitError('Comparison region exceeds 2560×2560 pixels; narrow the region')
        path = self.output / (prefix + '.png')
        reasons = []
        ready_ms = (time.monotonic() - began) * 1000
        screenshot_started = time.monotonic()
        with self._capture_options(page, request, reference) as excluded:
            page.screenshot(path, selector=selector)
            previous = self._owned_png(path).read_bytes()
            stable = False
            for _ in range(3):
                self._checkpoint()
                time.sleep(.08)
                page.screenshot(path, selector=selector)
                current = self._owned_png(path).read_bytes()
                if current == previous:
                    stable = True
                    break
                previous = current
            if not stable:
                reasons.append('continuously_changing_pixels')
            for box in excluded:
                if box['width'] >= area['width'] and box['height'] >= area['height']:
                    reasons.append('excluded_region_covers_capture')
            metric = None
            if diff_baseline:
                diff_path = self.output / (prefix + '-diff.png')
                command = ['diff', 'screenshot', '--baseline', str(diff_baseline), '--output', str(diff_path),
                           '--threshold', str(request.get('threshold', .1))]
                if selector:
                    command += ['--selector', selector]
                metric = _data(page.command(command))
                if metric.get('diffPath'):
                    proposed = Path(metric['diffPath'])
                    if proposed.resolve() != diff_path.resolve():
                        raise KitError('Native diff image is outside the requested observation artifact')
                    # The fixed dependency always echoes output, even when no diff PNG
                    # is written (identical pixels or unequal dimensions).
                    if metric.get('dimensionMismatch') or metric.get('differentPixels') == 0:
                        metric['diffPath'] = None
                    elif proposed.is_file():
                        self._owned_png(proposed)
                    else:
                        metric['diffPath'] = None
                        reasons.append('diff_image_unavailable')
                # The dependency captures again for its diff. Check the displayed actual
                # frame after that command instead of assuming the earlier frame persisted.
                page.screenshot(path, selector=selector)
                if self._owned_png(path).read_bytes() != current:
                    reasons.append('pixels_changed_during_comparison')
        diagnostics = measured.get('diagnostics', {})
        reasons += [name for name in ('failed_fonts', 'pending_fonts', 'failed_visible_images',
                                      'pending_visible_images', 'failed_resources') if diagnostics.get(name)]
        screenshot_ms = (time.monotonic() - screenshot_started) * 1000
        read_started = time.monotonic()
        png = self._owned_png(path).read_bytes()
        digest = hashlib.sha256(png).hexdigest()
        if check_viewport and (issue := viewport_issue(png)):
            reasons.append(issue)
        file_read_ms = (time.monotonic() - read_started) * 1000
        result = {'path': str(path), 'sha256': digest,
                  'measurement': measured, 'unverified': reasons, 'pixels': metric,
                  'timings_ms': {'ready': round(ready_ms, 2), 'screenshot': round(screenshot_ms, 2),
                                 'file_read': round(file_read_ms, 2)},
                  'elapsed_ms': round((time.monotonic() - began) * 1000, 2)}
        if check is not None:
            result['page_check'] = check
        return result

    def observe(self, request, observation_id):
        """Capture at most four views without creating a reference or changing page state."""
        validate(request)
        if request.get('op') != 'observe':
            raise KitError('observe requires an observation request')
        self._identity(observation_id)
        self.deadline = time.monotonic() + self.timeout_ms / 1000 * 4 + 5
        old_viewport = dict(self.target.viewport_size)
        scroll = self.target.evaluate('/* echo-kit-visual-scroll */ (() => ({x:scrollX,y:scrollY}))()')
        captures = []
        try:
            location = self.target.evaluate('/* echo-kit-visual-url */ location.href')
            for index, view in enumerate(request.get('viewports') or [old_viewport]):
                if view != self.target.viewport_size:
                    self.target.set_viewport(view)
                target = request.get('target')
                selector = target if request.get('capture') == 'element' else None
                sample = self._sample(self.target, selector, request, f'{observation_id}-actual-{index}',
                                      inspect_selector=target, check_viewport=selector is None)
                sample.pop('pixels', None)
                sample['viewport'] = dict(view)
                sample['url'] = public_address(location)
                sample['animations'] = request.get('animations', 'allow')
                sample['diagnostics'] = sample['measurement'].get('diagnostics', {})
                check = sample.get('page_check')
                if sample['unverified']:
                    if check is None:
                        check = self.target.check_page(None, time.monotonic() + 1)
                    self.target.page_errors(check)
                if check is not None:
                    sample['url'] = check.get('url') or sample['url']
                    sample['page_check'] = check
                captures.append(sample)
        finally:
            if self.target.viewport_size != old_viewport:
                self.target.set_viewport(old_viewport)
            self.target.evaluate('/* echo-kit-visual-scroll-restore */ (p=>scrollTo(p.x,p.y))(' + json.dumps(scroll) + ')')
        return {'captures': captures, 'images': [value['path'] for value in captures],
                'verification': 'unverified' if any(value['unverified'] for value in captures) else 'captured'}

    @staticmethod
    def _identity(observation_id):
        if not isinstance(observation_id, str) or not observation_id or any(c not in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-' for c in observation_id):
            raise KitError('Invalid observation identity')

    def dispatch(self, request, observation_id):
        validate(request)
        self._identity(observation_id)
        if type(request.get('keep_reference', False)) is not bool:
            raise KitError('keep_reference must be a boolean')
        self.deadline = time.monotonic() + self.timeout_ms / 1000 * 4 + 5
        op = request['op']
        if op == 'inspect':
            if request.get('page', 'target') == 'reference':
                with self._reference(request) as (page, server):
                    result = self._inspect(page, request.get('target') or 'body > *', request.get('limit', 20))
                    if server:
                        result = json.loads(json.dumps(result, ensure_ascii=False).replace(server.token, '[reference]'))
            else:
                result = self._inspect(self.target, request.get('target') or 'body > *', request.get('limit', 20))
            return {'inspection': result, 'images': [], 'spec': self.sources.get('spec')}
        if op == 'reference':
            return self._capture_reference(request, observation_id)
        if op == 'compare':
            return self._compare(request, observation_id)
        raise KitError('Native visual supports inspect, reference and compare')

    def _capture_reference(self, request, oid):
        if oid in self.baselines:
            raise KitError('Baseline identities are immutable; use a fresh observation identity')
        captures = []
        regions = request.get('regions') or [{'id': 'viewport', 'reference': None, 'target': None}]
        with self._reference(request) as (page, server):
            viewport = page.viewport_size
            scroll = page.evaluate('/* echo-kit-visual-scroll */ (() => ({x:scrollX,y:scrollY}))()')
            try:
                for index, view in enumerate(request.get('viewports') or [self.target.viewport_size]):
                    page.set_viewport(view)
                    for number, region in enumerate(regions):
                        sample = self._sample(page, region['reference'], request, f'{oid}-reference-{index}-{number}', True)
                        captures.append({'viewport': view, 'region': region, **sample})
            finally:
                page.set_viewport(viewport)
                page.evaluate('/* echo-kit-visual-scroll-restore */ (p=>scrollTo(p.x,p.y))(' + json.dumps(scroll) + ')')
            assets = dict(server.local.assets) if server else {}
            if server:
                for sample in captures:
                    if server.local.failures or server.local.changed(assets):
                        sample['unverified'].append('reference_assets_unverified')
                    # A private server token must not leak into normal diagnostics.
                    encoded = json.dumps(sample, ensure_ascii=False).replace(server.token, '[reference]')
                    sample.clear(); sample.update(json.loads(encoded))
        spec = self.sources.get('spec')
        if spec:
            spec = configuration(self.ws, spec=spec['path'])['spec']
        source = self.sources['reference']
        baseline = {'baseline_id': oid, 'time': time.time(), 'state': request.get('state', 'default'),
                    'source': source['source'], 'source_kind': source['kind'], 'captures': captures,
                    'assets': assets, 'spec': spec, 'exclude': request.get('exclude', []),
                    'animations': request.get('animations', 'allow')}
        baseline['source_digest'] = hashlib.sha256(json.dumps(
            [{'sha256': value['sha256'], 'viewport': value['viewport'], 'region': value['region'],
              'measurement': value['measurement']} for value in captures], sort_keys=True).encode('utf-8')).hexdigest()
        baseline['source_digest_kind'] = 'rendered-capture'
        self.baselines[oid] = baseline
        save(self.output / (oid + '-baseline.json'), baseline)
        return {**baseline, 'images': [c['path'] for c in captures],
                'verification': 'unverified' if any(c['unverified'] for c in captures) else 'captured'}

    def _compare(self, request, oid):
        baseline = self.baselines.get(request['baseline_id'])
        if not baseline:
            raise KitError('Unknown baseline in this session; explicitly capture reference first')
        if request.get('state', baseline['state']) != baseline['state']:
            raise KitError('State label differs from the baseline')
        for item in baseline['captures']:
            if hashlib.sha256(self._owned_png(item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise KitError('Reference screenshot changed; explicitly capture a new reference')
        if baseline['source_kind'] == 'local' and LocalReference(self.sources).changed(baseline['assets']):
            raise KitError('Reference assets changed; explicitly capture a new reference')
        if baseline['spec'] and configuration(self.ws, spec=baseline['spec']['path'])['spec']['sha256'] != baseline['spec']['sha256']:
            raise KitError('Requirements changed; explicitly capture a new reference')
        viewport = self.target.viewport_size
        scroll = self.target.evaluate('/* echo-kit-visual-scroll */ (() => ({x:scrollX,y:scrollY}))()')
        options = {**request, 'exclude': baseline['exclude'], 'animations': baseline['animations']}
        comparisons, images = [], []
        try:
            for index, reference in enumerate(baseline['captures']):
                self.target.set_viewport(reference['viewport'])
                actual = self._sample(self.target, reference['region']['target'], options, f'{oid}-actual-{index}',
                                      diff_baseline=reference['path'])
                pair = self.output / f'{oid}-pair-{index}.png'
                same_size = _pair(reference['path'], actual['path'], pair)
                pixel_data = actual.pop('pixels') or {}
                reasons = list(set(reference['unverified'] + actual['unverified']))
                if reference['measurement']['profile'] != actual['measurement']['profile']:
                    reasons.append('rendering_conditions_differ')
                if not same_size or pixel_data.get('dimensionMismatch'):
                    reasons.append('region_dimensions_differ')
                images.extend([actual['path'], str(pair)])
                if pixel_data.get('diffPath'):
                    images.append(str(self._owned_png(pixel_data['diffPath'])))
                metric = {key: pixel_data.get(key) for key in ('mismatchPercentage', 'totalPixels', 'differentPixels', 'dimensionMismatch')}
                if not same_size or pixel_data.get('dimensionMismatch'):
                    metric.update(mismatchPercentage=None, totalPixels=None, differentPixels=None)
                metric['parameters'] = {'engine': 'agent-browser', 'version': '0.43.0', 'threshold': request.get('threshold', .1)}
                comparisons.append({'region': reference['region']['id'], 'viewport': reference['viewport'],
                    'reference': {'path': reference['path'], 'sha256': reference['sha256']}, 'actual': actual,
                    'differences': differences(reference['measurement']['elements'][0], actual['measurement']['elements'][0]),
                    'pixels': metric, 'unverified': sorted(set(reasons))})
        finally:
            self.target.set_viewport(viewport)
            self.target.evaluate('/* echo-kit-visual-scroll-restore */ (p=>scrollTo(p.x,p.y))(' + json.dumps(scroll) + ')')
        return {'baseline_id': baseline['baseline_id'], 'state': baseline['state'], 'comparisons': comparisons,
                'images': images, 'spec': baseline['spec'],
                'verification': 'unverified' if any(c['unverified'] for c in comparisons) else 'compared',
                'meaning': 'Evidence captured; no overall visual or business acceptance verdict'}
