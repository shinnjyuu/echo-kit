# Echo Kit

Single install, independent capabilities. Preserve project-owned adapters and external instances. Core must not import project-specific business modules or embed environment addresses or credentials.

Run `uv run pytest` for targeted core checks. Real Docker and sample integration runs are separate evidence. Document platform and authentication limitations honestly. Do not publish, push, deploy or operate shared business environments without explicit task authorization.

Skills are bundled in `src/echo_kit/skills`; public protocol lives in `docs/protocol.md` and is included with exported skills. Keep these copies synchronized.

Skill instructions and the bundled protocol are versioned product content. When changing them, keep CLI documentation access/export, affected command behavior/help, and usage examples consistent. CLI documentation must come from the running package's resources, not mutable remote main-branch content. Do not present planned commands as implemented.

Distribute bundled Skill/protocol changes in a version-bumped package release, even when executable behavior is unchanged. Follow `docs/publishing.md` for synchronization, package verification and release. A Git push alone does not deliver these updates to installed users; report changes as pending release until publication is verified. Publishing still requires task authorization. Already-exported project Skills are independent copies: re-export to an empty directory, compare and merge while preserving project-owned edits.
