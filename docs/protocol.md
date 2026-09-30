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

Every run writes run.json, report.html, adapter request/result/stdout and declared artifacts. Active records block new business runs in that workspace. HTML is an escaped static view; trace.zip opens in Playwright Trace Viewer. Sharing raw traces and files requires business-data review.

CLI code 0: executed scope passed; 1: check failure; 2: blocked/unverified/config/dependency error; 130: interrupted. Cancellation/cleanup never automatically resubmits the case. Initialization never overwrites existing configuration. macOS remains unverified until real validation.
