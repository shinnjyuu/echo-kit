import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

tokens = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, data, content='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content)
        self.end_headers()
        self.wfile.write(data if isinstance(data, bytes) else json.dumps(data).encode())

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'ready': True})
        if self.headers.get('Authorization', '').removeprefix('Bearer ') not in tokens:
            return self.reply(401, {'error': 'unauthenticated'})
        if self.path == '/me':
            return self.reply(200, {'account': 'demo'})
        if self.path == '/download':
            return self.reply(200, b'Echo demo verified\n', 'text/plain')
        return self.reply(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
        if self.path == '/login' and body == {'account': 'demo', 'password': 'demo-only'}:
            token = secrets.token_hex(24)
            tokens.add(token)
            return self.reply(200, {'token': token})
        if self.path == '/logout':
            tokens.discard(self.headers.get('Authorization', '').removeprefix('Bearer '))
            return self.reply(200, {})
        return self.reply(401, {})


ThreadingHTTPServer(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
