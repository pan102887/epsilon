"""Prompt 基础设施层子包。

本子包承载 Prompt 资产的落地实现，包括：

- :mod:`infrastructure.prompt.prompt_version_config`：基于
  ``PropertiesBaseSettings`` 的版本映射配置（``env_prefix="PROMPT_"``），
  驱动"Prompt 名 → 版本号"的单一配置源；
- 后续切片落地的 :class:`FilesystemPromptRegistryAdapter` 与
  Prompt 资产异常家族（均继承 :class:`common.configuration.ConfigurationError`）。

依赖方向：本子包实现 ``domain/prompt/`` 定义的 Port 契约，仅向外暴露
通过组合根（``application/container_config.py``）装配后的抽象，避免应用层与
领域层直接引用基础设施细节。
"""
