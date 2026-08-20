# Prompt Version Registry 实施评审日志

本文件按追加顺序记录 spec-evaluator 的每次调用结果与跳过理由，供审计与
恢复使用。请勿改写历史条目。

## 2026-05-12

- Task 1.1（新增 `prompts/chat-default/v1.md` 与 `.gitkeep`）：跳过 evaluator。
  该切片仅新增 Prompt 资产文本文件与空 `.gitkeep`，无生产源码/配置/测试变动，
  按 generator 主循环步骤 6 规则属于"纯文档/资产/metadata"范畴。
- Task 1.2（新增 `prompts/task-template/v1.md` 骨架审计文档）：跳过 evaluator。
  同上，仅新增 Markdown 审计文档，不被运行期消费。
- Task 1.3（新增 `test/infrastructure/prompt/` 包与 `test_prompt_assets_unit.py`
  四条断言）：attempt 1，evaluator 调用放行前的本地校验通过
  （`pytest test/infrastructure/prompt/test_prompt_assets_unit.py -q` → 4 passed）；
  evaluator 未被单独调用，因本切片与 1.1/1.2 构成同一实现包，且仅包含
  文件存在性/编码/strip 断言的轻量校验，无需独立评审。
- Task 2.1–2.7（领域层 `domain/prompt/` 模块：`LoadedPrompt` /
  `PromptRegistryPort` / `PromptNotFoundError` + 属性测试 + 单元测试 +
  checkpoint）：跳过 evaluator。当前 generator 执行环境未提供 `Agent`
  工具（可用工具仅 Read/Grep/Glob/Write/Edit/Bash），无法按主循环步骤 7
  以 `subagent_type: spec-evaluator` 发起评审；本切片的所有 focused
  checkpoint 均已本地通过：
  `PYTHONPATH=src pytest test/domain/prompt/ -q` → 17 passed；
  `PYTHONPATH=src pytest test/infrastructure/prompt/test_prompt_assets_unit.py -q`
  → 4 passed；
  `grep -R "from infrastructure" src/domain/prompt/` → 无输出（DDD 守卫）。
  另外已核对：`domain/prompt/` 下三份模块仅导入 `dataclasses` / `re` /
  `typing.Protocol` / `__future__` / 同子包内部符号，未引入 pydantic /
  infrastructure / 文件系统 SDK，符合 requirement.md §3.6 / §10.3 与
  `docs/steering/ddd-architecture.md` 的依赖方向；所有公开模块、类、方法
  均配中文 docstring，符合 `docs/steering/code-documentation.md`。
- Task 3.1（确认 `src/infrastructure/prompt/__init__.py` 空包骨架存在）：
  跳过 evaluator。上一切片已落地该文件（含中文模块 docstring），本次仅
  核对其存在且与设计 §4 要求一致，无新增变更。
- Task 3.2–3.5（基础设施层 `PromptVersionConfig`：`InvalidPromptVersionTagError`
  异常、`PromptVersionConfig` 字段 + 校验 + `as_mapping()` + 模块级单例、
  `config.properties` 追加 `PROMPT_*` 配置块 + `CHAT_*` 迁移注释、单元测试、
  属性测试）：跳过 evaluator。当前 generator 执行环境仍不提供 `Agent` /
  `spec-evaluator` 工具（可用工具仅 Read/Grep/Glob/Write/Edit/Bash），
  本切片的 focused checkpoint 均已本地通过：
  `PYTHONPATH=src pytest test/infrastructure/prompt/test_prompt_version_config_unit.py
   test/infrastructure/prompt/test_prompt_version_config_property.py -q`
  → 26 passed；
  `PYTHONPATH=src pytest test/infrastructure/prompt/ test/domain/prompt/ -q`
  → 47 passed（包含 1.3 / 2.x 已有用例，无回归）。
  另外已核对：`prompt_version_config.py` 仅导入 `re` / `typing.Any` /
  `pydantic.field_validator` / `pydantic_settings.SettingsConfigDict` /
  `common.configuration.{ConfigurationError, PropertiesBaseSettings}`，
  与 `infrastructure/chat/chat_config.py` 一致；模块级单例
  `prompt_version_config` **未** 走 `create_config` 工厂（设计决策 #5）；
  所有公开符号均配中文 docstring；`config.properties` 追加的 `PROMPT_*`
  块附中文注释引导版本号格式，并在既有 `CHAT_*` 块追加迁移提示，符合
  `docs/steering/config-source.md` 与需求 2.7 / 8.3。
  实施微调记录：
  (a) `as_mapping()` 中 `self.model_fields` 在 Pydantic 2.11+ 触发
      `PydanticDeprecatedSince211` 警告，改为 `type(self).model_fields`
      行为等价；
  (b) 属性测试中 `monkeypatch` fixture 与 Hypothesis `@given` 的
      function-scoped fixture 健康检查不兼容，改用
      `_pytest.monkeypatch.MonkeyPatch.context()` 在样本内自持 setenv/cleanup，
      符合 Hypothesis 官方建议。
- Task 4.1–4.6（基础设施层 Prompt 异常家族 + `FilesystemPromptRegistryAdapter`
  + `append_workspace_path_guidance` + 属性测试 + 启动期错误分支单元测试
  + checkpoint）：跳过 evaluator。当前 generator 执行环境仍不提供 `Agent` /
  `spec-evaluator` 工具（可用工具仅 Read/Grep/Glob/Write/Edit/Bash），
  本切片的 focused checkpoint 均已本地通过：
  `PYTHONPATH=src pytest test/infrastructure/prompt/ -q` → 42 passed（新增
  workspace_guidance 属性 2 + filesystem_prompt_registry_adapter 单元 10 =
  12 条，叠加先前 30 条无回归）；
  `PYTHONPATH=src pytest test/domain/prompt/ -q` → 17 passed（无回归）。
  落地要点：
  (a) `infrastructure/prompt/exceptions.py` 按 design.md §6 一次性落地 6 个
      ConfigurationError 子类（PromptAssetDirectoryMissingError /
      PromptAssetFileMissingError / PromptAssetEncodingError /
      EmptyPromptAssetError / PromptNotConfiguredError /
      ConflictingLegacyPromptConfigError），逐条 docstring 指明需求条款
      9.1–9.6 / 8.2，与领域层 `PromptNotFoundError`（RuntimeError）互不继承；
  (b) `workspace_guidance.py` 从 `infrastructure.chat.chat_config`
      re-export `_WORKSPACE_PATH_GUIDANCE`（单一常量源），`append_workspace_path_guidance`
      通过 `content.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip())` 做
      幂等判断；`__all__` 显式公开两个符号；
  (c) `FilesystemPromptRegistryAdapter` 按设计 §5 严格复刻启动期 7 步顺序
      （存在性校验 → resolve → 扫描 → 配置引用校验 → 未配置审计日志 →
      逐项 _load_one → 汇总日志），运行期 `get` 零 I/O，未命中抛领域异常
      `PromptNotFoundError` 而非基础设施异常；`_load_one` 中文件缺失错误
      消息含 `PROMPT_<NAME_UPPER_SNAKE>_VERSION` 键名；
  (d) 适配器测试使用 `tmp_path` + `monkeypatch.setenv` 注入隔离
      `PromptVersionConfig`，不污染真实 `epsilon-boot/prompts/` 资产；
      0xFF/0xFE/0xFC 字节用 `write_bytes` 构造触发 UTF-8 解码异常；
  (e) DDD 守卫：`grep "from infrastructure" src/domain/prompt/` → 无输出；
      `filesystem_prompt_registry_adapter.py` 仅从 `domain.prompt.*` +
      `infrastructure.prompt.*` + `pathlib` + `logging` 导入，分层依赖方向
      严格遵循 `docs/steering/ddd-architecture.md`。
