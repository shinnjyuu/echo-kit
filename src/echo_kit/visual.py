"""Bounded reference acquisition and evidence, independent of project business logic."""

import hashlib
import json
import mimetypes
import os
import stat
import struct
import subprocess
import time
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import launches
from .core import KitError, Workspace, save
from .document_map import Discovery
from .project_docs import read_document
from . import storage_fs

ORIGIN = 'https://echo-kit-reference.invalid'
CAPABILITIES = ['observe', 'navigate', 'reload', 'close', 'inspect', 'reference', 'compare']
MAX_PIXELS = 2560 * 2560


def shared(discovery, raw, suffixes=None):
    path = discovery.target(raw)
    if path is None or (suffixes and path.suffix.lower() not in suffixes) or not path.is_file():
        raise KitError('Visual source must be an existing shared workspace file of the required type')
    return path


def configuration(ws, reference=None, root=None, spec=None):
    from .observation import public_url, url
    discovery = Discovery(ws)
    if discovery.issues:
        raise KitError('Resolve document ownership configuration before loading visual sources')
    settings = {'workspace': str(ws.root)}
    if reference:
        if urlsplit(reference).scheme in ('http', 'https'):
            url(reference)
            if root:
                raise KitError('--reference-root requires a local HTML reference')
            settings['reference'] = {'kind': 'url', 'url': reference, 'source': public_url(reference)}
        else:
            path = shared(discovery, reference, ('.html', '.htm'))
            resource_root = discovery.target(root, directory=True) if root else path.parent
            if resource_root is None or not resource_root.is_dir() or not path.resolve().is_relative_to(resource_root.resolve()):
                raise KitError('The reference HTML must be within its shared resource root')
            settings['reference'] = {'kind': 'local', 'path': str(path), 'root': str(resource_root),
                                     'source': path.relative_to(ws.root).as_posix()}
    elif root:
        raise KitError('--reference-root requires --reference')
    if spec:
        path = shared(discovery, spec, ('.md', '.markdown'))
        body = read_document(path)
        settings['spec'] = {'path': path.relative_to(ws.root).as_posix(),
                            'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
                            'lines': len(body.splitlines()), 'next_step': 'docs read ' + spec}
    return settings


class LocalReference:
    """Read only explicit shared assets through pinned, non-following handles."""
    def __init__(self, settings):
        self.ws = Workspace(settings['workspace'])
        self.discovery = Discovery(self.ws)
        self.source = settings['reference']
        self.root = storage_fs.plain_chain(self.source['root'])
        root_info = self.root.lstat()
        if not stat.S_ISDIR(root_info.st_mode):
            raise KitError('Reference asset root must be a plain directory')
        self.root_identity = storage_fs.identity(root_info)
        self.assets = {}
        self.failures = 0
        self.url = ORIGIN + '/' + Path(self.source['path']).relative_to(self.root).as_posix()

    def asset(self, value):
        parsed = urlsplit(value)
        if parsed.scheme + '://' + parsed.netloc != ORIGIN:
            raise KitError('Reference origin mismatch')
        raw = unquote(parsed.path).lstrip('/')
        if '\\' in raw or '\x00' in raw or any(part in ('.git', '.echo-kit', '..') or part.startswith('.') for part in raw.split('/')):
            raise KitError('Reference asset is outside the permitted root')
        path = self.root / raw
        if (not path.resolve().is_relative_to(self.root.resolve()) or self.discovery.allowed(path)
                or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024):
            raise KitError('Reference asset is unavailable or outside shared boundaries')
        return path

    def read_asset(self, path):
        """Anchor every parent and verify the leaf before reading bounded bytes."""
        path = Path(os.path.abspath(path))
        try:
            relative = path.relative_to(self.root)
            if Discovery(self.ws).allowed(path):
                raise KitError('Reference asset ownership is unavailable')
            expected = storage_fs.identity(path.lstat())
            if expected['mode'] != stat.S_IFREG or expected['size'] > 64 * 1024 * 1024:
                raise KitError('Reference asset is not a bounded regular file')
            directories = {parent.relative_to(self.root).as_posix(): storage_fs.identity(parent.lstat())
                           for parent in path.parents if parent != self.root and parent.is_relative_to(self.root)}
            with storage_fs.parent_handle(self.root, relative, self.root_identity, directories) as (parent, target):
                fd = None
                try:
                    if os.name == 'nt':
                        # READ_DATA + READ_ATTRIBUTES, OPEN_REPARSE_POINT and read-only
                        # sharing reject a replaced symlink/junction and pin this leaf.
                        import ctypes
                        import msvcrt
                        kernel = storage_fs._windows_api()
                        handle = kernel.CreateFileW(str(target), 0x81, 1, None, 3,
                                                    0x00200000 | 0x02000000, None)
                        if handle == ctypes.c_void_p(-1).value:
                            raise ctypes.WinError(ctypes.get_last_error())
                        try:
                            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                        except BaseException:
                            kernel.CloseHandle(handle)
                            raise
                    else:
                        fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=parent)
                    if not storage_fs.same(os.fstat(fd), expected, content=True):
                        raise KitError('Reference asset changed before its body could be read')
                    with os.fdopen(fd, 'rb') as stream:
                        fd = None
                        body = stream.read(64 * 1024 * 1024 + 1)
                        if (len(body) > 64 * 1024 * 1024 or len(body) != expected['size']
                                or not storage_fs.same(os.fstat(stream.fileno()), expected, content=True)):
                            raise KitError('Reference asset changed while being read')
                        current = target.lstat() if os.name == 'nt' else os.stat(
                            target.name, dir_fd=parent, follow_symlinks=False)
                        if not storage_fs.same(current, expected, content=True):
                            raise KitError('Reference asset identity changed while being read')
                        return body
                finally:
                    if fd is not None:
                        os.close(fd)
        except (OSError, ValueError, storage_fs.UnsafeStorage):
            raise KitError('Reference asset is unavailable or changed during its read') from None

    def route(self, route):
        try:
            if route.request.method not in ('GET', 'HEAD'):
                raise KitError('Local reference assets are read-only')
            path = self.asset(route.request.url)
            body = self.read_asset(path)
            self.assets[path.relative_to(self.root).as_posix()] = hashlib.sha256(body).hexdigest()
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            route.fulfill(status=200, body=body if route.request.method == 'GET' else b'', content_type=mime)
        except (KitError, OSError, ValueError):
            self.failures += 1
            route.fulfill(status=404, body='Reference asset unavailable')

    def changed(self, recorded):
        for name, digest in recorded.items():
            try:
                if hashlib.sha256(self.read_asset(self.asset(ORIGIN + '/' + name))).hexdigest() != digest:
                    return True
            except (KitError, OSError, ValueError):
                return True
        return False


def inspect(page, selector, limit, timeout_ms):
    script = files('echo_kit').joinpath('resources/visual-inspect.js').read_text(encoding='utf-8')
    value = page.evaluate(script, {'selector': selector, 'limit': limit, 'timeoutMs': timeout_ms})
    if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 64 * 1024:
        raise KitError('Inspection exceeds 64 KiB; narrow the selector')
    if value['truncated']:
        value['next_step'] = 'Narrow the selector; unread elements have not been inspected'
    return value


@contextmanager
def reference_context(context, page, settings, prepare=None):
    visual = settings.get('visual', {})
    descriptor = visual.get('reference')
    if not descriptor:
        yield None, None
        return
    profile = inspect(page, 'body', 1, settings['timeout_ms'])['profile']
    own = context.browser.new_context(viewport=profile['viewport'], device_scale_factor=profile['device_scale_factor'],
                                      locale=profile['locale'], timezone_id=profile['timezone_id'],
                                      color_scheme=profile['color_scheme'], reduced_motion=profile['reduced_motion'],
                                      service_workers='block')
    tracing = False
    try:
        own.set_default_timeout(settings['timeout_ms'])
        own.set_default_navigation_timeout(settings['timeout_ms'])
        own.tracing.start(screenshots=True, snapshots=True, sources=False)
        tracing = True
        local = LocalReference(visual) if descriptor['kind'] == 'local' else None
        if local:
            own.route(ORIGIN + '/**', local.route)
        reference = own.new_page()
        reference._echo_reference_errors = 0
        def failed(*_):
            reference._echo_reference_errors += 1
        reference.on('requestfailed', failed)
        reference.on('response', lambda response: failed() if response.status >= 400 else None)
        response = reference.goto(local.url if local else descriptor['url'], wait_until='domcontentloaded')
        if response and response.status >= 400:
            raise KitError('Reference page returned an HTTP error')
        if prepare:
            prepare(reference, {key: value for key, value in settings.items() if key != 'auth'})
        yield reference, local
    finally:
        try:
            if tracing:
                own.tracing.stop(path=str(Path(settings['output']) / 'reference-trace.zip'))
        finally:
            own.close()


def differences(expected, actual):
    """Measurements are observations, not automatic semantic acceptance."""
    found = []
    for group in ('box', 'document_box', 'styles'):
        for key, value in expected.get(group, {}).items():
            other = actual.get(group, {}).get(key)
            if value != other:
                found.append({'property': group + '.' + key, 'reference': value, 'actual': other})
    for pseudo in ('::before', '::after'):
        if expected.get('pseudo', {}).get(pseudo) != actual.get('pseudo', {}).get(pseudo):
            found.append({'property': pseudo, 'reference': expected.get('pseudo', {}).get(pseudo),
                          'actual': actual.get('pseudo', {}).get(pseudo)})
    return found


def image_diff(expected, actual, output, threshold=0.1, timeout=30):
    # Lazy imports keep normal observation, service queries and tray discovery unchanged.
    from PIL import Image, ImageChops, ImageOps
    with Image.open(expected) as source_a, Image.open(actual) as source_b:
        a, b = source_a.convert('RGBA'), source_b.convert('RGBA')
    width, height = max(a.width, b.width), max(a.height, b.height)
    if width * height > MAX_PIXELS:
        raise KitError('Comparison region exceeds 2560×2560 pixels; select a smaller region')
    same_size = a.size == b.size
    def padded(source):
        canvas = Image.new('RGBA', (width, height), 'white')
        canvas.paste(source, (0, 0))
        return canvas
    a, b = padded(a), padded(b)
    panel = Image.new('RGBA', (width * 2, height), 'white')
    panel.paste(a, (0, 0)); panel.paste(b, (width, 0))
    pair = str(output) + '-pair.png'
    delta = str(output) + '-diff.png'
    panel.save(pair)
    diff = Image.blend(Image.new('RGBA', a.size, 'white'), ImageOps.grayscale(a).convert('RGBA'), .1)
    bounds = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox()
    count = 0
    if bounds:
        # Native bounding-box calculation avoids Python comparison across identical areas.
        left, top, right, bottom = bounds
        bounds = (max(0, left - 2), max(0, top - 2), min(width, right + 2), min(height, bottom + 2))
        region = Image.new('RGBA', (bounds[2] - bounds[0], bounds[3] - bounds[1]))
        # The pinned Playwright distribution already ships Node on every supported platform.
        # Use the same configured runtime, without a global Node/npm dependency or a Python port.
        from playwright._impl._driver import compute_driver_executable
        node, _ = compute_driver_executable()
        command = [node, str(files('echo_kit').joinpath('resources/pixelmatch/bridge.cjs'))]
        cropped_a, cropped_b = a.crop(bounds), b.crop(bounds)
        payload = struct.pack('<IId', cropped_a.width, cropped_a.height, threshold) + cropped_a.tobytes() + cropped_b.tobytes()
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        launches.checkpoint()
        try:
            result = subprocess.run(command, input=payload, capture_output=True, timeout=timeout, **options)
        except (OSError, subprocess.TimeoutExpired):
            raise KitError('Official pixelmatch could not run; check the pinned Playwright runtime') from None
        launches.checkpoint()
        if result.returncode or len(result.stdout) != 4 + cropped_a.width * cropped_a.height * 4:
            raise KitError('Official pixelmatch returned invalid comparison data')
        count = struct.unpack('<I', result.stdout[:4])[0]
        region = Image.frombytes('RGBA', cropped_a.size, result.stdout[4:])
        diff.paste(region, bounds[:2])
    diff.save(delta)
    return {'images': [pair, delta], 'different_pixels': count, 'compared_pixels': width * height,
            'different_pixel_ratio': count / (width * height), 'same_dimensions': same_size,
            'canvas': {'width': width, 'height': height, 'alignment': 'top-left; padding only; no scaling'},
            'parameters': {'engine': 'mapbox/pixelmatch', 'version': '7.1.0', 'threshold': threshold,
                           'include_antialiasing': False}}


class Visual:
    def __init__(self, observer, reference=None, local=None, ready_reference=None, prepare_reference=None):
        self.observer = observer
        self.page = observer.page
        self.reference = reference
        self.local = local
        self.ready_reference = ready_reference
        self.prepare_reference = prepare_reference
        self.settings = observer.settings
        self.baselines = {}
        self.deadline = None

    def remaining(self):
        if self.deadline is None:
            return self.settings['timeout_ms'] / 1000
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise KitError('Visual request exceeded its time budget; select fewer regions or views')
        return value

    def dispatch(self, request, oid):
        from .observation import clean
        self.deadline = time.monotonic() + self.settings['timeout_ms'] / 1000 * 4 + 5
        if request['op'] == 'inspect':
            page = self.reference if request.get('page', 'target') == 'reference' else self.page
            if page is None:
                raise KitError('No visual reference configured; use --reference')
            result = inspect(page, request.get('target') or 'body > *', request.get('limit', 20), self.settings['timeout_ms'])
            return {'inspection': clean(result, self.observer.secrets, 64 * 1024), 'images': []}
        if self.reference is None:
            raise KitError('A visual baseline requires --reference; Markdown alone provides requirements, not pixels')
        return self.capture_reference(request, oid) if request['op'] == 'reference' else self.compare(request, oid)

    def sample(self, page, selector, request, prefix, reference=False):
        launches.checkpoint()
        began = time.monotonic()
        callback = self.ready_reference if reference else self.observer.ready
        if callback:
            callback(page, request)
        timeout_ms = min(self.settings['timeout_ms'], self.remaining() * 1000)
        target = page.locator(selector) if selector else page
        if selector and target.count() != 1:
            raise KitError('Each comparison selector must match exactly one element')
        if selector:
            target.wait_for(state='visible', timeout=timeout_ms)
            target.scroll_into_view_if_needed(timeout=timeout_ms)
        measurement = inspect(page, selector or 'body', 1, timeout_ms)
        if not measurement['elements'] or (selector and not measurement['elements'][0]['visible']):
            raise KitError('Comparison region is not visible')
        masks = [page.locator(item['reference' if reference else 'target']) for item in request.get('exclude', [])]
        reasons = []
        for value in masks:
            if value.count() != 1 or not value.is_visible():
                raise KitError('Each excluded region must match one visible element')
        area = measurement['elements'][0]['box'] if selector else measurement['profile']['viewport']
        if area['width'] * area['height'] > MAX_PIXELS:
            raise KitError('Comparison region exceeds 2560×2560 pixels; narrow the region')
        options = {'scale': 'css', 'animations': request.get('animations', 'allow'), 'mask': masks,
                   'timeout': min(timeout_ms, self.remaining() * 1000)}
        first = target.screenshot(**options)
        stable = False
        for _ in range(3):
            launches.checkpoint()
            self.remaining()
            page.wait_for_timeout(80)
            second = target.screenshot(**options)
            if first == second:
                stable = True
                break
            first = second
        if not stable:
            reasons.append('continuously_changing_pixels')
        for key in ('failed_fonts', 'pending_fonts', 'failed_visible_images', 'pending_visible_images', 'failed_resources'):
            if measurement['diagnostics'][key]:
                reasons.append(key)
        failed_requests = (self.reference._echo_reference_errors or (self.local and self.local.failures)) if reference else self.observer.errors['failed_requests']
        if failed_requests:
            reasons.append('resource_request_failed')
        if self.local and reference and self.local.changed(self.local.assets):
            reasons.append('reference_source_changed_during_capture')
        for mask in masks:
            rectangle = mask.bounding_box()
            if rectangle and rectangle['width'] >= area['width'] and rectangle['height'] >= area['height']:
                reasons.append('excluded_region_covers_capture')
        path = self.observer.output / (prefix + '.png')
        path.write_bytes(second)
        return {'path': str(path), 'sha256': hashlib.sha256(second).hexdigest(),
                'measurement': measurement, 'unverified': reasons,
                'elapsed_ms': round((time.monotonic() - began) * 1000, 2)}

    def capture_reference(self, request, oid):
        if request.get('reload', False):
            self.reference._echo_reference_errors = 0
            if self.local:
                self.local.assets.clear()
                self.local.failures = 0
            response = self.reference.reload(wait_until='domcontentloaded')
            if response and response.status >= 400:
                raise KitError('Reference reload returned an HTTP error')
            if self.prepare_reference:
                self.prepare_reference(self.reference, {key: value for key, value in self.settings.items() if key != 'auth'})
        old = self.reference.viewport_size
        scroll = self.reference.evaluate('() => ({x:scrollX,y:scrollY})')
        captures = []
        try:
            regions = request.get('regions') or [{'id': 'viewport', 'reference': None, 'target': None}]
            for i, view in enumerate(request.get('viewports') or [self.page.viewport_size]):
                self.reference.set_viewport_size(view)
                for j, region in enumerate(regions):
                    sample = self.sample(self.reference, region['reference'], request, f'{oid}-reference-{i}-{j}', True)
                    captures.append({'viewport': view, 'region': region, **sample})
        finally:
            self.reference.set_viewport_size(old)
            self.reference.evaluate('p => scrollTo(p.x,p.y)', scroll)
        descriptor = self.settings['visual']['reference']
        spec = self.settings.get('visual', {}).get('spec')
        if spec:
            spec = configuration(Workspace(self.settings['visual']['workspace']), spec=spec['path'])['spec']
        baseline = {'baseline_id': oid, 'state': request.get('state', 'default'), 'captures': captures,
                    'time': time.time(), 'source': descriptor['source'], 'source_kind': descriptor['kind'],
                    'document_sha256': hashlib.sha256(self.reference.content().encode('utf-8')).hexdigest(),
                    'assets': dict(self.local.assets) if self.local else {},
                    'exclude': request.get('exclude', []), 'animations': request.get('animations', 'allow'),
                    'spec': spec}
        self.baselines[oid] = baseline
        save(self.observer.output / (oid + '-baseline.json'), baseline)
        return {'baseline_id': oid, 'state': baseline['state'], 'source': baseline['source'],
                'verification': 'unverified' if any(c['unverified'] for c in captures) else 'captured',
                'captures': captures, 'images': [c['path'] for c in captures], 'spec': baseline['spec']}

    def compare(self, request, oid):
        baseline = self.baselines.get(request['baseline_id'])
        if not baseline:
            raise KitError('Unknown baseline in this observation session; capture reference explicitly')
        for captured in baseline['captures']:
            path = Path(captured['path'])
            if (not path.resolve().is_relative_to(self.observer.output.resolve()) or not path.is_file()
                    or path.stat().st_size > 64 * 1024 * 1024
                    or hashlib.sha256(path.read_bytes()).hexdigest() != captured['sha256']):
                raise KitError('Reference screenshot is missing or changed; explicitly capture a new reference')
        if request.get('state', baseline['state']) != baseline['state']:
            raise KitError('State label differs from the baseline; prepare matching states or capture a new reference')
        if self.local and self.local.changed(baseline['assets']):
            raise KitError('Reference assets changed; explicitly capture a new reference')
        if baseline['spec']:
            current = configuration(Workspace(self.settings['visual']['workspace']), spec=baseline['spec']['path'])['spec']
            if current['sha256'] != baseline['spec']['sha256']:
                raise KitError('Requirements document changed; explicitly capture a new reference')
        old = self.page.viewport_size
        scroll = self.page.evaluate('() => ({x:scrollX,y:scrollY})')
        comparisons, images = [], []
        options = {**request, 'exclude': baseline['exclude'], 'animations': baseline['animations']}
        try:
            for i, source in enumerate(baseline['captures']):
                self.page.set_viewport_size(source['viewport'])
                actual = self.sample(self.page, source['region']['target'], options, f'{oid}-actual-{i}')
                reasons = sorted(set(source['unverified'] + actual['unverified']))
                expected_measurement, actual_measurement = source['measurement'], actual['measurement']
                if expected_measurement['profile'] != actual_measurement['profile']:
                    reasons.append('rendering_conditions_differ')
                metric = image_diff(source['path'], actual['path'], self.observer.output / f'{oid}-{i}',
                                    request.get('threshold', .1), min(30, self.remaining()))
                if not metric['same_dimensions']:
                    reasons.append('region_dimensions_differ')
                images.extend([actual['path'], *metric.pop('images')])
                comparisons.append({'region': source['region']['id'], 'viewport': source['viewport'],
                                    'reference': {'path': source['path'], 'sha256': source['sha256']}, 'actual': actual,
                                    'differences': differences(expected_measurement['elements'][0], actual_measurement['elements'][0]),
                                    'pixels': metric, 'unverified': sorted(set(reasons))})
        finally:
            self.page.set_viewport_size(old)
            self.page.evaluate('p => scrollTo(p.x,p.y)', scroll)
        return {'baseline_id': baseline['baseline_id'], 'state': baseline['state'], 'comparisons': comparisons,
                'images': images, 'verification': 'unverified' if any(c['unverified'] for c in comparisons) else 'compared',
                'meaning': 'Evidence captured; no overall visual or business acceptance verdict', 'spec': baseline['spec']}
