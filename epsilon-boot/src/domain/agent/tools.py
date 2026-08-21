
"""Agent 工具抽象基类、结构化返回值与注册表模块。

提供 Tool 抽象基类，定义工具的统一接口规范，包括名称、描述、参数 schema、
类型转换、参数校验和执行逻辑。具体工具通过继承 Tool 来实现。

提供 ToolExecutionResult 值对象，作为 ``Tool.execute()`` 的统一返回类型，
封装回灌给 LLM 的文本内容和供 trace 记录使用的结构化元数据。

同时提供 ToolRegistry 工具注册表，集中管理所有已注册的 Tool 实例，
支持按名称查找、注册、移除和批量执行。
"""

import json
from abc import ABC, abstractmethod
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any

from common.json_schema_params import cast_json_schema_params, validate_json_schema_params
from domain.agent.exceptions import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterValidationError,
    ToolPermissionDeniedError,
)
from domain.agent.guardrails import ToolRiskLevel
from domain.model_access.value_objects import ToolCallRequest
from domain.run.value_objects import ToolReplayPolicy, ToolSideEffectLevel


def _metadata_dict() -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class ToolExecutionResult:
    """工具执行结果值对象。

    封装工具执行完成后的返回数据，包含回灌给 LLM 的文本内容和供 trace 记录使用的
    结构化元数据。

    ``content`` 字段语义等价于原 ``Tool.execute()`` 返回的 ``str``——它是回灌给
    LLM 上下文的完整文本，可按 ``RESULT_SUMMARY_MAX_LEN`` 截断后写入 trace 的
    ``result_summary`` 字段，但原始值始终完整回灌 LLM。

    ``metadata`` 字段为工具类型特有的结构化元数据 dict，值类型为 ``Any`` 的原因：
    metadata 为 free-form trace 扩展字段，不同工具产出的键值类型天然异构（int、
    str、bool 等），非 API 契约字段，不作为公共接口校验目标。各工具须在
    docstring 中说明每个 metadata 键的含义与类型。

    Attributes:
        content: 回灌给 LLM 的文本内容，等价于原 ``execute() -> str`` 的返回值。
        metadata: 供 trace 记录的结构化元数据，默认空 dict。
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=_metadata_dict)


class Tool(ABC):
    """工具抽象基类。

    定义了所有具体工具必须遵循的统一接口规范。每个工具自包含名称、描述、
    参数 schema、类型转换、参数校验和执行逻辑。

    子类必须实现以下抽象成员：
    - name: 工具唯一名称
    - description: 工具功能描述
    - parameters: JSON Schema 格式的参数描述
    - execute: 异步执行方法

    基类提供以下具体方法：
    - cast_params: 根据 schema 自动转换参数类型
    - validate_params: 校验参数是否符合 schema 约束
    - to_schema: 生成 OpenAI function calling 格式的 schema
    - run: 接受 ToolCallRequest，执行完整的解析→转换→校验→执行流水线
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具的唯一名称。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具的功能描述。"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。

        返回值应包含 ``"type": "object"``、``"properties"`` 和可选的 ``"required"`` 字段，
        用于向 LLM 描述工具的输入参数结构。
        """
        ...

    @property
    def timeout_seconds(self) -> float | None:
        """工具级执行超时（秒），``None`` 表示沿用 ``AgentConfig.tool_timeout_seconds``。

        语义约定：

        - ``None``（默认）：沿用 ``AgentConfig.tool_timeout_seconds`` 全局默认；
          全局亦为 ``None`` 时不引入 ``asyncio.wait_for`` 包裹。
        - ``> 0``：覆盖全局值，作为本工具的超时上限；超时后视为
          ``is_error=True``，``ToolMessage.metadata["error"] = True``，
          回灌内容为 ``"工具执行超时（{N}s)"``。

        既有抽象成员（``name`` / ``description`` / ``parameters`` / ``execute``）
        签名不变，本属性以 ``return None`` 默认实现追加，**不破坏**既有具体
        工具子类（NFR-2 不变量保持）。
        """
        return None

    @property
    def risk_level(self) -> ToolRiskLevel:
        """工具风险等级，未知工具默认按高风险观察。"""
        return ToolRiskLevel.HIGH

    @property
    def side_effect_level(self) -> ToolSideEffectLevel:
        """工具副作用级别，默认按外部写入处理以保持恢复安全。"""
        return ToolSideEffectLevel.EXTERNAL_WRITE

    @property
    def replay_policy(self) -> ToolReplayPolicy:
        """工具恢复重放策略，默认需要人工确认以避免重复副作用。"""
        return ToolReplayPolicy.MANUAL_REVIEW

    def idempotency_key(
        self,
        request: ToolCallRequest,
        execution_key: str,
    ) -> str | None:
        """返回外部幂等键；默认不声明工具具备外部幂等能力。"""
        return None

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行工具逻辑。

        由子类实现具体的工具执行逻辑。参数已经过 cast_params 类型转换
        和 validate_params 校验。

        Args:
            **kwargs: 经过类型转换和校验的工具参数。

        Returns:
            ToolExecutionResult，包含回灌给 LLM 的文本内容和供 trace
            记录使用的结构化元数据。
        """
        ...

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """根据 parameters schema 对参数值进行安全类型转换。

        按 JSON Schema 递归处理 ``properties``、``items``、``anyOf`` / ``oneOf``
        和 ``type`` 列表等常见结构，只执行无歧义转换。转换失败时保留原始值，
        由后续 ``validate_params`` 给出标准 schema 校验错误。

        Args:
            params: 待转换的参数字典（通常来自 JSON 解析结果）。

        Returns:
            类型转换后的参数字典（新字典，不修改原始输入）。
        """
        return cast_json_schema_params(params, self.parameters)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """校验参数是否符合 parameters JSON Schema 约束。

        本方法委托 ``jsonschema`` 执行标准校验，可覆盖 ``required``、``enum``、
        ``minItems`` / ``maxItems``、嵌套 ``items``、``anyOf`` / ``oneOf`` 等
        JSON Schema 约束。schema 自身非法时返回错误列表而非抛出运行时异常。

        Args:
            params: 待校验的参数字典（应已经过 cast_params 转换）。

        Returns:
            校验错误信息列表，为空表示校验通过。
        """
        return validate_json_schema_params(params, self.parameters)

    def to_schema(self) -> dict[str, Any]:
        """生成符合 OpenAI function calling 格式的 schema 字典。

        返回的字典可直接传递给 LLM API 的 tools 参数。

        Returns:
            包含 type、function（含 name、description、parameters）的 schema 字典。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def run(self, request: ToolCallRequest) -> ToolExecutionResult:
        """处理 ToolCallRequest，执行完整的工具调用流水线。

        流水线步骤：
        1. 将 request.arguments（JSON 字符串）解析为 dict
        2. cast_params 类型转换
        3. validate_params 参数校验
        4. execute 执行工具逻辑

        Args:
            request: LLM 返回的工具调用请求。

        Returns:
            ToolExecutionResult，包含回灌给 LLM 的文本内容和结构化元数据。

        Raises:
            ToolParameterValidationError: JSON 解析失败或参数校验不通过。
            ToolExecutionError: 工具执行过程中发生异常。
        """
        # 1. JSON 解析
        try:
            params = json.loads(request.arguments)
        except json.JSONDecodeError as e:
            raise ToolParameterValidationError(
                tool_name=self.name,
                errors=[f"JSON 解析失败: {e}"],
            ) from e

        # 2. 类型转换
        params = self.cast_params(params)

        # 3. 参数校验
        errors = self.validate_params(params)
        if errors:
            raise ToolParameterValidationError(
                tool_name=self.name,
                errors=errors,
            )

        # 4. 执行
        try:
            return await self.execute(**params)
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                message=str(e),
                tool_name=self.name,
            ) from e


class ToolRegistry:
    """工具注册表。

    集中管理所有已注册的 Tool 实例，提供按名称注册、查找、移除和批量执行能力。
    通过 execute 方法可直接处理 ToolCallRequest，自动查找对应工具并委托执行。
    """

    def __init__(self, circuit_breaker: Any = None) -> None:
        """初始化工具注册表。

        Args:
            circuit_breaker: 可选的工具熔断器实例（需有 ``guard(tool_name)``
                异步上下文管理器方法）。为 None 时不启用熔断保护。
        """
        self._tools: dict[str, Tool] = {}
        self._circuit_breaker = circuit_breaker

    def register(self, tool: Tool) -> None:
        """注册一个工具实例。

        按工具的 name 属性将其存入内部字典。若同名工具已存在，则覆盖。

        Args:
            tool: 要注册的 Tool 实例。
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称查找已注册的工具。

        Args:
            name: 工具名称。

        Returns:
            对应的 Tool 实例，未找到时返回 None。
        """
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """判断指定名称的工具是否已注册。

        Args:
            name: 工具名称。

        Returns:
            已注册返回 True，否则返回 False。
        """
        return name in self._tools

    def unregister(self, name: str) -> None:
        """按名称移除已注册的工具。

        若指定名称的工具不存在，则静默忽略。

        Args:
            name: 要移除的工具名称。
        """
        self._tools.pop(name, None)

    def get_schemas(self, tool_names: AbstractSet[str] | None = None) -> list[dict[str, Any]]:
        """返回已注册工具的 schema 列表，支持按名称子集过滤。

        当 tool_names 为 None 时，返回所有已注册工具的 schema 列表（向后兼容）。
        当 tool_names 为非空 set 时，仅返回名称在 tool_names 中的工具 schema。
        当 tool_names 为空 set 时，返回空列表。
        tool_names 中包含未注册的工具名称时，静默忽略。

        Args:
            tool_names: 可选的工具名称集合，为 None 时返回全量 schema。

        Returns:
            符合条件的工具 OpenAI function calling 格式 schema 列表。
        """
        if tool_names is None:
            return [tool.to_schema() for tool in self._tools.values()]
        return [tool.to_schema() for name, tool in self._tools.items() if name in tool_names]

    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        """查找并执行工具调用请求。

        根据 request.name 在注册表中查找对应工具，找到后委托给 Tool.run() 执行。
        未找到时抛出 ToolNotFoundError。

        Args:
            request: LLM 返回的工具调用请求。

        Returns:
            ToolExecutionResult，包含回灌给 LLM 的文本内容和结构化元数据。

        Raises:
            ToolNotFoundError: 请求的工具名称未在注册表中。
            ToolParameterValidationError: 参数校验失败。
            ToolExecutionError: 工具执行过程中发生异常。
        """
        tool = self._tools.get(request.name)
        if tool is None:
            raise ToolNotFoundError(tool_name=request.name)
        if self._circuit_breaker is not None:
            async with self._circuit_breaker.guard(tool.name):
                return await tool.run(request)
        return await tool.run(request)

    def create_scoped_view(self, tool_names: frozenset[str]) -> "ScopedToolRegistry":
        """创建工具作用域视图。

        返回一个 ScopedToolRegistry 实例，该实例仅暴露 tool_names 指定的工具子集。
        创建时快照语义：后续注册新工具不影响已创建视图的作用域。

        Args:
            tool_names: 允许的工具名称集合（frozenset，不可变）。

        Returns:
            仅暴露指定工具子集的 ScopedToolRegistry 实例。
        """
        return ScopedToolRegistry(registry=self, tool_names=tool_names)


class ScopedToolRegistry:
    """工具作用域视图。

    ToolRegistry 的轻量包装器，仅暴露指定工具子集的 get_schemas() 和 execute() 接口。
    不持有独立的工具存储，通过委托底层 ToolRegistry 实现功能。
    创建时快照语义：后续底层 ToolRegistry 注册新工具不影响已创建视图的作用域。

    Attributes:
        _registry: 底层 ToolRegistry 实例的引用
        _allowed_names: 允许的工具名称集合（frozenset，不可变）
    """

    def __init__(self, registry: ToolRegistry, tool_names: frozenset[str]) -> None:
        """初始化工具作用域视图。

        Args:
            registry: 底层 ToolRegistry 实例。
            tool_names: 允许的工具名称集合。
        """
        self._registry = registry
        self._allowed_names = tool_names

    def get_schemas(self) -> list[dict[str, Any]]:
        """返回作用域内工具的 schema 列表。

        委托底层 ToolRegistry.get_schemas()，仅返回 _allowed_names 中的工具 schema。

        Returns:
            作用域内工具的 OpenAI function calling 格式 schema 列表。
        """
        return self._registry.get_schemas(tool_names=self._allowed_names)

    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        """执行工具调用请求，仅允许作用域内的工具。

        先校验 request.name 是否在 _allowed_names 中，不在则抛出
        ToolPermissionDeniedError，在则委托底层 ToolRegistry.execute() 执行。

        Args:
            request: LLM 返回的工具调用请求。

        Returns:
            ToolExecutionResult，包含回灌给 LLM 的文本内容和结构化元数据。

        Raises:
            ToolPermissionDeniedError: 请求的工具不在作用域内。
            ToolNotFoundError: 请求的工具名称未在注册表中。
            ToolParameterValidationError: 参数校验失败。
            ToolExecutionError: 工具执行过程中发生异常。
        """
        if request.name not in self._allowed_names:
            raise ToolPermissionDeniedError(
                tool_name=request.name,
                allowed_tools=self._allowed_names,
            )
        return await self._registry.execute(request)
