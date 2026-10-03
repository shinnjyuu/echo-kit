import html
import json
from pathlib import Path

from .core import KitError, read, safe_name


def locate(ws, rid):
    path = ws.runs / safe_name(rid)
    if not (path / 'run.json').exists():
        raise KitError('Unknown run: ' + rid)
    return path


def compare(left, right):
    a = {c['name']: c for c in left.get('checks', [])}
    b = {c['name']: c for c in right.get('checks', [])}
    return {'left': left['id'], 'right': right['id'],
            'status': [left['status'], right['status']],
            'checks': [{'name': n, 'left': a.get(n), 'right': b.get(n)} for n in sorted(a.keys() | b.keys())],
            'metrics': [left.get('metrics', {}), right.get('metrics', {})]}


def report(path):
    path = Path(path)
    r = read(path / 'run.json')
    esc = lambda x: html.escape(str(x))
    rows = ''.join(f'<tr><td>{esc(c["name"])}</td><td>{esc(c["status"])}</td><td>{esc(c.get("detail", ""))}</td></tr>' for c in r.get('checks', []))
    events = ''.join(f'<li>{esc(e["label"])} — {esc(e["status"])}</li>' for e in r.get('events', []))
    links = []
    from urllib.parse import quote
    for artifact in r.get('artifacts', []):
        file = (path / artifact).resolve()
        if file.is_relative_to(path.resolve()) and file.is_file():
            links.append(f'<li><a href="{quote(artifact)}">{esc(artifact)}</a></li>')
    content = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Echo {esc(r['id'])}</title>
<style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:40px auto;padding:20px;color:#243244}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:10px;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
<h1>{esc(r['case'])}</h1><p>{esc(r['id'])} · {esc(r['environment'])} · <strong>{esc(r['status'])}</strong></p>
<p>{esc(r.get('reason', ''))}</p><h2>Checks</h2><table><tr><th>Check</th><th>Status</th><th>Detail</th></tr>{rows}</table>
<h2>Timeline</h2><ol>{events}</ol><h2>Artifacts</h2><ul>{''.join(links)}</ul>
<h2>Versions</h2><pre>{esc(json.dumps(r.get('versions', {}), indent=2))}</pre>
<h2>Echo Kit</h2><pre>{esc(json.dumps({'tool': r.get('tool'), 'task_id': r.get('task_id'), 'update': r.get('update')}, indent=2))}</pre>
<h2>Operation</h2><pre>{esc(json.dumps({'id': r.get('operation_id'), 'resources': r.get('resources', {}), 'occupants': r.get('occupants', [])}, indent=2))}</pre>
<h2>Running services</h2><pre>{esc(json.dumps(r.get('services', {}), indent=2))}</pre>
<p>Recorded evidence is not deterministic replay. Review artifacts for business data before sharing.</p></html>'''
    target = path / 'report.html'
    target.write_text(content, encoding='utf-8')
    return str(target)
