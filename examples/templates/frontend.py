import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTML = b'''<!doctype html><html><title>Echo demo</title><body><h1>Echo demo</h1>
<button id="download">Download verified file</button><p id="status">Ready</p>
<script>document.querySelector('button').onclick=async()=>{const r=await fetch('/api/download',{headers:{Authorization:'Bearer '+localStorage.token}});if(!r.ok){document.querySelector('#status').textContent='Failed';return;}const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='echo.txt';a.click();document.querySelector('#status').textContent='Downloaded';};</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.startswith('/api/'):
            req = urllib.request.Request('http://127.0.0.1:' + sys.argv[2] + self.path[4:], headers={'Authorization': self.headers.get('Authorization', '')})
            try:
                with urllib.request.urlopen(req) as r:
                    status, data = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML)


ThreadingHTTPServer(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
