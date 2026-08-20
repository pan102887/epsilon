"""Prompt 领域异常模块。

本模块定义 Prompt 领域层的运行期异常 :class:`PromptNotFoundError`。
与基础设施层的启动期 ``ConfigurationError`` 家族不同，本异常专表
"注册表构造成功、但运行期 :meth:`PromptRegistryPort.get` 被传入未注册
名称"的编程错误场景。

领域层约束：本模块**不**依赖 ``common.configuration.ConfigurationError``
或 ``infrastructure.*``，继承自 Python 标准库的 :class:`RuntimeError`，
以严格维持领域层对基础设施的无感知。
"""

from __future__ import annotations


class PromptNotFoundError(RuntimeError):
    """``PromptRegistryPort.get(name)`` 找不到对应已加载 Prompt 时抛出。

    正常路径下启动期校验已覆盖所有已配置 ``Prompt_Name``；本异常仅在
    Prompt 消费方传入硬编码的未注册名称（编程错误）时触发。不继承
    ``ConfigurationError`` 以避免与启动期 fail-fast 错误族混淆。

    Attributes:
        name: 被查询的 Prompt 名称。
        registered: 已注册的 Prompt 名称列表（字符串列表快照），便于错误
            消息诊断调用方是否把名称拼错。
    """

    def __init__(self, name: str, registered: list[str]) -> None:
        """记录被查询名称与已注册名称列表，生成中文错误消息。

        Args:
            name: 被查询的 Prompt 名称。
            registered: 已注册的 Prompt 名称列表；构造时会拷贝为独立
                ``list``，避免被外部修改。
        """
        self.name = name
        self.registered = list(registered)
        super().__init__(f"Prompt 未注册：name={name!r}，已注册={self.registered}")
