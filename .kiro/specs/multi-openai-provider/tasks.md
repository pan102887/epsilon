# 实施计划：多 OpenAI 兼容提供商支持（Multi OpenAI Provider）

## 概述

将 `OpenAIProviderConfig` 从固定 `env_prefix` 的模块级单例重构为可复用的模板类，通过 `type()` 动态创建子类支持多个 OpenAI 兼容提供商（智谱 AI、OpenAI、DeepSeek）的并行注册。实施按"模板类重构 → 容器初始化重构 → 路由器适配 → 配置文件更新 → 测试"的顺序递进。

## Tasks

- [x] 1. 重构 OpenAIProviderConfig 为模板类并添加工厂函数
  - [x] 1.1 重构 `openai_config.py`：移除硬编码 `model_config` 和模块级单例，添加 `create_openai_provider_config(env_prefix)` 工厂函数
    - 移除 `model_config = SettingsConfigDict(env_prefix="MODEL_OPENAI_")` 行
    - 移除模块级 `openai_config = create_config(OpenAIProviderConfig)` 单例
    - 添加 `create_openai_provider_config(env_prefix: str)` 工厂函数，内部通过 `type()` 动态创建子类，注入特定 `env_prefix` 的 `SettingsConfigDict`，再调用 `create_config()` 返回带热更新的代理实例
    - 动态子类的 `model_config` 必须包含完整的 `SettingsConfigDict`（含 `env_file`、`env_file_encoding`、`extra`、`frozen`），因为子类会完全覆盖父类的 `model_config`
    - 需要从 `configuration_utils` 导入 `_ENV_FILE` 路径
    - _需求: 1.1, 1.2, 1.3, 1.4, 7.1_

  - [x] 1.2 编写属性测试：模板类字段完整性
    - **Property 1: 模板类字段完整性**
    - 生成随机 `env_prefix` 字符串，验证通过 `create_openai_provider_config` 创建的动态子类实例保留所有 11 个字段及其默认值，且 `hot_reload` 类变量为 `True`
    - 测试文件：`test/infrastructure/model_access/test_openai_config.py`
    - **验证: 需求 1.1, 7.1**

  - [x] 1.3 编写属性测试：配置隔离性
    - **Property 2: 配置隔离性**
    - 生成两组随机配置值和不同的 `env_prefix`，写入临时 `config.properties`，验证两个实例各自读取正确的值，互不干扰
    - 使用 `tmp_path` fixture 和 `monkeypatch` 替换 `_ENV_FILE`、`_PROPERTIES_FILE` 路径
    - 测试文件：`test/infrastructure/model_access/test_openai_config.py`
    - **验证: 需求 1.3, 1.4, 3.1, 3.2**

- [x] 2. 重构 container_config.py 支持多提供商初始化
  - [x] 2.1 定义 `OPENAI_PROVIDERS` 注册列表并重构 `_init_model_client()`
    - 在 `container_config.py` 顶部定义 `OPENAI_PROVIDERS: list[tuple[str, str]]` 列表，包含 `("zhipu", "MODEL_ZHIPU_")`, `("openai", "MODEL_OPENAI_")`, `("deepseek", "MODEL_DEEPSEEK_")`
    - 替换模块级变量：将 `_model_http_client: httpx.AsyncClient | None` 替换为 `_openai_http_clients: dict[str, httpx.AsyncClient]` 和 `_openai_configs: dict[str, OpenAIProviderConfig]`
    - 重构 `_init_model_client()`：遍历 `OPENAI_PROVIDERS`，为每个提供商调用 `create_openai_provider_config(env_prefix)` 创建配置，根据 `enabled` 和 `api_key` 条件决定是否创建 `httpx.AsyncClient`
    - `enabled=False` 时记录 debug 日志并跳过；`enabled=True` 但 `api_key` 为空时记录 warning 日志并跳过；HTTP 客户端创建失败时捕获异常记录 error 日志并继续
    - 更新 import：从 `openai_config` 模块导入 `create_openai_provider_config` 而非 `openai_config` 单例
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 5.1, 5.3_

  - [x] 2.2 重构 `_create_model_access_adapter()` 注册多个 OpenAI 兼容提供商
    - 遍历 `_openai_http_clients` 和 `_openai_configs`，为每个提供商创建 `OpenAICompatibleAdapter` 并以 `provider_name` 为 key 注册到 `providers` 字典
    - 移除旧的单提供商注册逻辑和 `zhipu`/`openai` 双重注册 hack
    - Claude 适配器注册逻辑保持不变
    - _需求: 2.3, 4.1_

  - [x] 2.3 重构 `_cleanup_model_client()` 关闭所有提供商客户端
    - 遍历 `_openai_http_clients` 字典，逐个关闭客户端并记录日志
    - 清空 `_openai_http_clients` 和 `_openai_configs` 字典
    - Claude 客户端清理逻辑保持不变
    - _需求: 5.2_

  - [x] 2.4 编写属性测试：提供商过滤逻辑
    - **Property 3: 提供商过滤逻辑**
    - 生成随机的 `enabled`/`api_key` 组合列表，验证只有 `enabled=True` 且 `api_key` 非空的提供商被初始化
    - 使用 mock 对象模拟 `httpx.AsyncClient` 和 `create_openai_provider_config`
    - 测试文件：`test/application/test_container_config.py`
    - **验证: 需求 2.3, 2.4, 2.5, 5.1**

  - [x] 2.5 编写属性测试：客户端生命周期完整性
    - **Property 6: 客户端生命周期完整性**
    - 生成随机数量的 mock 客户端，验证 `_cleanup_model_client` 后所有客户端被关闭且字典清空
    - 测试文件：`test/application/test_container_config.py`
    - **验证: 需求 5.2**

- [x] 3. Checkpoint - 确保核心重构通过测试
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. 路由器适配与配置文件更新
  - [x] 4.1 在 `router_adapter.py` 的 `_select_provider()` 中新增 `deepseek-` 前缀匹配规则
    - 在 model_prefix 策略的 `elif` 链中，在 `glm-` 匹配之后添加 `deepseek-` 前缀匹配，路由到 `"deepseek"` 提供商
    - 匹配失败时记录 warning 日志并回退到默认提供商
    - _需求: 4.3_

  - [x] 4.2 更新 `config.properties` 添加多提供商配置段
    - 将现有 `MODEL_OPENAI_*` 配置段重命名为 `MODEL_ZHIPU_*`（保留当前值，因为当前实际连接的是智谱 AI）
    - 添加 `MODEL_OPENAI_*` 配置段（真正的 OpenAI 配置，默认 `ENABLED=false`）
    - 添加 `MODEL_DEEPSEEK_*` 配置段（默认 `ENABLED=false`）
    - 保持 `MODEL_ROUTER_DEFAULT_PROVIDER=zhipu` 不变
    - _需求: 3.1, 3.3, 6.2, 6.3_

  - [x] 4.3 编写属性测试：显式路由正确性
    - **Property 4: 显式路由正确性**
    - 生成随机的提供商名称集合和请求 `provider` 值，验证已注册名称返回正确结果，未注册名称抛出 `ModelAccessError`
    - 测试文件：`test/infrastructure/model_access/test_router_adapter.py`
    - **验证: 需求 4.2**

  - [x] 4.4 编写属性测试：模型前缀路由正确性
    - **Property 5: 模型前缀路由正确性**
    - 生成随机模型名称（带 `claude-`、`gpt-`、`glm-`、`deepseek-` 前缀），验证路由到正确的提供商（前提是该提供商已注册）
    - 测试文件：`test/infrastructure/model_access/test_router_adapter.py`
    - **验证: 需求 4.3**

- [x] 5. 向后兼容验证与热更新测试
  - [x] 5.1 编写向后兼容单元测试
    - 验证仅配置 `MODEL_OPENAI_` 前缀（`PROVIDER_NAME=zhipu`）时，系统行为与重构前一致：提供商注册为 `zhipu`，路由器 `default_provider=zhipu` 和 `glm-` 前缀匹配正常
    - 测试文件：`test/infrastructure/model_access/test_openai_config.py`
    - _需求: 6.1, 6.2, 6.3_

  - [x] 5.2 编写属性测试：动态子类热更新
    - **Property 7: 动态子类热更新**
    - 生成随机配置值，创建动态子类实例，修改临时配置文件，验证属性访问返回新值
    - 使用 `tmp_path` fixture 创建临时 `config.properties`，`monkeypatch` 替换文件路径
    - 测试文件：`test/infrastructure/model_access/test_openai_config.py`
    - **验证: 需求 7.2**

- [x] 6. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 测试运行命令：`cd epsilon-boot && uv run pytest`
- Checkpoint 任务确保增量验证
