# Clarification And Upstream Issue Rules

Use these rules whenever an unclear, ambiguous, uncertain, stale, contradictory, or incomplete point would affect the current artifact or implementation slice.

## General Clarification Gate

- Run the relevant clarification gate before writing or finalizing an artifact, and before editing implementation files.
- Ask only decision-grade questions. Do not ask about details already answered by explicit user instructions, upstream artifacts, steering docs, or existing code.
- If a detail does not affect the current artifact or implementation outcome, note a conservative assumption only when it helps downstream work.
- Blocking clarification questions pause the pipeline. Do not finalize the artifact, delegate to the next phase, edit code, mark a task complete, or write a PASS-style review-log entry until the decision is resolved.
- Clarification gates outrank approval gates and autonomous mode.

## Question Format

When asking for clarification:

- Use concise numbered questions, one decision per question.
- For every question, provide realistic selectable options, normally 2-4 options labeled `A`, `B`, `C`, etc.
- Mark one option as `推荐` when there is a defensible default, and briefly explain why.
- State the practical implication of each option so the user can choose without extra research.
- Include a final catch-all only when useful, such as "也可以给出其他方案，我会据此更新对应产物。"

Template:

```markdown
在继续前，我需要确认以下决策：

1. <问题>
   - A. <选项>（推荐）：<影响/理由>
   - B. <选项>：<影响>
   - C. <选项>：<影响>
```

## After The User Answers

- Update the owning artifact in place; do not acknowledge the decision only in chat.
- If the answer changes an upstream artifact, regenerate every stale downstream artifact.
- If the user already stated a preference in the current request, earlier chat, or existing artifacts, apply it and record it in the artifact rather than asking again.

## Routed Upstream Issues

For generator/evaluator-discovered upstream issues, include:

- `Artifact`: `requirement.md`, `design.md`, or `tasks.md`.
- `Location`: section, requirement number, property, component, task serial, or closest identifiable heading.
- `Problem`: why this blocks evaluation or implementation.
- `Options`: realistic selectable fixes, normally 2-4 options labeled `A`, `B`, `C`, etc.
- `Recommendation`: one option marked `推荐`, with a short reason.
- `Downstream impact`: which artifacts must be regenerated after the fix.
