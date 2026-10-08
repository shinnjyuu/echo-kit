---
name: echo-lab
description: Run and compare Echo Kit experiments when the user explicitly requests an experiment or Lab execution.
---

# Echo Lab

With Kit >= 0.3.0, use the task prefix for `--json docs list` and `docs show shared`; read `docs show private` explicitly when local context is needed. Shared hypotheses, reproducible methods and conclusions belong in tracked documents with relative file links. Personal paths and raw private evidence stay in ignored notes; sanitize conclusions before sharing. Personal notes do not silently override team rules or expand authorization; explain conflicts and follow current user instructions. Run `docs check` after document edits; report unconfigured workspaces without initializing them outside onboarding scope. Never automatically remove tracked private evidence or rewrite history. See [project document rules](../references/protocol.md#shared-and-private-project-documents).

When the project has `echo-kit.py`, start or resume a task and use its returned `command_prefix` in place of `echo-kit --workspace PATH` below. Read Skills via that prefix and `--json skills show NAME` so instructions match the task version. Start once per logical task; finish with `python echo-kit.py --json task finish ID` after runs and cleanup complete, or retain the ID for handoff. Updates are selected only between tasks under project policy. See [task and update rules](../references/protocol.md#tool-updates-and-pinned-tasks). A legacy pinned entry needs a separate one-time migration; do not silently override it during an experiment.

Use the project's existing case and interpreter. Read [the protocol](../references/protocol.md) when adding a case. State the hypothesis and required evidence. Prefer the smallest experiment that answers the user's question; default repeat is one, increase only with a reason or request.

`echo-kit --workspace PATH --json lab run CASE --repeat 1`

`echo-kit --workspace PATH --json lab compare CASE --variants baseline candidate --repeat 3`

Each candidate/repetition gets an independent directory and child process. Keep original failure records; never alter assertions to manufacture success. Explicitly identify mock, real and mixed execution. Process exit success is command-level evidence only; semantic correctness needs declared checks.

Inspect the JSON and `runs show ID` before reporting results. Use `runs compare LEFT RIGHT` for checks and actual metrics. Do not claim a single run establishes stability. Lab does not automatically invoke Workbench.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Finish old Kit operations before upgrading; older versions cannot participate in the new protection.
