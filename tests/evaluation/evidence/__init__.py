"""证据引用模型与校验包。

本包定义 spec-ai-evaluation 使用的证据引用结构 `EvidenceReference`、
证据类型枚举 `EvidenceKind`、证据解析 `parse_reference`，以及后续
证据存在性校验 `verify_evidence`。

对外 API：

    from tests.evaluation.evidence import (
        EvidenceKind,
        EvidenceReference,
        parse_reference,
    )

设计依据：`docs/spec/spec-ai-evaluation/design.md` 「组件 2：证据模型与
校验」。本包只依赖 Python 标准库与 `tests.evaluation.errors`。
"""

from tests.evaluation.evidence.models import (
    EvidenceKind,
    EvidenceReference,
    parse_reference,
)

__all__ = [
    "EvidenceKind",
    "EvidenceReference",
    "parse_reference",
]
