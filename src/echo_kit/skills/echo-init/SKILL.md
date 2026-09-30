---
name: echo-init
description: Initialize or maintain Echo Kit project adapters when the user explicitly requests Echo setup or project onboarding.
---

# Echo project onboarding

Use only for explicit onboarding requests. Inspect existing build, test, CI and launch files before generating anything. Discover repository paths from context; ask only about unresolved targets, authentication or allowed environments.

Run `echo-kit --help`. Initialize a workspace with `echo-kit --workspace PATH workspace init` only if absent. Exported skills do not initialize projects automatically. Read [the protocol](../references/protocol.md) before writing adapters.

Reuse existing commands. Shared `echo-kit.toml` and `echo/` scripts belong in Git; local absolute paths and credential references belong in ignored `.echo-kit/local.toml`. Never place secrets in commands, shared configuration or reports. Pin the team's tested Kit version.

Define only requested capabilities. Projects may be in separate directories; remote services use `mode="external"`. Lab needs neither services nor browsers. Do not copy Magic Cube's service names, Nacos assumptions or captcha workflow.

Validate with workspace check and a minimal authorized test. Report configured, verified and missing capabilities separately. Update existing scripts minimally; do not regenerate over team changes. Commit, push and shared-environment changes need their own task authorization.
