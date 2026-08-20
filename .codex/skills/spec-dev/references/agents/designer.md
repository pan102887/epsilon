# Designer Reference

## Role

Convert `requirement.md` into a detailed `design.md` that is specific enough for the tasker and generator to work without guessing, then wait for user confirmation before the tasker phase.

## Loop Contract

1. Receive the `requirement.md` path or spec directory.
2. Convert every requirement into detailed design coverage.
3. Write or update `design.md` in the same spec directory.
4. Run the clarification loop against the draft before asking the user to accept it.
5. Ask the user to confirm the design.
6. If the user accepts, stop and report the `design.md` path. If the user clarifies design details, update `design.md` in place, rerun the clarification loop, and repeat.

## Pipeline And Drift

- Pipeline order is `spec_planner -> spec_designer -> spec_tasker`.
- If the user requests changes that affect requirements, pause and ask for `requirement.md` to be updated by `spec_planner` first, then regenerate `design.md`.
- Before drafting, compare `requirement.md` mtime against `design.md` if present and trust any user declaration that upstream changed.
- If manifest mode is enabled, additionally compare `requirement.md` and `docs/steering` hashes. Hash mismatch is authoritative drift.

## Output Path

- Read `docs/spec/<feature-slug>/requirement.md`.
- Write `docs/spec/<feature-slug>/design.md`.
- If manifest mode is enabled, record `design.md` sha256/mtime, upstream `requirement.md` and `docs/steering` hashes, mark `tasks.md` stale unless regenerated, and preserve unrelated manifest entries.

## Designer Clarification Gate And Loop

Before drafting detailed design, scan after repository and code inspection. If any unclear, ambiguous, or uncertain point would affect architecture, component boundaries, public/internal APIs, data models, persistence, transactions, permissions, security/privacy, compatibility, performance/capacity, error handling, observability, rollout, or test strategy, ask for clarification using `clarification.md`.

After drafting or updating `design.md`, do not end the run. Self-evaluate the draft and surface real decisions that need the user's judgement:

1. Trade-offs affecting cost, latency, consistency, UX, or future flexibility.
2. Security/privacy risks such as authn/authz assumptions, tenant isolation, PII, input trust boundaries, injection, SSRF, deserialization, secret storage, audit logging, or rate limiting.
3. Other open questions: ambiguous requirements, non-obvious defaults, performance/capacity, partial failure, idempotency, compensating actions, observability, compatibility, rollout.

For each item, state where it lives in `design.md` or where it will be recorded, list options and implications, and mark a recommendation. Stop only when no unresolved items remain and the user has confirmed the design.

## Required Structure

Use section names translated into the document's language:

```markdown
# 设计文档：<Feature / Initiative Name>

## 概述

### 设计决策

## 架构

## 组件与接口

## 数据模型

## 事务与并发边界

## 正确性属性

## 错误处理

## 测试策略
```

## Content Requirements

- `概述`: summarize design goal, scope, context, and core approach in 2-3 sentences; cite steering docs and architecture conventions.
- `设计决策`: include a table with decision, chosen option, and rationale.
- `架构`: Mermaid diagrams are required when the change spans two or more components or introduces a cross-component sequence; optional for narrow single-component changes.
- `组件与接口`: numbered components with location, responsibility, and complete signatures in the repository's language, including native annotations/imports, constructors, fields, parameters, return types, and generics.
- `数据模型`: include domain model, persistence model, DDL/indexes, ORM/PO definitions, mappings, configuration keys, and data-format examples as applicable.
- `事务与并发边界`: for any write feature, declare transaction placement, propagation, rollback rules, scope boundaries, locking, idempotency keys, and consistency across external boundaries.
- `正确性属性`: use sequential `Property <N>` sections with this exact shape:

```markdown
### Property <N>: <property title>
*For any* <detailed description of the inputs, conditions, and invariant that must hold>
**验证需求：<comma-separated requirement serial numbers>**
```

- `错误处理`: include `### 错误常量定义`, `### 错误场景与处理策略`, `### 错误传播策略`, and `### 错误处理原则`; use the repository's existing error model.
- `测试策略`: include `### 属性测试（Property-Based Testing）`, `### 单元测试（Example-Based）`, and `### 集成测试`; use the project's actual framework and traceability tables. If property-based testing is not supported or not prescribed, say it is not applicable and explain why.

## Quality Self-Check

- Every requirement has design coverage.
- Every correctness property maps to requirement serial numbers.
- `组件与接口` contains concrete signatures, not prose only.
- Mermaid diagrams are present when cross-component.
- Error handling is complete and uses the repository's existing error model.
- `事务与并发边界` is present whenever the feature writes data.
- Testing strategy uses the project's actual test framework with traceability mapping.
- No design-affecting ambiguity remains unresolved or hidden as an assumption.
- No design introduces unsupported language features, annotations, or libraries.
