---
name: echo-init
description: Initialize or maintain Echo Kit project adapters when the user explicitly requests Echo setup or project onboarding.
---

# Echo project onboarding

With Kit >= 0.4.0, call echo-kit directly. Ordinary commands check for the latest stable compatible release and prepare it in an isolated uv environment before execution. Offline or preparation failure retains the current usable runtime with a notice; never retry a failed business command under another version. No task start/finish, --task, project version lock or policy is needed. Use --offline for intentional offline execution; --version and runtime info describe the installed entry. Migrate old generated entries once using updates setup; preserve project edits and historical records. See [latest-version behavior](../references/protocol.md#latest-version-execution).

For Kit >= 0.3.0, discover project documents with `--json docs list` directly with echo-kit. During authorized onboarding, use `docs setup` if absent; preserve existing entries or configure them instead of relocating documents. Read `docs show shared`, and explicitly read `docs show private` only as needed. Shared knowledge uses relative file links and belongs in Git; local paths and personal evidence belong in ignored private notes. Personal notes supplement team rules, never silently override them or expand authorization. Extract sanitized conclusions before sharing private material; keep shared instructions independently reproducible. After document edits run `docs check`; initial untracked shared files return unverified. Do not auto-delete tracked private files or rewrite Git history. See [project document rules](../references/protocol.md#shared-and-private-project-documents) for configuration, checking boundaries and conflicts.

Use only for explicit onboarding requests. Inspect existing build, test, CI and launch files before generating anything. Discover repository paths from context; ask only about unresolved targets, authentication or allowed environments.

Run `echo-kit --help`. Read `echo-kit --json skills show echo-init` and `echo-kit --json protocol show` for the executing package's documentation without a workspace; add --offline when network-free reads are needed. Initialize with `echo-kit --workspace PATH workspace init` only if absent. Exported skills do not initialize projects automatically.

Reuse existing commands. Shared `echo-kit.toml` and `echo/` scripts belong in project version control; local absolute paths and credential references belong in ignored `.echo-kit/local.toml`. Never place secrets in commands, shared configuration or reports. Do not create project version files or version tasks. Read Skills directly from the executing package; use `skills status DIR` to inspect exported copies, and merge updates without overwriting team rules.

Define only requested capabilities. Projects may be in separate directories; remote services use `mode="external"`. Lab needs neither services nor browsers. Derive service names, middleware configuration and authentication workflows from the target project instead of copying assumptions from another project.

Validate with workspace check and a minimal authorized test. Report configured, verified and missing capabilities separately. Update existing scripts minimally; do not regenerate over team changes. Commit, push and shared-environment changes need their own task authorization.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Before migrating an old entry, finish its active operations and business cleanup.


## Parallel service startup

Service startup defaults to all dependency-eligible selected services concurrently (0.6.0+), without a fixed limit of 4. Use a single `services up` call, not competing CLI processes. Optionally limit concurrency with `[service_startup] concurrency` or use `services up/restart --jobs 1` for serial diagnostics; Verify uses the configured limit. Preserve existing `depends`; project scripts own shared builds and special sequencing. Do not duplicate a project-wide build in each service's `prepare`, or launch unregistered background services from a wrapper. On partial failure, inspect the per-service result and operation timings, then reuse healthy instances; do not automatically roll back or retry business requests. Lab still does not prepare services. See [startup protocol](../references/protocol.md#parallel-service-startup).


## Visible browser

With Kit >= 0.6.0, managed browser debugging is headed by default and returns a local read-only `viewer_url`. Present that link when starting browser work; open it when the user asks to watch. Kit already attempts to open it in the system browser once per container: do not duplicate popups or reopen a viewer the user closed. `browser.auto_open=false` disables this; CI suppresses auto-open unless explicitly overridden. Closing the viewer does not stop the experiment. Use `browser.headless=true` when viewing is not needed; no viewer then exists. Legacy containers require explicit down/up after active operations end; do not rebuild them silently. Preserve project adapters and normal context/trace cleanup. Lab still does not prepare browsers. See [visible browser protocol](../references/protocol.md#visible-browser).
