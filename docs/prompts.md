# Prompt 资产目录与版本化注册

本文档说明后端 Prompt 资产的存放规则、版本化机制以及历史 `CHAT_SYSTEM_PROMPT`
配置项的迁移路径。所有改动需配合 [docs/spec/prompt-version-registry/](spec/prompt-version-registry/)
下的需求与设计文档一并阅读。

## 资产目录位置

- 根目录：`epsilon-boot/prompts/`
- 子目录：每个 Prompt 名称对应一个子目录，目录名使用小写连字符（kebab-case）
  - `prompts/chat-default/`：聊天编排默认系统提示词
  - `prompts/task-template/`：任务执行系统提示词骨架（仅作审阅文档）

## 命名与版本化规则

- 版本文件名：`v<N>.md`，其中 `<N>` 为正整数（首版为 `v1`，第 N 版为 `vN`）
- 版本号约束：必须匹配正则 `^v[1-9]\d*$`；禁止 `v0`、`v01`、`V1`、`v1.0.0`
- 文件编码：UTF-8、行尾使用 LF、不得包含 BOM、不得含 YAML front matter
- 文件内容：`strip()` 后必须非空；任何文件内容变更必须新建版本，不得就地覆盖

## 配置键与切换流程

`config.properties` 中通过下列两个键选择实际加载的版本：

| 键 | 默认 | 说明 |
|---|---|---|
| `PROMPT_CHAT_DEFAULT_VERSION` | `v1` | 选择 `prompts/chat-default/v<N>.md` |
| `PROMPT_TASK_TEMPLATE_VERSION` | `v1` | 选择 `prompts/task-template/v<N>.md` |

切换版本流程：

1. 在 `prompts/<name>/` 目录下新增 `v<N+1>.md`，写入新版本内容；
2. 更新 `config.properties` 中的 `PROMPT_<NAME>_VERSION=v<N+1>`；
3. 重启后端服务（不支持热更新；错误的版本号会在启动期 fail-fast）。

## 新增 Prompt 的步骤

1. 在 `prompts/` 下新建子目录，目录名遵循小写连字符规则；
2. 在子目录下创建 `v1.md`，写入初始内容；
3. 在 `infrastructure/prompt/prompt_version_config.py::PromptVersionConfig` 增加
   对应字段（`<name_with_underscore>_version: str = "v1"`）；
4. 在 `config.properties` 增加 `PROMPT_<NAME>_VERSION=v1`；
5. 在 Prompt 消费方（如 `ChatServiceAdapter` / `TaskAgentAdapter`）通过
   `PromptRegistryPort.get(name)` 获取 `LoadedPrompt`；
6. 编写或扩展单元测试，断言新版本 Prompt 的加载、`prompt_id` 透传与日志/trace
   属性符合预期。

## `CHAT_SYSTEM_PROMPT` 迁移三步法

`CHAT_SYSTEM_PROMPT` 已被资产目录与版本化键替代。任意环境（含 `.env` /
`config.properties`）若仍设置该键，容器启动期会抛出
`ConflictingLegacyPromptConfigError` 阻止启动。完成迁移：

1. 删除 `config.properties` 中的 `CHAT_SYSTEM_PROMPT` 键；
2. 删除环境变量 `CHAT_SYSTEM_PROMPT`；
3. 将自定义 system prompt 内容写入 `prompts/chat-default/v<N>.md`，并将
   `PROMPT_CHAT_DEFAULT_VERSION=v<N>` 写入 `config.properties`。

## 关联文档

- [docs/spec/prompt-version-registry/requirement.md](spec/prompt-version-registry/requirement.md)
- [docs/spec/prompt-version-registry/design.md](spec/prompt-version-registry/design.md)
- [docs/spec/prompt-version-registry/tasks.md](spec/prompt-version-registry/tasks.md)
