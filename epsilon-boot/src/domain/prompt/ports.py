"""Prompt 领域端口模块。

本模块定义 :class:`PromptRegistryPort` 协议，描述"按名取回已加载 Prompt"
的领域能力。协议本身不声明异步方法、不声明 I/O 相关异常；所有磁盘 I/O
均由基础设施层的 ``FilesystemPromptRegistryAdapter`` 在**构造阶段**
一次性完成，构造成功后运行期 :meth:`PromptRegistryPort.get` 零 I/O。

领域层约束：本模块仅导入 ``typing.Protocol`` 与同子包的
:class:`domain.prompt.value_objects.LoadedPrompt`；**禁止**引入
``infrastructure.*`` / ``pydantic-settings`` / 文件系统 SDK 等基础设施依赖。
"""

from __future__ import annotations

from typing import Protocol

from domain.prompt.value_objects import LoadedPrompt


class PromptRegistryPort(Protocol):
    """Prompt 注册表端口协议。

    由基础设施层提供实现（``FilesystemPromptRegistryAdapter``），领域层与
    应用层仅依赖此抽象。实现方应保证：

    - 构造阶段完成所有 I/O（目录扫描、文件读取、UTF-8 解码、非空校验），
      启动期任一校验失败以 ``ConfigurationError`` 子类 fail-fast；
    - 构造成功后实例对外只读，:meth:`get` 在运行期不触发磁盘 I/O；
    - 未注册的名称必须抛出 :class:`domain.prompt.exceptions.PromptNotFoundError`
      （领域异常），不得返回 ``None`` 或以任意默认 Prompt 替代。
    """

    def get(self, name: str) -> LoadedPrompt:
        """按 Prompt 名称返回已加载的值对象。

        Args:
            name: Prompt 名称（如 ``chat-default``）。

        Returns:
            对应的 :class:`LoadedPrompt` 实例（启动期快照，运行期不变）。

        Raises:
            PromptNotFoundError: ``name`` 未在启动期加载时抛出；正常路径下
                启动期校验已覆盖全部已配置名称，运行期触发本异常通常意味着
                Prompt 消费方传入了硬编码的错误名称（编程错误）。
        """
        ...

    def list_names(self) -> list[str]:
        """列出已加载的 Prompt 名称。

        Returns:
            Prompt 名称列表，顺序与启动期扫描 / 加载顺序保持一致。
            返回值应视为只读快照，调用方不得依赖可变性。
        """
        ...
