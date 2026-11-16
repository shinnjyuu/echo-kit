import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from echo_kit import cli, storage, storage_fs, storage_metadata, observation, catalogs, workspace_index
from echo_kit.core import KitError, Workspace, init_workspace, new_run, read, save
from echo_kit.operations import Journal, ResourceBusy, operation
from echo_kit.storage_config import roots, canonical


def workspace(path, config=''):
    init_workspace(path)
    with (path / 'echo-kit.toml').open('a', encoding='utf-8') as file:
        file.write('\n' + config)
    return Workspace(path)


ZERO = '''[storage.runs]
passed_days=0
other_days=0
keep_latest=0
[storage.observations]
keep_days=0
keep_latest=0
'''
CACHE = '''[storage.caches.archives]
path="artifacts/releases"
keep_days=0
keep_latest=0
include=["*.tar", "build-*"]
'''


def cache(ws, name='old.tar', data=b'archive'):
    path = ws.root / 'artifacts/releases' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (1, 1))
    return path


def run(ws, status='passed', age=45, variant='baseline'):
    path, record = new_run(ws, 'demo', 'lab')
    with operation(ws, 'lab.run', {}, run_id=path.name, evidence=path) as oid:
        record.update(status=status, variant=variant, finished=time.time() - age * 86400, operation_id=oid)
        save(path / 'run.json', record)
        (path / 'trace.zip').write_bytes(b'not read by inventory')
        os.utime(path / 'trace.zip', (1, 1))
        storage_metadata.own(ws, path, 'runs', oid, record)
    return path, record


def test_preview_partial_selection_keep_and_single_use(tmp_path):
    ws = workspace(tmp_path, CACHE)
    a, b = cache(ws), cache(ws, 'second.tar', b'second')
    bill = storage.disk(ws, limit=1)
    assert bill['total'] == 2 and len(bill['items']) == 1
    assert bill['summary']['logical_bytes'] == 13
    plan = storage.preview(ws)
    units = {u['name']: u['id'] for u in plan['items']}
    assert a.exists() and b.exists()
    storage.keep(units['old.tar'])
    result = storage.apply(plan['plan_id'], [units['old.tar']])
    assert result['items'][0]['status'] == 'skipped' and a.exists() and b.exists()
    storage.keep(units['old.tar'], True)
    plan2 = storage.preview(ws)
    result = storage.apply(plan2['plan_id'], [units['old.tar']])
    assert result['status'] == 'passed', result
    assert result['summary']['deleted_logical_bytes'] == 7
    assert not a.exists() and b.exists() and a.parent.is_dir()
    with pytest.raises(KitError, match='already been attempted'):
        storage.apply(plan2['plan_id'])


def test_run_conclusion_and_observation_receipt_survive_reclamation(tmp_path):
    ws = workspace(tmp_path, ZERO)
    path, record = run(ws, 'failed')
    plan = storage.preview(ws)
    assert plan['summary']['candidate_units'] == 1, plan
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'passed', result
    assert read(path / 'run.json') == record
    assert not (path / 'trace.zip').exists()
    assert storage_metadata.state(path)['state'] == 'reclaimed'
    assert catalogs.runs(ws)['items'][0]['reclamation']['state'] == 'reclaimed'
    from echo_kit.runs import report
    assert 'Attachments reclaimed' in Path(report(path)).read_text(encoding='utf-8')
    output = ws.state / 'observations' / ('a' * 32)
    output.mkdir(parents=True)
    image = output / 'shot.png'
    image.write_bytes(b'image')
    save(output / 'session.json', {'operation_id': 'a' * 32, 'status': 'closed'})
    storage_metadata.own(ws, output, 'observations', 'a' * 32, {'status': 'closed', 'finished': 1})
    os.utime(image, (1, 1))
    save(output / 'latest.json', {'id': 'v1', 'status': 'ok', 'images': [str(image)]})
    assert observation.read_reply(output / 'latest.json', 'v1')['status'] == 'ok'
    assert storage.apply(storage.preview(ws)['plan_id'])['status'] == 'passed'
    with pytest.raises(KitError, match='reclaimed'):
        observation.read_reply(output / 'latest.json', 'v1')


def test_retention_and_active_business_state(tmp_path):
    ws = workspace(tmp_path)
    completed = [run(ws, age=45 + i)[0] for i in range(5)]
    run(ws, status='failed', variant='other')
    result = storage.disk(ws)
    assert result['summary']['candidate_units'] == 2
    save(completed[-1] / 'active.json', {'remote_id': 'pending'})
    item = next(u for u in storage.disk(ws)['items'] if u['path'] == str(completed[-1]))
    assert 'business_cleanup_unconfirmed' in item['reasons'] and not item['eligible']


@pytest.mark.parametrize('change', ['file', 'configuration', 'parent', 'keep'])
def test_changed_preview_cannot_delete(tmp_path, change):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws)
    plan = storage.preview(ws)
    if change == 'file':
        file.write_text('updated data')
    elif change == 'configuration':
        with (ws.root / 'echo-kit.toml').open('a') as stream:
            stream.write('\n[storage.runs]\nkeep_latest=8\n')
    elif change == 'parent':
        file.parent.rename(file.parent.with_name('moved'))
        file.parent.mkdir()
        file.write_text('new directory')
    else:
        storage.keep(plan['items'][0]['id'])
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'unverified', result
    assert file.exists()


def test_live_writer_and_cleanup_exclude_each_other(tmp_path):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws)
    plan = storage.preview(ws)
    entered, finish = Event(), Event()
    def writer():
        with operation(ws, 'services.up', {}):
            entered.set()
            assert finish.wait(10)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(writer)
        assert entered.wait(5)
        try:
            assert not storage.disk(ws)['items'][0]['eligible']
            assert storage.apply(plan['plan_id'])['items'][0]['status'] == 'skipped'
            assert file.exists()
        finally:
            finish.set()
        future.result()
    # Opposite direction: admitted cleanup blocks a new writer before work.
    key = 'storage.root:' + canonical(file.parent)
    oid = Journal().acquire(ws, 'resources.clean', {key: 'exclusive'})
    try:
        with pytest.raises(ResourceBusy):
            with operation(ws, 'verify.run', {}):
                pytest.fail('writer entered during cleanup')
    finally:
        Journal().release(oid)


def test_tracked_hardlink_nested_git_and_scan_limit(tmp_path):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'add', 'artifacts'], check=True)
    assert 'protected_or_tracked_file' in storage.disk(ws)['items'][0]['reasons']
    other = cache(ws, 'hard.tar')
    os.link(other, other.with_suffix('.alias'))
    item = next(u for u in storage.disk(ws)['items'] if u['name'] == 'hard.tar')
    assert 'multiply_linked_file' in item['reasons']
    directory = file.parent / 'build-large'
    directory.mkdir()
    for i in range(40):
        (directory / str(i)).write_bytes(b'x')
    limited = storage.preview(ws, max_entries=10)
    assert limited['status'] == 'unverified' and limited['summary']['candidate_units'] == 0


def test_global_private_cache_and_environment(tmp_path):
    a = workspace(tmp_path / 'a')
    b = workspace(tmp_path / 'b', '[environments.dev]\n')
    outside = tmp_path / 'private cache'
    save(b.state / 'not-a-cache.json', {})
    (b.state / 'local.toml').write_text('[environments.dev.storage.caches.archives]\npath=' + json.dumps(str(outside)) + '\nkeep_days=0\n', encoding='utf-8')
    b = Workspace(b.root, 'dev')
    outside.mkdir()
    (outside / 'archive.tar').write_bytes(b'archive')
    os.utime(outside / 'archive.tar', (1, 1))
    workspace_index.remember(a)
    workspace_index.remember(b)
    result = storage.disk(all_workspaces=True)
    assert result['summary']['candidate_units'] == 1, result
    assert any(u['path'] == str(outside / 'archive.tar') for u in result['items'])


def test_cli_json_preview_and_apply_without_workspace(tmp_path, capsys, monkeypatch):
    ws = workspace(tmp_path, CACHE)
    cache(ws)
    assert cli.main(['--offline', '--json', '--workspace', str(ws.root), 'resources', 'clean']) == 0
    plan = json.loads(capsys.readouterr().out)
    monkeypatch.chdir(tmp_path.parent)
    assert cli.main(['--offline', '--json', 'resources', 'clean', '--apply', plan['plan_id']]) == 0
    assert json.loads(capsys.readouterr().out)['summary']['deleted_files'] == 1


@pytest.mark.parametrize('path', ['.', '.echo-kit', '.echo-kit/private', '../', '.GIT/objects', 'C:/outside' if os.name == 'nt' else '/outside'])
def test_unsafe_shared_cache_roots_rejected(tmp_path, path):
    with pytest.raises(KitError):
        workspace(tmp_path, '[storage.caches.bad]\npath=' + json.dumps(path))


def test_tampered_plan_never_opens_arbitrary_delete_target(tmp_path):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws)
    plan = storage.preview(ws)
    path = storage.home() / 'plans' / (plan['plan_id'] + '.json')
    raw = read(path)
    raw['plan']['units'][0]['path'] = str(tmp_path.parent)
    save(path, raw)
    with pytest.raises(KitError, match='Invalid or changed'):
        storage.apply(plan['plan_id'])
    assert file.exists()


def test_partial_failure_and_interrupt_retain_evidence_and_receipt(tmp_path, monkeypatch):
    ws = workspace(tmp_path, ZERO)
    path, record = run(ws)
    (path / 'extra.log').write_bytes(b'other')
    os.utime(path / 'extra.log', (1, 1))
    from echo_kit.runs import report
    report(path)
    plan = storage.preview(ws)
    original = storage.remove
    def denied(root, entry, *rest):
        if entry['path'].endswith('trace.zip'):
            raise PermissionError('controlled permission failure')
        return original(root, entry, *rest)
    monkeypatch.setattr(storage, 'remove', denied)
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'unverified' and result['summary']['deleted_files'] == 1
    assert read(path / 'run.json') == record
    assert (path / 'trace.zip').exists() and not (path / 'extra.log').exists()
    assert 'Some attachments are missing' in (path / 'report.html').read_text(encoding='utf-8')
    assert storage_metadata.state(path)['state'] == 'partial'
    assert read(Path(result['receipt']))['summary'] == result['summary']
    monkeypatch.setattr(storage, 'remove', original)
    remaining = storage.preview(ws)
    def interrupt(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(storage, 'remove', interrupt)
    result = storage.apply(remaining['plan_id'])
    assert result['status'] == 'interrupted' and (path / 'trace.zip').exists()
    assert storage_metadata.state(path)['state'] == 'partial'
    monkeypatch.setattr(storage, 'remove', original)
    assert storage.apply(storage.preview(ws)['plan_id'])['status'] == 'passed'


def test_root_only_cache_keeps_pending_and_service_used_paths(tmp_path):
    from echo_kit.processes import fingerprint
    ws = workspace(tmp_path, CACHE + ZERO)
    file = cache(ws)
    path, record = run(ws)
    save(path / 'active.json', {'state': 'business task pending'})
    rid = next(r['id'] for r in roots(ws) if r['kind'] == 'cache')
    result = storage.disk(ws, root_ids=[rid])
    assert 'business_cleanup_unconfirmed' in result['items'][0]['reasons']
    (path / 'active.json').unlink()
    save(ws.state / 'services-local.json', {'api': {'identity': fingerprint(os.getpid()), 'log': str(file)}})
    result = storage.disk(ws, root_ids=[rid])
    assert 'protected_or_tracked_file' in result['items'][0]['reasons']


def test_duplicates_use_conservative_retention_and_overlaps_skip(tmp_path):
    a = workspace(tmp_path / 'a')
    b = workspace(tmp_path / 'b')
    shared = tmp_path / 'private cache'
    shared.mkdir()
    (shared / 'old').write_bytes(b'data')
    os.utime(shared / 'old', (1, 1))
    for ws, days in [(a, 0), (b, 99999)]:
        ws.state.mkdir(exist_ok=True)
        (ws.state / 'local.toml').write_text('[storage.caches.cache]\npath=' + json.dumps(str(shared)) + f'\nkeep_days={days}\n', encoding='utf-8')
        workspace_index.remember(Workspace(ws.root))
    result = storage.disk(all_workspaces=True)
    assert len(result['items']) == 1 and result['summary']['candidate_units'] == 0
    assert len(next(r for r in result['roots'] if r['kind'] == 'cache')['references']) == 2
    with (b.state / 'local.toml').open('a', encoding='utf-8') as file:
        file.write('[storage.caches.nested]\npath=' + json.dumps(str(shared / 'nested')) + '\nkeep_days=0\n')
    (shared / 'nested').mkdir()
    (shared / 'nested' / 'data').write_bytes(b'retain')
    result = storage.preview(all_workspaces=True)
    assert result['summary']['candidate_units'] == 0
    assert any(d['reason'] == 'overlapping_storage_roots' for d in result['diagnostics'])


def test_old_evidence_requires_ownership_and_resolution_is_not_cleanup(tmp_path):
    ws = workspace(tmp_path, ZERO)
    path, record = run(ws)
    (path / storage_metadata.OWNER).unlink()
    assert storage.disk(ws)['summary']['candidate_units'] == 1
    Journal().update(record['operation_id'], result='manually_resolved', resolution_note='process exited')
    assert storage.disk(ws)['summary']['candidate_units'] == 0
    Journal().file(record['operation_id']).write_text('{broken SECRET}')
    bill = storage.disk(ws)
    assert bill['status'] == 'unverified' and bill['summary']['candidate_units'] == 0
    assert 'SECRET' not in json.dumps(bill)


def test_leaf_symlink_or_junction_skips_target(tmp_path):
    ws = workspace(tmp_path, CACHE)
    target = tmp_path / 'important'
    target.mkdir()
    (target / 'evidence').write_bytes(b'preserve')
    cache(ws)
    link = tmp_path / 'artifacts/releases/build-link'
    if os.name == 'nt':
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    bill = storage.disk(ws)
    item = next(i for i in bill['items'] if i['name'] == 'build-link')
    assert not item['eligible'] and 'linked_storage_path' in item['reasons']
    plan = storage.preview(ws)
    storage.apply(plan['plan_id'])
    assert (target / 'evidence').read_bytes() == b'preserve'


def test_scan_does_not_read_payload_and_honors_time_budget(tmp_path, monkeypatch):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws)
    original_read = Path.read_bytes
    def no_payload(path):
        assert path != file
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', no_payload)
    assert storage.disk(ws)['summary']['candidate_units'] == 1
    ticks = iter([0, 2, *([3] * 1000)])
    monkeypatch.setattr(storage.time, 'monotonic', lambda: next(ticks))
    result = storage.preview(ws, timeout=1)
    assert result['status'] == 'unverified' and result['summary']['candidate_units'] == 0


def test_already_absent_and_empty_scope(tmp_path):
    ws = workspace(tmp_path, CACHE)
    assert storage.apply(storage.preview(ws)['plan_id'])['status'] == 'passed'
    file = cache(ws)
    plan = storage.preview(ws)
    file.unlink()
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'passed' and result['items'][0]['status'] == 'already_missing'


def test_actual_apply_lease_blocks_concurrent_writer(tmp_path, monkeypatch):
    ws = workspace(tmp_path, CACHE)
    cache(ws)
    plan = storage.preview(ws)
    entered, proceed = Event(), Event()
    original = storage.remove
    def pause(*args):
        entered.set()
        assert proceed.wait(10)
        return original(*args)
    monkeypatch.setattr(storage, 'remove', pause)
    with ThreadPoolExecutor() as pool:
        job = pool.submit(storage.apply, plan['plan_id'])
        assert entered.wait(5)
        try:
            with pytest.raises(ResourceBusy):
                with operation(ws, 'browser.observe', {}):
                    pytest.fail('observer entered during reclamation')
        finally:
            proceed.set()
        assert job.result()['status'] == 'passed'


def test_new_unplanned_content_and_wrong_units_cannot_expand_plan(tmp_path):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws, 'build-dir')
    file.unlink()
    file.mkdir()
    (file / 'old').write_bytes(b'old')
    plan = storage.preview(ws)
    with pytest.raises(KitError, match='not a deletion candidate'):
        storage.apply(plan['plan_id'], ['f' * 32])
    (file / 'new').write_bytes(b'new')
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'unverified'
    assert (file / 'old').exists() and (file / 'new').exists()


def test_storage_configuration_sources_and_modules(tmp_path):
    path = tmp_path / 'bad'
    init_workspace(path)
    (path / 'echo-kit.toml').write_text('schema_version=1\n[storage.caches.bad]\npath="/absolute/shared"', encoding='utf-8')
    (path / '.echo-kit').mkdir()
    (path / '.echo-kit/local.toml').write_text('[storage.caches.bad]\npath="safe"', encoding='utf-8')
    with pytest.raises(KitError, match='Shared storage'):
        Workspace(path)
    module = tmp_path / 'module'
    init_workspace(module)
    (module / 'echo-kit.toml').write_text('schema_version=1\nincludes=["module.toml"]', encoding='utf-8')
    (module / 'module.toml').write_text('[storage.caches.bad]\npath="artifacts"', encoding='utf-8')
    with pytest.raises(KitError, match='Module configuration'):
        Workspace(module)


def test_resources_topic_is_optional_and_exported(tmp_path):
    from echo_kit import documentation
    for name in ('echo-init', 'echo-workbench', 'echo-lab'):
        compact = documentation.guide(name)
        topic = documentation.guide(name, topic='resources')
        assert '\n## Disk reclamation\n' not in compact['protocol']['content'].replace('\r\n', '\n')
        assert '\n## Disk reclamation\n' in topic['protocol']['content'].replace('\r\n', '\n')
    documentation.export(tmp_path)
    assert 'resources clean --apply' in (tmp_path / 'references/protocol.md').read_text(encoding='utf-8')


def test_many_small_files_and_sparse_size_budget(tmp_path):
    ws = workspace(tmp_path, CACHE)
    file = cache(ws, 'build-many')
    file.unlink()
    file.mkdir()
    for i in range(250):
        (file / str(i)).write_bytes(b'x')
    large = file / 'large'
    with large.open('wb') as stream:
        stream.truncate(4 * 1024 * 1024)
    result = storage.disk(ws)
    assert result['summary']['logical_bytes'] == 4 * 1024 * 1024 + 250
    assert result['summary']['scanned_entries'] == 252
    assert storage.preview(ws, max_entries=250)['summary']['candidate_units'] == 0


def test_current_environment_does_not_reclaim_other_environment(tmp_path):
    ws = workspace(tmp_path, ZERO + '[environments.dev]\n')
    local, _ = run(ws)
    development, _ = run(Workspace(ws.root, 'dev'))
    plan = storage.preview(ws)
    assert [i['path'] for i in plan['items']] == [str(local)]
    assert storage.apply(plan['plan_id'])['status'] == 'passed'
    assert not (local / 'trace.zip').exists() and (development / 'trace.zip').exists()
    assert storage.disk(all_workspaces=True)['summary']['candidate_units'] == 1


def test_directory_cache_deletes_frozen_files_and_empty_subdirs_only(tmp_path):
    ws = workspace(tmp_path, CACHE)
    unit = ws.root / 'artifacts/releases/build-output'
    (unit / 'nested').mkdir(parents=True)
    (unit / 'one').write_bytes(b'one')
    (unit / 'nested/two').write_bytes(b'two')
    plan = storage.preview(ws)
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'passed', result
    assert result['summary']['deleted_files'] == 2
    assert not unit.exists() and unit.parent.is_dir()


def test_content_arriving_during_delete_is_never_added_to_manifest(tmp_path, monkeypatch):
    ws = workspace(tmp_path, CACHE)
    unit = ws.root / 'artifacts/releases/build-output'
    unit.mkdir(parents=True)
    (unit / 'old').write_bytes(b'old')
    plan = storage.preview(ws)
    original = storage.remove
    def arrive(root, entry, *rest):
        if entry['path'].endswith('/old'):
            (unit / 'new-unplanned').write_bytes(b'preserve')
        return original(root, entry, *rest)
    monkeypatch.setattr(storage, 'remove', arrive)
    result = storage.apply(plan['plan_id'])
    assert result['status'] == 'unverified'
    assert (unit / 'new-unplanned').read_bytes() == b'preserve'
    assert not (unit / 'old').exists()


def test_declared_output_root_junction_is_not_resolved_away(tmp_path):
    ws = workspace(tmp_path / 'workspace', ZERO)
    config = ws.root / 'echo-kit.toml'
    config.write_text(config.read_text(encoding='utf-8').replace('[projects.main]', 'output="linked-output"\n\n[projects.main]'), encoding='utf-8')
    ws = Workspace(ws.root)
    target = tmp_path / 'outside'
    target.mkdir()
    link = ws.root / 'linked-output'
    if os.name == 'nt':
        subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    ws = Workspace(ws.root)
    path, _ = run(ws)
    assert path.parent == target
    plan = storage.preview(ws)
    assert plan['summary']['candidate_units'] == 0 and plan['status'] == 'unverified'
    assert storage.apply(plan['plan_id'])['summary']['deleted_files'] == 0
    assert (path / 'trace.zip').exists()


def test_environment_retention_overrides_apply_to_owned_evidence(tmp_path):
    ws = workspace(tmp_path, '''[storage.runs]
passed_days=100
other_days=100
keep_latest=3
[environments.dev.storage.runs]
passed_days=0
other_days=0
keep_latest=0
''')
    local, _ = run(ws, age=1)
    development, _ = run(Workspace(ws.root, 'dev'), age=1)
    plan = storage.preview(Workspace(ws.root, 'dev'))
    assert plan['summary']['candidate_units'] == 1
    assert plan['items'][0]['path'] == str(development)
    assert storage.apply(plan['plan_id'])['status'] == 'passed'
    assert (local / 'trace.zip').exists() and not (development / 'trace.zip').exists()
