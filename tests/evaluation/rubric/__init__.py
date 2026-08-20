"""评测 Rubric 包。

本包持有 spec-ai-evaluation 的 7 个评估维度、各维度权重、1-5 级
评分判据以及业界框架引用条目，全部以不可变数据结构对外暴露。

对外 API 仅需从本包导入：

    from tests.evaluation.rubric import (
        DimensionId,
        DimensionRubric,
        FrameworkCitation,
        RubricLevel,
        load_rubric,
    )

所有数据由人工依据 `docs/spec/spec-ai-evaluation/design.md` 与
公开业界资料撰写；本包不依赖任何基础设施（FastAPI / Redis / LLM
客户端），可被评测脚本与自测用例安全导入。
"""

from tests.evaluation.rubric.dimensions import (
    DimensionId,
    DimensionRubric,
    FrameworkCitation,
    RubricLevel,
    load_rubric,
)

__all__ = [
    "DimensionId",
    "DimensionRubric",
    "FrameworkCitation",
    "RubricLevel",
    "load_rubric",
]
