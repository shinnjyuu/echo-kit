# Echo Kit

[简体中文](README.md) | **English**

**Give your AI coding assistant reusable ways to experiment, debug, and verify your project.**

Echo Kit is a toolkit for AI coding assistants. It helps them reuse a project's startup and testing workflows, run checks, and keep results, logs, and files as evidence. A new AI session or another teammate can pick up the verification methods already established in the project.

This page is for people discovering Echo Kit for the first time. AI assistants setting up a project should read the [AI onboarding guide](README.ai.md) (Chinese).

## What can it help me do?

Suppose your AI has just fixed a file export feature. You still want to know: does the page work? Was the file actually downloaded? Is its content correct?

Once the relevant verification case is set up, you can simply tell your AI:

> Use Echo Kit to verify this export change. Check the page interaction, file download, and file contents, then show me the results and evidence.

The AI can follow the project's established workflow, prepare the services it needs, run the checks, and explain what passed, failed, or remains unverified. The same workflow is available the next time you change that feature.

| What you want to do | How Echo Kit helps |
| --- | --- |
| Compare algorithms, parameters, or implementations | Run candidates separately, retain each result, and compare checks and metrics |
| Verify a feature change | Prepare the services, authentication, and browser declared by a case, then collect API or page check results |
| Let future AI sessions and teammates continue the work | Keep configuration and verification scripts in the project, with inspectable run records |
| Work across multiple AI sessions | Detect conflicts over services and other resources on the same machine under the same OS user |
| Use current tooling and instructions | Ordinary commands check for and use the latest stable release; offline or preparation failures keep the current usable version (since 0.4.0) |

The project's own verification scripts define what to check and what counts as correct. During onboarding, the AI reuses or extends those scripts. Experiments, service management, and acceptance checks can each be used as needed.

## First use: copy this prompt to your AI

**You do not need to install Echo Kit or clone this repository first.** Open your own project in an AI coding assistant that can access the internet, read and write project files, and execute commands. Send it the entire prompt below, which includes the repository address.

```text
Set up Echo Kit for the current project so future AI sessions can reuse
its experiment, debugging, and verification workflows.

Official repository: https://github.com/shinnjyuu/echo-kit
First read README.ai.md at the repository root and follow its instructions
to obtain the tool. Then follow the echo-init Skill and public protocol
bundled with the version you use.
Do not assume Echo Kit or its runtime is already installed. Check the local
environment, install the required tools according to the official instructions,
and record the version actually used.

First understand the project's development conventions, directory structure,
startup commands, and existing tests. Reuse existing commands and scripts
where possible. If onboarding is partially complete, build on it and preserve
team changes. Choose capabilities based on this project's needs, and get
one small, useful local verification workflow working first.

Leave reusable configuration, adapters, and instructions for future AI sessions
in the project. Call the latest Echo Kit directly by default; no project version
files or version-task lifecycle are required. Handle local paths and credentials
according to the protocol. Ask me only about critical onboarding decisions
that cannot be determined from the project.
Obtain separate authorization before operating shared business environments.
Do not commit or push code.

When finished, explain what you set up, what you actually verified, what remains
unfinished, where the evidence is, and how I can ask an AI to use these workflows
in the future.
```

The AI will inspect the project and local environment, install the tool, configure the needed capabilities, and run a minimal verification. You only need to supply information it cannot infer from the project, such as the intended test environment or authentication method.

## What will my project gain?

- **Reusable verification workflows:** configuration and scripts that later sessions can call again.
- **Results backed by evidence:** checks and reports for each run, with optional screenshots, downloaded files, and other artifacts.
- **Clear handoff instructions:** which capabilities are available, what has actually been tested, and how to use them next time.

After the relevant capabilities are set up, you can ask your AI:

> Use Echo Kit to compare the current implementation with the candidate. Run each three times and show me the metric differences.

> Run this project's existing API acceptance checks with Echo Kit. Explain what passed, failed, and remains unverified.

> Review the last failed run and use its logs and check results to investigate the problem.

The project continues to own its business dependencies and development workflow. Frontend, backend, and verification scripts can live in separate directories, and cases can connect to existing external services.

## Requirements and current scope

Echo Kit requires Python 3.11+; your project can use other languages. Your AI can help install the tool and its runtime. Ordinary experiments and API verification do not require Docker. Docker is needed for the Kit-managed browser.

**Managed local services may stop when the AI application exits, updates, or crashes. This is an allowed lifecycle outcome.** Echo Kit supports reusing services after a command finishes, but does not guarantee that they survive their host application exiting or that they are automatically cleaned up when it exits. A later AI session should inspect service status before reusing or starting them. See the [service lifecycle rules](docs/protocol.md#managed-service-lifetime).

Current validation evidence covers Windows and Linux containers; macOS has not been verified. Authentication must follow the project's normal login flow. Screenshots, logs, and downloads may contain business data: review them before sharing.

Verification conclusions cover only the checks actually executed. Kit distinguishes failed, blocked, and unverified outcomes. Historical records help explain what happened, but do not guarantee identical results on a rerun.

## Shared and private project documentation

Since 0.3.0, Echo Kit distinguishes team-shared documents from personal, private documents. Shared documents use relative paths and belong in Git. Private documents can use absolute local paths and must stay out of Git. AI assistants can discover entries with `docs list`, read them with `docs show`, and validate them with `docs check`. Existing projects can opt in with `docs setup`, which preserves existing files. See the [project documentation guide](docs/project-documents.md) (Chinese).

## Further reading

The public protocol is in English; the other guides below are currently in Chinese.

- [AI onboarding guide](README.ai.md): from a fresh installation to project setup, also useful for developers configuring the tool manually.
- [Standalone demo](docs/demo.md): try experiments, API verification, and browser verification in a sample project.
- [Public protocol](docs/protocol.md): write project adapters, configure authentication, and manage run results.
- [Latest-version execution and legacy entry migration](docs/updates.md): startup checks, offline fallback, and documentation updates.
- [Validation records](docs/validation.md) and [multi-session validation](docs/operations-validation.md): tested behavior and platform limitations.
- [Publishing guide](docs/publishing.md): build and release instructions for maintainers.

## Start multiple services together

Since 0.6.0, `echo-kit services up` starts all selected services whose dependencies are ready concurrently. The AI does not need to start them one at a time. Existing instances are reused; failures retain already-started services and report what went wrong. Use `services up --jobs 1` for serial troubleshooting, or set an explicit limit such as `[service_startup] concurrency = 4` when resources are constrained.

Project scripts own shared builds and special startup sequences. Kit manages service processes, waits for readiness, and records timings. See the [startup rules](docs/protocol.md#parallel-service-startup).

## Watch the page your AI is debugging

Since 0.6.0, managed browsers run in headed mode by default. `echo-kit browser up` or a browser verification case starts the Docker browser and attempts to open a read-only viewer in your system's default browser. You see the same desktop the AI is operating. There is no Edge requirement and no VNC client to install. The viewer cannot send mouse or keyboard input, so it does not interfere with the experiment.

First use builds and caches a browser image, requiring internet access to download dependencies and taking longer than subsequent starts. Kit attempts to open the viewer only once per container. Closing it does not stop the experiment or cause it to reopen automatically. You can reopen the returned `viewer_url` at any time. `browser down` stops and removes the owned container, subject to active-operation protections.

```toml
[browser]
headless = false    # Headed by default; use true for runs that need no viewer
# auto_open = false  # Disable automatic opening; it is off by default in CI
# viewer_port = 19324
```

Existing containers do not silently change modes. After active experiments finish, follow the migration prompt to run `browser down`, then `browser up`. See the [visible browser rules](docs/protocol.md#visible-browser).

## Current guides and upgrade guidance

On first Kit use in an AI session, read `echo-kit guide echo-init`, `echo-kit guide echo-lab` or `echo-kit guide echo-workbench`. Read again when the execution version changes. Each returns a compact Skill, core rules, topic directory, release notes and actual version from one package; use `--topic TOPIC` for details or `--full` for the full protocol and references; JSON and workspace-free reads are supported. Ordinary calls check the latest release; `--offline` reads the usable package. Installed-entry diagnostics (`--version`, `runtime info`) are not business execution provenance.

Use `echo-kit updates notes` for this release or `echo-kit updates notes --since 0.5.0` for later bundled history. An upgrade notice does not mean the project has been adapted.

Prefer the Kit-managed Docker browser and read-only noVNC viewer. Report Docker/build failures instead of falling back to an external endpoint. Existing `browser.endpoint` remains an explicit exception: Kit does not manage its image, display or viewer, or guarantee external viewer read-only access. Read browser up/status capability information; inspect configuration sources and active operations before an authorized configuration change and managed startup. Never stop or adopt the external container.

Project documents hold startup commands, cases, authentication workflows, team rules and environment ownership; private notes hold personal machine facts. Project Skills should point to the CLI guide. Preserve existing project facts and edits when replacing copied generic instructions. Full exports remain available for explicit offline distribution through an empty directory and deliberate comparison/merge.

## Windows tray viewer (0.8.0+)

On an interactive Windows desktop, starting/reusing managed services or a managed browser (including Verify preparation) automatically offers a per-session tray entry. Close a noVNC tab freely, then choose the project and Open debugging viewer from the tray to view the same experiment again; do not ask AI to restart it. The tray checks process identity and container ownership only, not application readiness, and never executes project health commands. It provides no stop/restart/cleanup actions for business resources.

`echo-kit tray start` registers the current workspace, including existing owned resources. `echo-kit --json tray status` and `echo-kit tray stop` need no workspace. Stop/Exit closes only the tray. A later startup operation can restore it, but the same operation cannot override a manual dismissal. After all resources are confirmed stopped, the tray exits after a 30-second grace; unknown states retain it. External instances are excluded; headless/legacy browsers have no viewer. CI, noninteractive desktops and `ECHO_KIT_TRAY=0` disable automatic startup. Other platforms keep existing CLI/viewer URLs. No boot service or service watchdog is installed. A restrictive Windows Job can prevent independent UI startup: report the notice and retain the viewer link, without failing the experiment. Windows may place the icon in its overflow area.

## Prepare test identities during onboarding

For cases requiring authentication, onboarding guidance first reuses project test identities, seeds and registration/management commands. When no repeatable entry exists, write a project-owned preparation script. Preparation remains explicit; login, service startup and Verify do not silently create accounts. Skip anonymous cases.

Verify login and the required role/tenant separately. Reuse matching identities without password resets or privilege expansion; retain reusable identities and clean only this run's temporary creations. Read `echo-kit guide echo-init --topic auth` for the bundled checklist and pseudocode. No new account-management CLI is introduced.


Share startup commands, relative paths and team defaults. Keep machine-specific port allocations and absolute paths in ignored, untracked `.echo-kit/local.toml`. AI guidance checks environment precedence and related readiness settings while preserving team startup workflows. See [paired configuration examples](docs/protocol.md#shared-and-local-configuration).


## Find experiments without loading everything

Use `echo-kit cases list` to browse categories, `cases list --query login` to search concise metadata, then `cases show CASE` to read only the selected explanation. Optional case description/category/tags/docs reuse existing configuration and files. Read `guide NAME --topic TOPIC` before the related capability; full documents remain available with `--full` and export.

`runs list --case CASE` now returns the latest 20 summaries, not full records; `--limit` (1..200) and `--offset` page through results. JSON consumers must use `items` plus pagination instead of the old array; `runs show ID` retains the complete record. Keep mandatory rules in the project entry, experiment explanations separately, and source/logs/traces as on-demand evidence. Existing cases and scripts keep working.

## Shared services across workspaces

Since 0.10.0, identical explicit `services.NAME.resource_id` values reuse one compatible managed instance under the same local user. Services without an ID stay independent. Cases share service access by default; set `service_access = "exclusive"` for data changes that must not overlap. Active usage blocks stop/restart.

Browsers remain workspace-local. Put local port overrides in `.echo-kit/local.toml`; the tray separates shared services from project viewers. Read `echo-kit guide echo-workbench --topic services` for adoption and stop scope.

## Find module documents and scope rules

`echo-kit docs map` discovers Markdown across the entire workspace, not just echo/. Before editing a module, use `docs context MODULE_DIR` for its AGENTS.md chain and `docs read PATH` for selected explanations. Keep project-wide constraints at root, local constraints with modules, and detailed requirements in linked documents.

`docs check --rules` checks rule files and direct references. It does not move documents or resolve semantic conflicts. Private material still requires explicit private-entry reads. See `guide echo-init --topic documents` for boundaries.

Update routing fix (0.11.1): concurrent checks wait briefly and reuse results; network failures prefer verified cached newer runtimes. Subcommand help follows routing; explicit `--offline` stays on the calling package. Upgrade an older installed entry once to acquire the fix; see [updates](docs/updates.md).

Modular configuration (0.12.0): root `echo-kit.toml` can declare `includes = ["services/orders/echo.toml"]` for module-owned cases, services, auth and projects. Path bases and local/environment precedence stay unchanged. `workspace check` lists config files; `cases show ID` identifies definition sources. Small projects can keep one file. See [protocol](docs/protocol.md#modular-configuration).
