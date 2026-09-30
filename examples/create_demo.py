"""Create three independent directories. Does not launch anything or overwrite."""
import argparse
import json
import shutil
import sys
from pathlib import Path


def create(root, api_port=18761, web_port=18762):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit('Output must be absent or empty')
    for folder in ('backend', 'frontend', 'qa/echo'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / 'templates'
    for filename, dest in [('server.py', 'backend/server.py'), ('frontend.py', 'frontend/server.py'),
                           ('adapter.py', 'qa/echo/adapter.py'), ('auth.py', 'qa/echo/auth.py')]:
        shutil.copyfile(source / filename, root / dest)
    python = json.dumps(sys.executable)
    config = f'''schema_version = 1
name = "echo-demo"
default_environment = "local"
[projects.api]
path = "../backend"
[projects.web]
path = "../frontend"
[projects.qa]
path = "."
[services.api]
project = "api"
command = [{python}, "server.py", "{api_port}"]
port = {api_port}
timeout = 10
[services.api.ready]
url = "http://127.0.0.1:{api_port}/health"
[services.web]
project = "web"
command = [{python}, "server.py", "{web_port}", "{api_port}"]
port = {web_port}
timeout = 10
[services.web.ready]
url = "http://127.0.0.1:{web_port}/health"
[auth.demo]
project = "qa"
command = [{python}, "echo/auth.py"]
account = "demo"
target = "http://127.0.0.1:{api_port}"
[cases.arithmetic]
project = "qa"
command = [{python}, "echo/adapter.py", "math"]
required_checks = ["sum"]
mode = "unit"
[cases.arithmetic.inputs]
numbers = [2, 3]
factor = 1
[cases.arithmetic.variants.double]
factor = 2
[cases.api]
project = "qa"
command = [{python}, "echo/adapter.py", "api"]
services = ["api"]
auth = "demo"
required_checks = ["download"]
mode = "integration"
[cases.api.inputs]
url = "http://127.0.0.1:{api_port}"
[cases.page]
project = "qa"
command = [{python}, "echo/adapter.py", "page"]
services = ["api", "web"]
auth = "demo"
browser = true
required_checks = ["download", "page"]
mode = "integration"
timeout = 90
[cases.page.inputs]
url = "http://host.docker.internal:{web_port}"
[environments.external.services.api]
mode = "external"
[environments.external.services.web]
mode = "external"
[environments.frontend-only.services.api]
mode = "external"
'''
    (root / 'qa/echo-kit.toml').write_text(config, encoding='utf-8')
    (root / 'qa/.gitignore').write_text('.echo-kit/\n', encoding='utf-8')
    return root / 'qa'


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--api-port', type=int, default=18761)
    p.add_argument('--web-port', type=int, default=18762)
    a = p.parse_args()
    print(create(a.output, a.api_port, a.web_port))
