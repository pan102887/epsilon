"""Agent 工具抽象层模块。

本模块提供通用的 Tool 抽象基类体系，用于定义、注册和执行 LLM 工具调用。
核心组件包括：

- Tool：工具抽象基类，定义工具的名称、描述、参数 schema、类型转换、参数校验和执行逻辑
- ToolRegistry：工具注册表，集中管理所有已注册的 Tool 实例，支持按名称查找和执行
- ToolExecutionError：工具执行异常基类
- ToolNotFoundError：工具未找到异常
- ToolParameterValidationError：工具参数校验失败异常
- HandoffPerformed：Handoff 控制转移成功信号（不是错误）
- DelegationRequest / HandoffResult：并行委派与 handoff 值对象
"""

from .exceptions import (
    HandoffPerformed,
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterValidationError,
)
from .guardrails import (
    GuardrailAction,
    GuardrailDecision,
    GuardrailEvaluationStage,
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailObservation,
    GuardrailPolicy,
    GuardrailReason,
    GuardrailRuntimeStats,
    GuardrailSummary,
    TaskExecutionClass,
    ToolRiskLevel,
    estimate_guardrail_model_cost,
    mark_guardrail_summary_stale,
    merge_guardrail_summary,
)
from .tools import Tool, ToolRegistry
from .value_objects import DelegationRequest, HandoffResult

__all__ = [
    "DelegationRequest",
    "GuardrailAction",
    "GuardrailDecision",
    "GuardrailEvaluationStage",
    "GuardrailMode",
    "GuardrailModelPricing",
    "GuardrailObservation",
    "GuardrailPolicy",
    "GuardrailReason",
    "GuardrailRuntimeStats",
    "GuardrailSummary",
    "HandoffPerformed",
    "HandoffResult",
    "TaskExecutionClass",
    "Tool",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolParameterValidationError",
    "ToolRegistry",
    "ToolRiskLevel",
    "estimate_guardrail_model_cost",
    "mark_guardrail_summary_stale",
    "merge_guardrail_summary",
]
