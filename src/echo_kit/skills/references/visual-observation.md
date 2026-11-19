# Fast native observation and interaction

Use short native commands for one-off page debugging. Use a retained observe pipe when the host already supports persistent stdin/stdout. Both use a real Kit session worker that owns service reservations and supervises its own Chrome and fixed agent-browser. Native headed Chrome is the default; Docker/noVNC and Python callbacks are explicit Playwright compatibility only. Do not create a case or permanent control script merely to inspect a page.

## Short commands

```sh
echo-kit --workspace PATH --actor visual browser up --url http://localhost:5173 --service web
echo-kit --workspace PATH --actor visual browser exec -- snapshot -c
echo-kit --workspace PATH --actor visual browser exec -- click @e1
echo-kit --workspace PATH --actor visual browser exec -- screenshot
echo-kit --workspace PATH --actor visual browser down
```

Select only services this page uses; dependencies are included. Reuse existing auth adapters and startup entrypoints. Use a distinct actor for concurrent agents and --session ID only for an explicitly owned session. Native URLs are host-reachable URLs; do not blindly substitute Docker host.docker.internal addresses. The local headed window is interactive, not a read-only viewer; user input or closing it can affect the experiment. A lost page is reported, not recreated and re-executed.

Supported commands are a bounded subset: navigation, snapshots, click/fill, get text/box/styles/url, readiness waits and screenshots. This is not arbitrary upstream CLI passthrough: runtime installation/upgrades, personal auto-connect, foreign session/profile selection and backend changes are not session actions. Existing-session short actions route to the running owner without a network version check per click. A session keeps its actual Kit/agent-browser/Chrome versions; new sessions use ordinary update routing.

Sensitive input goes through private stdin/IPC and existing authentication channels, never argv, shared configuration or ordinary reports. Do not use raw fill arguments for passwords. Authentication state is opaque: only explicitly supported validated cookie/localStorage formats can be imported, otherwise use the project's established login entrypoint. Personal browser state is never inherited. A successful login does not prove role/tenant authorization.

## Persistent protocol 1

```sh
echo-kit --workspace PATH --actor visual browser observe --url http://localhost:5173 --service web
```

The command remains alive and exchanges JSON lines regardless of --json. Progress goes to stderr. Startup returns ready, actual versions/backend, operation/session identity, output directory and reply file. Each request needs a fresh nonempty id; read/display only current matching image paths. Initial viewport is 1440×900; timeout defaults 10000ms and accepts 1..120000.

```json
{"op":"observe","id":"o1"}
{"op":"observe","id":"o2","target":"#panel","capture":"element"}
{"op":"exec","id":"a1","command":["snapshot","-c"]}
{"op":"exec","id":"a2","command":["click","@e1"]}
{"op":"reload","id":"r1"}
{"op":"navigate","id":"n1","url":"http://localhost:5173/details"}
{"op":"close","id":"c1"}
```

Default observe keeps current state; navigation/reload is explicit. HMR should follow the project's actual dev server; a capture alone does not prove new code loaded. Native screenshots can capture selected main-document CSS regions without scrolling the page. Explicit multi-viewport capture restores the original viewport; responsive application code can still react to requested resize events. At most four views are allowed, dimensions 240..2560.

Capture replies include current observation identity, paths/hashes, viewport and bounded diagnostics/timing. `status: ok` means capture completed; visual quality and business checks need evidence. Images are empty on request failure; never substitute a previous response. For fixed HTML/URL references and Markdown provenance, read the [visual comparison reference](visual-comparison.md).

With a configured reference, `browser exec --session ID --page reference -- snapshot -c` explicitly opens and retains the isolated reference page for interaction. Its handles and authentication are separate from the target; normal reference capture remains temporary. Read the visual topic before selecting regions or freezing a baseline.

## Page readiness and blank evidence

Choose a CSS marker that appears on the intended page after it renders. Use the current session and actor:

```sh
echo-kit --workspace PATH --actor visual browser exec -- screenshot --ready-selector '#toolbox-title'
```

In the observation stream, use `{"op":"observe","id":"toolbox","target":"#toolbox-title"}` to wait and capture the viewport. Select `capture: "element"` for a close-up. The readiness selector is independent of the short command's cropping `--selector`. An exact, visible match satisfies the check within the session timeout. For a blocked response, read `page_check.reason` and `matches`, inspect `diagnostic_images` when present, and resolve the prerequisite before dependent business actions.

`page_check.url` is the final address without credentials, query or fragment. `document_status` is the available main document status. Script HTTP statuses describe observable failed script responses, and page/console error counts cover the indicated `error_scope`. The native source is the recent owned daemon record; Playwright observes events since the check was attached to its page. Null means the signal was unavailable. Raw console messages, request headers and response bodies stay out of the readiness receipt.

An ordinary full viewport image that is a single color remains available with `verification: unverified` and `uniform_viewport`. This includes intentional single-color states; interpret those using the expected page state. Use captures tied to the verified page state for a visual handoff, or explain which evidence is unavailable. `diagnostic_images` belong to the current failed request and supplement its reason. Reload, navigate, restart and retry only as an explicit next action after inspecting the results.

## Runtime and failure boundaries

Kit uses the pinned native binary only as a controlled daemon attached to Kit's existing owned Chrome, through a fixed-version contract adapter. It does not let the upstream CLI create/restart arbitrary daemons or browsers. Each action is sent once; a sent request with an unknown result returns outcome_unknown and retains evidence. It must not be replayed to obtain an answer. Window, tab, daemon or Chrome loss requires explicit new startup; no silent personal-browser, Docker or headless fallback.

close/down saves labelled native Chrome/CDP traceEvents JSON and ends only the owned session, then releases confirmed local reservations. It does not stop reusable microservices or certify business cleanup. Native trace is not Playwright trace.zip and cannot be presented as compatible with the old Trace Viewer. Emergency covers worker, native daemon, Chrome and pending starts; stale occupancy is resolved only after process identity checks and with the existing reason note.

Screenshots, receipts and traces can contain business data. Keep them in ignored, untracked .echo-kit/observations under the existing operation and storage unit. Active/pending evidence is protected; keep marks and explicit disk reclamation continue to apply. A reclaimed or missing picture is not an available successful result.

## Explicit Playwright compatibility

For a project that deliberately needs its old browser.session helper, Playwright adapter callbacks or trace.zip, choose backend="playwright" in configuration or the startup --backend flag. Old image/port/viewer_port/endpoint fields and an existing container never choose it automatically. Before changing backend inspect actual registrations/occupancy and stop the old owned instance explicitly; do not stop or adopt external endpoints.

The previous prepare(page, options, auth), ready(page, request), diagnose(page, request) and serve_observer adapter workflow is compatibility-only. Read [the compatibility helper](interactive-session.md) only when that backend is selected. Check the Playwright actually imported by that interpreter against required_playwright_version. Docker failure does not select an external endpoint, and native failure does not select compatibility automatically.

## Host image delivery

The bundled [observe-client.mjs](observe-client.mjs) supports persistent pipe hosts. It keeps request IDs and current-image checks; the host still needs subprocess access and local image display. A normal shell host may instead call short browser exec commands and display their artifact paths. Windows retained PTY clients can use the existing matching receipt reader with the ready event's actual Python interpreter:

```sh
python -m echo_kit.observation --read-reply REPLY_FILE --request-id NEW_REQUEST_ID --timeout-ms 30000
```

A timeout is a failure. Worker measurements exclude host file delivery, model image processing and AI reasoning. Platform/desktop and authentication limitations must be stated from actual validation, not inferred from runtime installation.
