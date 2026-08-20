---
name: long-running-app-harness
description: Use when coordinating Anthropic-style long-running app development with planner, generator, and evaluator agents. Use for turning a short app/product idea into an ambitious product spec, building working software from that spec, reviewing sprint contracts, running QA, and iterating on evaluator feedback.
---

# Harness

Use this skill as the orchestrator for an Anthropic-style application development loop:

`planner -> generator -> evaluator -> generator fixes -> evaluator re-review`

This workflow is based on the harness pattern from Anthropic's "Harness design for long-running application development": split planning, building, and judging across separate agents so each role has a clearer job and better incentives.

## Project Agents

Use these project-scoped Codex custom subagents:

- `planner`: expands a short product idea into an ambitious high-level product spec.
- `generator`: turns the planner spec into working software, usually feature-by-feature or sprint-by-sprint.
- `evaluator`: independently reviews contracts and completed work through skeptical QA.

Agent definitions:

- `.codex/agents/planner.toml`
- `.codex/agents/generator.toml`
- `.codex/agents/evaluator.toml`

These are distinct from `spec_planner`, `spec_generator`, and `spec_evaluator`. Do not route this workflow to the spec-driven agents unless the user explicitly asks for the formal spec pipeline.

## Orchestrator Role

Classify the user's request and route to the right agent:

| User Intent / State | Delegate To | Expected Output |
| --- | --- | --- |
| Short app idea, product concept, or vague build request | `planner` | Product spec with workflows, scope, AI opportunities, UI direction, deliverables, and evaluation notes |
| Product spec exists and implementation is requested | `generator` | Working software for the next meaningful slice or full build |
| Generator proposes a sprint contract | `evaluator` | Contract approval or revision feedback |
| Generator completes a slice | `evaluator` | PASS/FAIL QA report with concrete findings |
| Evaluator returns FAIL | `generator` | Fixes for required issues |
| Evaluator returns PASS and more scope remains | `generator` | Next feature/sprint |
| All core scope passes evaluation | No delegation by default | Final summary, changed paths, verification results, and residual risks |

## Workflow

1. Inspect the repository enough to understand stack, entry points, existing conventions, and whether a product spec already exists.
2. Verify that `.codex/agents/planner.toml`, `.codex/agents/generator.toml`, and `.codex/agents/evaluator.toml` exist before routing work.
3. If the user gave only a raw idea, delegate to `planner` first.
4. Review the planner output for obvious omissions or conflicts with the user's request.
5. If implementation should proceed, delegate to `generator` with the planner spec and relevant repository context.
6. For substantial work, have `generator` produce a sprint contract before coding.
7. Route the sprint contract to `evaluator` for contract review.
8. If the contract needs revision, route feedback back to `generator`.
9. Once the contract is accepted, let `generator` implement the agreed slice.
10. Route completed work to `evaluator` for independent QA.
11. If `evaluator` returns FAIL, route required fixes back to `generator`, then re-review.
12. If `evaluator` returns PASS, continue to the next slice or produce the final handoff.

## Delegation Handoff

When delegating, include compact context:

- User's latest request.
- Current phase and selected agent.
- Planner spec path or full spec text if available.
- Current sprint contract, if available.
- Evaluator feedback, if this is a fix pass.
- Relevant repository paths, commands, stack, and constraints discovered locally.
- Expected output and whether the user requested autonomous execution or approval gates.

## Default Artifacts

If the user does not provide artifact paths, use:

- `docs/spec/<feature-slug>/product-spec.md`
- `docs/spec/<feature-slug>/sprint-<n>-contract.md`
- `docs/spec/<feature-slug>/sprint-<n>-handoff.md`
- `docs/spec/<feature-slug>/sprint-<n>-evaluation.md`

Do not force these files when the user wants a lightweight run or when existing project conventions suggest a better place.

## Quality Rules

- Keep planning ambitious but implementation-safe.
- Do not let the generator begin broad implementation from a vague prompt when a planner spec is missing.
- Do not let the evaluator approve display-only or stub-only core features.
- Treat evaluator FAIL as blocking for the current slice.
- Keep planner, generator, and evaluator responsibilities separate.
- Preserve unrelated user or agent changes in the worktree.
- Prefer real running-app verification for interactive products.
- If custom subagent invocation is unavailable in the current environment, perform the orchestration manually using the same role boundaries and report that limitation.

## Final Handoff

At the end, report:

- Product/spec artifact paths, if created.
- Implemented slices or features.
- Evaluator verdicts.
- Changed code paths.
- Verification commands and results.
- Known residual risks or deferred scope.
