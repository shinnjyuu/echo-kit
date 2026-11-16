# Prototype evidence and visual comparison (1.0.0)

Use this only for prototype fidelity or relevant visual questions. Ordinary page edits keep proportionate acceptance; backend changes need no visual comparison. Reuse startup, authentication and existing pages. Do not create a case, permanent script, full viewport matrix or real model calls merely for comparison.

## Sources and preparation

```sh
echo-kit browser observe --url http://localhost:5173 --service web \
  --reference prototypes/dashboard.html --spec docs/dashboard.md
```

`--reference` accepts a shared workspace HTML file or an HTTP(S) URL without embedded credentials. Local HTML uses its directory as the asset root; `--reference-root prototypes` explicitly selects a different shared workspace root containing that HTML. Paths remain workspace-relative. Assets are served by a short-lived read-only 127.0.0.1 server on an allocated port, under a private capability path; it closes with the reference capture/session and is not a global background service. The reference uses an independent Kit-owned native browser session and never inherits target login state. Traversal, private/ignored material, nested repository boundaries and symlink escapes are rejected. Keep the permitted asset directory narrow. External resources remain real network requests and may fail; the result must record that limitation.

`--spec` accepts shared Markdown using the same ownership boundaries as `docs read`. The ready event associates its path, SHA-256 and line count, not the body. Read only relevant lines using `docs read PATH --start-line N --limit N`. Markdown alone provides requirements, not a rendered visual baseline. Do not render invented screenshots from prose and call them approved prototypes.

Read project precedence rules and the current user request. If a PRD and prototype disagree, identify the conflicting sources and apply established precedence; unresolved intent is unverified. Tool measurements do not decide that business question.

## Protocol 1 additions

Every request needs a fresh string `id`. The ready event advertises `capabilities`; old hand-written workers with no list support only observe/navigate/reload/close. The native session worker owns these capabilities. Existing Python serve_observer adapters require explicitly selected Playwright compatibility mode. The following commands are JSON lines sent to the same retained session:

```json
{"op":"inspect","id":"i1","page":"reference","target":"header","limit":20}
{"op":"inspect","id":"i2","page":"target","target":"[data-testid=header]"}
{"op":"reference","id":"r1","state":"overview","regions":[{"id":"header","reference":"header","target":"[data-testid=header]"}]}
{"op":"compare","id":"c1","baseline_id":"BASELINE_ID_FROM_r1","state":"overview"}
```

`inspect` reads only selected elements: tag, viewport/document boxes, a fixed computed-style allowlist, pseudo-element measurements and bounded image/font diagnostics. It excludes form values, full DOM and network bodies. `page` defaults to `target`, selector to `body > *`, and limit to 20; the body is capped at 64 KiB. Truncation means unread elements were not inspected: narrow the selector.

In 1.0 selectors address the main document using CSS; iframe contents, closed shadow roots and host-specific selector extensions are not automatically expanded. Region screenshots may show embedded content without establishing its internal measurements or business behavior.

Reference capture starts an isolated native reference session and closes it after capture by default. Use keep_reference: true only when continued reference interaction is needed; explicit close ends it. `reference` freezes the current reference state into a session-local baseline with source digest/time, screenshot hashes, measurements, profile, Markdown provenance and region mappings. With no regions it captures the current viewport. Each selector must uniquely match a visible element. Explicit regions may pair different DOM structures; tags/classes need not match. At most ten regions and four explicit `viewports` are allowed. One region is limited to 2560×2560 pixels. No full-page sweep is automatic.

`compare` requires that exact session's `baseline_id`. It captures the current actual page against the baseline's fixed regions, viewport list, animation policy and exclusions. The optional state label must match; the label records declared intent, not proof that business states match. State preparation and verification remain the adapter/Agent's responsibility. The page is not refreshed or navigated. Viewport and scroll are restored after capture; region capture does not navigate or scroll the page. Responsive scripts may react to explicitly requested viewport changes.

Returned image paths belong to the current observation ID. Use current `images` only, including actual captures, side-by-side pairs (reference left, actual right), and pixel differences. A previous baseline path may appear as provenance, not a new screenshot. No image is scaled to disguise a size difference: unequal regions are displayed at their original sizes in the pair and flagged region_dimensions_differ; the dependency reports dimensionMismatch instead of pretending a pixel comparison succeeded.

Native diffs use the pinned agent-browser 0.43.0 implementation. Pillow only prepares the diagnostic side-by-side panel without scaling. Native runtime assets are fixed and checksum-verified; no global Node/npm is required. Threshold defaults to 0.1 and is a per-pixel color sensitivity, not an acceptance percentage. The engine/version/parameters are recorded; its metric must not be presented as the previous Playwright/pixelmatch metric. A caller may explicitly set threshold but must not relax it to conceal failures.

Font/image/resource failures, unstable consecutive screenshots, different rendering profiles or entirely excluded captures produce `verification: unverified`. `status: ok` means the request completed. `verification: captured/compared` means evidence acquisition was verified; neither means that appearance or business behavior passed. Layout/style differences and pixel counts are observations, not a universal score.

Dynamic exclusions are explicit paired selectors on the **reference request**:

```json
{"op":"reference","id":"r2","state":"streaming","exclude":[{"reference":"#clock","target":"#live-clock"}]}
```

Their selectors and animation choice are fixed in the baseline and preserved in its artifact. Explicit exclusions temporarily hide those regions while capturing and are restored in finally; restoration failures are errors, not successful captures. Excluded pixels provide no evidence. Ordinary animations remain allowed; disabling them requires `animations: "disabled"` when capturing the baseline. A changed local source or Markdown hash invalidates reuse. Explicit `reference` with `reload: true` reloads only the reference page and creates a new baseline; it never overwrites an existing baseline or refreshes the actual page. Remote sources record a point-in-time capture, not assurance of the remote site's current contents.

## Project preparation and host clients

Use short browser up/exec commands or the existing retained observe pipe. No permanent Python control script is needed for normal native interaction. Prepare services/auth using project-owned existing entrypoints, then use supported commands to enter the necessary state and wait for the actual business condition. Complex business data and authorization are not generalized into Kit.

The native target and reference are independent sessions: do not copy target auth into the reference. For explicit reference interaction use the session's reference-page capability and keep_reference; ordinary capture closes that extra browser to limit steady-state memory. The persistent protocol adds managed exec requests alongside observe/inspect/reference/compare; normal observation does not navigate or refresh.

When a retained session has a configured reference, select it explicitly for short interaction:

```sh
echo-kit browser exec --session SESSION_ID --page reference -- snapshot -c
echo-kit browser exec --session SESSION_ID --page reference -- click @e1
```

The first explicit reference interaction retains its isolated reference browser; the target stays untouched. References/element handles belong to the selected page. Use returned reference inspection or a reference snapshot to obtain its handles; never reuse the target's handles on it. Closing the session closes the retained reference as well. To freeze the selected reference state, send an explicit reference capture; a compare request never redefines that baseline.

The exported observe-client.mjs remains a host pipe client; current images must match the request ID. Windows PTY clients use the existing atomic receipt reader and ready event's interpreter. Alternatively, short browser exec calls return the current capture artifacts for the host to display. Do not claim all Agent hosts have been verified.

Old serve_observer Python callbacks and browser.session are only for explicit backend="playwright" compatibility. Their Playwright trace.zip is distinct from the native Chrome/CDP traceEvents JSON; always record actual backend and trace format.

## Evidence and handoff

For each selected requirement, record document path/lines, the observed measurement/image or actual interaction, and `conforms`, `deviates`, or `unverified`. Explain discrepancies in concrete terms (for example, expected 62px header, observed 72px), then fix the affected region and compare against the same baseline. Missing evidence stays unverified. Do not silently replace the reference with the implementation, change requirements, invent differences or demand a fixed number of defects.

Show a small set of useful key screenshots with captions, and distinguish appearance evidence from checks of cancellation, permissions, persistence, timing and real model quality. Browser comparison cannot establish those behaviors. Mocked/controlled data must be labelled.

Both owned sessions, labelled traces, baseline records, receipts and comparison images belong to the existing observation operation and ignored storage unit. Keep the same service/browser occupancy for the whole session. Close/EOF closes only these owned contexts and saves traces; emergency stops cover the worker and comparison subprocess. Unconfirmed cleanup retains protection. Existing keep/reclamation rules apply; do not duplicate these artifacts into shared project documents or create a separate baseline registry.

## Engineering references

The browser execution dependency is [Vercel Labs agent-browser 0.43.0](https://github.com/vercel-labs/agent-browser/tree/v0.43.0), with fixed binary, browser and contract checks. Its [snapshot/measurement commands](https://agent-browser.dev/commands) and [diff support](https://agent-browser.dev/diffing) supply the native actions. Kit owns process lifetime, exact sessions, data boundaries and evidence; direct upstream automatic restart behavior is not inherited. [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots) inform stable captures and matching render conditions for explicit compatibility. No small cloning Skill or Python comparison port is a product dependency or validation authority.
