# Fast visual observation

Use this entry when iterating on a page's appearance. It prepares requested services and a managed Docker browser once, then preserves its own page, login state, open dialogs and unsaved input across observations. Human acceptance still uses the business frontend URL. Read the browser topic before starting.

## CLI session

```sh
echo-kit --workspace PATH --actor visual browser observe --url BROWSER_REACHABLE_URL --service web
echo-kit --workspace PATH browser observe --url BROWSER_REACHABLE_URL --service web --auth test --adapter echo/visual.py
```

Repeat `--service` for additional services. Their dependencies are included. Select only services this page uses. The URL must be reachable from Docker, including the effective private port assignment. An existing external browser endpoint requires an explicit project decision before using this managed-only entry. The initial viewport is 1440×900. `--timeout-ms` defaults to 10000 and accepts 1..120000.

The command remains alive and exchanges JSON lines regardless of `--json`. Startup emits a `ready` event with the executing version, `python_executable`, operation ID, output directory, viewer URL and reply file. Progress goes to stderr. Choose a new nonempty string `id` for every request.

```json
{"op":"observe","id":"1"}
{"op":"observe","id":"2","target":"#panel","capture":"element"}
{"op":"observe","id":"3","viewports":[{"width":1440,"height":900},{"width":390,"height":844}]}
{"op":"reload","id":"4"}
{"op":"navigate","id":"5","url":"BROWSER_REACHABLE_URL"}
{"op":"close","id":"6"}
```

`observe` captures the current page. It waits for fonts, visible images and short layout stability without waiting for network idle. HMR readiness and business-state readiness can be supplied by the project. Explicit reload/navigation changes page state. CSS changes become visible through the project's normal hot update, or after an explicit reload. Observing alone does not establish that a new bundle was loaded.

Targets must identify one visible CSS element. `capture` defaults to `viewport`; `element` requires a target. A target is scrolled into view. Up to four custom viewports are captured sequentially on the owned page, then its original viewport is restored. Responsive application code can react to these changes. Dimensions accept integers 240..2560. Animations remain enabled. Set `"animations":"disabled"` deliberately for a stable capture and report that choice.

Responses include an observation ID, timestamp, public URL, image paths/hashes, viewport, limited diagnostics and readiness/screenshot/file-read times. URL query values are omitted. Diagnostics report bounded geometry/font data, visible image/font failures, overflow and error counts. Project code can add thresholds or declared non-overlapping regions. Error counts are cumulative within this page session. Successful capture reports `status: ok`; assess visual quality from the image. When observation fails, `images` is empty, including a later failure in a multi-viewport request. Read only images named by the current matching response.

## Project adapter

The CLI starts an optional Python script as a tracked subprocess using the executing Kit interpreter. Project code imports the helper and owns its business rules.

```python
from echo_kit.observation import serve_observer

def prepare(page, options, auth):
    # Runs once after initial navigation. Apply the project's login/page setup here.
    # auth is private state returned by the selected existing auth adapter.
    # page.context belongs only to this worker.
    return {"data": "controlled", "purpose": "layout review"}

def ready(page, request):
    page.locator("[data-page-ready]").wait_for()

def diagnose(page, request):
    return {"business_ready": page.locator("[data-page-ready]").count() == 1}

serve_observer(prepare=prepare, ready=ready, diagnose=diagnose)
```

All callbacks are optional. `prepare` returns small descriptive annotations included with captures. `ready` and `diagnose` run per captured viewport. Requests may include an `options` object for project-specific choices. Keep cookies, tokens, input values and response bodies out of returned annotations/diagnostics. Metadata is limited to 32 KiB. The initial authentication state can contain Playwright `cookies`/`origins`, or a `storage_state` object. Other authentication formats require a prepare hook. Prepare identities through the existing explicit account preparation flow.

Request interception, reduced motion, test data, language and target-specific readiness remain explicit project choices. Label synthetic or isolated states. Use existing cleanup evidence before any asynchronous business work. The hook can call `Journal().update(options['operation_id'], requires_resolution=True)` before such work and clear it only after verified business completion. Shared service leases do not isolate data. Operations requiring exclusive service access use the existing project experiment/cleanup workflow.

## One tool call to receive and show images

Export the installed resources with `echo-kit skills export EMPTY_DIR`. The export includes [the Node pipe client](observe-client.mjs). In a persistent Node execution tool with subprocess access, import that file and retain the returned client.

```javascript
var { openObserver } = await import('file:///ABSOLUTE_EXPORT_DIR/references/observe-client.mjs');
var visual = await openObserver({ workspace: workspacePath, url: browserReachableUrl, services: ['web'] });
var fs = await import('node:fs/promises');
var result = await visual.observe({ target: '#panel', capture: 'element' });
nodeRepl.write({ status: result.status, captures: result.captures, error: result.error });
for (var path of result.images) {
  await nodeRepl.emitImage({ bytes: await fs.readFile(path), mimeType: 'image/png' });
}
// At the end of this work session
nodeRepl.write(await visual.close());
```

The host must support persistent subprocesses and image display. Host tool delivery, model image processing and AI reasoning have their own cost. Worker timings exclude them. No fixed cross-machine latency is guaranteed.

For command-only hosts, start the CLI with a retained process session. Windows PTY consumers can request a screenshot over that session's stdin, then run the following with a Python interpreter containing this Kit package and display the returned image paths in the same host tool invocation.

```sh
python -m echo_kit.observation --read-reply REPLY_FILE --request-id NEW_REQUEST_ID --timeout-ms 30000
```

Use the actual ready event's `python_executable` and reply file, with a fresh matching ID. This also selects the right interpreter after CLI update forwarding. A timeout is a failure. The reader never substitutes an older receipt.

## Ownership and handoff

The supervisor holds existing service/browser reservations and tracks its worker and descendants. Emergency stop cancels old admission generations and kills workers even while they wait for input. `close` or stdin EOF closes this session's context/connection, writes trace and releases confirmed reservations. Failed cleanup or pending business work retains the operation for inspection. Services and the browser remain reusable.

Images, receipts, worker diagnostics and traces are stored under ignored, untracked `.echo-kit/observations/OPERATION_ID/`. Traces/screenshots can contain business data. Ordinary replies exclude credentials and full request/response bodies. This session has operation/observation IDs and does not create a case run or formal run report. At handoff show key images with captions and state what was actually checked. Validate business correctness, real model behavior and build/export output as required by the task.
