# 需求文档

## 简介

为项目实现 Web 搜索工具，使 LLM Agent 具备实时联网搜索能力。该工具基于已有的 Tavily Python SDK（`tavily-python>=0.7.18`），遵循项目 DDD 架构规范，继承 `Tool` 抽象基类，注册到 `ToolRegistry` 供 Agent 调用。

## 术语表

- **WebSearchTool**：Web 搜索工具，基础设施层的 Tool 实现，封装 Tavily API 提供联网搜索能力
- **TavilyClient**：Tavily Python SDK 提供的搜索客户端，负责与 Tavily API 通信
- **ToolRegistry**：工具注册表，集中管理所有已注册的 Tool 实例，定义于 `domain/agent/tools.py`
- **Tool**：工具抽象基类，定义工具的统一接口规范（name、description、parameters、execute），定义于 `domain/agent/tools.py`
- **ToolExecutionError**：工具执行异常，定义于 `domain/agent/exceptions.py`，用于标准化工具层的错误处理
- **config.properties**：项目配置文件，存放所有配置项，位于项目根目录

## 需求

### 需求 1：Tavily API 配置管理

**用户故事：** 作为开发者，我希望通过 config.properties 集中管理 Tavily API 配置，以便统一维护和灵活切换。

#### 验收标准

1. THE WebSearchTool 配置模块 SHALL 从 config.properties 读取 `TAVILY_API_KEY` 配置项作为 Tavily API 密钥
2. THE WebSearchTool 配置模块 SHALL 从 config.properties 读取 `TAVILY_SEARCH_MAX_RESULTS` 配置项作为默认最大返回结果数，默认值为 5
3. IF `TAVILY_API_KEY` 配置项为空或未设置，THEN THE WebSearchTool SHALL 在工具注册阶段记录警告日志并跳过注册

### 需求 2：Web 搜索工具实现

**用户故事：** 作为 LLM Agent，我希望能够调用 Web 搜索工具获取实时网络信息，以便回答需要最新数据的问题。

#### 验收标准

1. THE WebSearchTool SHALL 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
2. THE WebSearchTool 的 `name` 属性 SHALL 返回 `"web_search"`
3. THE WebSearchTool 的 `parameters` 属性 SHALL 声明一个必填参数 `query`（类型 string，搜索关键词）和一个可选参数 `max_results`（类型 integer，最大返回结果数，默认值取自配置）
4. WHEN Agent 调用 `execute` 方法并传入有效的 `query` 参数时，THE WebSearchTool SHALL 调用 TavilyClient 执行搜索并返回格式化的搜索结果字符串
5. THE WebSearchTool 返回的搜索结果 SHALL 包含每条结果的标题（title）、URL 和内容摘要（content），各结果之间使用分隔符区分
6. IF TavilyClient 调用过程中发生异常，THEN THE WebSearchTool SHALL 将异常包装为 ToolExecutionError 并抛出，错误信息中包含原始异常描述

### 需求 3：工具注册集成

**用户故事：** 作为开发者，我希望 WebSearchTool 能自动注册到 ToolRegistry，以便 Agent 在对话中直接使用搜索能力。

#### 验收标准

1. THE WebSearchTool SHALL 在 `_create_tool_registry()` 函数中完成注册，注册位置位于现有 filesystem 工具注册之后
2. WHEN `TAVILY_API_KEY` 配置有效时，THE `_create_tool_registry()` 函数 SHALL 实例化 WebSearchTool 并注册到 ToolRegistry
3. IF `TAVILY_API_KEY` 配置为空或未设置，THEN THE `_create_tool_registry()` 函数 SHALL 记录调试日志并跳过 WebSearchTool 注册，不影响其他工具的正常注册

### 需求 4：DDD 架构合规

**用户故事：** 作为开发者，我希望 WebSearchTool 遵循项目 DDD 架构规范，以便保持代码结构一致性。

#### 验收标准

1. THE WebSearchTool 实现文件 SHALL 位于 `infrastructure/tools/web_search/` 包下，遵循现有工具包的组织方式
2. THE WebSearchTool 模块 SHALL 包含中文 docstring，说明模块职责、类作用和方法功能
3. THE WebSearchTool SHALL 仅依赖 `domain/agent/tools.py` 中的 `Tool` 基类和 `domain/agent/exceptions.py` 中的 `ToolExecutionError`，不在领域层引入基础设施依赖
