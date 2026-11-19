"""Bounded, read-only checks of the page already owned by an acceptance session."""

import time
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import launches
from .core import KitError, save


# Only return counts and status codes. Page text, raw console messages and request
# contents can carry authentication or application data.
PAGE_SAMPLE = '''selector => {
  let matches = null, visible = null, invalid = false;
  if (selector !== null) {
    try {
      const nodes = document.querySelectorAll(selector);
      matches = nodes.length;
      if (matches === 1) {
        const node = nodes[0];
        const box = node.getBoundingClientRect();
        visible = box.width > 0 && box.height > 0 && node.checkVisibility({
          opacityProperty: true, visibilityProperty: true
        });
      }
    } catch (error) {
      if (!(error instanceof DOMException) || error.name !== 'SyntaxError') throw error;
      invalid = true;
    }
  }
  const navigation = performance.getEntriesByType('navigation')[0];
  const statuses = 'responseStatus' in PerformanceResourceTiming.prototype
    ? performance.getEntriesByType('resource').filter(
        entry => entry.initiatorType === 'script' && entry.responseStatus >= 400
      ).map(entry => entry.responseStatus)
    : null;
  return {
    matches, visible, invalid,
    url: location.protocol === 'http:' || location.protocol === 'https:'
      ? (location.origin + location.pathname).slice(0, 2048)
      : location.href === 'about:blank' ? 'about:blank' : null,
    document_status: navigation?.responseStatus || null,
    script_http_errors: statuses?.length ?? null,
    script_http_statuses: statuses === null ? null : [...new Set(statuses)].slice(0, 8)
  };
}'''

REASONS = {
    'target_missing': 'The expected page element did not appear',
    'target_hidden': 'The expected page element stayed hidden',
    'target_ambiguous': 'The expected selector matched more than one element',
    'invalid_selector': 'The readiness selector is not valid CSS',
    'page_probe_unavailable': 'The browser could not report page readiness',
}


def validate_selector(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or '\x00' in value:
        raise KitError('Use a nonempty CSS readiness selector of at most 512 characters')
    return value


def public_address(value):
    if not isinstance(value, str):
        return None
    if value == 'about:blank':
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ('http', 'https') and parsed.hostname:
            return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))[:2048]
    except ValueError:
        pass
    return None


def count(value):
    return value if type(value) is int and value >= 0 else None


def http_status(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def page_check(probe, selector, deadline):
    """Wait on the current document; polling only reads its state."""
    if selector is not None:
        validate_selector(selector)
    sample = {}
    status, reason = 'unverified', 'page_probe_unavailable'
    while (remaining := deadline - time.monotonic()) > 0:
        launches.checkpoint()
        value = probe(selector, remaining)
        if not isinstance(value, dict):
            break
        sample = value
        matches = count(sample.get('matches'))
        if selector is None:
            status, reason = 'not_checked', None
            break
        if sample.get('invalid') is True:
            status, reason = 'blocked', 'invalid_selector'
            break
        if matches == 1 and sample.get('visible') is True:
            status, reason = 'ready', None
            break
        if matches is None:
            break
        status = 'blocked'
        reason = 'target_missing' if matches == 0 else 'target_hidden' if matches == 1 else 'target_ambiguous'
        time.sleep(max(0, min(.05, deadline - time.monotonic())))
    if selector is not None and status != 'ready':
        status = 'blocked'
    statuses = sample.get('script_http_statuses')
    return {
        'status': status, 'reason': reason, 'url': public_address(sample.get('url')),
        'matches': count(sample.get('matches')),
        'visible': sample['visible'] if type(sample.get('visible')) is bool else None,
        'document_status': http_status(sample.get('document_status')),
        'script_http_errors': count(sample.get('script_http_errors')),
        'script_http_statuses': [x for x in statuses[:8] if http_status(x) and x >= 400]
        if isinstance(statuses, list) else None,
        'script_request_errors': None, 'page_errors': None, 'console_errors': None,
        'error_scope': None,
    }


class PageNotReady(KitError):
    def __init__(self, result, diagnostic_images=()):
        super().__init__(REASONS[result['reason']])
        self.details = {'page_check': result, 'diagnostic_images': list(diagnostic_images),
                        'verification': 'unverified'}


def viewport_issue(png):
    """Recognize a single-color viewport without treating pixels as page readiness."""
    from PIL import Image
    try:
        with Image.open(BytesIO(png), formats=['PNG']) as image:
            if image.width * image.height > 8_000_000:
                return 'viewport_check_unavailable'
            if all(low == high for low, high in image.convert('RGB').getextrema()):
                return 'uniform_viewport'
    except (OSError, ValueError, Image.DecompressionBombError):
        return 'viewport_check_unavailable'
    return None


class PageReadiness:
    """Collect safe Playwright diagnostics while the adapter uses its own page."""

    def __init__(self, page):
        self.page = page
        self.errors = {'page_errors': 0, 'console_errors': 0, 'failed_requests': 0}
        self.script_request_errors = 0
        self.script_http_errors = 0
        self.script_http_statuses = set()
        self.listeners = {
            'pageerror': lambda _: self._increment('page_errors'),
            'console': self._console,
            'requestfailed': self._request_failed,
            'response': self._response,
        }

    def __enter__(self):
        for event, handler in self.listeners.items():
            self.page.on(event, handler)
        return self

    def __exit__(self, *_):
        for event, handler in self.listeners.items():
            self.page.remove_listener(event, handler)

    def _increment(self, name):
        self.errors[name] += 1

    def _console(self, message):
        if message.type == 'error':
            self._increment('console_errors')

    def _request_failed(self, request):
        self._increment('failed_requests')
        if request.resource_type == 'script':
            self.script_request_errors += 1

    def _response(self, response):
        if response.status >= 400:
            self._increment('failed_requests')
            if response.request.resource_type == 'script':
                self.script_http_errors += 1
                self.script_http_statuses.add(response.status)

    def _probe(self, selector, budget):
        from playwright.sync_api import Error
        try:
            handle = self.page.wait_for_function(PAGE_SAMPLE, arg=selector, timeout=max(1, budget * 1000))
            try:
                return handle.json_value()
            finally:
                handle.dispose()
        except Error:
            return None

    def inspect(self, selector, deadline):
        result = page_check(self._probe, selector, deadline)
        observed = result['script_http_errors'] or 0
        statuses = set(result['script_http_statuses'] or []) | self.script_http_statuses
        result.update(page_errors=self.errors['page_errors'], console_errors=self.errors['console_errors'],
                      script_http_errors=max(observed, self.script_http_errors),
                      script_http_statuses=sorted(statuses)[:8], script_request_errors=self.script_request_errors,
                      error_scope='page_since_attach')
        result['url'] = result['url'] or public_address(self.page.url)
        return result

    def failure_image(self, path):
        from playwright.sync_api import Error
        try:
            self.page.screenshot(path=str(path), scale='css', timeout=2500)
        except (Error, OSError):
            return []
        return [str(path)] if path.is_file() else []

    def check(self, selector, *, timeout_ms=10000, navigation=None, output=None):
        """Return one adapter check and optional paths relative to its output folder."""
        validate_selector(selector)
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000:
            raise KitError('Page readiness timeout must be 1..120000 milliseconds')
        result = self.inspect(selector, time.monotonic() + timeout_ms / 1000)
        if navigation is not None and result['document_status'] is None:
            result['document_status'] = http_status(navigation.status)
        passed = result['status'] == 'ready'
        reason = 'Expected page element is visible' if passed else REASONS[result['reason']]
        artifacts = []
        if output is not None and not passed:
            directory = Path(output)
            stem = 'page-readiness-' + uuid.uuid4().hex
            image = directory / (stem + '.png')
            artifacts.extend(Path(p).name for p in self.failure_image(image))
            save(directory / (stem + '.json'), result)
            artifacts.append(stem + '.json')
        return {'check': {'name': 'page_ready', 'status': 'passed' if passed else 'blocked',
                          'detail': reason}, 'page_check': result, 'artifacts': artifacts}
