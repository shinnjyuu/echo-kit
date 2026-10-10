---
name: echo-init
description: Initialize or maintain Echo Kit project adapters when the user explicitly requests Echo setup or project onboarding.
---

# Echo project onboarding

At first Kit use in a new AI session, read `echo-kit guide echo-init` (add `--json` before guide for structured output). It returns this Skill, the public protocol and current release notes from one executing package, without a workspace. Re-read when the actual execution version changes, not every turn. `--offline` reads the usable local package; `--version`/`runtime info` describe the installed entry, not necessarily business execution. Use `echo-kit updates notes` or `updates notes --since VERSION` when the previous version is known. Reading a notice does not complete project adaptation; do not write acknowledgement files.

Recommend Kit-managed Docker browsers for frontend debugging. Do not discover and save an external endpoint during onboarding or silently fall back when Docker/build fails. Existing `browser.endpoint` is an explicit project exception: preserve it, explain that Kit does not manage its image/display or guarantee a read-only viewer, and assess whether it meets the user's viewing needs. To switch, inspect shared/local/environment configuration sources and `operations list`; adjust configuration within authorization, then `browser up`. Never stop, delete or take over the external instance.

Project documents hold project facts and team rules; private notes hold personal machine facts without expanding authorization. When a project Skill entry is needed, make it a short pointer to `echo-kit guide echo-init`, not a copied Kit manual. To tidy an existing copy, preserve project facts and team rules, replace generic Kit instructions deliberately, and never overwrite existing documents. Full `skills export` remains available for explicit offline distribution: export to an empty directory, compare and merge edits.

With Kit >= 0.4.0, call echo-kit directly. Ordinary commands check for the latest stable compatible release and prepare it in an isolated uv environment before execution. Offline or preparation failure retains the current usable runtime with a notice; never retry a failed business command under another version. No task start/finish, --task, project version lock or policy is needed. Use --offline for intentional offline execution; --version and runtime info describe the installed entry. Migrate old generated entries once using updates setup; preserve project edits and historical records. See [latest-version behavior](../references/protocol.md#latest-version-execution).

For Kit >= 0.3.0, discover project documents with `--json docs list` directly with echo-kit. During authorized onboarding, use `docs setup` if absent; preserve existing entries or configure them instead of relocating documents. Read `docs show shared`, and explicitly read `docs show private` only as needed. Shared knowledge uses relative file links and belongs in Git; local paths and personal evidence belong in ignored private notes. Personal notes supplement team rules, never silently override them or expand authorization. Extract sanitized conclusions before sharing private material; keep shared instructions independently reproducible. After document edits run `docs check`; initial untracked shared files return unverified. Do not auto-delete tracked private files or rewrite Git history. See [project document rules](../references/protocol.md#shared-and-private-project-documents) for configuration, checking boundaries and conflicts.

Use only for explicit onboarding requests. Inspect existing build, test, CI and launch files before generating anything. Discover repository paths from context; ask only about unresolved targets, authentication or allowed environments.

Run `echo-kit --help`. Read `echo-kit --json guide echo-init` for the executing package's documentation without a workspace; add --offline when network-free reads are needed. Initialize with `echo-kit --workspace PATH workspace init` only if absent. Exported skills do not initialize projects automatically.

Reuse existing commands. Shared `echo-kit.toml` and `echo/` scripts belong in project version control; local absolute paths and credential references belong in ignored `.echo-kit/local.toml`. Never place secrets in commands, shared configuration or reports. Do not create project version files or version tasks. Read Skills directly from the executing package; use `skills status DIR` to inspect exported copies, and merge updates without overwriting team rules.

Define only requested capabilities. Projects may be in separate directories; remote services use `mode="external"`. Lab needs neither services nor browsers. Derive service names, middleware configuration and authentication workflows from the target project instead of copying assumptions from another project.

Validate with workspace check and a minimal authorized test. Report configured, verified and missing capabilities separately. Update existing scripts minimally; do not regenerate over team changes. Commit, push and shared-environment changes need their own task authorization.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Before migrating an old entry, finish its active operations and business cleanup.


## Prepare test identities when needed

During onboarding, identify which requested cases need login, roles, permissions and tenant membership; skip anonymous cases. Inspect existing seeds, fixtures, registration APIs, management commands and preparation scripts first. Reuse them; when absent, write a repeatable project script in the existing script directory or echo/. Follow [test identity preparation](../references/test-identities.md) for pseudocode, sanitized output and handoff checks.

Within existing authorization and an exact allowed target, explicitly prepare the identity, authenticate through the existing adapter, then verify the required role/tenant with a minimal check. Report not-needed, reused, created-and-verified or still-needs-preparation; writing a script is not verification. Never hide provisioning inside login, service startup or Verify. Repeated calls reuse matching accounts; mismatches do not authorize password resets, privilege expansion or takeover. Shared environments require configured targets and task authorization. Keep credentials out of arguments/documents/reports; reusable identities remain, while case-owned temporary IDs use the existing project cleanup path and retain failure evidence.


## Parallel service startup

Service startup defaults to all dependency-eligible selected services concurrently (0.6.0+), without a fixed limit of 4. Use a single `services up` call, not competing CLI processes. Optionally limit concurrency with `[service_startup] concurrency` or use `services up/restart --jobs 1` for serial diagnostics; Verify uses the configured limit. Preserve existing `depends`; project scripts own shared builds and special sequencing. Do not duplicate a project-wide build in each service's `prepare`, or launch unregistered background services from a wrapper. On partial failure, inspect the per-service result and operation timings, then reuse healthy instances; do not automatically roll back or retry business requests. Lab still does not prepare services. See [startup protocol](../references/protocol.md#parallel-service-startup).


## Visible browser

With Kit >= 0.6.0, managed browser debugging is headed by default and returns a local read-only `viewer_url`. Present that link when starting browser work; open it when the user asks to watch. Kit already attempts to open it in the system browser once per container: do not duplicate popups or reopen a viewer the user closed. `browser.auto_open=false` disables this; CI suppresses auto-open unless explicitly overridden. Closing the viewer does not stop the experiment. Use `browser.headless=true` when viewing is not needed; no viewer then exists. Legacy containers require explicit down/up after active operations end; do not rebuild them silently. Preserve project adapters and normal context/trace cleanup. Lab still does not prepare browsers. See [visible browser protocol](../references/protocol.md#visible-browser).

## Windows tray viewer (0.8.0+)

On an interactive Windows desktop, starting/reusing managed services or a managed browser (including Verify preparation) automatically offers a per-session tray entry. Close a noVNC tab freely, then choose the project and Open debugging viewer from the tray to view the same experiment again; do not ask AI to restart it. The tray checks process identity and container ownership only, not application readiness, and never executes project health commands. It provides no stop/restart/cleanup actions for business resources.

`echo-kit tray start` registers the current workspace, including existing owned resources. `echo-kit --json tray status` and `echo-kit tray stop` need no workspace. Stop/Exit closes only the tray. A later startup operation can restore it, but the same operation cannot override a manual dismissal. After all resources are confirmed stopped, the tray exits after a 30-second grace; unknown states retain it. External instances are excluded; headless/legacy browsers have no viewer. CI, noninteractive desktops and `ECHO_KIT_TRAY=0` disable automatic startup. Other platforms keep existing CLI/viewer URLs. No boot service or service watchdog is installed. A restrictive Windows Job can prevent independent UI startup: report the notice and retain the viewer link, without failing the experiment. Windows may place the icon in its overflow area.


## Configuration ownership

Before editing configuration, inspect shared, local and selected environment sources and classify each value. Keep reusable startup commands/scripts, relative directories, dependencies, readiness rules and team default ports shared. Put machine-specific port allocations, absolute paths, mirror sources and local endpoints in ignored, untracked `.echo-kit/local.toml`, using minimal overrides. Match the environment table when it supplies the value; local root values do not always win. Reconcile startup inputs, declared ports, readiness and consuming URLs; verify effective non-secret values and necessary startup behavior. Check both Git tracking and ignore rules; report tracked private files without automatic deletion/history rewriting. Preserve team defaults and existing edits; secrets use existing injection/authentication, runtime identities stay in registration files. Follow the [paired examples and precedence rules](../references/protocol.md#shared-and-local-configuration).
