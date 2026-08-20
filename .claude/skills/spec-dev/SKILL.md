---
name: spec-dev
description: Use when converting feature ideas into a spec-driven development workflow with requirement.md, design.md, tasks.md, implementation, and evaluator review. Use for staged feature planning, steering-aligned design, task decomposition, code generation from specs, and strict review gates.
---

# Spec-Driven Development

Orchestrator for a repository-local spec pipeline:

`planner -> designer -> tasker -> generator -> evaluator`

Default artifact location `docs/spec/<feature-slug>/`:

- `requirement.md`: structured requirements
- `design.md`: technical design aligned with repository steering
- `tasks.md`: executable implementation and validation tasks
- `review-log.md`: append-only evaluator history (owned by `spec-generator`)
- `summary.md`: final handoff written once all tasks are checked

Database migration / DDL / backfill scripts go under the directory mandated by `docs/steering/` — never hardcoded here.

Communicate and write artifacts in the user's language; when unclear, follow the language of the latest user message and the repository's existing documentation. The skill is stack-agnostic — concrete code style, frameworks, error model, response wrapper, persistence, test framework, logging, naming, and directory layout **must** be resolved from `docs/steering/` and the codebase, not from this skill or its sub-agents.

## Subagents And Phase Routing

| State | Delegate (`subagent_type`) | Definition | Output |
| --- | --- | --- | --- |
| New idea, missing `requirement.md`, or requirements revision | `spec-planner` | `.claude/agents/spec-planner.md` | `requirement.md` |
| `requirement.md` exists; `design.md` missing/stale/revising | `spec-designer` | `.claude/agents/spec-designer.md` | `design.md` |
| `design.md` exists; `tasks.md` missing/stale/revising | `spec-tasker` | `.claude/agents/spec-tasker.md` | `tasks.md` |
| `tasks.md` has unchecked items | `spec-generator` | `.claude/agents/spec-generator.md` | Code/tests for next task; checkbox set only after review |
| Generated code/tests need review | `spec-evaluator` | `.claude/agents/spec-evaluator.md` | PASS/FAIL with actionable feedback |
| All tasks checked, last verdict PASS | (no delegation) | — | Final handoff + `summary.md` |

Use the Claude Code `Agent` tool for delegation. Do not invent subagent names.

## Initialization

1. Identify what the user wants to build, revise, implement, or review.
2. List `docs/spec/` to find existing specs and pick the target `docs/spec/<feature-slug>/`.
3. Inspect relevant code so delegated agents receive repository-specific context (tech stack, conventions, error model, test framework, persistence, layering).
4. Read every file under `docs/steering/` as binding rules — they apply together. Resolve all project-specific constraints from steering and the codebase.
5. If the target feature/spec directory is ambiguous and cannot be inferred, ask one concise clarification question before delegating.

## Delegation Protocol

In each `Agent` call, pass a compact handoff prompt with: feature slug + spec directory; user request/feedback; current phase and why; existing artifact paths; relevant code paths and architectural constraints from steering and inspection; expected output path or review scope; whether autonomous mode is active.

## Workflow

1. Detect the phase using `docs/spec/`, the latest user request, and artifact freshness.
2. Detect **upstream drift** before routing to implementation:
   - User says they edited `requirement.md` or `design.md`, or
   - Upstream mtime > downstream mtime (`stat -c '%Y %n' …`), or
   - Downstream references something that no longer exists upstream.
   On drift: regenerate downstream (`spec-designer` and/or `spec-tasker`) before `spec-generator`.
3. Delegate the phase to the matching subagent via the `Agent` tool.
4. Sanity-check the delegated output for routing mistakes, missing files, or upstream conflicts.
5. Ask the user to approve major artifacts before the next phase, unless autonomous mode is active.
6. Keep upstream/downstream consistent: requirement change → re-run designer + tasker; design change → re-run tasker.
7. Route unchecked `tasks.md` items to `spec-generator` in order. The generator decides per-slice whether to invoke `spec-evaluator`; the orchestrator does not force review on doc-only or rename-only slices.
8. On evaluator FAIL, route feedback back to `spec-generator`. After three consecutive failures on the same task, stop and ask the user.
9. When all `tasks.md` items are checked and the last verdict is PASS, the orchestrator (not a subagent) writes `docs/spec/<feature-slug>/summary.md`: feature slug, final artifact list, notable design decisions, test coverage, follow-ups. Leave `review-log.md` in place.

## Autonomous Execution vs Approval Gates

- Default is **approval-gated**: pause after each major artifact and each implementation slice for explicit approval before delegating the next phase.
- The user opts into **autonomous mode** with phrasing like "autonomously execute", "自动执行到底", or "run through the full pipeline". Carry that choice for the session.
- Autonomous mode never suppresses the FAIL loop. After three consecutive FAILs on one task, stop and ask the user.

For cold/mid-pipeline resume, drift detection commands, and handoff/completion checklists: see `references/prompts/orchestrator.md`.

## Operating Rules

- Keep `SKILL.md` lean; load detailed agent definitions only when delegating.
- Do not broaden implementation beyond the approved spec. If a task needs out-of-scope changes, stop and surface the mismatch.
- Evaluator mode is review-only — never edit code while acting as evaluator.
- Small coordination fixes (e.g., correcting a stale path in a spec artifact) may be done directly by the orchestrator. Feature implementation and formal artifact generation must be delegated.
- Each subagent must rediscover language/framework/error-model/test-framework/SQL-directory from `docs/steering/` and the codebase on every run.
