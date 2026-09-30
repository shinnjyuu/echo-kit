# Echo Kit v1 adapter protocol

## Configuration

`schema_version=1`. Projects are `[projects.NAME] path="relative-or-absolute"`. `.echo-kit/local.toml` overrides shared values; `[environments.NAME]` overlays for explicit `--environment NAME`. Arrays replace, dictionaries merge. `output` overrides the default `.echo-kit/runs`.

Services define `command=[...]`, optional `project`, `depends`, `port`, `timeout` and a `ready` table containing `url` plus expected `status`, or `host/port`, or `command/timeout`. `mode="external"` is check-only. `prepare` is an optional explicit build command table. No implicit framework detection at runtime. Startup versions describe owned launch snapshots, not unowned remote software.

Commands execute without shell, with project cwd. `{python}` is the Kit interpreter for helpers; specify a project's interpreter for its business dependencies. `${ENV_NAME}` expansion is for non-secret command arguments. `env_refs` maps child variable names to parent variables. Secret arguments are forbidden because OS command lines and process state are observable.

Cases use `[cases.NAME]`, `command`, `project`, `inputs`, `variants.NAME` input overrides, `timeout`, `required_checks`, and `mode` (unit/probe/integration or project-defined scope). `protocol="command"` records only exit-code success. Verify additionally accepts `services=[...]`, `auth="NAME"`, `browser=true`. Lab deliberately does not prepare these capabilities.

## Experiment and verification subprocess

Environment variables: ECHO_REQUEST (JSON input path), ECHO_RESULT (JSON output path), ECHO_RUN_DIR (artifact directory), ECHO_ACTIVE (parent run task state).

Request: `protocol`, `run_id`, `environment`, `inputs`, `variant`, `iteration`, optional `browser.endpoint`, `active_file`. Credentials never belong in inputs. JSON stdin contains ephemeral private `auth` state. Read stdin once; never echo it.

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

Managed Docker browser is pinned to Playwright 1.63.0 and loopback host port 19323 (override `browser.port`). `browser.image` can select a registry mirror with the same version tag. External `browser.endpoint` is connect-only; Playwright handshake verifies compatible protocol. Browser project adapters can import `echo_kit.browser.session` when Echo is installed in their interpreter; otherwise use their own compatible Playwright client. Context traces are saved and closed in finally.

Every run writes run.json, report.html, adapter request/result/stdout and declared artifacts. Active records protect their associated resources; unrelated runs may continue. Legacy active records without operation IDs require cleanup before mutation. HTML is an escaped static view; trace.zip opens in Playwright Trace Viewer. Sharing raw traces and files requires business-data review.

CLI code 0: executed scope passed; 1: check failure; 2: blocked/unverified/config/dependency error; 130: interrupted. Cancellation/cleanup never automatically resubmits the case. Initialization never overwrites existing configuration. macOS remains unverified until real validation.

## Shared operations (local user)

`operations list`, `operations show ID`, and `operations resolve ID --note "confirmed cleanup"` work without a workspace. Global `--actor` labels the task. A conflict returns code 2, `reason="resource_busy"`, and `occupants` with operation ID, actor, action, duration, workspace and query command; no business action has run. There is no queue or automatic retry. Service status includes current occupants.

Journal location: Windows `%LOCALAPPDATA%/echo-kit/operations`, Linux `$XDG_DATA_HOME/echo-kit/operations` (default `~/.local/share`), macOS `~/Library/Application Support/echo-kit/operations`. `ECHO_KIT_DATA_HOME` overrides the base for tests only; all cooperating invocations must use the same location. History persists after release. Different OS users/machines and tools outside Echo are not coordinated. Records and evidence can contain local paths and task labels; review before sharing.

Services use the actual endpoint host/port (localhost, IPv4/IPv6 loopback normalized). URL paths are ignored and secrets in URLs are not recorded as resource IDs. Custom host aliases are not resolved. Add a common `resource_id` when multiple names address the same resource, or when readiness is a command without a declared endpoint. Optional service `artifacts=["target/app.jar"]` paths are relative to its project and are hashed at launch. Artifact declaration records provenance; it does not prove that arbitrary launch commands actually load those files. Don't declare fixed artifacts for hot-reload services.

A prepare command requires a Git project; source fingerprints include tracked and untracked nonignored file contents. Ignore build outputs before using prepare. Source changes during a build prevent launching its output. A running owned instance retains its original source/artifact record until explicitly restarted. Shared build-directory reservations last only through service preparation. Service reservations last through verification/cleanup. Direct Lab cases do not prepare services; declaring services still reserves those resources when the experiment uses them.

Auth check/refresh/logout uses a separate account/target reservation. Browser up holds a short-lived startup reservation and shared endpoint use; concurrent contexts share the endpoint, but down needs exclusive access. Runs in different environments still conflict if they address the same service. Configured external instances remain externally owned.

Crashed owners leave resources protected, with `needs_cleanup` reported from process identity, not an expiry timer. Adapter/build children are recorded, so a live child also prevents manual release. `runs cleanup` checks the original operation and never overwrites the run's original verdict. If no cleanup adapter exists, the user must confirm external terminal state before `operations resolve`; the note is preserved, historical run/active evidence is not deleted. Manual resolution does not stop any process. Legacy unresolved runs must be cleaned first. Stop old-version executions before upgrading; they do not recognize these reservations.
