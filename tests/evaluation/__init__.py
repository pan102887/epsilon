"""评测代码包入口。

该包位于仓库根 `tests/evaluation/`，承载 AI Agent 工作台系统性评估
（spec-ai-evaluation）所需的 Rubric 定义、证据模型、桩依赖、Runner、
以及三项核心指标的评测样本。

所有模块均属于"评测视图"代码：
- 不在 `epsilon-boot/src/domain/`、`application/`、`infrastructure/`、
  `common/` 下新增或修改文件；
- 不触碰 `epsilon-client/` 业务代码；
- 仅通过业务公开领域模型与 Port 协议达成解耦（结构类型匹配，禁止继承 Adapter）。

详细背景与设计请阅读 `docs/spec/spec-ai-evaluation/design.md`。
"""
