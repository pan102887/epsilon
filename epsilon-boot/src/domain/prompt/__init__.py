"""领域层 Prompt 抽象子包。

本子包承载 Prompt 资产目录与版本化注册特性在领域层的核心抽象：

- :mod:`domain.prompt.value_objects`：``LoadedPrompt`` 不可变值对象，
  表达一次已加载的 Prompt 资产（``prompt_id`` / ``name`` / ``version`` /
  ``content``）。
- :mod:`domain.prompt.ports`：``PromptRegistryPort`` 协议（``Protocol``），
  定义"按名取回已加载 Prompt"的领域能力。
- :mod:`domain.prompt.exceptions`：领域层 Prompt 异常（``PromptNotFoundError``）。

领域层严格遵循 ``docs/steering/ddd-architecture.md`` 的依赖方向：
本子包仅允许依赖 Python 标准库与 ``common/`` 中与业务无关的共享抽象，
**禁止**导入 ``infrastructure/*``、``pydantic-settings``、文件系统 SDK
或任何 Web 框架；所有 I/O 均在基础设施层适配器的构造期完成。
"""
