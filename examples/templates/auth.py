"""Demo credentials are public fixture values, not a production login shortcut."""
import json
import sys
import urllib.request
import urllib.error

q = json.load(sys.stdin)
token = (q.get('state') or {}).get('token', '')
action = q['action']
url = q['target'] + ('/login' if action in ('login', 'refresh') else '/logout' if action == 'logout' else '/me')
data = json.dumps({'account': q['account'], 'password': q.get('credentials', {}).get('password', 'demo-only')}).encode() if action in ('login', 'refresh', 'logout') else None
req = urllib.request.Request(url, data=data, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        value = json.load(r)
    print(json.dumps({'valid': True, 'account': q['account'], 'target': q['target'], 'state': {'token': value.get('token', token)}}))
except urllib.error.HTTPError:
    print(json.dumps({'valid': False, 'account': q['account'], 'target': q['target']}))
