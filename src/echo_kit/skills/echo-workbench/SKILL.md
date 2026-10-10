---
name: echo-workbench
description: Manage Echo Kit services or perform API and browser verification when the user explicitly requests Workbench or acceptance testing.
---

# Echo Workbench

At first Kit use in a new AI session, read `echo-kit guide echo-workbench` (add `--json` before guide for structured output). It returns this Skill, the public protocol and current release notes from one executing package, without a workspace. Re-read when the actual execution version changes, not every turn. `--offline` reads the usable local package; `--version`/`runtime info` describe the installed entry, not necessarily business execution. Use `echo-kit updates notes` or `updates notes --since VERSION` when the previous version is known. Reading a notice does not complete project adaptation; do not write acknowledgement files.

Recommend Kit-managed Docker browsers for frontend debugging. Do not discover and save an external endpoint during onboarding or silently fall back when Docker/build fails. Existing `browser.endpoint` is an explicit project exception: preserve it, explain that Kit does not manage its image/display or guarantee a read-only viewer, and assess whether it meets the user's viewing needs. To switch, inspect shared/local/environment configuration sources and `operations list`; adjust configuration within authorization, then `browser up`. Never stop, delete or take over the external instance.

Project documents hold project facts and team rules; private notes hold personal machine facts without expanding authorization. When a project Skill entry is needed, make it a short pointer to `echo-kit guide echo-workbench`, not a copied Kit manual. To tidy an existing copy, preserve project facts and team rules, replace generic Kit instructions deliberately, and never overwrite existing documents. Full `skills export` remains available for explicit offline distribution: export to an empty directory, compare and merge edits.

With Kit >= 0.4.0, call echo-kit directly. Ordinary commands check for the latest stable compatible release and prepare it in an isolated uv environment before execution. Offline or preparation failure retains the current usable runtime with a notice; never retry a failed business command under another version. No task start/finish, --task, project version lock or policy is needed. Use --offline for intentional offline execution; --version and runtime info describe the installed entry. Migrate old generated entries once using updates setup; preserve project edits and historical records. See [latest-version behavior](../references/protocol.md#latest-version-execution).

With Kit >= 0.3.0, use echo-kit for `--json docs list` and `docs show shared`; explicitly read `docs show private` when personal environment context is needed. Keep team workflows and reproducible acceptance conclusions in tracked documents with relative file references. Local paths and private evidence stay in ignored notes; extract sanitized conclusions before sharing. Private notes supplement team guidance, never silently override it or authorize operations; explain conflicts and follow current user instructions. Run `docs check` after document changes. Missing document setup does not authorize onboarding or migration during acceptance work. Report tracked private files for deliberate remediation without deleting evidence or rewriting history. See [project document rules](../references/protocol.md#shared-and-private-project-documents).

Inspect workspace/environment and the requested case first. Read [the protocol](../references/protocol.md) before changing adapters. Start only declared dependencies; a frontend may use remote APIs and QA may need no local business services.

Use `services status`, `services up NAME`, `auth login NAME`, `browser up` independently as needed. `verify run CASE` prepares only that case's declared capabilities. Global options precede the module.

Leave owned services running after a case for reuse. Never adopt or kill an unknown process, silently change shared configuration, or bypass normal authentication. An unavailable secure vault means current-invocation auth only. Browser and HTTP account/environment must match.

Local managed services may stop when the launching AI application or terminal exits, updates or crashes; they may also remain running. This is a design-allowed boundary, with neither guaranteed survival nor automatic cleanup. When resuming, follow the [service lifetime rules](../references/protocol.md#managed-service-lifetime): inspect operations and readiness, reuse healthy instances, and start only needed stopped services within authorized scope after resolving associated unfinished work. Host or service exit does not confirm business task completion.

For asynchronous business work, adapters write only this run's task IDs to ECHO_ACTIVE and provide a cleanup command that checks terminal state. A timeout never triggers automatic resubmission. Inspect retained active records and use `runs cleanup ID`; do not delete markers to pretend cleanup succeeded.

Report required checks, failures and unverified scope with evidence. HTTP success, page display, file download, file contents and client delivery are different claims. Screenshots and traces can contain business data; review before sharing. This skill does not require or automatically run Lab.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Before migrating an old entry, finish its active operations and business cleanup.


## Test identity prerequisite

For authenticated cases, locate the project's explicit account preparation entry and confirm the required identity, role and tenant are usable before verification. Reuse existing accounts; login success alone does not prove business permissions. If preparation is missing, follow [test identity preparation](../references/test-identities.md) within the user's scope, or report the prerequisite as pending. Do not silently provision/reset accounts inside Verify or auth login. Keep secrets in the existing private authentication channel, and clean up only temporary identities created by the current run.


## Parallel service startup

Service startup defaults to all dependency-eligible selected services concurrently (0.6.0+), without a fixed limit of 4. Use a single `services up` call, not competing CLI processes. Optionally limit concurrency with `[service_startup] concurrency` or use `services up/restart --jobs 1` for serial diagnostics; Verify uses the configured limit. Preserve existing `depends`; project scripts own shared builds and special sequencing. Do not duplicate a project-wide build in each service's `prepare`, or launch unregistered background services from a wrapper. On partial failure, inspect the per-service result and operation timings, then reuse healthy instances; do not automatically roll back or retry business requests. Lab still does not prepare services. See [startup protocol](../references/protocol.md#parallel-service-startup).


## Visible browser

With Kit >= 0.6.0, managed browser debugging is headed by default and returns a local read-only `viewer_url`. Present that link when starting browser work; open it when the user asks to watch. Kit already attempts to open it in the system browser once per container: do not duplicate popups or reopen a viewer the user closed. `browser.auto_open=false` disables this; CI suppresses auto-open unless explicitly overridden. Closing the viewer does not stop the experiment. Use `browser.headless=true` when viewing is not needed; no viewer then exists. Legacy containers require explicit down/up after active operations end; do not rebuild them silently. Preserve project adapters and normal context/trace cleanup. Lab still does not prepare browsers. See [visible browser protocol](../references/protocol.md#visible-browser).

## Windows tray viewer (0.8.0+)

On an interactive Windows desktop, starting/reusing managed services or a managed browser (including Verify preparation) automatically offers a per-session tray entry. Close a noVNC tab freely, then choose the project and Open debugging viewer from the tray to view the same experiment again; do not ask AI to restart it. The tray checks process identity and container ownership only, not application readiness, and never executes project health commands. It provides no stop/restart/cleanup actions for business resources.

`echo-kit tray start` registers the current workspace, including existing owned resources. `echo-kit --json tray status` and `echo-kit tray stop` need no workspace. Stop/Exit closes only the tray. A later startup operation can restore it, but the same operation cannot override a manual dismissal. After all resources are confirmed stopped, the tray exits after a 30-second grace; unknown states retain it. External instances are excluded; headless/legacy browsers have no viewer. CI, noninteractive desktops and `ECHO_KIT_TRAY=0` disable automatic startup. Other platforms keep existing CLI/viewer URLs. No boot service or service watchdog is installed. A restrictive Windows Job can prevent independent UI startup: report the notice and retain the viewer link, without failing the experiment. Windows may place the icon in its overflow area.
