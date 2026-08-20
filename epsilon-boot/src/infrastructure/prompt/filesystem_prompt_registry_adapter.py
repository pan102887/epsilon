"""文件系统 Prompt 注册表适配器模块。

本模块实现 :class:`domain.prompt.ports.PromptRegistryPort` 协议的基础设施
适配器 :class:`FilesystemPromptRegistryAdapter`。适配器在 **构造阶段**
一次性扫描 ``Prompt_Asset_Directory``（默认 ``epsilon-boot/prompts/``）、
按 :class:`infrastructure.prompt.prompt_version_config.PromptVersionConfig`
指定的版本加载每个 Prompt 到只读内存字典；构造成功即代表启动期所有
I/O 校验通过，运行期 :meth:`get` 零磁盘 I/O。

任一启动期校验失败抛出 :mod:`infrastructure.prompt.exceptions` 下的
``ConfigurationError`` 子类，配合 DI 容器的 ``container.start()`` 触发
fail-fast 回滚（见设计 §启动期时序图）。与领域异常
:class:`domain.prompt.exceptions.PromptNotFoundError` 的区分原则：

- 基础设施异常（本模块 raise 的 ``ConfigurationError`` 子类）= 启动期错误；
- 领域异常（``PromptNotFoundError``）= 运行期 :meth:`get` 被传入未注册
  名称（编程错误，正常路径下不会触发）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from domain.prompt.exceptions import PromptNotFoundError
from domain.prompt.ports import PromptRegistryPort
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.prompt.exceptions import (
    EmptyPromptAssetError,
    PromptAssetDirectoryMissingError,
    PromptAssetEncodingError,
    PromptAssetFileMissingError,
    PromptNotConfiguredError,
)
from infrastructure.prompt.prompt_version_config import PromptVersionConfig

logger = logging.getLogger(__name__)


class FilesystemPromptRegistryAdapter(PromptRegistryPort):
    """:class:`PromptRegistryPort` 的文件系统实现。

    构造阶段完成以下顺序校验与加载：

    1. ``root`` 存在性与类型校验（不存在或不是目录 → 抛
       :class:`PromptAssetDirectoryMissingError`，需求 9.1）；
    2. ``root.resolve()`` 规范化为绝对路径；
    3. 扫描一级子目录列表 ``existing_subdirs``；
    4. 对 :meth:`PromptVersionConfig.as_mapping` 返回的每个 ``name``：若
       ``name`` 不在 ``existing_subdirs``（= 配置引用但目录缺失）→ 抛
       :class:`PromptNotConfiguredError`（需求 9.6）；
    5. ``existing_subdirs`` 中未被配置引用的子目录记录到启动日志
       ``logger.info("... 已跳过加载 ...")``，**不**抛错（需求 9.5）；
    6. 对每个已配置 ``(name, version)`` 调用 :meth:`_load_one` 解析并
       构造 :class:`LoadedPrompt`，写入内部只读字典；
    7. 记录汇总日志，列出已加载 ``prompt_id`` 与规范化后的根目录。

    构造成功后实例对外只读，:meth:`get` / :meth:`list_names` 不触发任何
    磁盘 I/O；多协程并发调用 :meth:`get` 安全（Python dict 在 GIL 下的读
    操作线程安全）。

    Attributes:
        _root: 已 ``resolve()`` 的 Prompt 资产根目录绝对路径。
        _prompts: ``name -> LoadedPrompt`` 的只读字典；构造后不再变更。
    """

    def __init__(self, root: Path, version_config: PromptVersionConfig) -> None:
        """一次性扫描并加载所有已配置 Prompt。

        Args:
            root: Prompt 资产目录路径（通常为 ``<backend>/prompts/``）。
            version_config: :class:`PromptVersionConfig` 实例，提供
                ``name -> version`` 映射；由组合根在容器装配期注入。

        Raises:
            PromptAssetDirectoryMissingError: ``root`` 不存在或不是目录
                （需求 9.1）。
            PromptNotConfiguredError: ``version_config.as_mapping()`` 引用
                的 ``name`` 在资产目录下无同名子目录（需求 9.6）。
            PromptAssetFileMissingError: 目标 ``<name>/<version>.md`` 缺失
                （需求 9.2）。
            PromptAssetEncodingError: 目标文件 UTF-8 解码失败（需求 9.3）。
            EmptyPromptAssetError: 目标文件内容全空白（需求 9.4）。
        """
        if not root.exists() or not root.is_dir():
            raise PromptAssetDirectoryMissingError(f"Prompt 资产目录不存在或不是目录：{root}")

        self._root: Path = root.resolve()

        mapping = version_config.as_mapping()
        existing_subdirs = {p.name for p in self._root.iterdir() if p.is_dir()}

        # 需求 9.6：配置引用但目录缺失 → fail-fast。
        for name in mapping:
            if name not in existing_subdirs:
                raise PromptNotConfiguredError(
                    f"PromptVersionConfig 引用了不存在的 Prompt 名：{name!r}，"
                    f"期望目录：{self._root / name}"
                )

        # 需求 9.5：目录存在但配置缺失 → 允许跳过，但记录审计日志。
        unconfigured = sorted(existing_subdirs - set(mapping))
        if unconfigured:
            logger.info(
                "Prompt 目录下存在未配置的子目录（已跳过加载）：%s",
                unconfigured,
            )

        self._prompts: dict[str, LoadedPrompt] = {}
        for name, version in mapping.items():
            self._prompts[name] = self._load_one(name, version)

        logger.info(
            "FilesystemPromptRegistryAdapter 初始化完成：loaded=%s root=%s",
            [lp.prompt_id for lp in self._prompts.values()],
            self._root,
        )

    def _load_one(self, name: str, version: str) -> LoadedPrompt:
        """加载单个 ``<name>/<version>.md`` 文件并返回 :class:`LoadedPrompt`。

        本方法仅在 :meth:`__init__` 内部调用；错误消息中附带 ``PROMPT_<NAME>_VERSION``
        键名（需求 9.2），帮助运维者在错误日志中直接定位应修改的配置键。

        Args:
            name: Prompt 名称（来自 ``version_config`` 映射键，如 ``chat-default``）。
            version: Prompt 版本号（形如 ``v3``），由 ``PromptVersionConfig``
                字段值提供，已通过 :class:`InvalidPromptVersionTagError` 校验。

        Returns:
            ``LoadedPrompt(prompt_id=f"{name}@{version}", name, version, content)``。

        Raises:
            PromptAssetFileMissingError: ``<root>/<name>/<version>.md`` 不存在
                或不是常规文件（需求 9.2）。
            PromptAssetEncodingError: UTF-8 解码失败（需求 9.3）。
            EmptyPromptAssetError: 文件内容全空白（需求 9.4）。
        """
        path = self._root / name / f"{version}.md"
        if not path.is_file():
            config_key = f"PROMPT_{name.upper().replace('-', '_')}_VERSION"
            raise PromptAssetFileMissingError(
                f"Prompt 资产文件缺失：path={path}，对应配置键={config_key}"
            )
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PromptAssetEncodingError(
                f"Prompt 资产 UTF-8 解码失败：path={path}，offset={exc.start}，reason={exc.reason}"
            ) from exc
        if not content.strip():
            raise EmptyPromptAssetError(f"Prompt 资产内容为空白：path={path}")
        return LoadedPrompt(
            prompt_id=f"{name}@{version}",
            name=name,
            version=version,
            content=content,
        )

    def get(self, name: str) -> LoadedPrompt:
        """按名称返回已加载的 :class:`LoadedPrompt`（零 I/O）。

        运行期仅做字典查找；命中返回构造期加载的只读快照引用。

        Args:
            name: Prompt 名称（如 ``chat-default``）。

        Returns:
            对应的 :class:`LoadedPrompt` 实例。

        Raises:
            PromptNotFoundError: ``name`` 未在构造期加载（领域异常，需求 3.5）。
                正常路径下启动期校验已覆盖全部已配置名称，运行期触发意味着
                Prompt 消费方传入了硬编码的错误名称。
        """
        lp = self._prompts.get(name)
        if lp is None:
            raise PromptNotFoundError(name, sorted(self._prompts.keys()))
        return lp

    def list_names(self) -> list[str]:
        """返回已加载 Prompt 名称列表。

        Returns:
            按构造期加载顺序的 Prompt 名称列表；与 ``PromptVersionConfig``
            的字段声明顺序一致。返回值为新列表，调用方可安全修改。
        """
        return list(self._prompts.keys())
