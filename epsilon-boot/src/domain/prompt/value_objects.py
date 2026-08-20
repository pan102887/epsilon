"""Prompt 领域值对象模块。

本模块定义 Prompt 资产加载后的不可变值对象 :class:`LoadedPrompt`，
作为 :meth:`domain.prompt.ports.PromptRegistryPort.get` 的返回类型，
承载 Prompt 身份（``prompt_id`` / ``name`` / ``version``）与内容
（``content``）。

领域层约束：本模块仅依赖 Python 标准库（``dataclasses`` / ``re``）与
``__future__`` 注解工具；**不得**引入 ``pydantic`` / ``pydantic-settings`` /
``infrastructure.*`` 等外部或基础设施依赖，以维持 ``domain/`` 对存储与
框架的无感知。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_PATTERN = re.compile(r"^v[1-9]\d*$")
"""合法 Prompt 版本号正则：小写 ``v`` + 无前导零正整数。

用于在 :meth:`LoadedPrompt.__post_init__` 中校验 ``version`` 字段。
匹配 ``v1`` / ``v2`` / ``v10``；不匹配 ``v0`` / ``v01`` / ``v1.0.0`` /
大写 ``V1`` / 空字符串等非法写法。
"""

_PROMPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9\-]*@v[1-9]\d*$")
"""合法 Prompt 标识符正则：以小写字母开头的 ``name`` + ``@`` + ``v<N>``。

``name`` 段由小写字母与连字符组成（首字符必须为小写字母），``version``
段沿用 :data:`_VERSION_PATTERN` 的正整数版本约定。示例：
``chat-default@v3`` / ``task-template@v1``。
"""


@dataclass(frozen=True)
class LoadedPrompt:
    """已加载 Prompt 值对象。

    表达一次在启动期已被 ``FilesystemPromptRegistryAdapter`` 从资产目录
    成功加载的 Prompt 快照。构造后不可变，字段由适配器一次性赋值并
    在容器生命周期内重复使用。

    Attributes:
        prompt_id: 组合标识符，形如 ``chat-default@v3``；等于
            ``f"{name}@{version}"``，且满足 :data:`_PROMPT_ID_PATTERN`。
        name: Prompt 名称，小写字母开头，仅包含小写字母、数字与连字符。
        version: Prompt 版本号，形如 ``v3``，满足 :data:`_VERSION_PATTERN`。
        content: Prompt 文本内容，UTF-8 解码后的原文，不得为空白字符串；
            本字段**不**包含 ``_WORKSPACE_PATH_GUIDANCE``（路径规范文案
            由 Prompt 消费方在构造 ``AgentConfig.system_prompt`` 时由
            ``append_workspace_path_guidance`` 幂等追加）。
    """

    prompt_id: str
    name: str
    version: str
    content: str

    def __post_init__(self) -> None:
        """校验字段一致性与非空语义。

        校验顺序：

        1. ``content`` 非 ``None`` 且 ``strip()`` 后非空；
        2. ``version`` 匹配 :data:`_VERSION_PATTERN`（``v<正整数>``，
           无前导零）；
        3. ``prompt_id`` 严格等于 ``f"{name}@{version}"``；
        4. ``prompt_id`` 整体匹配 :data:`_PROMPT_ID_PATTERN`。

        Raises:
            ValueError: 任一校验失败时抛出；错误消息指明违反的校验规则
                与实际取值，便于调用方定位。
        """
        if not self.content or not self.content.strip():
            raise ValueError("LoadedPrompt.content 不能为空白")
        if not _VERSION_PATTERN.match(self.version):
            raise ValueError(f"非法版本号：{self.version!r}，期望 v<正整数>（示例：v1、v2、v10）")
        expected_id = f"{self.name}@{self.version}"
        if self.prompt_id != expected_id:
            raise ValueError(
                "prompt_id 与 name@version 不一致："
                f"prompt_id={self.prompt_id!r}，期望={expected_id!r}"
            )
        if not _PROMPT_ID_PATTERN.match(self.prompt_id):
            raise ValueError(f"非法 prompt_id 格式：{self.prompt_id!r}")
