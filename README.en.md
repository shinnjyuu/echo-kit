# Echo Kit

[简体中文](README.md) | **English**

**Give your AI coding assistant reusable ways to experiment, debug, and verify your project.**

Echo Kit is a toolkit for AI coding assistants. It helps them reuse a project's startup and testing workflows, run checks, and keep results, logs, and files as evidence. A new AI session or another teammate can pick up the verification methods already established in the project.

This page is for people discovering Echo Kit for the first time. AI assistants setting up a project should read the [AI onboarding guide](README.ai.md) (Chinese).

## 1.0: implement from a prototype, compare with real evidence

These capabilities belong to 1.0.0; official PyPI determines release availability. Existing browser configurations must select a backend explicitly.

Daily frontend debugging defaults to a Kit-owned headed local Chrome driven by the pinned agent-browser. The AI reads, clicks, fills and captures the page through short commands without first writing a control script. Docker/noVNC is used only when `backend = "playwright"` is explicitly selected.

```sh
echo-kit browser up --url http://localhost:5173 --service web
echo-kit browser exec -- snapshot -c
echo-kit browser exec -- click @e1
echo-kit browser exec -- screenshot
```

For prototype fidelity, use a persistent observation session:

```sh
echo-kit browser observe --url http://localhost:5173 \
  --reference prototypes/dashboard.html --spec docs/dashboard.md
```

HTML/URLs provide rendered references; Markdown/PRDs provide requirements. Explicitly capture a fixed baseline, inspect selected measurements and images, correct evidenced differences, then compare again. Reference browsers are temporary by default. Ordinary edits need no fixed scripts or whole-site matrix. Markdown alone cannot establish pixel fidelity. Read `echo-kit guide echo-workbench --topic visual`.

Upgrading does not rewrite business projects. Old browser configuration must explicitly select native or compatibility mode. Playwright helpers, callbacks and trace.zip are compatibility-only; native traces are Chrome/CDP JSON. The 1.x stability baseline starts with these explicitly distinguished interfaces, not a claim that all 0.15 browser scripts migrate unchanged.

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

For visible page or interaction changes, the updated guidance asks the AI to proactively show key screenshots with short location, change, and usage captions, reusing this task's acceptance captures. Simulated data and isolated pages are labeled; behavior verification is reported separately. Backend-only changes use text without default diagrams. If screenshots are unavailable, explain why, provide an available business URL and pending checks, and keep the selected human acceptance approach without requiring screenshot scripts. See [page change handoff](docs/protocol.md#page-change-handoff).

After the relevant capabilities are set up, you can ask your AI:

> Use Echo Kit to compare the current implementation with the candidate. Run each three times and show me the metric differences.

> Run this project's existing API acceptance checks with Echo Kit. Explain what passed, failed, and remains unverified.

> Review the last failed run and use its logs and check results to investigate the problem.

The project continues to own its business dependencies and development workflow. Frontend, backend, and verification scripts can live in separate directories, and cases can connect to existing external services.

## Requirements and current scope

Echo Kit requires Python 3.11+; your project can use other languages. Your AI can help install the tool and its runtime. Ordinary experiments and API verification do not require Docker. Docker is needed only for the explicitly selected Playwright compatibility browser.

**Managed local services may stop when the AI application exits, updates, or crashes. This is an allowed lifecycle outcome.** Echo Kit supports reusing services after a command finishes, but does not guarantee that they survive their host application exiting or that they are automatically cleaned up when it exits. A later AI session should inspect service status before reusing or starting them. See the [service lifecycle rules](docs/protocol.md#managed-service-lifetime).

Current validation evidence covers Windows and Linux containers; The native runtime supports Windows x64 and Linux x64; Linux browser checks use Xvfb. macOS and other architectures are not yet supported by the native backend. Authentication must follow the project's normal login flow. Screenshots, logs, and downloads may contain business data: review them before sharing.

Verification conclusions cover only the checks actually executed. Kit distinguishes failed, blocked, and unverified outcomes. Historical records help explain what happened, but do not guarantee identical results on a rerun.

## Shared and private project documentation

Since 0.3.0, Echo Kit distinguishes team-shared documents from personal, private documents. Shared documents use relative paths and belong in Git. Private documents can use absolute local paths and must stay out of Git. AI assistants can discover entries with `docs list`, read them with `docs show`, and validate them with `docs check`. Existing projects can opt in with `docs setup`, which preserves existing files. See the [project documentation guide](docs/project-documents.md) (Chinese).

## Further reading

Use `echo-kit browser observe --url URL --service web` for repeated visual feedback on a page. The persistent session keeps its own login, dialogs and unsaved inputs, returning fresh screenshots and bounded layout diagnostics. Reload and navigation are explicit. Native pages use short managed commands and explicit readiness checks; old Python callbacks require explicit Playwright compatibility mode. See [fast visual observation](src/echo_kit/skills/references/visual-observation.md).

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

1.0 defaults to an independent Kit-owned headed local Chrome. You see the browser directly, without Docker, virtual desktop or noVNC. Kit prepares pinned, checksum-verified runtime assets; first preparation can require a download. Explicit offline mode uses already-prepared assets only.

```toml
[browser]
backend = "agent-browser"
headless = false
```

A native window is interactive and can be closed by the user. Closing the page can end the session and lose unsaved state; Kit does not silently recreate it or repeat an action. Status/tray can locate an existing owned window; down closes only the selected owned session.

Old Docker/read-only noVNC remains available only through explicit backend="playwright" or browser up --backend playwright. Old ports, image settings, endpoints and containers never select compatibility automatically. Resolve active use and stop the old owned instance before switching; failures never silently fall back. Read `guide echo-workbench --topic browser-compatibility` for old Python helpers only when explicitly needed; use the `browser` topic for native interaction. See [browser behavior](docs/protocol.md#visible-browser).

## Current guides and upgrade guidance

On first use read `echo-kit guide echo-init`, `echo-kit guide echo-lab` or `echo-kit guide echo-workbench`; read again when the actual execution version changes. Guides contain the compact Skill, core rules, topic directory and current release notes from the executing package. Load --topic TOPIC only when needed; --full and export remain available. Explicit --offline stays on the current package. `--version` uses normal update routing; runtime info diagnoses the local entry. Active native sessions retain their real runtime versions; short commands route to the running owner without a network update on every click.

Use updates notes and updates notes --since VERSION to inspect bundled changes. An upgrade notice is not project adaptation. Frontend debugging defaults to managed native headed Chrome plus agent-browser. Docker/noVNC and external Playwright endpoints require explicit compatibility mode; old image/port/endpoint fields never enable it automatically. Diagnose failures without silently switching backend or adopting personal/external instances.

Keep project facts, startup commands, cases, authentication workflows, team rules and environment ownership in project documents; personal machine facts stay private. Re-export to an empty directory and merge deliberately while preserving project-owned edits.

## Windows tray viewer (0.8.0+)

The Windows tray lists owned services, native browser sessions and explicit compatibility containers. Select the workspace/session to focus its existing native Chrome window; Docker compatibility keeps its noVNC link. Closing the native window can end the page and lose unsaved state; tray actions do not create a new page and pretend it was restored.

tray start registers this workspace; tray status and tray stop need no workspace. Stopping the tray affects only the UI. Unknown resources remain unknown; all-confirmed-stopped resources trigger the existing 30-second exit grace. CI, noninteractive desktops and ECHO_KIT_TRAY=0 suppress automatic startup. A restrictive Windows Job can prevent independent lifetime; report that limitation honestly. Other platforms retain CLI capabilities; unverified desktop behavior is not claimed.

The top-level **急停所有工作区** action and emergency stop --all cover owned coordination processes, agent-browser, Chrome, legacy containers, services and tasks. They skip business saving/cleanup and retain uncertain results. Restart the tray after installation to load the new UI. See the [emergency protocol](docs/protocol.md#emergency-stop).

## On-demand disk reclamation

`echo-kit resources disk` inventories run attachments, closed observation sessions and explicitly declared disposable caches. Add --all for known workspaces. `resources clean` **only previews**; execute its frozen list with `resources clean --apply PLAN_ID`. Repeated --root filters preview roots; repeated --unit selects plan candidates. `resources keep UNIT_ID` pins important evidence locally (add --remove to unpin).

Recent evidence, latest results, run records and compact reports are retained. Active/pending work, tracked files and uncertain ownership stay protected; changes after preview cause a skip. Declare regenerable cache roots in root configuration or local overrides under [storage.caches.NAME]; personal paths and retention belong in .echo-kit/local.toml. No background deletion or Docker image cleanup is added. Logical size does not guarantee an equal free-space increase. Read [disk reclamation](docs/protocol.md#disk-reclamation) for configuration, limits and receipts.

## Resource usage

Run `echo-kit resources status` for the current workspace's managed services and browser, or `resources status api worker` for only the selected services. `echo-kit --json resources status --all` reads all known workspaces without loading business configuration; do not combine it with workspace, environment or service filters. Results are memory-descending, paginated with --limit/--offset (20 by default), and include full-selection totals, references and operation occupants.

The Windows tray shows read-only memory summaries, the three largest services and per-service/browser measurements. Updates run about every five seconds; data older than fifteen seconds is marked stale. After installing the new package, use tray stop then tray start to load the menu. Linux supports the CLI.

Native memory is process-tree RSS/Working Set with shared instances deduplicated; container figures use Docker's separate accounting. These observations are not whole-machine usage or guaranteed reclaimable memory. Unknown values remain unknown. Queries do not run application health checks or stop resources. See [resource usage](docs/protocol.md#resource-usage).

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

Version queries (0.12.3): `echo-kit --version` emits one version line after routing. Use `--offline --version` to inspect a specific installation, or JSON `runtime info` (`version_scope: installed_entry`) for local diagnosis. Older installed entries need one explicit upgrade to adopt this behavior; publishing alone does not change their version flag.

For confirmed stale reservations, the AI may inspect the operation and recorded children, then use `operations resolve ID --note ...` without a separate approval prompt once execution processes have exited. Record checks and reason. Resolution does not stop services, delete data or certify business cleanup; use cleanup adapters when needed and retain outstanding facts. Local exit does not prove remote task completion. Existing exported guides remain unchanged: export to an empty directory, compare and merge.

Choose acceptance by need: prepare services and a business URL for human testing; use compatible interactive browser tools for one-off AI acceptance without mandatory scripts or cases; preserve repeatable experiments when timing, repetition or regression assertions warrant them. noVNC remains view-only. Host interaction support must be verified; browser startup is not acceptance success.

Reuse existing startup, login and acceptance entrypoints instead of rebuilding an environment just to use Kit. Before a full page flow, confirm the native session, target identity and necessary login. In explicit Playwright compatibility mode, additionally compare required_playwright_version with the client actually imported by that script and make a minimal connection check. Fix the failing prerequisite first. Label controlled replies used for page-flow checks; evaluate real model quality separately. Model experiments start with a few representative samples for contracts, units, references and result storage before expanding, and report requests, retries, wall time and usage.

Use `echo-kit operations list --unfinished` for current work; unfiltered operations list retains all history. `services status NAME...` probes only named services. Internal queries skip process checks for explicitly finished, empty-held history and reuse resource matching and source snapshots within a query, preserving history and reservations. Daily work and guide reads use ordinary commands; --offline and offline environment switches stay on the calling package, for actual offline work or selected-package validation only.

Runtime debugging is optional: inspect actual values and branches for logic problems, and correlated non-pausing events for timing problems. Read the services/browser/cases guide topic for isolated Python and Node examples; debugger availability is not proof of diagnosis. No new debug CLI or mandatory runtime dependency.
