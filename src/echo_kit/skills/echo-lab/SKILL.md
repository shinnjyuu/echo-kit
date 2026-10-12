---
name: echo-lab
description: Run and compare Echo Kit experiments when the user explicitly requests an experiment or Lab execution.
---

# Echo Lab

At first use read `echo-kit guide echo-lab` for core rules and the topic directory from the executing package. Re-read when its execution version changes, not every turn. `--offline` reads the usable local package. For exported offline Skills, read [core rules](../references/protocol.md#core-rules) first, then only the required linked sections below. Full reference distribution remains available via skills export to an empty directory; preserve project edits when comparing updates.

## Discover, then read

Read `docs list` and the shared project entry for mandatory team rules; read private notes explicitly only when needed. Use `cases list`, `cases list --category CATEGORY` or `cases list --query TEXT` to find existing experiments. Read `cases show ID` for the chosen explanation and declared prerequisites; these are not proof of readiness. Only read source or raw evidence when modifying or verifying it. Use paginated `runs list --case ID` to find history, then `runs show ID` for the selected record; never substitute a page of history for complete operation/cleanup checks.

## Required topics

Before using a capability, run `echo-kit guide echo-lab --topic TOPIC` (or read the linked exported section). This adds the core rules and only the chosen details:

- `configuration`: [shared/local/environment edits](../references/protocol.md#shared-and-local-configuration).
- `cases`: [case discovery](../references/protocol.md#experiment-directory-and-layered-reading) and [adapter contract](../references/protocol.md#experiment-and-verification-subprocess).
- `services`: [shared instances and usage](../references/protocol.md#shared-service-instances), [startup](../references/protocol.md#parallel-service-startup), [lifetime](../references/protocol.md#managed-service-lifetime) and [occupancy](../references/protocol.md#shared-operations-local-user).
- `browser`: [Docker debugging and viewer](../references/protocol.md#visible-browser), [external exceptions](../references/protocol.md#current-guides-and-release-notes) and [tray](../references/protocol.md#windows-tray-viewer-080).
- `auth`: [authentication](../references/protocol.md#authentication-subprocess) and [test identity preparation](../references/test-identities.md), only when authentication is needed.
- `documents`: [shared/private documents](../references/protocol.md#shared-and-private-project-documents).
- `updates`: [upgrade adaptation](../references/protocol.md#latest-version-execution).

## Workflow

Use the project's existing case/interpreter. State the hypothesis and required evidence; choose the smallest experiment that answers it. Lab does not automatically prepare services, authentication or browsers, and does not automatically invoke Workbench.

After discovery and required topic reads, use `echo-kit --workspace PATH --actor "experiment purpose" --json lab run CASE --repeat 1`; increase repetitions only for a reason. `lab compare CASE --variants baseline candidate --repeat 3` compares explicit project variants. Each candidate/repetition uses independent records. Inspect the selected JSON and `runs show ID`; use `runs compare LEFT RIGHT` for checks and metrics. One run does not establish stability. Read cases before adding/changing adapters or interpreting their output.

Before sharing a microservice across workspaces, read services: use an explicit compatible resource_id; keep machine-specific mappings local. Cases default to shared use; choose service_access="exclusive" for non-concurrent data changes. Browser viewers remain workspace-specific. References are not active usage, and old offline Kit sessions do not participate in the new registry.
