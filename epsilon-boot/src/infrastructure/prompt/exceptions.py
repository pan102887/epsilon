"""Prompt 基础设施异常模块。

本模块定义 Prompt 资产加载与版本配置相关的启动期 fail-fast 异常族，
全部继承 :class:`common.configuration.ConfigurationError`，以便与既有
``_create_local_filesystem_workspace`` / ``_validate_local_persistence_root``
等启动期校验一致地被 DI 容器 ``container.start()`` 捕获并触发 fail-fast
回滚。本模块与 ``domain.prompt.exceptions.PromptNotFoundError``（领域层
运行期异常）**相互独立**，避免启动期与运行期错误族混淆。

异常家族与需求条款对照：

- :class:`PromptAssetDirectoryMissingError`：需求 9.1；
- :class:`PromptAssetFileMissingError`：需求 9.2；
- :class:`PromptAssetEncodingError`：需求 9.3；
- :class:`EmptyPromptAssetError`：需求 9.4；
- :class:`PromptNotConfiguredError`：需求 9.6；
- :class:`ConflictingLegacyPromptConfigError`：需求 8.2。
"""

from __future__ import annotations

from common.configuration import ConfigurationError


class PromptAssetDirectoryMissingError(ConfigurationError):
    """Prompt 资产根目录缺失或不是目录时抛出（需求 9.1）。

    触发条件：:class:`FilesystemPromptRegistryAdapter` 构造期，
    传入的 ``root`` 路径 ``exists()`` 为假或 ``is_dir()`` 为假。
    错误消息应包含期望路径，便于运维者定位镜像构建或挂载配置问题。
    """


class PromptAssetFileMissingError(ConfigurationError):
    """目标 ``<name>/<version>.md`` Prompt 资产文件缺失时抛出（需求 9.2）。

    触发条件：:class:`PromptVersionConfig` 指向的版本文件在资产目录下
    不存在；错误消息应包含文件绝对路径与对应配置键名
    （``PROMPT_<NAME_UPPER_SNAKE>_VERSION``），帮助运维者选择"补齐资产
    文件"或"改回上一版本键"的两种修复路径之一。
    """


class PromptAssetEncodingError(ConfigurationError):
    """Prompt 资产 UTF-8 解码失败时抛出（需求 9.3）。

    触发条件：:meth:`pathlib.Path.read_text` 以 ``encoding="utf-8"`` 读取
    资产文件时抛出 :class:`UnicodeDecodeError`。错误消息应保留底层异常的
    位置信息（``offset`` / ``reason``），便于运维者定位是 BOM、GBK 编码
    还是二进制文件混入等问题。
    """


class EmptyPromptAssetError(ConfigurationError):
    """Prompt 资产内容仅含空白字符时抛出（需求 9.4）。

    触发条件：文件可被 UTF-8 解码，但 ``content.strip() == ""``。避免
    "加载成功但 LLM 收到空 system prompt"的隐式失败模式；错误消息应
    包含文件路径，便于运维者检查资产文件是否被误清空或仅包含 BOM。
    """


class PromptNotConfiguredError(ConfigurationError):
    """Prompt 版本配置引用了不存在的 ``Prompt_Name`` 子目录时抛出（需求 9.6）。

    触发条件：:meth:`PromptVersionConfig.as_mapping` 返回的映射中包含某个
    ``name``，但资产目录下无同名一级子目录。此场景下绝不允许"隐式创建
    目录"或"回退默认版本"，必须 fail-fast 以暴露配置失配，符合术语表
    ``Prompt_Fallback_Semantics`` 条款。
    """


class ConflictingLegacyPromptConfigError(ConfigurationError):
    """检测到历史 ``CHAT_SYSTEM_PROMPT`` 型配置与 Prompt 版本机制并存时抛出（需求 8.2）。

    触发条件：容器装配期 ``_check_legacy_prompt_conflict`` 在环境变量或
    ``config.properties`` 中发现 ``CHAT_SYSTEM_PROMPT`` / ``chat.system.prompt``
    等"prompt 文本直写"型键。错误消息必须引导运维者按需求 8.2 的三步
    迁移路径操作（另存为 ``prompts/chat-default/v<N+1>.md`` →
    更新 ``PROMPT_CHAT_DEFAULT_VERSION`` → 删除旧键）。
    """
