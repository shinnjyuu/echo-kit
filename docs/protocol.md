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

## Tool updates and pinned tasks

Echo Kit 0.2.0 introduces a project entrypoint and task runtime protocol 1. `updates setup --policy patch|manual|latest` creates project-owned `echo-kit.py` and `echo-kit.lock.json`; existing customized entrypoints are never overwritten. The JSON version file has `schema_version=1`, `package`, exact `version`, `policy`, and `launcher_digest`. Commit these entry files according to project authorization. The business configuration schema remains 1.

From the workspace, `python echo-kit.py --json task start --label "purpose"` refreshes release metadata and returns an `id`, exact version, environment, document digest, update/adoption result and executable argument-array `command_prefix`. Use that prefix, or `python echo-kit.py --task ID`, for every command in the task. An installed older launcher forwards a pinned task to its exact runtime before parsing the target command. Task runtime protocol 1 requires `runtime info` and these task/documentation interfaces to remain compatible. Environment defaults to the task's recorded environment; an explicitly different environment is rejected.

New tasks may automatically select stable, non-yanked, Python-compatible official PyPI releases. `patch` permits updates within the same major/minor version; `manual` only reports updates; `latest` permits newer stable releases across major/minor boundaries. Candidate preparation uses an isolated uv tool environment and validates its exact version, runtime protocol, documentation identity and read-only workspace check before atomically selecting it. This is not business acceptance testing. Failed checks retain the previous selection. An explicit `updates apply --version VERSION` also supports rollback to a stable version >= 0.2.0, subject to the same checks and task/operation barriers. No global installation or business interpreter is modified.

A CLI command finishing does not end the task. Open tasks and unfinished workspace operations/runs defer upgrades. `task list` and `task show ID` support resumption; `task finish ID` closes a task only when its commands and associated operations/runs have finished. Closing a task does not stop reusable services. Tasks are not expired or automatically discarded after crashes; do not delete task records to bypass upgrade barriers. Lifecycle commands and changing update policy run outside `--task`. Direct legacy commands remain available but do not provide a multi-command task boundary. Existing 0.1.x callers require a one-time migration and must finish old operations first.

Version selection holds an exclusive `toolchain:<canonical workspace path>` journal reservation. New business operations hold it shared for their duration, including direct calls without a task. This prevents supported callers from starting during a version switch; old tool versions cannot honor the new reservation. Locks and tasks coordinate the same local OS user, not machines or external tools. The version file pins Echo Kit, not all transitive dependencies or project business builds.

Ordinary `updates check`, workspace check, doctor and unbound Lab/Verify use a one-hour release cache; failures are throttled for five minutes. `updates check --refresh` and task start refresh explicitly. Short-timeout network failures are advisory. Global `--offline`, `ECHO_KIT_OFFLINE=1`, or `UV_OFFLINE=1` prohibits update network access, marks cached information stale and prevents automatic selection. Only already available runtimes work offline. Update metadata is kept in the local-user Echo data directory under `updates/`; no project configuration or credentials are sent to the release index.

`skills list`, `skills show NAME`, `protocol show`, and `runtime info` work offline without a workspace. `--json` documents include the running package version, documentation version/digest and content; non-JSON show commands render Markdown. With `--task`, documentation belongs to the pinned runtime and a changed document digest blocks task reuse. Export still requires an empty directory and adds `.echo-kit-skills.json` with version and per-file hashes. `skills status DIR` reports upstream changes, missing files and local edits without modifying them; exports without provenance are unverified. Re-export and merge project copies separately, preserving local rules. Repository README and validation records are not exported.

Task records live in `.echo-kit/tasks/`. Run JSON and HTML include `tool` (package/version/documentation identity), `task_id`, and `update` (discovery snapshot plus selected version). `versions` continues to describe business repositories. Operation journal entries include `task_id` and `tool_version`. JSON output remains machine-readable and update availability does not change business exit codes. Incomplete cleanup and external task completion retain their existing semantics.

## Shared and private project documents

Since 0.3.0, `docs setup|list|show shared|show private|check` manages project documentation discovery, separately from bundled `skills` and `protocol`. Global options precede the module. These commands work with the existing task entrypoint and pinned runtime; project contents remain live project files, not versioned task snapshots.

Configure `[documents] shared="echo/README.md"` only in shared `echo-kit.toml` and `[documents] private=".echo-kit/private/README.md"` only in local `.echo-kit/local.toml`. Shared entry paths are workspace-relative and must resolve inside the workspace outside `.echo-kit/`; private entry paths may be local absolute paths. Document fields in any environment, or in the wrong source file, are rejected before merging. This validation applies to `docs`; `workspace check`/`doctor` only return an advisory `documents` summary and do not block runtime upgrade compatibility probes on document problems. Old unconfigured workspaces return `unverified`, `reason=not_configured`, and a `docs setup` hint without writing files.

`docs setup` creates missing default entries and source-specific configuration while preserving existing documents and TOML comments. It adds ignore rules for `.echo-kit/` and any custom in-workspace private entry file, never stages files or rewrites Git history, and reports `created`, `reused`, and checks. External private entry files must already exist; setup does not write outside the workspace. The local entry configuration itself must also stay untracked and ignored. Custom private companions need their own ignore rules. Initial untracked shared files result in exit 2 even when setup completed. `list` returns scope/path/existence/Git metadata without bodies. `show` reads only the specified entry; a missing private entry is optional. Private show output is returned explicitly to the caller, not persisted in Kit reports/logs; do not redirect it to shared artifacts. Private storage is not encryption and is not a replacement for authentication storage.

`docs check` follows local Markdown links, reference links, images and HTML href/src from registered entries with cycle detection. Shared file references are relative to their containing document, must stay inside the workspace after symlink resolution, and must not target registered private entries, `.echo-kit/`, or ignored files. Windows/UNC/POSIX/file-URI absolute references are violations. Shared traversal never reads rejected private targets. Non-Markdown references receive existence/Git checks without body reads. URLs and anchors are not fetched/validated. Private Markdown traversal follows explicit local references, including absolute paths and file URIs, excluding documents already discovered through the shared graph; it never enumerates personal directories. Inspect private files against their owning Git repository, even when outside the workspace. No whole-drive, unreferenced-file, or Git-history scan is performed.

Diagnostics have `code`, `path`, `status`, and a `line` when a reference or body location is available; never include source excerpts or private bodies. Missing shared files, invalid shared paths/references, tracked private files (even if missing in the working tree), and existing private files in Git repositories without ignore protection fail (exit 1). Untracked shared files, Git unavailable/non-Git workspaces, missing private references, suspected personal paths in prose/code, read errors, and scan limits are unverified (exit 2). Failures take precedence. Limits are 500 files and 2 MiB per document. This structural check cannot prove absence of sensitive content or implicit dependencies. Only fully checked, compliant scope passes (exit 0).

AI workflow: discover and read shared instructions first; read personal notes only when useful to the task. Shared knowledge belongs in tracked portable documents; local facts and raw private records belong in ignored personal documents. Personal notes supplement team rules, never silently override them or grant operational authorization. Explain conflicts and follow current user instructions. Preserve reproducible conclusions in shared documents without relying on inaccessible private evidence. Before promoting private content, extract and sanitize the necessary conclusions rather than copying raw notes. Run `docs check` after document edits; respect task authorization for Git changes and report tracked private files for deliberate remediation instead of deleting evidence or rewriting history automatically.
