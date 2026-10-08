# Echo Kit v1 adapter protocol

## Configuration

`schema_version=1`. Projects are `[projects.NAME] path="relative-or-absolute"`. `.echo-kit/local.toml` overrides shared values; `[environments.NAME]` overlays for explicit `--environment NAME`. Arrays replace, dictionaries merge. `output` overrides the default `.echo-kit/runs`.

Services define `command=[...]`, optional `project`, `depends`, `port`, `timeout` and a `ready` table containing `url` plus expected `status`, or `host/port`, or `command/timeout`. `mode="external"` is check-only. `prepare` is an optional explicit build command table. No implicit framework detection at runtime. Startup versions describe owned launch snapshots, not unowned remote software.

Commands execute without shell, with project cwd. `{python}` is the Kit interpreter for helpers; specify a project's interpreter for its business dependencies. `${ENV_NAME}` expansion is for non-secret command arguments. `env_refs` maps child variable names to parent variables. Secret arguments are forbidden because OS command lines and process state are observable.

Cases use `[cases.NAME]`, `command`, `project`, `inputs`, `variants.NAME` input overrides, `timeout`, `required_checks`, and `mode` (unit/probe/integration or project-defined scope). `protocol="command"` records only exit-code success. Verify additionally accepts `services=[...]`, `auth="NAME"`, `browser=true`. Lab deliberately does not prepare these capabilities.

## Managed service lifetime

Kit leaves managed service processes running after a case or CLI invocation finishes so later invocations can reuse them. Survival across the launching AI application's or terminal's exit, update, restart or crash, or an OS restart, is not guaranteed. A service stopping at those boundaries is allowed by design. Depending on the platform and launch method, it may also remain running; host exit must not be treated as guaranteed cleanup.

Service registration preserves process identity, logs and launch metadata. It can outlive the process and does not establish current readiness. Kit provides no independent service daemon, automatic restart or boot persistence. This boundary concerns local managed service processes; external instances and Docker browsers retain their own ownership and lifecycle.

When resuming work, inspect `operations list` and `services status` in the intended workspace/environment. Reuse healthy instances. Resolve associated unfinished work before starting needed stopped services with `services up NAME` within the authorized scope. Preserve identity checks and resource reservations; do not delete state or take over unknown processes. Host or local service exit does not confirm that remote/asynchronous business tasks have ended: the normal cleanup and terminal-evidence rules still apply.

## Experiment and verification subprocess

Environment variables: ECHO_REQUEST (JSON input path), ECHO_RESULT (JSON output path), ECHO_RUN_DIR (artifact directory), ECHO_ACTIVE (parent run task state).

Request: `protocol`, `run_id`, `environment`, `inputs`, `variant`, `iteration`, optional `browser` metadata (including `endpoint`, `headless`, `viewer_url`), `active_file`. Credentials never belong in inputs. JSON stdin contains ephemeral private `auth` state. Read stdin once; never echo it.

Result example:

```json
{"status":"passed","checks":[{"name":"download","status":"passed","detail":"bytes matched"}],"metrics":{"elapsed_ms":120},"artifacts":["download.txt"]}
```

Result statuses: passed, failed, blocked, unverified, interrupted. Checks additionally allow not_applicable; a required not_applicable check does not pass. Checks must have unique names. Artifacts must exist beneath ECHO_RUN_DIR. stdout is saved as a log: adapters are trusted project code and must redact private material.

If the adapter starts remote/asynchronous work, set `creates_tasks=true`, immediately save IDs to ECHO_ACTIVE, retaining run_id. Successful result must include `terminal_confirmed=true`. Optional `[cases.NAME.cleanup]` defines a separate command that receives `{run_id, active}`; it must target only those IDs and return `status="passed", terminal_confirmed=true`. Cleanup runs after the case if configured, including timeout. Missing terminal evidence retains active.json. Later use `runs cleanup ID`; it preserves the original failure verdict.

## Authentication subprocess

Auth differs intentionally: no secret request/result files. `[auth.NAME]` has command, project, account, target, timeout and optional credentials mapping fields to environment variable names.

stdin JSON: `{action, account, target, state, credentials}`. Actions: login, check, refresh, logout. stdout JSON: `{valid, account, target, state}`. No diagnostic text on stdout; stderr is private and not persisted by Kit. Returned identity and target must exactly match configuration. State is opaque JSON, passed to case stdin. Browser adapters can translate it to normal application storage or Playwright storage_state.

Kit checks stored state, tries refresh, then login. Only system credential backends are used for persistence. Without one, the same verify invocation can use the state; another CLI invocation logs in again. This does not bypass user, organization or password checks. Demonstration credentials are fixtures only.

## Browser and records

Managed Docker browser is pinned to Playwright 1.63.0 and loopback host port 19323 (override `browser.port`). Since 0.6.0 it uses the [visible browser runtime](#visible-browser) by default. `browser.image` selects its base image, including a registry mirror with the same version tag. External `browser.endpoint` is connect-only; Playwright handshake verifies compatible protocol. Browser project adapters can import `echo_kit.browser.session` when Echo is installed in their interpreter; otherwise use their own compatible Playwright client. Context traces are saved and closed in finally.

Every run writes run.json, report.html, adapter request/result/stdout and declared artifacts. Active records protect their associated resources; unrelated runs may continue. Legacy active records without operation IDs require cleanup before mutation. HTML is an escaped static view; trace.zip opens in Playwright Trace Viewer. Sharing raw traces and files requires business-data review.

CLI code 0: executed scope passed; 1: check failure; 2: blocked/unverified/config/dependency error; 130: interrupted. Cancellation/cleanup never automatically resubmits the case. Initialization never overwrites existing configuration. macOS remains unverified until real validation.

## Shared operations (local user)

`operations list`, `operations show ID`, and `operations resolve ID --note "confirmed cleanup"` work without a workspace. Global `--actor` labels the task. A conflict returns code 2, `reason="resource_busy"`, and `occupants` with operation ID, actor, action, duration, workspace and query command; no business action has run. There is no queue or automatic retry. Service status includes current occupants.

Journal location: Windows `%LOCALAPPDATA%/echo-kit/operations`, Linux `$XDG_DATA_HOME/echo-kit/operations` (default `~/.local/share`), macOS `~/Library/Application Support/echo-kit/operations`. `ECHO_KIT_DATA_HOME` overrides the base for tests only; all cooperating invocations must use the same location. History persists after release. Different OS users/machines and tools outside Echo are not coordinated. Records and evidence can contain local paths and task labels; review before sharing.

Services use the actual endpoint host/port (localhost, IPv4/IPv6 loopback normalized). URL paths are ignored and secrets in URLs are not recorded as resource IDs. Custom host aliases are not resolved. Add a common `resource_id` when multiple names address the same resource, or when readiness is a command without a declared endpoint. Optional service `artifacts=["target/app.jar"]` paths are relative to its project and are hashed at launch. Artifact declaration records provenance; it does not prove that arbitrary launch commands actually load those files. Don't declare fixed artifacts for hot-reload services.

A prepare command requires a Git project; source fingerprints include tracked and untracked nonignored file contents. Ignore build outputs before using prepare. Source changes during a build prevent launching its output. A running owned instance retains its original source/artifact record until explicitly restarted. Shared build-directory reservations last only through service preparation. Service reservations last through verification/cleanup. Direct Lab cases do not prepare services; declaring services still reserves those resources when the experiment uses them.

Auth check/refresh/logout uses a separate account/target reservation. Browser up holds a short-lived startup reservation and shared endpoint use; concurrent contexts share the endpoint, but down needs exclusive access. Runs in different environments still conflict if they address the same service. Configured external instances remain externally owned.

Crashed owners leave resources protected, with `needs_cleanup` reported from process identity, not an expiry timer. Adapter/build children are recorded, so a live child also prevents manual release. `runs cleanup` checks the original operation and never overwrites the run's original verdict. If no cleanup adapter exists, the user must confirm external terminal state before `operations resolve`; the note is preserved, historical run/active evidence is not deleted. Manual resolution does not stop any process. Legacy unresolved runs must be cleaned first. Stop old-version executions before upgrading; they do not recognize these reservations.

## Latest-version execution

Since 0.4.0, use `echo-kit` directly. Ordinary commands discover the newest stable, non-yanked official PyPI release supporting the current Python before parsing business arguments, allowing new commands to be forwarded. Successful metadata is refreshed on every invocation; failed checks are throttled for five minutes. `updates check --refresh` bypasses that throttle. Checks use a short network timeout and keep advisory cache data under the user's Echo data directory.

A newer release is prepared in an isolated uv environment and checked only for successful startup and correct package/version identity via `runtime info`. No workspace, task protocol, documentation digest or business acceptance probe is required. Once prepared, the original command is executed exactly once by that release. Internal subprocess markers prevent recursive updates and are not propagated into business subprocesses. Preparation does not modify global installations, business dependencies or running services.

Check/preparation failures keep the calling runtime usable and emit a stderr notice. Explicit `--offline`, `ECHO_KIT_OFFLINE=1` and `UV_OFFLINE=1` prohibit update networking; stale metadata never establishes latest-version status. After business execution begins, failures and interrupts are returned without retries or version fallback. JSON stdout and child exit codes are preserved. `--version`, help, `runtime info`, and `updates check` diagnose the installed entry without forwarding; ordinary commands, including Skill/protocol reads, use latest-version discovery.

There are no project-selected versions, update policies, version tasks, task environment inheritance or documentation locks. New runs and operation records contain the actual tool version but no task_id. Existing historical task_id fields remain readable in old reports. Service ownership, business resource reservations, ECHO_ACTIVE, cleanup and authorization rules remain in force. Update selection no longer acquires a toolchain resource reservation.

`updates setup` is now a one-time migration of an unchanged official 0.2.0/0.3.0 generated entry. It checks for actual pending operations and active runs, backs up the old launcher and lockfile in ignored `.echo-kit/migrations/`, replaces the launcher with a thin call to installed Echo Kit >= 0.4.0, and removes the lockfile. No entry is created on a fresh project. Customized, linked or unrecognized entries are preserved for manual migration. Historical task records remain untouched and open version tasks alone do not block migration. Removed task/--task/--policy/updates apply interfaces return migration guidance. Invoke migration from a newly installed CLI, not the old pinned entry. Project README/AGENTS/exported Skills require separate review; do not silently change them or shared business environments.

`skills list/show/export/status`, `protocol show` and `runtime info` use the executing package's resources. Offline reads need no workspace or network. JSON includes version and documentation digest as provenance, not a lifecycle lock. Export still requires an empty directory and writes `.echo-kit-skills.json`; status reports modifications and upstream differences without overwriting project edits. Explicit-version release validation or debugging uses `--offline` to avoid forwarding. New project work does not require generated launchers or version files.

## Shared and private project documents

Since 0.3.0, `docs setup|list|show shared|show private|check` manages project documentation discovery, separately from bundled `skills` and `protocol`. Global options precede the module. Call these commands directly; project contents remain project-owned files independent of the executing tool version.

Configure `[documents] shared="echo/README.md"` only in shared `echo-kit.toml` and `[documents] private=".echo-kit/private/README.md"` only in local `.echo-kit/local.toml`. Shared entry paths are workspace-relative and must resolve inside the workspace outside `.echo-kit/`; private entry paths may be local absolute paths. Document fields in any environment, or in the wrong source file, are rejected before merging. This validation applies to `docs`; `workspace check`/`doctor` only return an advisory `documents` summary and do not block ordinary business checks on document problems. Old unconfigured workspaces return `unverified`, `reason=not_configured`, and a `docs setup` hint without writing files.

`docs setup` creates missing default entries and source-specific configuration while preserving existing documents and TOML comments. It adds ignore rules for `.echo-kit/` and any custom in-workspace private entry file, never stages files or rewrites Git history, and reports `created`, `reused`, and checks. External private entry files must already exist; setup does not write outside the workspace. The local entry configuration itself must also stay untracked and ignored. Custom private companions need their own ignore rules. Initial untracked shared files result in exit 2 even when setup completed. `list` returns scope/path/existence/Git metadata without bodies. `show` reads only the specified entry; a missing private entry is optional. Private show output is returned explicitly to the caller, not persisted in Kit reports/logs; do not redirect it to shared artifacts. Private storage is not encryption and is not a replacement for authentication storage.

`docs check` follows local Markdown links, reference links, images and HTML href/src from registered entries with cycle detection. Shared file references are relative to their containing document, must stay inside the workspace after symlink resolution, and must not target registered private entries, `.echo-kit/`, or ignored files. Windows/UNC/POSIX/file-URI absolute references are violations. Shared traversal never reads rejected private targets. Non-Markdown references receive existence/Git checks without body reads. URLs and anchors are not fetched/validated. Private Markdown traversal follows explicit local references, including absolute paths and file URIs, excluding documents already discovered through the shared graph; it never enumerates personal directories. Inspect private files against their owning Git repository, even when outside the workspace. No whole-drive, unreferenced-file, or Git-history scan is performed.

Diagnostics have `code`, `path`, `status`, and a `line` when a reference or body location is available; never include source excerpts or private bodies. Missing shared files, invalid shared paths/references, tracked private files (even if missing in the working tree), and existing private files in Git repositories without ignore protection fail (exit 1). Untracked shared files, Git unavailable/non-Git workspaces, missing private references, suspected personal paths in prose/code, read errors, and scan limits are unverified (exit 2). Failures take precedence. Limits are 500 files and 2 MiB per document. This structural check cannot prove absence of sensitive content or implicit dependencies. Only fully checked, compliant scope passes (exit 0).

AI workflow: discover and read shared instructions first; read personal notes only when useful to the task. Shared knowledge belongs in tracked portable documents; local facts and raw private records belong in ignored personal documents. Personal notes supplement team rules, never silently override them or grant operational authorization. Explain conflicts and follow current user instructions. Preserve reproducible conclusions in shared documents without relying on inaccessible private evidence. Before promoting private content, extract and sanitize the necessary conclusions rather than copying raw notes. Run `docs check` after document edits; respect task authorization for Git changes and report tracked private files for deliberate remediation instead of deleting evidence or rewriting history automatically.


## Parallel service startup

Since 0.6.0, `services up`, the startup phase of `services restart`, and service preparation in `verify run` default to scheduling every dependency-eligible selected service concurrently. The executor size is the deduplicated selection plus dependencies, with no fixed limit of 4; empty selections need no executor. Set `[service_startup] concurrency = 4` to impose an explicit limit; `services up/restart --jobs N` overrides it for one call. Existing explicit limits remain unchanged. Values must be positive integers; `--jobs 1` is serial. Existing `depends` waits for dependencies to become ready. Unknown services, dependency cycles and malformed selected configurations fail before any service is changed. Each selected service and dependency is scheduled once.

Kit does not infer framework dependencies or merge build commands. Existing per-service `prepare` remains supported. Within one invocation, services sharing a canonical project directory serialize preparation through artifact checking and launch registration; their readiness waits overlap. Cross-operation resource reservations remain in force. For a shared build, run the project's build script once, verify its exit code, then call Kit to start services without duplicate `prepare` commands:

```sh
python scripts/build_services.py
echo-kit services up api worker web
```

The first command must succeed before the second is executed. These names and script are project-defined examples, not Kit-provided commands. Special startup sequencing belongs in project scripts. Each service command must remain in the foreground, propagate failure, and keep its process tree attached for Kit ownership and stopping. Team startup scripts belong in Git; machine-specific values belong in local configuration.

On failure, stop scheduling new work and drain already-started work within its timeouts. Keep registered services, including running-but-not-ready instances, and return per-service results (`ready`, `reused`, `failed`, `not_started`) with errors and log/stop guidance. Do not retry or roll back automatically. Interruption cancels queued work, stops active preparation processes and exits readiness waits; registered services remain available for inspection. Unconfirmed preparation termination retains operation resources for cleanup.

Startup returns the existing service-name mapping with an additional `startup` object for requested services and dependencies. It contains `result`, optional error/guidance, and `timings` in seconds: `queue` (including directory-lock wait), `build`, `start`, `ready`, and `total` (elapsed since scheduling began). An unexecuted phase is zero; unselected services have `ready=null` in this startup snapshot; use `services status` for a fresh full readiness check. Failures return `status=failed` (exit 1) or `interrupted` (exit 130) with the mapping in `services`. Configuration/ownership reservation errors before scheduling remain blocked (exit 2). `operations show` includes stage events and final startup summaries. Human progress goes to stderr; JSON mode emits one final stdout JSON object. Inspect failed services before using them for business requests.


## Visible browser

Since 0.6.0, managed browsers default to `browser.headless=false`. Kit builds a local image from bundled Dockerfile/server resources and the fixed Playwright base (`browser.image`), caches it by base reference and resource hash, and uses `chromium.launchServer` with the existing `chromium.connect(endpoint)` protocol. The first build downloads system and npm dependencies; subsequent runs reuse the image without runtime installation. `browser.build_timeout` defaults to 600 seconds; readiness `browser.timeout` defaults to 90. Build failures include a local build log; readiness failures retain container identity and a `docker logs` command. No silent fallback to headless.

Headed mode runs Chromium, Xvfb, x11vnc and websockify/noVNC. The supervisor exits on any critical component's exit. Readiness verifies Playwright, viewer HTTP, and the WebSocket-to-RFB transport. VNC enforces view-only and disables selection/clipboard exchange on the server; changing URL parameters cannot grant input. Control port `browser.port=19323` and `browser.viewer_port=19324` bind only to host loopback. Port conflicts fail explicitly. Viewer ports join existing operation reservations; active experiments block container removal. These local interfaces are not an authenticated remote-sharing service.

`browser up/status` return actual `headless`, `endpoint`, `viewer_url` and `viewer_read_only`; external or unavailable guarantees use null. Verify passes the same metadata to adapters, stores `browser.json` beside evidence, and includes `browser` in the run record. Startup/status never invent a viewer URL for a legacy container. A ready browser with no open experiment pages can show an empty desktop. Multiple simultaneous experiments use isolated browser contexts but share one display; the foreground window can change. This version has no human takeover or per-experiment video stream.

For managed headed containers, `browser.auto_open` defaults to true except when `CI` is `1`, `true` or `yes` (case-insensitive). An explicit boolean overrides this default. Kit attempts to open `viewer_url` via the system browser once per container, after readiness. It records the attempt before opening and does not repeatedly popup after failure or user closure. Auto-open failure is nonfatal and returns the URL with a notice. Status never opens a browser. Agents present the link when starting visible debugging and open it on request, without duplicate popups or forcing a closed viewer to reopen. The viewer is browser-neutral.

`browser.headless=true` starts no display/VNC/web components, publishes no viewer port and returns `viewer_url=null`. Closing a viewer does not stop automation; experiment contexts still close and traces still save through `browser.session`. Containers stay available for reuse until `browser down`. Changes to runtime resources, base image, published ports or headless mode require explicit down/up after active operations end; no automatic replacement. Existing 0.5 and older registrations are reported as legacy headless with no viewer, even when the new configuration defaults to headed. Auto-open preference changes alone do not require rebuilding.

External `browser.endpoint` stays connect-only. An optional HTTP(S) `browser.viewer_url` is returned as supplied, never auto-opened or labeled read-only by Kit. External browser mode and viewer access remain the owner's responsibility. Do not overwrite project adapters or exported Skills when upgrading; compare fresh exports and preserve project edits.
