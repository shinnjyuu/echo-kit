---
name: echo-workbench
description: Manage Echo Kit services or perform API and browser verification when the user explicitly requests Workbench or acceptance testing.
---

# Echo Workbench

Inspect workspace/environment and the requested case first. Read [the protocol](../references/protocol.md) before changing adapters. Start only declared dependencies; a frontend may use remote APIs and QA may need no local business services.

Use `services status`, `services up NAME`, `auth login NAME`, `browser up` independently as needed. `verify run CASE` prepares only that case's declared capabilities. Global options precede the module.

Keep services running after a case. Never adopt or kill an unknown process, silently change shared configuration, or bypass normal authentication. An unavailable secure vault means current-invocation auth only. Browser and HTTP account/environment must match.

For asynchronous business work, adapters write only this run's task IDs to ECHO_ACTIVE and provide a cleanup command that checks terminal state. A timeout never triggers automatic resubmission. Inspect retained active records and use `runs cleanup ID`; do not delete markers to pretend cleanup succeeded.

Report required checks, failures and unverified scope with evidence. HTTP success, page display, file download, file contents and client delivery are different claims. Screenshots and traces can contain business data; review before sharing. This skill does not require or automatically run Lab.
