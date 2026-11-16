"""Small ownership and reclamation facts; no payload or credentials."""
import time
from pathlib import Path

from .core import read, save

OWNER = '.echo-owner.json'
RECLAMATION = '.echo-reclamation.json'
KEPT = {'run.json', 'report.html', 'session.json', 'latest.json', OWNER, RECLAMATION}


def own(ws, path, kind, operation_id=None, record=None):
    path = Path(path)
    value = {'format': 1, 'workspace': str(ws.root), 'environment': ws.environment,
             'kind': kind, 'id': path.name, 'operation_id': operation_id, 'ended': None}
    if record is not None:
        value.update(status=record.get('status'), case=record.get('case'), variant=record.get('variant'),
                     ended=record.get('finished', time.time()))
    save(path / OWNER, value)


def state(path):
    try:
        marker = Path(path) / RECLAMATION
        try:
            info = marker.lstat()
        except FileNotFoundError:
            return {'state': 'not_reclaimed', 'availability_checked': False}
        from .storage_fs import linked
        if linked(info) or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
            raise ValueError('Invalid reclamation marker')
        value = read(marker, {})
        if not value:
            return {'state': 'not_reclaimed', 'availability_checked': False}
        if not isinstance(value, dict) or value.get('state') not in ('reclaimed', 'partial', 'in_progress'):
            raise ValueError('Invalid reclamation record')
        return {key: value.get(key) for key in ('state', 'plan_id', 'time', 'deleted_files', 'deleted_logical_bytes')}
    except (OSError, ValueError):
        return {'state': 'unverified', 'availability_checked': False}
