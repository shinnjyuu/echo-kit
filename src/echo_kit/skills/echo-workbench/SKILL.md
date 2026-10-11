---
name: echo-workbench
description: Manage Echo Kit services or perform API and browser verification when the user explicitly requests Workbench or acceptance testing.
---

# Echo Workbench

At first use read `echo-kit guide echo-workbench` for core rules and the topic directory from the executing package. Re-read when its execution version changes, not every turn. `--offline` reads the usable local package. For exported offline Skills, read [core rules](../references/protocol.md#core-rules) first, then only the required linked sections below. Full reference distribution remains available via skills export to an empty directory; preserve project edits when comparing updates.

## Discover, then read

Read `docs list` and the shared project entry for mandatory team rules; read private notes explicitly only when needed. Use `cases list`, `cases list --category CATEGORY` or `cases list --query TEXT` to find existing experiments. Read `cases show ID` for the chosen explanation and declared prerequisites; these are not proof of readiness. Only read source or raw evidence when modifying or verifying it. Use paginated `runs list --case ID` to find history, then `runs show ID` for the selected record; never substitute a page of history for complete operation/cleanup checks.

## Required topics

Before using a capability, run `echo-kit guide echo-workbench --topic TOPIC` (or read the linked exported section). This adds the core rules and only the chosen details:

- `configuration`: [shared/local/environment edits](../references/protocol.md#shared-and-local-configuration).
- `cases`: [case discovery](../references/protocol.md#experiment-directory-and-layered-reading) and [adapter contract](../references/protocol.md#experiment-and-verification-subprocess).
- `services`: [startup](../references/protocol.md#parallel-service-startup), [lifetime](../references/protocol.md#managed-service-lifetime) and [occupancy](../references/protocol.md#shared-operations-local-user).
- `browser`: [Docker debugging and viewer](../references/protocol.md#visible-browser), [external exceptions](../references/protocol.md#current-guides-and-release-notes) and [tray](../references/protocol.md#windows-tray-viewer-080).
- `auth`: [authentication](../references/protocol.md#authentication-subprocess) and [test identity preparation](../references/test-identities.md), only when authentication is needed.
- `documents`: [shared/private documents](../references/protocol.md#shared-and-private-project-documents).
- `updates`: [upgrade adaptation](../references/protocol.md#latest-version-execution).

## Workflow

Inspect the intended workspace/environment and selected case. Read cases plus topics for its declared dependencies before execution. `verify run CASE` prepares only declared services/auth/browser; use services/auth/browser commands independently when appropriate. Start only needed dependencies; a frontend may use remote APIs. Do not automatically run Lab.

Leave owned services available for reuse. On resuming work, check operations and readiness before starting stopped resources; host exit is neither guaranteed survival nor cleanup. Before authenticated verification, read auth and confirm identity preparation, role and tenant separately from login success. Read browser before frontend work: prefer Kit-managed Docker, preserve explicit external exceptions and do not silently fall back.

Inspect the selected run and required evidence. Distinguish HTTP success, visible page, downloaded file, verified content and delivery; report failed and unverified checks honestly.
