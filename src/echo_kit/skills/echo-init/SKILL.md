---
name: echo-init
description: Initialize or maintain Echo Kit project adapters when the user explicitly requests Echo setup or project onboarding.
---

# Echo Init

At first use read `echo-kit guide echo-init` for core rules and the topic directory from the executing package. Re-read when its execution version changes, not every turn. `--offline` reads the usable local package. For exported offline Skills, read [core rules](../references/protocol.md#core-rules) first, then only the required linked sections below. Full reference distribution remains available via skills export to an empty directory; preserve project edits when comparing updates.

## Discover, then read

Read `docs list` and the shared project entry for mandatory team rules; read private notes explicitly only when needed. Use `cases list`, `cases list --category CATEGORY` or `cases list --query TEXT` to find existing experiments. Read `cases show ID` for the chosen explanation and declared prerequisites; these are not proof of readiness. Only read source or raw evidence when modifying or verifying it. Use paginated `runs list --case ID` to find history, then `runs show ID` for the selected record; never substitute a page of history for complete operation/cleanup checks.

## Required topics

Before using a capability, run `echo-kit guide echo-init --topic TOPIC` (or read the linked exported section). This adds the core rules and only the chosen details:

- `configuration`: [shared/local/environment edits](../references/protocol.md#shared-and-local-configuration).
- `cases`: [case discovery](../references/protocol.md#experiment-directory-and-layered-reading) and [adapter contract](../references/protocol.md#experiment-and-verification-subprocess).
- `services`: [shared instances and usage](../references/protocol.md#shared-service-instances), [startup](../references/protocol.md#parallel-service-startup), [lifetime](../references/protocol.md#managed-service-lifetime) and [occupancy](../references/protocol.md#shared-operations-local-user).
- `browser`: [Docker debugging and viewer](../references/protocol.md#visible-browser), [external exceptions](../references/protocol.md#current-guides-and-release-notes) and [tray](../references/protocol.md#windows-tray-viewer-080).
- `auth`: [authentication](../references/protocol.md#authentication-subprocess) and [test identity preparation](../references/test-identities.md), only when authentication is needed.
- `documents`: [shared/private documents](../references/protocol.md#shared-and-private-project-documents) and [directory rules](../references/protocol.md#directory-documentation-and-rules).
- `updates`: [upgrade adaptation](../references/protocol.md#latest-version-execution).

## Workflow

Use only for requested onboarding. Inspect existing project README, build/test/CI/startup files and existing configuration first; reuse project conventions and ask only about unresolved target, authentication or scope. Initialize with `echo-kit --workspace PATH workspace init` only if absent. Define only requested capabilities; do not regenerate team adapters.

Read configuration and cases topics before registering capabilities. For requested authenticated cases read auth, inspect reusable identity preparation and verify login plus required role/tenant; anonymous cases skip this. Prepare identities explicitly, never as an implicit login/startup side effect. Report not-needed, reused, created-and-verified or still-needs-preparation.

Within authorized onboarding, read documents before docs setup; preserve existing entries. Validate with workspace check and one minimal authorized test. Report configured, verified and missing capabilities separately. Keep project entry documents short, add concise case metadata and independent explanations without moving existing scripts.

Before sharing a microservice across workspaces, read services: use an explicit compatible resource_id; keep machine-specific mappings local. Cases default to shared use; choose service_access="exclusive" for non-concurrent data changes. Browser viewers remain workspace-specific. References are not active usage, and old offline Kit sessions do not participate in the new registry.

For module work, read the documents topic and `docs context PATH`; discover related material with `docs map` and explicitly `docs read PATH`. Before writing classify team/personal, project/module and constraint/detailed requirement. Keep global rules at root, local rules with their module, and link detailed requirements. These commands cover the whole workspace, not just echo/. Resolve semantic conflicts explicitly; partial reads are not completed reading. Run `docs check --rules` after rule edits and ordinary `docs check` when registered document entries change.

For update/version mismatches, read the updates topic. Subcommand help follows routing; top-level version/help identify the entry. Explicit offline stays on the calling package. Older entries need a one-time installation upgrade for the 0.11.1 router fix; never repeat a started business command just to change versions.
