# spec-ai-evaluation 交付总结

## Feature

- **名称**: spec-ai-evaluation
- **描述**: AI Agent 工作台系统性评估——产出七维度评估报告 + 三项核心自动化评测脚本 + 回归对比工具链
- **状态**: 已完成

## 最终产物清单

### 文档产出 (`docs/evaluation/`)

| 文件 | 用途 |
|---|---|
| `report.md` | 主报告（执行摘要 / 评分汇总表 / 改进清单 / 附录） |
| `scores.toml` | 人工评分源（7 维度 × score + rationale + evidence） |
| `scores.json` | 机器可读聚合结果（由 aggregate_scores 自动生成） |
| `dimensions/1-architecture.md` | 维度 1 子报告 |
| `dimensions/2-agent-core.md` | 维度 2 子报告 |
| `dimensions/3-model-prompt.md` | 维度 3 子报告 |
| `dimensions/4-security.md` | 维度 4 子报告 |
| `dimensions/5-reliability.md` | 维度 5 子报告 |
| `dimensions/6-testability.md` | 维度 6 子报告 |
| `dimensions/7-frontend-ux.md` | 维度 7 子报告 |
| `results/*.json` | 评测运行快照 |
| `results/dry-run-*.log` | 端到端演练日志 |

### 评测代码 (`tests/evaluation/`)

| 模块 | 用途 |
|---|---|
| `rubric/dimensions.py` | 7 维度 × 5 级 Rubric + 业界框架引用 |
| `evidence/catalog.py` | 33 条证据清单（每维度 ≥ 3） |
| `evidence/models.py` + `verifier.py` | 证据解析与校验 |
| `stubs/model_access.py` | 桩 `ModelAccessPort`（scripted + stream） |
| `stubs/agent_registry.py` | 桩 `AgentRegistryPort` |
| `stubs/session_context_store.py` | 桩 `SessionContextStorePort` |
| `runner/runner.py` + `models.py` | EvalRunner + 数据模型 |
| `metrics/test_tool_call_success_rate.py` | 指标 1：工具调用成功率（20 样本） |
| `metrics/test_delegation_correctness.py` | 指标 2：委派正确性（15 样本） |
| `metrics/test_context_compaction_effectiveness.py` | 指标 3：压缩有效性（36 样本） |
| `self_tests/` | 9 个自测文件（Rubric / 证据 / Runner / 端到端等） |

### 脚本入口 (`scripts/evaluation/`)

| 脚本 | 用途 | 退出码 |
|---|---|---|
| `run_eval.py` | 评测主入口 | 0/1/2 |
| `compare_baseline.py` | 回归对比 | 0/1/2 |
| `aggregate_scores.py` | 评分聚合 + 报告刷新 | 0/1 |
| `verify_evidence.py` | 证据存在性校验 | 0/1 |
| `README.md` | QA/平台工程师操作手册 | — |

## 关键设计决策

1. **不改业务代码**：全部产物落在 `docs/evaluation/` / `tests/evaluation/` / `scripts/evaluation/` 白名单目录
2. **桩 Port 注入**：通过结构类型匹配（鸭子类型）注入 `ScriptedModelAccess` 等桩，不触碰 DI 容器或业务配置
3. **确定性判定**：三项指标全部走桩 + 规则判定，无 LLM-as-judge，无外部网络依赖
4. **回归阈值**：百分点差（pp）语义，默认 5.0pp，退出码 2 表示回退
5. **AUTO 区块合并**：`aggregate_scores.py` 以命名区块替换策略刷新 `report.md`，人工段落不被覆盖
6. **v3 兼容**：评测桩 `stream()` 产出等价 StreamingChunk，适配 agent-adapter-refactor-v3 全程 stream 决策

## 测试覆盖

- **self_tests**: 45 passed
- **metrics (evaluation)**: 71 passed
- **metrics (evaluation_self)**: 3 passed
- **合计**: 119 passed
- **唯一已知未通过**: `test_delivery_path_guard`（git 未提交文件不在 `git diff --name-only HEAD` 输出中，属正常状态）

## 指标基线

| 指标 | 比率 | 样本数 |
|---|---|---|
| tool_call_success_rate | 0.3000 | 20 |
| delegation_correctness | 0.4000 | 15 |
| context_compaction_effectiveness | 1.0000 | 36 |

加权总分：**3.560 / 5**

## 已知限制与后续

1. **证据行号漂移**：`reliability` 维度引用的 `routers/chat.py:L108-L134` 因 v3 refactor 文件缩短而失效，需更新 `catalog.py` 中对应行号
2. **CI 集成**：评测脚本尚未接入 CI 门禁（作为 P0 改进建议 IR-TEST-01 登记）
3. **LLM-as-judge**：本期全部确定性判定；未来若引入 LLM 判定，在 `tests/evaluation/judges/` 单独模块化
