# 维度 1：架构与工程化

## 评估结论

**评分：4 / 5**。本仓库采用 DDD 六边形架构，Port 与 Adapter 一一映射、组合根集中在单一 `container_config.py`、自建 DI 容器托管全部异步资源生命周期；但"可插拔架构守卫"（`import-linter` 或等价的机器可读依赖方向检查）尚未落地，是 4 → 5 的主要差距。

## 证据与分析

- [`epsilon-boot/src/application/container_config.py:L1017-L1042`](../../../epsilon-boot/src/application/container_config.py)
  组合根集中 Port → Adapter 绑定：`ModelAccessPort` / `ModelRegistryPort` / `SessionContextStorePort` / `ToolRegistry` / `ContextCompactionPort` / `AgentPort` / `AgentRegistryPort` / `TaskAgentPort` / `DelegationPort` / `ChatServicePort` 全部通过 `container.register(...)` 装配，业务代码只 import Port 而不 import Adapter。这是"依赖单向"原则被真正落地的直接证据。
- [`epsilon-boot/src/common/container.py:L80-L98`](../../../epsilon-boot/src/common/container.py)
  自建 DI 容器同时持有 `_registry`（类型 → Provider 映射）与 `_async_resources`（生命周期列表）两套注册表，使"装配"与"启停"彻底解耦；`resolve` 通过循环依赖检测集 `_resolving` 抓出解析环，这在 `DelegateToAgentTool` 的运行时解析环被打破的场景里尤其关键。
- [`epsilon-boot/src/application/container_config.py:L982-L1011`](../../../epsilon-boot/src/application/container_config.py)
  按"telemetry → model_client → redis / local_persistence（二选一）→ gateway → workspace"五段式装配异步资源，关闭时逆序清理；Telemetry 最先初始化以保证后续资源启动也能被 trace 记录。
- [`docs/steering/ddd-architecture.md`](../../../docs/steering/ddd-architecture.md)
  Steering 规范显式列出 Port/Adapter 归属、禁止的导入方向与允许的例外。现状代码与该规范基本一致（评测脚本本身也遵循"测试代码可例外 import Adapter"的表述）；缺失的是 **机器自动执行** 的导入守卫。

## 业界框架对照

- **OpenAI — A Practical Guide to Building Agents（Agent design patterns）**：要求核心 Loop 与 Provider / Tool 相互解耦，能通过替换单一组件扩展能力。本仓库通过 `AgentPort` / `ToolRegistry` / `ModelAccessPort` 三条 Port 完成了替换面，但缺少"配置驱动替换 Agent 策略"的能力（目前只有 `ReActAgentAdapter` 一个绑定）。
- **Google ADK — Agent Development Kit（Multi-agent architecture patterns）**：建议 Multi-Agent 场景下的注册中心与委派入口要显式可测、可观测。`AgentRegistryPort` + `DelegationPort` 的拆分正好对齐，但注册来源仍是代码硬编码（`AgentRegistryAdapter`），缺少配置驱动注入。
- **Anthropic — Building effective agents**：强调"组合式 Workflow"与"Agent 本体"的二分；项目当前是"单 ReAct Adapter + Task Agent 委派"，未形成组合式 Workflow，处于 Anthropic 建议的"Agent"端。

## 改进建议

1. **P1 — 引入机器可读的架构守卫**：以 `import-linter`（或等价的自定义脚本）固化 `domain → application → infrastructure` 的导入方向；把评测脚本的白名单路径检查扩展为 PR 前置 CI 守卫。预期收益：杜绝"偶发的绕过 Port 直连 Adapter"类回归。
2. **P2 — Adapter 可替换策略落配置**：把 `AgentPort` / `ContextCompactionPort` 的实现类选择下沉到 `config.properties`（如 `AGENT_IMPL=react` / `COMPACTION_IMPL=sliding_window`），在 `container_config.py` 以分发表承接。预期收益：替换实验性 Adapter（如未来的 Plan-Execute、Reflect）不需要改组合根代码。
3. **P2 — Agent 注册表配置化**：把 `AgentRegistryAdapter` 的命名 Agent 清单迁到 `config.properties` 或独立 TOML，形成"配置驱动的多 Agent DAG"；与委派深度/权限白名单合并成完整的多 Agent 蓝图。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：4 / 5，**权重**：0.18，**加权得分**：0.720

**人工打分理由**：仓库采用 DDD 六边形架构，`container_config.py` 以组合根形式集中装配所有 Port→Adapter，自建 DI 容器通过 `_registry` / `_async_resources` 两套注册表 明确区分"装配"与"启停"，符合 OpenAI "A Practical Guide to Building Agents — Agent design patterns" 对核心 Loop 与 Provider/Tool 解耦的要求。Google ADK "Multi-agent architecture patterns" 建议的注册中心与委派入口 已通过 `AgentRegistryPort` + `DelegationPort` 落地，异步资源按五段式顺序启停 并逆序清理；距离 5 分的差距是缺少机器可读的架构守卫（如 `import-linter`）与 Adapter 策略配置化，这些按 Anthropic "Building effective agents" 组合式 Workflow 建议是下一步方向。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/src/application/container_config.py:1017-1042`
- `epsilon-boot/src/common/container.py:80-98`
- `epsilon-boot/src/application/container_config.py:982-1011`
- `docs/steering/ddd-architecture.md`

<!-- AUTO-END: aggregate_scores -->
