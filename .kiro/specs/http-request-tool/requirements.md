# 需求文档

## 简介

为 LLM Agent 实现通用 HTTP 请求工具，使 Agent 具备直接访问指定 URL 获取网页内容和调用 API 的能力。该工具基于已有的 `httpx` 异步 HTTP 客户端库，支持 GET/POST/PUT/DELETE/PATCH 全部 HTTP 方法，根据响应 Content-Type 自动切换处理策略（JSON 直接返回、HTML 使用 readability-lxml 提取正文、二进制返回元数据）。与 WebSearchTool 互补：WebSearchTool 搜索返回摘要列表，HttpRequestTool 可获取具体 URL 的详细内容或调用外部 API。

工具内置 SSRF（Server-Side Request Forgery）基础防护，在发起请求前对目标 URL 进行 DNS 解析并校验解析后的 IP 是否属于私有网段，防止 Agent 被诱导访问内部服务。

遵循项目 DDD 架构规范：领域层定义接口和异常，基础设施层提供具体实现，应用层负责工具注册编排，配置通过 `config.properties` + `PropertiesBaseSettings` 统一管理。

## 术语表

- **HttpRequestTool**：HTTP 请求工具，基础设施层的 Tool 实现，封装 httpx 异步客户端提供通用 HTTP 请求能力
- **HttpRequestConfig**：HTTP 请求工具配置类，继承 PropertiesBaseSettings，从 config.properties 加载以 `HTTP_REQUEST_` 为前缀的配置项
- **SSRF**：Server-Side Request Forgery，服务端请求伪造攻击，攻击者诱导服务端向内部网络发起请求
- **ToolRegistry**：工具注册表，集中管理所有已注册的 Tool 实例，定义于 `domain/agent/tools.py`
- **Tool**：工具抽象基类，定义工具的统一接口规范（name、description、parameters、execute），定义于 `domain/agent/tools.py`
- **ToolExecutionError**：工具执行异常，定义于 `domain/agent/exceptions.py`，用于标准化工具层的错误处理
- **config.properties**：项目配置文件，存放所有配置项，位于项目根目录
- **readability-lxml**：Python 库，基于 Arc90 Readability 算法智能提取网页正文内容（类似浏览器阅读模式），去除导航栏、广告、脚本等噪音
- **Private_IP**：私有 IP 地址段，包括 127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、169.254.0.0/16、::1 等不可路由的内部网络地址

## 需求

### 需求 1：HTTP 请求工具配置管理

**用户故事：** 作为开发者，我希望通过 config.properties 集中管理 HTTP 请求工具的配置，以便统一维护超时、响应大小上限和启用状态。

#### 验收标准

1. THE HttpRequestConfig SHALL 从 config.properties 读取 `HTTP_REQUEST_TIMEOUT` 配置项作为默认请求超时秒数，默认值为 30
2. THE HttpRequestConfig SHALL 从 config.properties 读取 `HTTP_REQUEST_MAX_RESPONSE_SIZE` 配置项作为响应体大小上限（字节），默认值为 51200（50KB）
3. THE HttpRequestConfig SHALL 从 config.properties 读取 `HTTP_REQUEST_ENABLED` 配置项作为工具启用开关，默认值为 true
4. IF `HTTP_REQUEST_ENABLED` 配置项为 false，THEN THE HttpRequestTool SHALL 在工具注册阶段记录日志并跳过注册

### 需求 2：HTTP 请求执行

**用户故事：** 作为 LLM Agent，我希望能够向指定 URL 发起 HTTP 请求，以便获取网页详细内容或调用外部 API。

#### 验收标准

1. THE HttpRequestTool SHALL 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
2. THE HttpRequestTool 的 `name` 属性 SHALL 返回 `"http_request"`
3. THE HttpRequestTool 的 `parameters` 属性 SHALL 声明以下参数：一个必填参数 `url`（类型 string，请求目标 URL）；四个可选参数 `method`（类型 string，HTTP 方法，默认 "GET"，可选值 GET/POST/PUT/DELETE/PATCH）、`headers`（类型 object，自定义请求头）、`body`（类型 string，请求体 JSON 字符串，用于 POST/PUT/PATCH）、`timeout`（类型 integer，单次请求超时秒数，默认取自配置）
4. WHEN Agent 调用 `execute` 方法并传入有效的 `url` 参数时，THE HttpRequestTool SHALL 使用 httpx.AsyncClient 向目标 URL 发起异步 HTTP 请求并返回处理后的响应内容
5. THE HttpRequestTool SHALL 在构造时创建独立的 httpx.AsyncClient 实例，不复用 GatewayClient 的连接池
6. IF `execute` 过程中发生任何异常（网络超时、连接失败、DNS 解析失败等），THEN THE HttpRequestTool SHALL 将异常包装为 ToolExecutionError 并抛出，错误信息中包含原始异常描述

### 需求 3：SSRF 安全防护

**用户故事：** 作为开发者，我希望 HTTP 请求工具内置 SSRF 防护机制，以防止 Agent 被诱导访问内部网络服务。

#### 验收标准

1. WHEN HttpRequestTool 收到请求时，THE HttpRequestTool SHALL 在发起 HTTP 请求前对目标 URL 的主机名进行 DNS 解析，获取解析后的 IP 地址
2. IF DNS 解析后的 IP 地址属于私有网段（127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、169.254.0.0/16、::1），THEN THE HttpRequestTool SHALL 拒绝请求并抛出 ToolExecutionError，错误信息中包含 "SSRF" 关键词和被拒绝的 IP 地址
3. IF 目标 URL 的主机名 DNS 解析失败，THEN THE HttpRequestTool SHALL 抛出 ToolExecutionError，错误信息中包含解析失败的主机名

### 需求 4：响应内容处理

**用户故事：** 作为 LLM Agent，我希望 HTTP 请求工具能根据响应类型智能处理内容，以便获取结构化或可读的响应数据。

#### 验收标准

1. WHEN 响应 Content-Type 为 `application/json` 时，THE HttpRequestTool SHALL 返回格式化的 JSON 字符串
2. WHEN 响应 Content-Type 为 `text/html` 时，THE HttpRequestTool SHALL 使用 readability-lxml 库提取网页正文内容，去除导航栏、广告、脚本等噪音元素
3. WHEN 响应 Content-Type 为其他文本类型（如 text/plain、text/xml）时，THE HttpRequestTool SHALL 直接返回原始文本内容
4. WHEN 响应 Content-Type 为二进制类型（如 image/png、application/pdf）时，THE HttpRequestTool SHALL 返回响应元数据（状态码、Content-Type、Content-Length），不返回二进制内容本身
5. WHEN 响应体大小超过配置的上限（HTTP_REQUEST_MAX_RESPONSE_SIZE）时，THE HttpRequestTool SHALL 截断响应内容并在末尾附加提示信息 "[响应已截断，原始大小: XXX bytes]"
6. THE HttpRequestTool 返回的响应内容 SHALL 包含 HTTP 状态码信息，便于 Agent 判断请求结果

### 需求 5：工具注册集成

**用户故事：** 作为开发者，我希望 HttpRequestTool 能按条件自动注册到 ToolRegistry，以便 Agent 在对话中直接使用 HTTP 请求能力。

#### 验收标准

1. THE HttpRequestTool SHALL 在 `_create_tool_registry()` 函数中完成注册，注册位置位于 WebSearchTool 条件注册之后
2. WHEN `HTTP_REQUEST_ENABLED` 配置为 true 时，THE `_create_tool_registry()` 函数 SHALL 实例化 HttpRequestTool 并注册到 ToolRegistry
3. IF `HTTP_REQUEST_ENABLED` 配置为 false，THEN THE `_create_tool_registry()` 函数 SHALL 记录日志并跳过 HttpRequestTool 注册，不影响其他工具的正常注册

### 需求 6：DDD 架构合规

**用户故事：** 作为开发者，我希望 HttpRequestTool 遵循项目 DDD 架构规范，以便保持代码结构一致性。

#### 验收标准

1. THE HttpRequestTool 实现文件 SHALL 位于 `infrastructure/tools/http_request/` 包下，遵循现有工具包的组织方式
2. THE HttpRequestTool 模块 SHALL 包含中文 docstring，说明模块职责、类作用和方法功能
3. THE HttpRequestTool SHALL 仅依赖 `domain/agent/tools.py` 中的 `Tool` 基类和 `domain/agent/exceptions.py` 中的 `ToolExecutionError`，不在领域层引入基础设施依赖

### 需求 7：依赖管理

**用户故事：** 作为开发者，我希望新增的 readability-lxml 依赖通过 uv 包管理工具正确添加到项目中。

#### 验收标准

1. THE 项目 SHALL 在 `pyproject.toml` 的 `[project.dependencies]` 中声明 `readability-lxml` 依赖
2. THE readability-lxml 依赖 SHALL 通过 `uv add readability-lxml` 命令添加，遵循项目 UV 包管理规范
