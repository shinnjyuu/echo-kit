---
name: echo-init
description: Initialize or maintain Echo Kit project adapters when the user explicitly requests Echo setup or project onboarding.
---

# Echo project onboarding

Use only for explicit onboarding requests. Inspect existing build, test, CI and launch files before generating anything. Discover repository paths from context; ask only about unresolved targets, authentication or allowed environments.

Run `echo-kit --help`. Read `echo-kit --json skills show echo-init` and `echo-kit --json protocol show` for the running package's documentation without a workspace or network. Initialize with `echo-kit --workspace PATH workspace init` only if absent. Exported skills do not initialize projects automatically.

For onboarding with Kit >= 0.2.0, run `echo-kit --workspace PATH --json updates setup --policy patch` to create the project entrypoint and single version file. Respect existing team pins: use `manual` for strict version policies, and migrate old fixed invocations only within the requested scope after their operations finish. Preserve project-owned entrypoint edits. See [tool updates and pinned tasks](../references/protocol.md#tool-updates-and-pinned-tasks) for policies, migration and offline behavior.

Start a task from the workspace with `python echo-kit.py --json task start --label "purpose"`. Reuse its returned `command_prefix` for all work and documentation reads, including across resumed conversations. End with `python echo-kit.py --json task finish ID` after associated cleanup. Do not create a new task after every command, leave completed tasks open, or remove records to bypass deferred updates. Record the task ID when handing off incomplete work.

Reuse existing commands. Shared `echo-kit.toml`, `echo/` scripts, `echo-kit.py` and `echo-kit.lock.json` belong in project version control; local absolute paths and credential references belong in ignored `.echo-kit/local.toml`. Never place secrets in commands, shared configuration or reports. Keep version selection in the version file instead of repeating it in every instruction. Read task-version Skills directly; use `skills status DIR` to inspect exported copies, and merge updates without overwriting team rules.

Define only requested capabilities. Projects may be in separate directories; remote services use `mode="external"`. Lab needs neither services nor browsers. Derive service names, middleware configuration and authentication workflows from the target project instead of copying assumptions from another project.

Validate with workspace check and a minimal authorized test. Report configured, verified and missing capabilities separately. Update existing scripts minimally; do not regenerate over team changes. Commit, push and shared-environment changes need their own task authorization.

Before changing shared services or running a case, inspect `operations list` and `services status`. Supply `--actor "task description"` so other sessions can identify the work. On `reason=resource_busy`, read the occupant with `operations show ID`, explain what is occupied, and retry later only if useful; do not force a restart or remove journal/lock files. There is no automatic queue.

For `needs_cleanup`, use the original run's `runs cleanup ID`. Only use `operations resolve ID --note "confirmed outcome"` after the user explicitly confirms cleanup and all recorded execution processes have exited. Never release another session merely to continue. Do not reinterpret unresolved work as success.

Report the running service's launch/artifact identity separately from current source. Source/hot-reload services are not version-pinned: coordinate edits or use an explicitly configured fixed artifact; do not silently disable version checks. Finish old Kit operations before upgrading; older versions cannot participate in the new protection.
