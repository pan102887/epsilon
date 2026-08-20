# ddd-implementation-review — 落地总结（需求 6）

## Feature

`ddd-implementation-review`：一轮「本项目 DDD 落地 vs 业界主流」的分析衍生出的重构清单（需求 1–6）。**本轮仅落地需求 6：补齐本项目缺失的 DDD 战术建模规范约束**——从规范根源堵住需求 1–5 那些代码偏差的复发。需求 1–5 的代码级纠偏不在本轮范围内。

## 背景一句话

现有 steering 在**分层/六边形架构**与**工程治理（ADR/SRP/change-discipline）**维度达标甚至超越主流，但**战术设计维度覆盖不完整**：`ddd-architecture.md` 全文仅一句提及「实体/值对象/领域事件」，无任何建模规则——于是贫血模型在现规范下反而「合规」。需求 6 补齐这道护栏。

## 最终产物清单

### 新增
- `docs/steering/ddd-tactical-modeling.md` — 战术建模 steering 规范（`inclusion: always`），9 节：概述/值对象/实体/领域服务与放置规则/聚合根与聚合边界判定/仓储 Port 语义/限界上下文与通用语言/不推荐领域事件/与其它规范衔接。各构件以真实正向样板 `RunStateMachine`、`WorkflowExecutionPolicy.validate()`、`ReadinessAggregator.check_readiness()`、`WorkspacePolicy.resolve()` 举例。
- `docs/adr/0007-establish-domain-tactical-modeling-and-pydantic-boundary.md` — ADR-0007（`Accepted`），四段式，`supersedes:` 留空、尊重不回退 ADR-0001。

### 修订
- `docs/steering/pydantic-model.md` — 第 3/9/21 行三处整行精确替换，消解「领域层用 Pydantic」二义（属 change-discipline §4 显式规范修订）。
- `docs/steering/README.md`、`docs/adr/README.md`、仓库根 `CLAUDE.md`、`AGENT.md` — 各新增一行索引（doc-sync §3；`AGENT.md` 实际存在，按 tasks 4.3 兜底一并同步）。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 战术规范承载 | 独立文件 `ddd-tactical-modeling.md`，不并入 `ddd-architecture.md` | SRP，避免分层规范膨胀 |
| Pydantic 边界 | 领域层用 dataclass、Pydantic 仅在 API/DTO/配置边界 | 代码以脚投票（domain 19 dataclass / 0 Pydantic） |
| ADR-0007 vs ADR-0001 | 不 supersede，显式尊重 | 领域事件移除是既定前提 |
| 聚合边界 | 轻量约束 + 何时才需引入的判定指引 | Agent 工作台会话/流式态，避免过度设计 |
| 限界上下文 | 子域目录即天然上下文，不引 Context Map | AC7 轻量表达 |
| 仓储语义 | 以 `ports.py` 的 Port 承载，不新增 `repository.py` | 与既有 Port 实践一致 |

## 验证结论

零源码改动（`git diff` 改动全在 `docs/` 与根索引，`epsilon-boot/` 零命中）。文档一致性校验全绿：
- Pydantic 冲突措辞残留 0；新措辞已落地。
- 战术规范四正向样板符号均命中；领域事件仅出现在「不推荐/已移除」语境并回链 ADR-0001。
- ADR-0007 存在、`supersedes:` 为空、README 已登记；三处（四处含 AGENT.md）索引一致。
- `PYTHONPATH=src uv run --frozen pytest` → **2824 passed, 3 skipped, 0 failed**（零源码影响旁证）。

AC1–AC11 与 Property 1–6 全覆盖（详见 `tasks.md` 追溯表与 `review-log.md`）。本轮纯文档切片，按流水线规则跳过 spec-evaluator，以校验命令 + 全绿测试为验收依据。

## 后续事项（Follow-ups，均不在本轮范围）

- **需求 1**（高风险）：`react_agent_adapter.py`（3310 行）Agent Loop 上提领域层——建议独立 spec + 先写 ADR。
- **需求 2**（中）：以本规范为据，择一子域（`domain/task` 或 `domain/agent`）做贫血模型充血化试点。
- **需求 3**（低）：`domain/` 内 `to_dict()` 序列化职责外移。
- **需求 4**（低）：应用层大文件（`container_config.py` 等）拆分方案登记。
- **需求 5**（轻微）：`domain/chat/context.py` 移除 `logging` 依赖。
