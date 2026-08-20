"""Prompt 版本配置模块。

基于 :class:`common.configuration.PropertiesBaseSettings`，从
``config.properties`` 与环境变量加载 ``PROMPT_*_VERSION`` 键，为每个已知
Prompt 名称提供单一字段（如 ``chat_default_version``）。本模块不承担文件
加载职责，只负责映射与格式校验；实际文件扫描由
``infrastructure.prompt.filesystem_prompt_registry_adapter``（后续切片）完成。

与 ``infrastructure.chat.chat_config`` 的关键差异：
:data:`prompt_version_config` **不** 经由 ``create_config`` 工厂包装为
``ConfigProxy``（见设计决策 #5）。原因是 Prompt 为审计关键字段，
``prompt_id`` 在启动时落定后必须与已记录的 trace / 日志一一对齐，
运行期热更新会破坏这一一致性。因此此处通过普通构造一次性读取
``config.properties``，实例生命周期与容器一致。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings

_VERSION_PATTERN = re.compile(r"^v[1-9]\d*$")
"""合法 ``Prompt_Version_Tag`` 正则：小写 ``v`` + 无前导零正整数。"""


class InvalidPromptVersionTagError(ConfigurationError):
    """``PROMPT_<NAME>_VERSION`` 值不符合 ``v<正整数>`` 格式时抛出。

    继承 :class:`common.configuration.ConfigurationError`，由容器启动期
    fail-fast 语义（``container.start()``）一并捕获。错误消息包含字段名、
    实际取值与期望格式示例，便于运维快速定位配置键。
    """


class PromptVersionConfig(PropertiesBaseSettings):
    """Prompt 版本映射配置。

    每个已注册的 ``Prompt_Name`` 对应一个 ``<name_snake>_version: str`` 字段，
    其中 ``name_snake`` 是 ``Prompt_Name`` 把连字符替换为下划线的形式。
    新增 Prompt 时，需同时在此类追加字段、在 ``config.properties`` 追加键、
    在 ``prompts/<name>/`` 下放置版本文件。

    字段取值必须符合 ``Prompt_Version_Tag`` 格式（``^v[1-9]\\d*$``），
    否则构造期抛 :class:`InvalidPromptVersionTagError` 触发 fail-fast。

    Attributes:
        chat_default_version: ``chat-default`` 对应的版本号，默认 ``"v1"``；
            对应配置键 ``PROMPT_CHAT_DEFAULT_VERSION``。
        task_template_version: ``task-template`` 对应的版本号，默认 ``"v1"``；
            对应配置键 ``PROMPT_TASK_TEMPLATE_VERSION``。
        context_summary_version: ``context-summary`` 对应的版本号，默认 ``"v1"``；
            对应配置键 ``PROMPT_CONTEXT_SUMMARY_VERSION``。
    """

    model_config = SettingsConfigDict(env_prefix="PROMPT_")

    chat_default_version: str = "v1"
    task_template_version: str = "v1"
    context_summary_version: str = "v1"

    @field_validator(
        "chat_default_version",
        "task_template_version",
        "context_summary_version",
    )
    @classmethod
    def _validate_version_tag(cls, value: str, info: Any) -> str:
        """校验字段值符合 ``v<正整数>`` 格式。

        Args:
            value: 字段实际取值。
            info: pydantic 提供的校验上下文，``info.field_name`` 为当前字段名。

        Returns:
            原样返回已校验值。

        Raises:
            InvalidPromptVersionTagError: 格式非法时抛出；错误消息含字段名、
                实际取值、期望格式示例。
        """
        if not _VERSION_PATTERN.match(value):
            raise InvalidPromptVersionTagError(
                f"字段 {info.field_name!r} 取值非法：{value!r}，期望 v<正整数>（示例：v1、v2、v10）"
            )
        return value

    def as_mapping(self) -> dict[str, str]:
        """返回 ``{prompt_name: version}`` 形式的映射，便于适配器遍历。

        字段名通过去 ``_version`` 尾 + ``_`` → ``-`` 还原为 ``Prompt_Name``。
        例如字段 ``chat_default_version`` 对应 Prompt 名 ``chat-default``。

        Returns:
            形如 ``{"chat-default": "v1", "task-template": "v1"}`` 的字典；
            键顺序与类中字段声明顺序一致。
        """
        result: dict[str, str] = {}
        for field_name in type(self).model_fields:
            if not field_name.endswith("_version"):
                continue
            prompt_name = field_name[: -len("_version")].replace("_", "-")
            result[prompt_name] = getattr(self, field_name)
        return result


prompt_version_config = PromptVersionConfig()
"""模块级 :class:`PromptVersionConfig` 单例。

与 ``chat_config`` / ``workspace_config`` 风格上一致（模块级单例），
但 **不走** :func:`common.configuration.create_config` 工厂（设计决策 #5）。

不走 ``create_config`` 的原因：

1. ``create_config`` 会把对象包装为 ``ConfigProxy``，其默认行为在
   ``hot_reload=True`` 时会在 ``config.properties`` 的 mtime 变化时触发重载；
2. Prompt 是审计关键字段，``prompt_id`` 在启动时落定后必须与已记录的
   trace / 日志中字段一一对齐，运行期重载会破坏这一一致性；
3. 运维变更 Prompt 版本必须走发版流程（重启容器），``hot_reload=False``
   只是对配置类的双重保险，不构成"可热更新"承诺。

因此此处 **直接构造** 普通 :class:`PropertiesBaseSettings` 实例——
在容器装配期读一次 ``config.properties`` 后字段即冻结为只读，
后续任何 ``config.properties`` 修改在不重启的前提下都不会生效，
与需求 2.5 对齐。
"""
