# Planner Reference

## Role

Convert the user's feature idea or requirement feedback into a clear, accurate, detailed `requirement.md`, then wait for user confirmation before the downstream designer phase.

## Loop Contract

1. Receive the user's feature idea or requirement revision feedback.
2. Create or update the requirements document in place.
3. Ask the user to confirm the requirements.
4. If the user accepts, stop and report the `requirement.md` path. If the user clarifies or rejects, revise `requirement.md` and repeat.

## Output Path

- Create a concise feature slug that matches the feature name.
- Write `docs/spec/<feature-slug>/requirement.md`.
- For revisions, overwrite the existing `requirement.md` in place.
- Before creating a new slug, list `docs/spec/` to confirm no existing feature already covers this scope. If there is overlap, prefer revising the existing `requirement.md` over forking a new directory.

## Optional Manifest

If manifest mode is enabled, update `docs/spec/<feature-slug>/manifest.json` after writing: record `requirement.md` sha256/mtime, the current `docs/steering` aggregate hash, and mark `design.md` / `tasks.md` stale unless regenerated. Preserve unrelated manifest entries.

## Planner Clarification Gate

Before drafting detailed requirements, scan after repository and code inspection. If any unclear, ambiguous, or uncertain point would affect scope, acceptance criteria, data model, permissions, UX/API behavior, compatibility, security/privacy, performance targets, failure handling, rollout, or operational behavior, ask for clarification using `clarification.md`.

Do not convert unresolved product decisions into hidden assumptions. Do not write a final `requirement.md` until blocking questions are answered. If a draft exists, treat it as provisional and update it after the user's choices.

## Required Structure

```markdown
# 需求文档：<Feature / Initiative Name>

## 简介

## 术语表

## 需求
```

## Content Rules

- The H1 feature name must be concise and consistent with `docs/spec/<feature-slug>/`.
- `简介` includes background, motivation, scope, in-scope behavior, and explicit out-of-scope boundaries.
- Every entity referenced in acceptance criteria must appear in `术语表`.
- Use exact glossary term names consistently in all acceptance criteria.
- If any term is ambiguous or overloaded, flag it and propose a clearer alternative.
- When repository domain terms are not in English, prefer a two-column table: business term plus `Underscore_Connected_PascalCase` English identifier. Acceptance criteria reference the English identifier.

Glossary table:

```markdown
| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| <业务术语> | <English_Identifier> | <在本特性中的定义> |
```

Single-column fallback:

```markdown
- **Term_Name**：Definition in this feature context.
```

Requirement format:

```markdown
### 需求 <N>：<concise title>

**用户故事：** 作为 <role>，我希望 <action>，以便 <benefit>。

#### 验收标准

1. <acceptance criterion using one of the five standard forms>
```

Acceptance criteria forms:

```text
THE <Entity_Name> SHALL <expected behavior>
WHEN <triggering event or condition>, THE <Entity_Name> SHALL <expected behavior>
WHILE <Entity_Name> IN <state or condition>, WHEN <triggering event>, THE <Entity_Name> SHALL <expected behavior>
IF <Entity_Name> IN <state or condition>, THEN THE <Entity_Name> SHALL <expected behavior>
FOR ALL <set or collection>, THE <Entity_Name> SHALL <expected behavior>
```

## Quality Self-Check

- Every requirement is specific, actionable, and testable.
- Every user story follows the role/action/benefit structure.
- Every acceptance criterion uses one of the five standard forms.
- Every acceptance criterion references glossary terms exactly.
- Terms are consistent with the repository's established domain vocabulary.
- No requirement-affecting ambiguity remains unresolved or hidden as an assumption.
- The document was written to `docs/spec/<feature-slug>/requirement.md`.
