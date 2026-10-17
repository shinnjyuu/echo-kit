# Temporary interactive acceptance

This is a reference using existing Python helpers, not a new CLI or a mandatory project adapter. Load the function in a compatible persistent Python execution tool; keep the `with` block and process alive across actions. Host tools that cannot preserve this lifecycle are not automatically supported. Do not create a permanent case just to use this example.

Prepare only needed services and the managed browser first, within authorization. Check readiness and authentication using existing project capabilities. Supply the actual workspace and all services used by the interaction; dependencies are included. The example does not start services or create accounts. For manual testing provide the business frontend URL instead, without this session or a Docker requirement.

```python
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from echo_kit import browser
from echo_kit.core import Workspace, KitError
from echo_kit.operations import Journal, operation, browser_resources, service_resources


@contextmanager
def interactive_session(workspace, services=(), environment=None, storage_state=None):
    ws = Workspace(workspace, environment)
    output = ws.state / 'interactive' / uuid4().hex
    resources = service_resources(ws, services, builds=False, access='shared')
    resources.update(browser_resources(ws))
    with operation(ws, 'browser.interactive', resources) as oid:
        info = browser.manage(ws, 'status')
        if info.get('status') not in ('running', 'ready', 'available'):
            raise KitError('Prepare the browser before interactive acceptance')
        try:
            with browser.session(info['endpoint'], output, storage_state) as context:
                yield context, oid, output
        except BaseException:
            # Preserve uncertain interruption/cleanup for inspection, not automatic retry.
            Journal().update(oid, requires_resolution=True)
            raise
```

Verify `.echo-kit/` is ignored and untracked before creating artifacts. Keep this function in the temporary tool session or ignored local material. The interpreter must contain the running Kit package and its compatible Playwright client. Pass credentials through existing private mechanisms, never paste them into shared snippets or logs.

Inside the live session, create your own page and perform successive actions based on observed results. `context, oid, output` stay valid only inside the block. Save necessary screenshots under `output`; the helper saves `trace.zip` and closes its own context/connection on exit. Other contexts and the container remain available. `oid` is an operation ID, not a run ID; this example creates no formal experiment report.

This example is for observational acceptance. Before actions that create asynchronous work or temporary business data, establish project cleanup and durable evidence; mark `Journal().update(oid, requires_resolution=True)` before starting such work. Do not clear that flag until actual completion/cleanup is verified. Prefer a repeatable case with the existing cleanup adapter when reliable lifecycle tracking is needed. Shared service reservations do not isolate business data; non-concurrent changes require exclusive service use. Interruptions retain a reservation for inspection, and confirmed stale reservations may be resolved without a routine human approval prompt.

Direct host browser tools must connect to the actual managed Playwright endpoint, maintain an owned context and provide equivalent occupancy/cleanup coverage. Kit does not supply a universal bridge for arbitrary host tools. If unavailable, report that limitation and alternatives; do not silently switch to an external browser or claim verification from startup alone. noVNC is a viewing link, not the human interaction URL.
