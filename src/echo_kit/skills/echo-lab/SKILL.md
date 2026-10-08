---
name: echo-lab
description: Run and compare Echo Kit experiments when the user explicitly requests an experiment or Lab execution.
---

# Echo Lab

With Kit >= 0.4.0, call echo-kit directly. Ordinary commands check for the latest stable compatible release and prepare it in an isolated uv environment before execution. Offline or preparation failure retains the current usable runtime with a notice; never retry a failed business command under another version. No task start/finish, --task, project version lock or policy is needed. Use --offline for intentional offline execution; --version and runtime info describe the installed entry. Migrate old generated entries once using updates setup; preserve project edits and historical records. See [latest-version behavior](../references/protocol.md#latest-version-execution).

With Kit >= 0.3.0, use echo-kit for `--json docs list` and `docs show shared`; read `docs show private` explicitly when local context is needed. Shared hypotheses, reproducible methods and conclusions belong in tracked documents with relative file links. Personal paths and raw private evidence stay in ignored notes; sanitize conclusions before sharing. Personal notes do not silently override team rules or expand authorization; explain conflicts and follow current user instructions. Run `docs check` after document edits; report unconfigured workspaces without initializing them outside onboarding scope. Never automatically remove tracked private evidence or rewrite history. See [project document rules](../references/protocol.md#shared-and-private-project-documents).

Use the project's existing case and interpreter. Read [the protocol](../references/protocol.md) when adding a case. State the hypothesis and required evidence. Prefer the smallest experiment that answers the user's question; default repeat is one, increase only with a reason or request.

`echo-kit --workspace PATH --json lab run CASE --repeat 1`

`echo-kit --workspace PATH --json lab compare CASE --variants baseline candidate --repeat 3`

Each candidate/repetition gets an independent directory and child process. Keep original failure records; never alter assertions to manufacture success. Explicitly identify mock, real and mixed execution. Process exit success is command-level evidence only; semantic correctness needs declared checks.

Inspect the JSON and `runs show ID` before reporting results. Use `runs compare LEFT RIGHT` for checks and actual metrics. Do not claim a single run establishes stability. Lab does not automatically invoke Workbench.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Before migrating an old entry, finish its active operations and business cleanup.


## Parallel service startup

Service startup defaults to 4 concurrent services (0.5.0+). Use a single `services up` call, not competing CLI processes. Configure `[service_startup] concurrency` or use `services up/restart --jobs 1` for serial diagnostics; Verify uses the configured limit. Preserve existing `depends`; project scripts own shared builds and special sequencing. Do not duplicate a project-wide build in each service's `prepare`, or launch unregistered background services from a wrapper. On partial failure, inspect the per-service result and operation timings, then reuse healthy instances; do not automatically roll back or retry business requests. Lab still does not prepare services. See [startup protocol](../references/protocol.md#parallel-service-startup).
