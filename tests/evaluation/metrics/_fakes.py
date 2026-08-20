"""三项核心指标评测用例共享的内置辅助对象。

本模块收敛指标样本所需的"最小真实实现"和"轻量桩"，避免在三份 ``test_*.py``
中复制代码。具体包含：

- :class:`FakeEchoTool`：最小可注册的 ``Tool`` 子类，用于 Tool_Call_Success_Rate
  指标覆盖"正常成功""返回空串""参数非法"等场景。
- :class:`FakeFailingTool`：执行时抛业务异常的 ``Tool`` 子类，用于覆盖
  ``ToolExecutionError`` 场景。
- :class:`StaticModelRegistry`：结构类型匹配 ``ModelRegistryPort`` 的内存桩，
  用于 Delegation_Correctness 指标注入一个"按名直取"的 Provider 表。
- :func:`load_agent_max_delegation_depth`：从 ``epsilon-boot/config.properties``
  读取 ``AGENT_MAX_DELEGATION_DEPTH``，在文件不可用时回退到业务默认值 ``3``，
  与 ``src/application/container_config.py`` 的解析规则对齐。

这些对象仅用于评测路径；若 Runner 不调度，它们不会被导入，也不影响业务运行时。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import Tool, ToolExecutionResult
from domain.chat.ports import ContextBuilderPort
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ModelInfo
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)

# ---------------------------------------------------------------------------
# 工具桩
# ---------------------------------------------------------------------------


class FakeEchoTool(Tool):
    """最小 Echo 工具：返回参数 ``text`` 的字符串形式。

    用于 Tool_Call_Success_Rate 指标的"正常成功"与"返回空串"两类场景：
    正常样本传入 ``text="hi"`` → 返回 ``"hi"``；空串样本传入 ``text=""`` →
    返回空字符串，触发"返回长度 > 0"判据的反例。
    """

    @property
    def name(self) -> str:
        """工具唯一名称，固定为 ``fake_echo``。"""
        return "fake_echo"

    @property
    def description(self) -> str:
        """工具功能描述。"""
        return "回显参数 text，仅用于评测。"

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema：必填字符串 ``text``。"""
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "待回显的文本"},
            },
            "required": ["text"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """直接返回 ``text`` 参数。

        Args:
            **kwargs: 经过 ``cast_params`` / ``validate_params`` 后的参数。

        Returns:
            ``content`` 为 ``text`` 字符串形式的工具执行结果；若 ``text=""``，
            则结果内容为空字符串。
        """
        return ToolExecutionResult(content=str(kwargs.get("text", "")))


class FakeFailingTool(Tool):
    """执行时必抛 :class:`ToolExecutionError` 的工具。

    用于 Tool_Call_Success_Rate 指标的"执行异常"场景：模型被脚本指定调用
    本工具时，``ToolRegistry.execute`` 会包装 ``Tool.run`` 抛出的异常为
    ``ToolExecutionError``，而 :class:`ReActAgentAdapter.run` 会把错误消息
    作为 :class:`ToolMessage` 回写到上下文。评测判据据此记为分子贡献为 0。
    """

    @property
    def name(self) -> str:
        """工具唯一名称，固定为 ``fake_fail``。"""
        return "fake_fail"

    @property
    def description(self) -> str:
        """工具功能描述。"""
        return "永远执行失败，仅用于评测。"

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema：允许任意空参数。"""
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """始终抛出 :class:`ToolExecutionError`。

        Raises:
            ToolExecutionError: 固定消息 ``"模拟执行失败"``。
        """
        raise ToolExecutionError(message="模拟执行失败", tool_name=self.name)


# ---------------------------------------------------------------------------
# 模型注册中心桩
# ---------------------------------------------------------------------------


@dataclass
class StaticModelRegistry:
    """最小结构类型匹配 :class:`ModelRegistryPort` 的模型注册中心。

    评测仅需从 ``TaskAgentAdapter.execute`` 中为子 Agent 解析出一个
    :class:`ModelAccessPort`；真实 Provider 适配器会打开 HTTP 连接池，
    不适合评测路径。本桩通过 ``model -> ModelAccessPort`` 字典直接返回
    桩模型实例。

    Attributes:
        adapters: 模型名称到 :class:`ModelAccessPort` 的映射。
        default_model: 未显式指定模型时返回的默认名称。
    """

    adapters: dict[str, ModelAccessPort] = field(default_factory=dict)
    default_model: str = "scripted"

    def register_provider(
        self,
        provider_name: str,
        adapter: ModelAccessPort,
        models: list[str],
    ) -> bool:
        """按模型名称注册适配器；多名称指向同一适配器。

        Args:
            provider_name: 提供商唯一标识（桩实现仅记录日志时用）。
            adapter: 实现 :class:`ModelAccessPort` 协议的适配器实例。
            models: 该适配器承载的模型名称列表。

        Returns:
            成功则返回 ``True``；模型名列表为空时返回 ``False``。
        """

        if not models:
            return False
        for model in models:
            self.adapters[model] = adapter
        return True

    def list_models(self) -> list[ModelInfo]:
        """返回已注册模型信息列表（仅 id 字段有效）。"""

        return [ModelInfo(id=name) for name in self.adapters]

    def get_adapter_for_model(self, model: str) -> ModelAccessPort:
        """按模型名称返回适配器。

        Args:
            model: 模型名称；若未注册则尝试返回 ``default_model`` 对应的
                适配器。

        Returns:
            对应的 :class:`ModelAccessPort` 实例。

        Raises:
            KeyError: 模型与 ``default_model`` 均未注册。
        """

        if model in self.adapters:
            return self.adapters[model]
        if self.default_model in self.adapters:
            return self.adapters[self.default_model]
        raise KeyError(f"模型 {model!r} 未在 StaticModelRegistry 中注册")

    def get_default_model(self) -> str:
        """返回默认模型名称。"""

        return self.default_model


# ---------------------------------------------------------------------------
# Prompt 注册表桩
# ---------------------------------------------------------------------------


class StaticPromptRegistry:
    """最小结构类型匹配 ``PromptRegistryPort`` 的内存桩。

    仅为 ``TaskAgentAdapter.__init__`` 中的 ``prompt_registry.get('task-template')``
    调用提供一个占位 ``LoadedPrompt``。评测路径中 ``TaskAgentAdapter`` 实际执行
    时不会再次查询注册表（仅在构造期缓存 ``prompt_id``）。
    """

    def get(self, name: str) -> LoadedPrompt:
        """按名称返回占位 ``LoadedPrompt``。"""

        return LoadedPrompt(
            prompt_id=f"{name}@v1",
            name=name,
            version="v1",
            content=f"评测用占位 Prompt: {name}",
        )

    def list_names(self) -> list[str]:
        """返回空列表。"""

        return []


# ---------------------------------------------------------------------------
# 上下文构建器桩
# ---------------------------------------------------------------------------


class _NoopEnvironmentProvider:
    """评测用空环境上下文提供器。

    满足 ``ContextBuilderAdapter`` 对 ``EnvironmentContextProvider`` 结构类型的要求，
    但不注入任何环境消息。
    """

    def build(self) -> str:
        """返回空字符串，不注入环境上下文。"""
        return ""


def build_context_builder(max_messages: int = 50) -> ContextBuilderPort:
    """构建评测用的最小 ``ContextBuilderPort`` 实例。

    使用 ``SlidingWindowCompactionAdapter`` + 空环境提供器，
    与生产环境的上下文构建流程功能一致但不引入文件 I/O 或网络。

    Args:
        max_messages: 滑动窗口保留的最大非 system 消息数。

    Returns:
        可直接传入 ``ReActAgentAdapter(context_builder=...)`` 的实例。
    """

    compaction = SlidingWindowCompactionAdapter(max_messages=max_messages)
    return ContextBuilderAdapter(
        compaction=compaction,
        environment_provider=_NoopEnvironmentProvider(),
    )


# ---------------------------------------------------------------------------
# 配置读取工具
# ---------------------------------------------------------------------------


def load_agent_max_delegation_depth(
    properties_path: Path | None = None,
) -> int:
    """从 ``config.properties`` 读取 ``AGENT_MAX_DELEGATION_DEPTH``。

    按 ``src/application/container_config.py`` 的解析规则：``<= 0`` 时回退
    为默认值 ``3``；文件不存在或格式异常时同样回退为 ``3``，与业务行为对齐。

    本实现读取文件而非通过 ``config_proxy``，是因为 ``config_proxy`` 需要
    ``PropertiesBaseSettings`` 类作为入参；当前评测路径不需要热更新，直接
    解析文件足以得到确定的值。这一选择不改变最终语义：``config_proxy``
    底层也是读这同一份 ``config.properties``。

    Args:
        properties_path: 配置文件路径；``None`` 时按仓库结构自动定位到
            ``epsilon-boot/config.properties``。

    Returns:
        解析出的正整数；任何异常路径均回退为 ``3``。
    """

    if properties_path is None:
        # tests/evaluation/metrics/_fakes.py → repo root → epsilon-boot/config.properties
        properties_path = (
            Path(__file__).resolve().parents[3]
            / "epsilon-boot"
            / "config.properties"
        )
    try:
        for raw in properties_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "AGENT_MAX_DELEGATION_DEPTH":
                depth = int(value.strip())
                return depth if depth > 0 else 3
    except (OSError, ValueError):
        return 3
    return 3
