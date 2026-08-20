# Orchestrator Reference

Canonical rules live in `../../SKILL.md`. Use this reference for **cold/mid-pipeline resume**, **drift detection commands**, and **handoff/completion checklists**.

Work only in `docs/spec/` and the repo root; never touch `/etc`, `~/.ssh`, or `.git/`.

## 1. Resume Checklist

```bash
# Locate the target spec directory; ask the user if multiple slugs plausibly match.
ls -la docs/spec/

# For the chosen slug, list artifacts and their mtimes.
ls -la docs/spec/<feature-slug>/

# Confirm migration artifacts (if any) live in the directory mandated by docs/steering/.
```

Then route per the state table in `SKILL.md` (`Subagents And Phase Routing`).

## 2. Upstream Drift Detection

Downstream artifacts (`design.md`, `tasks.md`) must be regenerated when upstream changes:

1. **User-declared** — user says they edited `requirement.md` or `design.md`.
2. **Mtime** — upstream newer than downstream:

   ```bash
   stat -c '%Y %n' docs/spec/<feature-slug>/requirement.md docs/spec/<feature-slug>/design.md docs/spec/<feature-slug>/tasks.md
   ```

   `requirement.md` newer than `design.md` → re-run designer + tasker.
   `design.md` newer than `tasks.md` → re-run tasker.
3. **Content** — downstream references a class/field/criterion that no longer exists upstream.

On drift, **pause before delegating to `spec-generator`** — generating against stale tasks wastes work.

## 3. Handoff Checklist (before pausing or completing a phase)

Report:

- target spec directory (`docs/spec/<feature-slug>/`)
- artifacts created or revised this turn
- subagent invoked
- next phase and whether user approval is required (approval is the default; autonomous only when opted in)
- blockers, failed evaluations, upstream inconsistencies
- for `spec-generator` slices: whether `review-log.md` was appended

## 4. Completion Checklist (all tasks checked, last verdict PASS)

- Write/update `docs/spec/<feature-slug>/summary.md`: feature slug, final artifact list, notable design decisions, test coverage summary, known follow-ups.
- Leave `review-log.md` in place as the audit trail.
- Announce completion; do not invoke another subagent by default.

## 5. General Rules

- Communicate and write artifacts in the user's language; fall back to the latest user message and existing repo docs when unspecified.
- Never fabricate a subagent name. Use only the five defined in `.claude/agents/`.
- Inspect relevant code before delegation so the subagent receives repository-specific context.
- Stack-agnostic: sub-agents resolve language, framework, error model, test framework, and SQL/migration directory from `docs/steering/` plus the codebase.
