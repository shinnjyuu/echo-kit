import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.request import Request

import pytest
from PIL import Image, ImageChops

from echo_kit.core import KitError, Workspace, init_workspace
from echo_kit.native_visual import NativeVisual, ReferenceServer
from echo_kit.visual import configuration


@pytest.fixture
def ws(tmp_path):
    init_workspace(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '原型 页面').mkdir()
    (tmp_path / '原型 页面/index.html').write_text('<h1>Controlled reference</h1>', encoding='utf-8')
    (tmp_path / 'spec.md').write_text('# Dashboard\nHeader is 62px high.\n', encoding='utf-8')
    (tmp_path / 'style.css').write_text('h1{height:62px}', encoding='utf-8')
    return Workspace(tmp_path)


class Page:
    """Controlled facade: only the native protocol boundary is simulated."""
    def __init__(self, height=62, color='white'):
        self.height, self.color = height, color
        self.viewport_size = {'width': 240, 'height': 240}
        self.masked = False
        self.closed = False
        self.fail_capture = False
        self.draft = 'unsaved controlled draft'
        self.commands = []

    def set_viewport(self, value):
        self.viewport_size = dict(value)

    def check_page(self, selector, deadline):
        return {'status': 'ready' if selector else 'not_checked', 'matches': 1 if selector else None}

    def page_errors(self, result):
        result.update(page_errors=0, console_errors=0)

    def evaluate(self, script):
        if 'echo-kit-visual-inspect' in script:
            options = json.loads(script.rsplit(')(', 1)[1][:-1])
            if options['selector'] == '#missing':
                return {'matched': 0, 'elements': []}
            box = {'x': 0, 'y': 0, 'width': 20, 'height': self.height}
            return {'matched': 1, 'elements': [{'visible': True, 'box': box, 'styles': {'height': str(self.height) + 'px'}}],
                    'profile': {'viewport': dict(self.viewport_size), 'device_scale_factor': 1}, 'diagnostics': {}}
        if 'echo-kit-visual-mask' in script:
            options = json.loads(script.rsplit('})(', 1)[1][:-1])
            self.masked = bool(options['selectors'])
            return [{'x': 0, 'y': 0, 'width': 1, 'height': 1}] if self.masked else []
        if 'echo-kit-visual-restore' in script:
            self.masked = False
            return True
        if 'echo-kit-visual-scroll' in script:
            return {'x': 0, 'y': 0}
        return None

    def screenshot(self, path, selector=None):
        if self.fail_capture:
            raise KitError('Controlled screenshot failure')
        size = (20, self.height) if selector else tuple(self.viewport_size.values())
        Image.new('RGB', size, self.color).save(path)
        return {'path': str(path)}

    def command(self, command):
        self.commands.append(command)
        assert command[:2] == ['diff', 'screenshot']
        baseline = Path(command[command.index('--baseline') + 1])
        output = Path(command[command.index('--output') + 1])
        with Image.open(baseline) as source:
            image = Image.new('RGB', (20, self.height) if '--selector' in command else tuple(self.viewport_size.values()), self.color)
            if source.size != image.size:
                return {'success': True, 'data': {
                    'dimensionMismatch': {'expected': {'width': source.width, 'height': source.height},
                                          'actual': {'width': image.width, 'height': image.height}},
                    'differentPixels': source.width * source.height, 'diffPath': str(output)}}
            difference = ImageChops.difference(source.convert('RGB'), image)
            rgb = difference.tobytes()
            count = sum(rgb[i:i+3] != bytes(3) for i in range(0, len(rgb), 3))
            if count:
                difference.save(output)
            return {'success': True, 'data': {'dimensionMismatch': False, 'totalPixels': image.width * image.height,
                    'differentPixels': count, 'mismatchPercentage': count / (image.width * image.height) * 100,
                    'diffPath': str(output), 'match': count == 0}}


def visual(ws, tmp_path, target=None):
    created = []
    @contextmanager
    def factory(url):
        assert url.startswith('http://127.0.0.1:')
        with urlopen(url) as response:
            assert response.read().startswith(b'<h1>')
        page = Page()
        created.append(page)
        try:
            yield page
        finally:
            page.closed = True
    output = ws.state / 'observations/controlled'
    return NativeVisual(ws, output, target or Page(), factory, reference='原型 页面/index.html', spec='spec.md'), created


def test_read_only_temporary_reference_server_rejects_private_paths(ws):
    private = ws.state / 'private'
    private.mkdir(parents=True)
    (private / 'secret.txt').write_text('private-body', encoding='utf-8')
    settings = configuration(ws, '原型 页面/index.html', '.')
    with ReferenceServer(settings) as server:
        assert urlopen(server.url).read() == b'<h1>Controlled reference</h1>'
        origin = 'http://' + urlsplit(server.url).netloc
        stylesheet = Request(origin + '/style.css', headers={'Cookie': 'echo-reference=' + server.token})
        assert urlopen(stylesheet).read() == b'h1{height:62px}'
        with pytest.raises(HTTPError):
            urlopen(origin + '/style.css')
        for path in ('/.git/config', '/no-capability/spec.md', '/' + server.token + '/%2e%2e/spec.md',
                     '/' + server.token + '/.echo-kit/private/secret.txt'):
            with pytest.raises(HTTPError) as error:
                urlopen(origin + path)
            assert error.value.code == 404
        port = server.server.server_port
    import socket
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0


def test_reference_closes_after_capture_and_comparison_preserves_target(ws, tmp_path):
    target = Page(color='black')
    engine, created = visual(ws, tmp_path, target)
    baseline = engine.dispatch({'op': 'reference', 'id': 'r', 'regions': [
        {'id': 'header', 'reference': '#reference-header', 'target': '#actual-header'}]}, 'reference1')
    assert baseline['verification'] == 'captured' and created[0].closed
    assert 'Header is' not in json.dumps(baseline)
    assert '[reference]' not in str(baseline.get('source'))
    result = engine.dispatch({'op': 'compare', 'id': 'c', 'baseline_id': baseline['baseline_id']}, 'compare1')
    assert result['verification'] == 'compared'
    assert result['comparisons'][0]['pixels']['differentPixels'] == 20 * 62
    assert all(Path(path).name.startswith('compare1-') for path in result['images'])
    assert target.draft == 'unsaved controlled draft' and not target.closed
    assert len(created) == 1
    assert 'match' not in result['comparisons'][0]['pixels']
    assert 'score' not in result and 'pass' not in result


def test_dimension_difference_is_reported_without_rescaling(ws, tmp_path):
    engine, _ = visual(ws, tmp_path, Page(height=72))
    baseline = engine.dispatch({'op': 'reference', 'id': 'r', 'regions': [
        {'id': 'header', 'reference': '#reference-header', 'target': '#actual-header'}]}, 'ref')
    result = engine.dispatch({'op': 'compare', 'id': 'c', 'baseline_id': baseline['baseline_id']}, 'cmp')
    item = result['comparisons'][0]
    assert item['unverified'] == ['region_dimensions_differ']
    assert {'property': 'box.height', 'reference': 62, 'actual': 72} in item['differences']
    assert result['verification'] == 'unverified'
    assert item['pixels']['differentPixels'] is None
    assert len(result['images']) == 2
    assert Image.open(result['images'][1]).size == (40, 72)


def test_identical_diff_response_may_echo_a_nonexistent_diff_image(ws, tmp_path):
    engine, _ = visual(ws, tmp_path)
    baseline = engine.dispatch({'op': 'reference', 'id': 'r'}, 'ref')
    result = engine.dispatch({'op': 'compare', 'id': 'c', 'baseline_id': baseline['baseline_id']}, 'cmp')
    assert result['verification'] == 'compared'
    assert result['comparisons'][0]['pixels']['differentPixels'] == 0
    assert len(result['images']) == 2
    assert not (engine.output / 'cmp-actual-0-diff.png').exists()


def test_observe_multiple_views_restores_current_page_without_reference(ws, tmp_path):
    engine, created = visual(ws, tmp_path)
    original = dict(engine.target.viewport_size)
    result = engine.observe({'op': 'observe', 'id': 'o', 'target': '#actual-header', 'capture': 'element',
                             'viewports': [{'width': 390, 'height': 844}, {'width': 1440, 'height': 900}]}, 'observe1')
    assert len(result['images']) == 2 and len(result['captures']) == 2
    assert result['verification'] == 'captured'
    assert result['captures'][0]['viewport'] == {'width': 390, 'height': 844}
    assert all(Path(item['path']).name.startswith('observe1-') for item in result['captures'])
    assert engine.target.viewport_size == original
    assert engine.target.draft == 'unsaved controlled draft' and not created
    assert set(result['captures'][0]['timings_ms']) == {'ready', 'screenshot', 'file_read'}


def test_observe_keeps_uniform_viewport_as_unverified_evidence(ws, tmp_path):
    engine, _ = visual(ws, tmp_path)
    result = engine.observe({'op': 'observe', 'id': 'o'}, 'uniform-view')
    assert result['verification'] == 'unverified'
    assert result['captures'][0]['unverified'] == ['uniform_viewport']
    assert result['captures'][0]['page_check']['status'] == 'not_checked'
    image, = result['images']
    assert Path(image).is_file()


def test_observe_failure_restores_viewport_and_does_not_return_partial_images(ws, tmp_path):
    engine, _ = visual(ws, tmp_path)
    original = dict(engine.target.viewport_size)
    engine.target.fail_capture = True
    with pytest.raises(KitError, match='Controlled screenshot'):
        engine.observe({'op': 'observe', 'id': 'o', 'viewports': [{'width': 390, 'height': 844}]}, 'observe1')
    assert engine.target.viewport_size == original


def test_temporary_exclusions_restore_after_capture_failure(ws, tmp_path):
    target = Page()
    engine, _ = visual(ws, tmp_path, target)
    baseline = engine.dispatch({'op': 'reference', 'id': 'r', 'exclude': [
        {'reference': '#ref-clock', 'target': '#clock'}]}, 'ref')
    target.fail_capture = True
    with pytest.raises(KitError, match='Controlled screenshot'):
        engine.dispatch({'op': 'compare', 'id': 'c', 'baseline_id': baseline['baseline_id']}, 'cmp')
    assert not target.masked


def test_reference_and_spec_changes_require_new_baseline(ws, tmp_path):
    engine, _ = visual(ws, tmp_path)
    before = engine.dispatch({'op': 'reference', 'id': 'r'}, 'old')
    (ws.root / 'spec.md').write_text('# Changed selected requirement', encoding='utf-8')
    with pytest.raises(KitError, match='Requirements changed'):
        engine.dispatch({'op': 'compare', 'id': 'c', 'baseline_id': 'old'}, 'wrong')
    new = engine.dispatch({'op': 'reference', 'id': 'r2'}, 'new')
    assert new['spec']['sha256'] != before['spec']['sha256']
    (ws.root / '原型 页面/index.html').write_text('<h1>Changed reference</h1>', encoding='utf-8')
    with pytest.raises(KitError, match='Reference assets changed'):
        engine.dispatch({'op': 'compare', 'id': 'c2', 'baseline_id': 'new'}, 'wrong2')


def test_keep_reference_is_explicit_and_close_ends_it(ws, tmp_path):
    engine, created = visual(ws, tmp_path)
    engine.dispatch({'op': 'inspect', 'id': 'i', 'page': 'reference', 'keep_reference': True}, 'inspect1')
    assert not created[0].closed
    engine.dispatch({'op': 'reference', 'id': 'r'}, 'ref')
    assert len(created) == 1
    engine.close()
    assert created[0].closed


def test_markdown_alone_is_not_a_rendered_baseline(ws, tmp_path):
    engine = NativeVisual(ws, tmp_path, Page(), None, spec='spec.md')
    assert engine.dispatch({'op': 'inspect', 'id': 'i'}, 'inspect')['inspection']['matched'] == 1
    with pytest.raises(KitError, match='Markdown alone'):
        engine.dispatch({'op': 'reference', 'id': 'r'}, 'ref')
