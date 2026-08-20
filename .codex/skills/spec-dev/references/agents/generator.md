# Generator Reference

## Role

Implement pending tasks from `tasks.md` one at a time, using `requirement.md` and `design.md` as the source of truth, and keep each slice auditable.

## Startup And Drift

- Read and parse `requirement.md`, `design.md`, and `tasks.md` from the same spec directory.
- If manifest mode is enabled, also read `docs/spec/<feature-slug>/manifest.json`.
- Stop and ask for the correct path if any required artifact is missing.
- Before selecting a task, trust user-declared upstream changes and compare mtimes across artifacts.
- If manifest mode is enabled, verify `tasks.md.upstream` hashes match current `requirement.md`, `design.md`, and `docs/steering`; hash mismatch is authoritative drift.
- If drift is detected, stop and require regeneration in pipeline order before implementation.

## Implementation Readiness Gate

Before editing files for a task, verify that the related artifacts and current codebase give enough information to implement the slice without guessing.

If any unclear, ambiguous, uncertain, contradictory, or stale point would affect implementation scope, file paths, class/type/function signatures, layer boundaries, data mappings, error behavior, test strategy, migration placement, or focused checks, pause before editing and surface the blocker using `clarification.md`.

When surfacing a blocker, identify the exact task serial number, the conflicting or missing source of truth, the owning artifact, selectable options, recommendation, and downstream regeneration impact. Do not implement, mark the checkbox, or append a PASS-style review-log entry until resolved.

## Main Loop

1. Pick the next unchecked eligible task from `tasks.md` in order.
2. Respect dependencies.
3. Identify requirement serial numbers from `_需求:` or `**验证: 需求 ...**`.
4. Read the full matching requirements, acceptance criteria, design sections, component signatures, data model, correctness properties, error handling, and testing strategy.
5. Run the implementation readiness gate.
6. Implement only the current task's scope.
7. For implementation tasks, create/modify production source code.
8. For validation tasks, create the specified tests using `design.md` and the repository's test framework.
9. Run focused checks that fit the change. Focused checks are narrowly scoped tests that do not boot heavyweight infrastructure; note larger checks as skipped with reason when appropriate.
10. Run evaluator review only when the slice adds/modifies production source/configuration or test files. Skip review for documentation, comments, rename-only refactors, or metadata-only slices; mark the checkbox directly, append a one-line `review-log.md` note, and proceed.
11. If direct evaluator delegation is supported, invoke `spec_evaluator` with the Evaluation Request. Otherwise emit the request and pause for review.
12. On PASS, update only the current checkbox from `- [ ]` to `- [x]`, append the PASS entry, then proceed.
13. On FAIL from implementation defects, append the FAIL entry, fix, and re-review. On FAIL from upstream issues, stop and route to the owning artifact. After three consecutive FAILs on one task, stop and ask the user.

## Review Log

- Maintain `docs/spec/<feature-slug>/review-log.md` as append-only history.
- For every evaluator invocation, record task serial, attempt number, verdict, blocking issue summary, and what changed before re-review.
- For skipped evaluator slices, record one line noting the task and why review was skipped.

## Evaluation Request Shape

```markdown
## Evaluation Request

### Task
<task serial number and description from tasks.md>

### Requirements
<full text of the corresponding requirement(s) from requirement.md>

### Design
<relevant design sections from design.md, including applicable component signatures, data model details, error handling, testing strategy, and correctness properties>

### Generated Code
<complete generated or modified code for this task, or a precise file/path summary plus enough code excerpts for review>
```

## Output And Logging

- Preserve unrelated user changes.
- Do not modify unrelated tasks, headings, notes, or formatting.
- Keep optional task markers intact.
- For each completed task, report changed paths and a one-line behavior summary.
- At the end, list completed tasks, skipped tasks, blocked tasks, and verification commands run.
