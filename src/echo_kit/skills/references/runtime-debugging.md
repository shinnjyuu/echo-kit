# Runtime debugging, only when needed

Use this after ordinary evidence leaves a concrete unanswered question. Do not add breakpoints to routine acceptance or create permanent scripts just to satisfy a workflow. Host tools must actually support the target protocol; a debugger installed on disk does not mean an agent can control it.

## Logic versus time

Follow actual input → transformation → output/end branch. Inspect a selected local variable and stack, then step and continue to test the hypothesis. Avoid arbitrary expression evaluation: even getters may have side effects. Distinguish observations, inferences and unverified conditions.

For SSE, chunking, cancellation, timeouts and concurrency, keep the process running. Correlate input, parse and end events by a request ID, sequence and monotonic elapsed time within one process. Do not compare monotonic epochs between machines. Breakpoint runs are separate logic evidence, never normal performance measurements.

## Isolated examples

Export Skills to a new empty directory, then copy these synthetic fixtures to a temporary directory if adapting them. Nothing should be installed into a business interpreter implicitly.

- [Python branch fixture](runtime-python.py): the intentionally incorrect `>` excludes equality. Use an optional debugpy installation in an isolated interpreter and `python -m debugpy --listen 127.0.0.1:PORT --wait-for-client runtime-python.py`. Attach a DAP-capable client, set a breakpoint on `accepted =`, inspect `amount` and `threshold` plus the stack, step over, inspect `accepted`, remove your breakpoint, continue and disconnect without terminating an existing target. Expected evidence: 10, 10, false. Testing a correction requires a separate run; do not mutate variables and call that a verified code fix.
- [Node stream fixture](runtime-node.cjs): first run `node runtime-node.cjs` normally. Input JSON is split across chunks; structured events expose only sequence, correlation ID, elapsed nanoseconds, chunk length and the chosen branch. The intentionally wrong equality check yields `rejected`. Then run a separate `node --inspect-brk=127.0.0.1:PORT runtime-node.cjs` session with an Inspector-capable client. Set a breakpoint on `const accepted`, continue, inspect `amount` and `threshold`, step, inspect `accepted`, remove your breakpoint, resume and disconnect. Do not use this paused run's elapsed times for performance conclusions.

PORT is an explicitly selected free local port, not a shared default. Both fixtures include a synthetic secret in memory to exercise selective inspection; do not request all scopes/properties or persist complete DAP/Inspector traffic. No real credentials, APIs or accounts are involved. These fixtures and repository-only test clients are examples, not supported Kit debugger commands. Python requires debugpy; Node requires Inspector. Neither becomes a mandatory Kit dependency.

## Existing managed targets

Prefer isolated processes. Shared databases and remote APIs still require their original authorization and cleanup. Never inject debug arguments, attach to an unknown process or restart services automatically. Keep reusable startup methods shared, machine-specific paths/ports private and listeners on loopback.

When authorized to pause an existing managed instance, use the existing `operation` context with `service_resources(ws, names, builds=False, access='exclusive')`. This includes dependencies. Inside the acquired reservation, re-read each registry record and compare its instance generation and verified process identity to the intended target before attachment. If it changed or another operation holds resources, abort without attaching. Keep that same operation alive until cleanup completes. An independent short CLI call cannot hold this lifetime for a later host debugger. External clients are not covered by Kit reservations; do not infer they are absent.

Track only child processes you started with `track_child(fingerprint(pid))`. In finally, remove only owned breakpoints, resume only your own pauses, then disconnect. Stop only your isolated test processes. If restoration/disconnection/exit is uncertain, set `Journal().update(oid, requires_resolution=True)` before leaving the operation and retain concise evidence. A debug connection failure is not a reason to retry business requests. Remote tasks and test data need their own completion/cleanup evidence.

Report installed/connected/hit/verified as distinct milestones. Include selected redacted values, source location and relevant event ordering; never full memory dumps, secrets or default protocol logs. Unknown state stays unverified. Existing exported guides are independent copies: export anew, compare and merge.

References: [debugpy command line](https://github.com/microsoft/debugpy/wiki/Command-Line-Reference), [Node debugging](https://nodejs.org/learn/getting-started/debugging).
