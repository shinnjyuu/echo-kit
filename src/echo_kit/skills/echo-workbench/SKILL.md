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
- `services`: [shared instances and usage](../references/protocol.md#shared-service-instances), [startup](../references/protocol.md#parallel-service-startup), [lifetime](../references/protocol.md#managed-service-lifetime) and [occupancy](../references/protocol.md#shared-operations-local-user).
- `browser`: [Docker debugging and viewer](../references/protocol.md#visible-browser), [external exceptions](../references/protocol.md#current-guides-and-release-notes) and [tray](../references/protocol.md#windows-tray-viewer-080).
- `auth`: [authentication](../references/protocol.md#authentication-subprocess) and [test identity preparation](../references/test-identities.md), only when authentication is needed.
- `documents`: [shared/private documents](../references/protocol.md#shared-and-private-project-documents) and [directory rules](../references/protocol.md#directory-documentation-and-rules).
- `updates`: [upgrade adaptation](../references/protocol.md#latest-version-execution).

## Workflow

Inspect the intended workspace/environment and selected case. Read cases plus topics for its declared dependencies before execution. `verify run CASE` prepares only declared services/auth/browser; use services/auth/browser commands independently when appropriate. Start only needed dependencies; a frontend may use remote APIs. Do not automatically run Lab.

Leave owned services available for reuse. On resuming work, check operations and readiness before starting stopped resources; host exit is neither guaranteed survival nor cleanup. Before authenticated verification, read auth and confirm identity preparation, role and tenant separately from login success. Read browser before frontend work: prefer Kit-managed Docker, preserve explicit external exceptions and do not silently fall back.

Inspect the selected run and required evidence. Distinguish HTTP success, visible page, downloaded file, verified content and delivery; report failed and unverified checks honestly.

Before sharing a microservice across workspaces, read services: use an explicit compatible resource_id; keep machine-specific mappings local. Cases default to shared use; choose service_access="exclusive" for non-concurrent data changes. Browser viewers remain workspace-specific. References are not active usage, and old offline Kit sessions do not participate in the new registry.

For module work, read the documents topic and `docs context PATH`; discover related material with `docs map` and explicitly `docs read PATH`. Before writing classify team/personal, project/module and constraint/detailed requirement. Keep global rules at root, local rules with their module, and link detailed requirements. These commands cover the whole workspace, not just echo/. Resolve semantic conflicts explicitly; partial reads are not completed reading. Run `docs check --rules` after rule edits and ordinary `docs check` when registered document entries change.

For update/version mismatches, read the updates topic. Use --version and subcommand help for routed execution; runtime info and --offline --version identify the local entry. Actual experiments use the version recorded in their run. Explicit offline stays on the calling package. Older entries need a one-time installation upgrade for the 0.12.3 version-query behavior; never repeat a started business command just to change versions.

Keep small workspaces in one TOML file. For module-owned configuration, read `configuration` / [modular configuration](../references/protocol.md#modular-configuration); discover a case and inspect `cases show` configuration_sources before editing. Shared includes do not change IDs or path bases; machine overrides stay in local.toml. Never split or migrate a business project implicitly.

For stale occupancy, inspect `operations show ID` and the evidence. After confirming recorded execution processes and children have exited, resolve the stale reservation with `operations resolve ID --note ...` and continue without a separate approval prompt. Record checks and reason; this does not certify business cleanup. Use an available cleanup adapter when needed, preserve outstanding cleanup facts, and do not infer remote task completion from local exit. Ask only for uncertain state, a necessary user decision or actions beyond existing authorization.
