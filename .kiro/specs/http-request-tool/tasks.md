# 实施计划：HTTP Request Tool

## 概述

为 LLM Agent 实现基于 httpx 的通用 HTTP 请求工具。实施按"依赖安装 → 配置模块 → 工具实现（SSRF 防护 + 响应处理 + 主工具类）→ 包导出 → 容器注册 → 配置文件 → 测试"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 添加 readability-lxml 依赖
  - 在 `epsilon-boot/` 目录下执行 `uv add readability-lxml`
  - 验证 `pyproject.toml` 的 `[project.dependencies]` 中已包含 `readability-lxml`
  - _需求: 7.1, 7.2_

- [x] 2. 创建 HttpRequestConfig 配置类
  - [x] 2.1 新建 `infrastructure/tools/http_request/http_request_config.py`
    - 继承 `PropertiesBaseSettings`，使用 `SettingsConfigDict(env_prefix="HTTP_REQUEST_")` 前缀
    - 定义 `timeout: int = 30`（对应 `HTTP_REQUEST_TIMEOUT`）
    - 定义 `max_response_size: int = 51200`（对应 `HTTP_REQUEST_MAX_RESPONSE_SIZE`）
    - 定义 `enabled: bool = True`（对应 `HTTP_REQUEST_ENABLED`）
    - 使用 `create_config` 工厂函数创建模块级 `http_request_config` 实例
    - 添加中文 docstring
    - _需求: 1.1, 1.2, 1.3, 6.2_

  - [x] 2.2 编写属性测试：配置读取正确性
    - **Property 1: 配置读取正确性**
    - 生成随机 timeout 整数值、max_response_size 整数值和 enabled 布尔值，通过 monkeypatch 写入环境变量，验证 HttpRequestConfig 读取后字段值一致；未设置时默认值分别为 30、51200 和 True
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_config.py`
    - **验证: 需求 1.1, 1.2, 1.3**

- [x] 3. 实现 HttpRequestTool 工具类
  - [x] 3.1 新建 `infrastructure/tools/http_request/http_request_tool.py`，实现 SSRF 防护函数
    - 定义 `_PRIVATE_NETWORKS` 私有网段列表（127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、169.254.0.0/16、::1/128）
    - 实现 `validate_url_safety(url: str) -> None` 函数：解析 URL 主机名 → DNS 解析获取 IP → 检查是否属于私有网段 → 属于则抛出 ToolExecutionError（含 "SSRF" 关键词和 IP 地址）
    - DNS 解析失败时抛出 ToolExecutionError（含主机名）
    - 添加中文 docstring
    - _需求: 3.1, 3.2, 3.3, 6.2_

  - [x] 3.2 编写属性测试：SSRF 私有 IP 拒绝
    - **Property 3: SSRF 私有 IP 拒绝**
    - 从各私有网段生成随机 IP 地址，Mock `socket.getaddrinfo` 返回该 IP，验证 `validate_url_safety` 抛出 ToolExecutionError 且错误信息包含 "SSRF" 和被拒绝的 IP
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - **验证: 需求 3.1, 3.2**

  - [x] 3.3 在 `http_request_tool.py` 中实现响应处理函数
    - 实现 `process_response(response: httpx.Response, max_size: int) -> str` 函数
    - Content-Type 为 `application/json` 时：`json.dumps(response.json(), ensure_ascii=False, indent=2)` 格式化
    - Content-Type 为 `text/html` 时：使用 `readability.Document(html).summary()` 提取正文，清理残留 HTML 标签；readability 提取失败时回退为原始 HTML
    - Content-Type 为其他 `text/*` 时：直接返回 `response.text`
    - 二进制类型：返回元数据字符串（Content-Type、Content-Length）
    - 超过 `max_size` 时截断并附加 `"[响应已截断，原始大小: XXX bytes]"` 提示
    - 添加中文 docstring
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 6.2_

  - [x] 3.4 编写属性测试：响应内容 Content-Type 分派
    - **Property 4: 响应内容 Content-Type 分派**
    - 生成随机 Content-Type 和对应内容（JSON dict / HTML 字符串 / 纯文本 / 二进制），Mock httpx.Response，验证输出格式符合分派规则且包含状态码
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - **验证: 需求 4.1, 4.2, 4.3, 4.4, 4.6**

  - [x] 3.5 编写属性测试：响应体截断
    - **Property 5: 响应体截断**
    - 生成随机 max_response_size 和超过该大小的响应体，验证截断行为和提示信息格式 `"[响应已截断，原始大小: XXX bytes]"`
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - **验证: 需求 4.5**

  - [x] 3.6 在 `http_request_tool.py` 中实现 HttpRequestTool 类
    - 继承 `Tool` 抽象基类，实现 `name`、`description`、`parameters`、`execute` 四个抽象成员
    - `name` 返回 `"http_request"`
    - 构造函数接收 `timeout: int = 30` 和 `max_response_size: int = 51200`，创建独立的 `httpx.AsyncClient` 实例（不复用 GatewayClient）
    - `parameters` 返回 JSON Schema：必填 `url`（string），可选 `method`（string, enum）、`headers`（object）、`body`（string）、`timeout`（integer）
    - `execute` 流程：提取参数 → `validate_url_safety(url)` → 解析 body JSON → httpx 异步请求 → 检查响应大小 → `process_response()` → 返回 `"HTTP {status_code} ...\n\n{内容}"`
    - 异常时包装为 `ToolExecutionError`（`tool_name="http_request"`），错误信息包含原始异常描述
    - 添加中文 docstring
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 6.1, 6.2, 6.3_

  - [x] 3.7 编写属性测试：异常包装正确性
    - **Property 6: 异常包装正确性**
    - 生成随机异常类型（httpx.TimeoutException、httpx.ConnectError 等）和消息，Mock httpx 抛出异常，验证包装后的 ToolExecutionError 保留原始信息且 `tool_name` 为 `"http_request"`
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - **验证: 需求 2.6**

  - [x] 3.8 编写单元测试：HttpRequestTool 接口合规与边界情况
    - 验证继承 `Tool`、`name` 返回 `"http_request"`、`parameters` schema 结构正确
    - 验证构造时创建的 AsyncClient 不是 GatewayClient 实例
    - 验证不传 `method` 时默认使用 GET
    - 验证 `body` 参数 JSON 解析失败时抛出 ToolExecutionError
    - 验证 DNS 解析失败时错误信息包含主机名
    - 验证空 JSON 响应 `{}` 正常格式化
    - 验证 readability 提取失败时回退为原始 HTML
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - _需求: 2.1, 2.2, 2.3, 2.5, 3.3, 4.1, 4.2_

- [x] 4. Checkpoint - 确保工具实现测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 5. 包导出与工具注册集成
  - [x] 5.1 新建 `infrastructure/tools/http_request/__init__.py`
    - 导出 `HttpRequestTool`，定义 `__all__ = ["HttpRequestTool"]`
    - _需求: 6.1_

  - [x] 5.2 在 `config.properties` 中追加 HTTP 请求工具配置项
    - 新增 `HTTP_REQUEST_TIMEOUT=30`
    - 新增 `HTTP_REQUEST_MAX_RESPONSE_SIZE=51200`
    - 新增 `HTTP_REQUEST_ENABLED=true`
    - _需求: 1.1, 1.2, 1.3_

  - [x] 5.3 修改 `application/container_config.py` 的 `_create_tool_registry()` 函数
    - 在 WebSearchTool 条件注册之后、DelegateToAgentTool 条件注册之前，添加 HttpRequestTool 条件注册逻辑
    - 读取 `http_request_config.enabled`，为 True 时实例化 `HttpRequestTool(timeout=..., max_response_size=...)` 并注册到 ToolRegistry
    - `enabled` 为 False 时记录 `logger.info` 并跳过注册
    - 导入失败时记录 `logger.debug` 并跳过注册
    - _需求: 1.4, 5.1, 5.2, 5.3_

  - [x] 5.4 编写属性测试：条件注册正确性
    - **Property 2: 条件注册正确性**
    - 生成随机 enabled 布尔值，验证 enabled=True 时 ToolRegistry 包含 `"http_request"` 工具，enabled=False 时不包含且其他工具不受影响
    - 测试文件：`test/infrastructure/tools/http_request/test_http_request_tool.py`
    - **验证: 需求 1.4, 5.2, 5.3**

- [x] 6. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试使用 Hypothesis 库，建议 `@settings(max_examples=100, deadline=5000)`
- 测试运行命令：`cd epsilon-boot && uv run pytest test/infrastructure/tools/http_request/ -v`
- 变更范围集中在 `infrastructure/tools/http_request/`、`application/container_config.py`、`config.properties` 三处
- HttpRequestTool 仅依赖领域层的 `Tool` 基类和 `ToolExecutionError`，不在领域层引入基础设施依赖
- `httpx>=0.28.1` 已在项目依赖中，无需额外添加；仅需通过 `uv add readability-lxml` 添加 readability-lxml
