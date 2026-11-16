import io
import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from echo_kit import observation, observer_cli
from echo_kit.core import KitError, Workspace, init_workspace, read
from echo_kit.visual import LocalReference, Visual, configuration, image_diff


@pytest.fixture
def workspace(tmp_path):
    init_workspace(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / '原型 页面').mkdir()
    (tmp_path / '原型 页面/index.html').write_text('<main>Reference</main>', encoding='utf-8')
    (tmp_path / 'spec.md').write_text('# Requirements\nHeader is 62px high.\n', encoding='utf-8')
    return Workspace(tmp_path)


def test_sources_provenance_and_no_spec_body(workspace):
    value = configuration(workspace, '原型 页面/index.html', spec='spec.md')
    assert value['reference']['kind'] == 'local'
    assert value['spec']['lines'] == 2 and len(value['spec']['sha256']) == 64
    assert 'Header' not in json.dumps(value)
    assert configuration(workspace, spec='spec.md')['spec'] == value['spec']
    assert configuration(workspace, 'https://example.com/ref?token=test')['reference']['source'].endswith('?<redacted>')
    with pytest.raises(KitError):
        configuration(workspace, root='.')
    with pytest.raises(KitError):
        configuration(workspace, 'https://example.com/ref', root='.')


def test_private_ignored_and_outside_sources_are_not_read(workspace):
    private = workspace.state / 'private'
    private.mkdir(parents=True)
    (private / 'secret.md').write_text('private-body-never-output', encoding='utf-8')
    (workspace.root / '.gitignore').write_text('.echo-kit/\nignored.html\n', encoding='utf-8')
    (workspace.root / 'ignored.html').write_text('private-body-never-output', encoding='utf-8')
    for reference, spec in (('.echo-kit/private/secret.md', None), ('ignored.html', None),
                            (None, '.echo-kit/private/secret.md'), ('../outside.html', None)):
        with pytest.raises(KitError) as error:
            configuration(workspace, reference, spec=spec)
        assert 'private-body-never-output' not in str(error.value)
    (workspace.root / '.echo-kit/local.toml').write_text('[documents]\nprivate="spec.md"\n', encoding='utf-8')
    with pytest.raises(KitError):
        configuration(Workspace(workspace.root), spec='spec.md')


def test_local_route_only_serves_explicit_shared_assets(workspace):
    cfg = configuration(workspace, '原型 页面/index.html', '.')
    local = LocalReference(cfg)
    good = local.asset(local.url)
    assert good.name == 'index.html'
    for url in ('https://echo-kit-reference.invalid/.git/config',
                'https://echo-kit-reference.invalid/原型 页面/%2e%2e/spec.md',
                'https://echo-kit-reference.invalid/.echo-kit/local.toml',
                'https://other.invalid/原型 页面/index.html'):
        with pytest.raises(KitError):
            local.asset(url)
    response = {}
    local.route(SimpleNamespace(request=SimpleNamespace(method='GET', url=local.url),
                                fulfill=lambda **kw: response.update(kw)))
    assert response['status'] == 200
    before = dict(local.assets)
    assert not local.changed(before)
    good.write_text('Changed', encoding='utf-8')
    assert local.changed(before)


def test_local_source_link_cannot_leave_workspace(workspace, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside.html')
    outside.write_text('outside-body', encoding='utf-8')
    link = workspace.root / 'escape.html'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('File symlink unavailable on this Windows account')
    with pytest.raises(KitError):
        configuration(workspace, 'escape.html')


def test_asset_replacement_between_validation_and_open_is_rejected_before_read(workspace, monkeypatch):
    from echo_kit import storage_fs
    import os
    local = LocalReference(configuration(workspace, '原型 页面/index.html'))
    path = local.asset(local.url)
    original_parent_handle = storage_fs.parent_handle
    opened_streams = []
    original_fdopen = os.fdopen
    @contextmanager
    def replace_before_open(*args, **kwargs):
        path.rename(path.with_suffix('.before'))
        path.write_text('replacement-body-must-not-be-read', encoding='utf-8')
        with original_parent_handle(*args, **kwargs) as value:
            yield value
    def body_stream(*args, **kwargs):
        opened_streams.append(True)
        return original_fdopen(*args, **kwargs)
    monkeypatch.setattr(storage_fs, 'parent_handle', replace_before_open)
    monkeypatch.setattr(os, 'fdopen', body_stream)
    with pytest.raises(KitError, match='changed') as error:
        local.read_asset(path)
    assert not opened_streams
    assert 'replacement-body' not in str(error.value)


@pytest.mark.parametrize('payload', [
    {'op': 'inspect', 'limit': 21}, {'op': 'inspect', 'page': 'all'},
    {'op': 'reference', 'regions': [{}]}, {'op': 'reference', 'exclude': [{}]},
    {'op': 'compare'}, {'op': 'compare', 'baseline_id': 'b', 'threshold': float('nan')},
    {'op': 'compare', 'baseline_id': 'b', 'regions': []},
])
def test_invalid_visual_requests_fail_before_execution(payload):
    with pytest.raises(KitError):
        observation.validate({'id': 'x', **payload})


def test_image_difference_has_no_rescaling_or_overall_verdict(tmp_path):
    a, b = tmp_path / 'a.png', tmp_path / 'b.png'
    Image.new('RGB', (80, 60), 'white').save(a)
    Image.new('RGB', (80, 60), 'white').save(b)
    equal = image_diff(a, b, tmp_path / 'same')
    assert equal['different_pixels'] == 0 and equal['same_dimensions']
    changed = Image.open(b)
    for x in range(10, 30):
        for y in range(10, 30):
            changed.putpixel((x, y), (0, 0, 0))
    changed.save(b)
    drift = image_diff(a, b, tmp_path / 'drift')
    assert drift['different_pixels'] == 400 and drift['parameters']['threshold'] == .1
    assert 'status' not in drift and 'score' not in drift
    Image.new('RGB', (80, 65), 'black').save(b)
    unequal = image_diff(a, b, tmp_path / 'unequal')
    assert not unequal['same_dimensions'] and unequal['canvas']['height'] == 65
    assert Image.open(unequal['images'][0]).size == (160, 65)


def test_official_algorithm_is_pinned_and_licensed():
    from importlib.resources import files
    resources = files('echo_kit').joinpath('resources/pixelmatch')
    provenance = json.loads(resources.joinpath('provenance.json').read_text(encoding='utf-8'))
    assert provenance['name'] == 'mapbox/pixelmatch' and provenance['version'] == '7.1.0'
    assert hashlib.sha256(resources.joinpath('index.mjs').read_bytes()).hexdigest() == provenance['sha256']
    assert 'ISC License' in resources.joinpath('LICENSE').read_text(encoding='utf-8')


def test_normal_observation_does_not_load_comparison_engine():
    result = subprocess.run([sys.executable, '-c',
        "import sys; from echo_kit import observation; assert 'echo_kit.visual' not in sys.modules; "
        "assert 'PIL' not in sys.modules"], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_old_worker_does_not_receive_new_operations(tmp_path, monkeypatch):
    requests = io.StringIO('{"id":"i","op":"inspect"}\n{"id":"c","op":"close"}\n')
    monkeypatch.setattr(sys, 'stdin', requests)
    monkeypatch.setattr(sys, 'stdout', io.StringIO())
    class CapturedInput(io.StringIO):
        def close(self):
            self.sent = self.getvalue()
            super().close()
    pipe = CapturedInput()
    child = SimpleNamespace(poll=lambda: None, stdin=pipe, wait=lambda **_: 0)
    replies = __import__('queue').Queue()
    replies.put(json.dumps({'protocol': 1, 'status': 'closed', 'id': 'c'}))
    observer_cli.relay(child, replies, tmp_path, 1)
    assert 'inspect' not in pipe.sent and json.loads(pipe.sent)['op'] == 'close'
    assert read(tmp_path / 'latest.json')['status'] == 'closed'


def test_baseline_is_session_scoped_and_never_implicitly_replaced(tmp_path):
    page = SimpleNamespace()
    observer = SimpleNamespace(page=page, settings={'output': str(tmp_path), 'timeout_ms': 10000}, secrets=[])
    visual = Visual(observer)
    with pytest.raises(KitError, match='--reference'):
        visual.dispatch({'op': 'compare', 'baseline_id': 'unknown'}, 'new')
    visual.reference = object()
    with pytest.raises(KitError, match='Unknown baseline'):
        visual.compare({'baseline_id': 'unknown'}, 'new')
    visual.baselines['fixed'] = {'state': 'dialog', 'captures': []}
    with pytest.raises(KitError, match='State label'):
        visual.compare({'baseline_id': 'fixed', 'state': 'closed'}, 'new')
    assert list(visual.baselines) == ['fixed']


def test_changed_baseline_image_is_not_used_as_evidence(tmp_path):
    file = tmp_path / 'reference.png'
    file.write_bytes(b'original controlled screenshot')
    observer = SimpleNamespace(page=SimpleNamespace(), settings={'timeout_ms': 10000}, secrets=[], output=tmp_path)
    visual = Visual(observer)
    visual.baselines['b'] = {'captures': [{'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}]}
    file.write_bytes(b'replaced screenshot')
    with pytest.raises(KitError, match='screenshot is missing or changed'):
        visual.compare({'baseline_id': 'b'}, 'next')
