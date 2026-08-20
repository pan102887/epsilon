# Spec Agent Common Rules

These rules apply to every `spec-dev` Codex role after the role loads this file from its `.toml` entrypoint.

## Repository Context

- Communicate and write artifacts in the user's language; when unclear, follow the latest user message and the repository's existing documentation.
- Treat the repository root `CLAUDE.md` as the index when present. Open topic docs under `docs/` when they name the affected module, build command, architecture, or domain.
- Read every file under `docs/steering/` before producing, implementing, or evaluating an artifact. Steering docs are binding.
- Inspect relevant code before drafting, tasking, implementing, or evaluating. Match the repository's actual programming language, framework version, annotations/imports, module layout, naming, test framework, persistence APIs, logging, error model, response wrapper, and domain-modeling boundaries.
- Do not invent stack details, migration directories, error-return styles, libraries, annotations, language features, or architecture boundaries. Resolve them from steering docs and existing code.
- Place SQL / DDL / data-backfill scripts only in the canonical directory mandated by steering docs.

## Pipeline Consistency

- The pipeline order is `spec_planner -> spec_designer -> spec_tasker -> spec_generator -> spec_evaluator`.
- Upstream artifacts are authoritative for downstream work: `requirement.md` -> `design.md` -> `tasks.md` -> implementation/evaluation.
- If an upstream artifact changes, regenerate stale downstream artifacts before implementation resumes.
- If artifacts conflict, pause and route the issue to the owning upstream artifact instead of choosing a hidden default.
- If the target feature slug or spec directory is ambiguous and cannot be inferred from `docs/spec/`, ask one concise clarification question before writing.

## Optional Manifest Mode

- Hash-based drift tracking via `docs/spec/<feature-slug>/manifest.json` is optional. Enable it only when the user explicitly asks.
- When enabled, follow `.codex/skills/spec-dev/references/prompts/manifest.md`.
- When disabled, do not create or touch `manifest.json`; rely on user-declared drift, mtime comparison, and content sanity.

## Safety Boundaries

- Do not broaden implementation beyond the approved spec.
- Do not treat autonomous mode as permission to make product, architecture, task-planning, or implementation-scope decisions that the agent has no authority to make.
- Do not mark a downstream task complete while a blocking upstream ambiguity remains unresolved.
- You are not alone in the codebase. Do not revert edits made by others; adapt to existing changes.
