# 实施计划：Web Search Tool

## 概述

为 LLM Agent 实现基于 Tavily SDK 的 Web 搜索工具。实施按"配置模块 → 工具实现 → 容器注册 → 配置文件"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 创建 TavilyConfig 配置类
  - [x] 1.1 新建 `infrastructure/tools/web_search/tavily_config.py`
    - 继承 `PropertiesBaseSettings`，使用 `SettingsConfigDict(env_prefix="TAVILY_")` 前缀
    - 定义 `api_key: str = ""`（对应 `TAVILY_API_KEY`）
    - 定义 `search_max_results: int = 5`（对应 `TAVILY_SEARCH_MAX_RESULTS`）
    - 使用 `create_config` 工厂函数创建模块级 `tavily_config` 实例
    - 添加中文 docstring
    - _需求: 1.1, 1.2_

  - [x] 1.2 编写属性测试：配置读取正确性
    - **Property 1: 配置读取正确性**
    - 生成随机 api_key 字符串和 max_results 整数值，通过 monkeypatch 写入环境变量，验证 TavilyConfig 读取后字段值一致；未设置 `TAVILY_SEARCH_MAX_RESULTS` 时默认值为 5
    - 测试文件：`test/infrastructure/tools/web_search/test_tavily_config.py`
    - **验证: 需求 1.1, 1.2**

- [x] 2. 实现 WebSearchTool 工具类
  - [x] 2.1 新建 `infrastructure/tools/web_search/web_search_tool.py`
    - 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
    - `name` 返回 `"web_search"`
    - 构造函数接收 `api_key: str` 和 `default_max_results: int = 5`，在 `__init__` 中创建 `TavilyClient` 实例
    - `parameters` 返回 JSON Schema，包含必填参数 `query`（string）和可选参数 `max_results`（integer）
    - `execute` 流程：提取 query 和 max_results → 调用 `TavilyClient.search()` → 格式化结果（含标题、URL、摘要，用 `---` 分隔）→ 空结果返回 "未找到相关搜索结果"
    - 异常时包装为 `ToolExecutionError` 抛出，错误信息包含原始异常描述
    - 添加中文 docstring
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 2.2 新建 `infrastructure/tools/web_search/__init__.py`
    - 导出 `WebSearchTool`，定义 `__all__ = ["WebSearchTool"]`
    - _需求: 4.1_

  - [x] 2.3 编写属性测试：搜索结果格式化完整性
    - **Property 3: 搜索结果格式化完整性**
    - 生成随机搜索结果列表（含随机 title/url/content），Mock `TavilyClient.search` 返回该列表，验证格式化输出包含每条结果的标题、URL 和内容摘要，且各结果之间使用 `---` 分隔
    - 测试文件：`test/infrastructure/tools/web_search/test_web_search_tool.py`
    - **验证: 需求 2.4, 2.5**

  - [x] 2.4 编写属性测试：异常包装正确性
    - **Property 4: 异常包装正确性**
    - 生成随机异常类型和消息，Mock `TavilyClient.search` 抛出该异常，验证 `execute` 将其包装为 `ToolExecutionError` 且错误信息包含原始异常描述
    - 测试文件：`test/infrastructure/tools/web_search/test_web_search_tool.py`
    - **验证: 需求 2.6**

  - [x] 2.5 编写单元测试：WebSearchTool 接口合规与边界情况
    - 验证继承 `Tool`、`name` 返回 `"web_search"`、`parameters` schema 结构正确
    - 验证空结果时返回 "未找到相关搜索结果"
    - 验证不传 `max_results` 时使用配置默认值
    - 测试文件：`test/infrastructure/tools/web_search/test_web_search_tool.py`
    - _需求: 2.1, 2.2, 2.3_

- [x] 3. Checkpoint - 确保工具实现测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. 工具注册与配置集成
  - [x] 4.1 在 `config.properties` 中追加 Tavily 配置项
    - 新增 `TAVILY_API_KEY=`（默认为空）
    - 新增 `TAVILY_SEARCH_MAX_RESULTS=5`
    - _需求: 1.1, 1.2_

  - [x] 4.2 修改 `application/container_config.py` 的 `_create_tool_registry()` 函数
    - 在 filesystem 工具注册之后、DelegateToAgentTool 条件注册之前，添加 WebSearchTool 条件注册逻辑
    - 读取 `tavily_config.api_key`，非空时实例化 `WebSearchTool` 并注册到 `ToolRegistry`
    - API Key 为空时记录 `logger.warning` 并跳过注册
    - 导入失败时记录 `logger.debug` 并跳过注册
    - _需求: 1.3, 3.1, 3.2, 3.3_

  - [x] 4.3 编写属性测试：条件注册正确性
    - **Property 2: 条件注册正确性**
    - 生成随机 API 密钥字符串（含空字符串），验证非空时 ToolRegistry 包含 `"web_search"` 工具，空时不包含且其他工具不受影响
    - 测试文件：`test/infrastructure/tools/web_search/test_web_search_tool.py`
    - **验证: 需求 1.3, 3.2, 3.3**

- [x] 5. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，建议 `@settings(max_examples=100, deadline=5000)`
- 测试运行命令：`cd epsilon-boot && uv run pytest test/infrastructure/tools/web_search/ -v`
- 变更范围集中在 `infrastructure/tools/web_search/`、`application/container_config.py`、`config.properties` 三处
- WebSearchTool 仅依赖领域层的 `Tool` 基类和 `ToolExecutionError`，不在领域层引入基础设施依赖
