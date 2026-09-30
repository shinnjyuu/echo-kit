---
name: echo-lab
description: Run and compare Echo Kit experiments when the user explicitly requests an experiment or Lab execution.
---

# Echo Lab

Use the project's existing case and interpreter. Read [the protocol](../references/protocol.md) when adding a case. State the hypothesis and required evidence. Prefer the smallest experiment that answers the user's question; default repeat is one, increase only with a reason or request.

`echo-kit --workspace PATH --json lab run CASE --repeat 1`

`echo-kit --workspace PATH --json lab compare CASE --variants baseline candidate --repeat 3`

Each candidate/repetition gets an independent directory and child process. Keep original failure records; never alter assertions to manufacture success. Explicitly identify mock, real and mixed execution. Process exit success is command-level evidence only; semantic correctness needs declared checks.

Inspect the JSON and `runs show ID` before reporting results. Use `runs compare LEFT RIGHT` for checks and actual metrics. Do not claim a single run establishes stability. Lab does not automatically invoke Workbench.
