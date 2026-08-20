---
status: Accepted
date: 2026-07-05
deciders: [spec-designer, 平台架构负责人]
supersedes:
superseded-by:
---

# ADR-0004：config.local.properties 本地覆盖配置的优先级插入位置

## 背景与问题（Context）

当前本地覆盖只能用 `.env`。需求 5 要求引入 `.epsilon/config.local.properties`，其优先级须**低于环境变量、高于 `config.properties`**，以便本地调试而不污染主配置源、也不越过部署期环境变量。这修订了 `PropertiesBaseSettings.settings_customise_sources` 这一跨模块配置解析契约，属方向级决策。

现状源链（高→低）：`init > env > PropertiesFileSettingsSource(config.properties) > dotenv(.env) > secrets > 默认`。

## 决策（Decision）

我们将在 `PropertiesBaseSettings.settings_customise_sources` 中，于 `env_settings` 与既有 `config.properties` 源之间插入一个新的 **`config.local.properties` 源**，得到新链（高→低）：

`init > env > config.local.properties > config.properties > .env > secrets > 默认`

实现方式：**复用既有 `PropertiesFileSettingsSource` 类，传入不同 `properties_path`**（`PropertiesFileSettingsSource(settings_cls, properties_path=_LOCAL_PROPERTIES_FILE)`），而不新增一个近乎重复的源类（SRP / 最小改动）。

- 新增模块级 `_LOCAL_PROPERTIES_FILE = _find_file_in_epsilon("config.local.properties")`：先在 `<WORKSPACE_ROOT 或 CWD>/.epsilon/config.local.properties` 定位，再退回 `_find_file` 向上查找兜底；**文件缺失时 `_parse_properties_file` 返回空 dict，不报错**（需求 5.5 行为完全一致）。
- `ConfigProxy` 的热更新源文件列表新增 `config.local.properties`（存在时纳入 mtime 监听），使本地覆盖热更新与 `.env` / `config.properties` 一致。
- 键名到字段的转换规则与 `config.properties` 完全相同（`.`→`_`、大写、按 `env_prefix` 匹配）。

## 后果（Consequences）

- **正面**：本地调试可覆盖 `config.properties` 而不改主配置源、不越过部署期 env；复用现有源类，改动集中在一个方法与两个模块级常量。
- **负面 / 代价**：源链新增一层，配置解析多读一个文件；须以多源覆盖测试锁定「env > local > properties」全序关系。`config.local.properties` 含本地敏感值，须默认入 `.gitignore`（需求 7.2，`.epsilon/` 已被忽略）。
- **后续影响**：`docs/configuration.md` 须新增 `config.local.properties` 与优先级说明；不得违反 steering `config-source.md`——`config.properties` 仍是「新增/修改配置项优先写入」的主源。

## 备选方案（Alternatives）

- **方案 A：新增独立的 `LocalPropertiesFileSettingsSource` 类** —— 未采纳：与 `PropertiesFileSettingsSource` 逻辑几乎完全重复，违反 SRP 与最小改动；复用同类 + 不同路径即可。
- **方案 B：把 local 覆盖放在 `.env` 之下** —— 未采纳：违反需求 5.2 明确的优先级（local 须高于 `config.properties`）。
- **方案 C：让 local 覆盖高于环境变量** —— 未采纳：会让本地文件越过部署期 env，破坏运维在生产用 env 强制覆盖的能力（需求 5.3）。
