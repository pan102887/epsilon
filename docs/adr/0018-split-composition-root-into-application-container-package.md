---
status: Accepted
date: 2026-07-09
deciders: [Codex, scoped evaluator]
supersedes:
superseded-by:
---

# ADR-0018：拆分组合根为 application/container 子包

## 背景与问题（Context）

`application/container_config.py` 是当前唯一公共组合根入口，集中注册异步资源、Port→Adapter 绑定、应用服务与基础设施 adapter。随着 Chat、Task、Run、Agent、Storage、Tool 等装配逻辑增长，该文件的注册区密度过高，局部修改容易影响无关装配，也让测试 monkeypatch 兼容点难以辨认。

本项目静态导入守卫要求普通 application 模块不得导入 infrastructure；只有组合根可以引用 concrete adapter。拆分组合根时必须保持公共入口、生命周期顺序、后置委派工具注册和静态边界可审计。

## 决策（Decision）

我们将 `application/container/*.py` 确认为组合根受控子模块，包括 `agent.py`、`chat.py`、`task.py`、`run.py`、`tools.py`、`storage.py`。这些模块只提供 `register_*_components(...)` 分组注册函数，可以导入 infrastructure concrete adapter，因为它们属于组合根的一部分。

`application/container_config.py::configure_container()` 保留唯一公共入口，继续负责配置加载、异步资源注册、公共 factory facade 与生命周期顺序，并按分组调用 `register_storage_components`、`register_tool_components`、`register_chat_components`、`register_task_components`、`register_agent_components`、`register_run_components`。

静态导入守卫以组合根路径方式允许 `application/container/*.py`，但保持 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS == {}`，不把普通 application service 纳入 infrastructure import 例外。

## 后果（Consequences）

- **正面**：组合根注册按领域切片分组，`container_config.py` 保持公共入口与兼容 facade；新增或审查某类装配时可以聚焦对应子模块；静态边界仍可精确审计。
- **负面 / 代价**：组合根文件数增加，注册顺序需要通过 `configure_container()` 和 focused wiring tests 共同维护；测试中依赖 monkeypatch 的 factory 兼容点需要继续留在 facade 或显式传入。
- **后续影响**：后续新增 Port→Adapter 装配应优先放入对应 `application/container/<area>.py`；只有组合根子模块可以新增 infrastructure import。若未来再扩大组合根目录或改变公共入口，需要新增 ADR 或修订本决策。

## 备选方案（Alternatives）

- **继续把所有注册留在 `container_config.py`** —— 未采纳原因：单文件注册区会持续膨胀，跨领域 wiring 修改和评审成本过高。
- **把 factory 与注册全部迁移到子模块并删除 facade** —— 未采纳原因：会破坏既有测试 monkeypatch 点和公共导入路径，增加本次行为等价重构风险。
- **用静态例外表放宽多个 application 文件导入 infrastructure** —— 未采纳原因：例外表会掩盖普通 application service 的反向依赖风险；组合根路径应保持显式、可枚举、范围最小。
