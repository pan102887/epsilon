# 配置数据来源规范

本项目配置数据来源优先使用 `config.properties` 文件。

## 规则

- 新增或修改配置项时，应优先写入 `config.properties` 文件，而非 `.env` 文件
- `config.properties` 位于项目根目录 `epsilon-boot/config.properties`
- 仅在需要覆盖 `config.properties` 中的值时（如本地开发调试），才使用 `.env` 文件或环境变量

## 测试隔离规范（强制）

单元测试**不得依赖仓库内真实 `config.properties` / `.env` 的内容，也不得依赖宿主/CI 环境变量**。
否则一旦有人调整配置文件、或运行环境注入了同名前缀的环境变量，直接实例化配置类并断言
具体值的测试就会误红（Hermetic Testing 原则：测试断言的应是**代码契约**，而非配置快照）。

`test/conftest.py` 提供 autouse 夹具 `isolate_config_sources`，默认对**每个**用例：
- 清空所有配置类 `env_prefix` 覆盖的宿主/CI 环境变量；
- 将 `config.properties` 与 `config.local.properties` 重定向到不存在的临时路径，
  使配置类回落到**代码内声明的字段默认值**。

因此编写配置相关测试时：

- **测字段默认值 / 校验、归一化逻辑**：直接实例化（`XxxConfig()`）或用构造参数
  （`XxxConfig(field=x)`），断言的是代码默认值与逻辑，天然与外部源解耦；
- **测「从环境变量加载」**：在用例体内用 `monkeypatch.setenv(...)`（隔离夹具先执行、
  注入在后，仍生效）；
- **测「从 properties 文件加载」**：优先用 `config_factory` 夹具（见下）声明式给出
  配置文本；需精细控制文件路径时也可用 `tmp_path` 写临时文件并显式传入
  `PropertiesFileSettingsSource(..., properties_path=...)`，自带数据源；
- **确需读取仓库内真实配置文件**（集成校验）：在用例/测试类上标注
  `@pytest.mark.real_config` 显式退出隔离。

新增配置类若引入新的 `env_prefix`，需同步补充到 `test/conftest.py` 的 `_CONFIG_ENV_PREFIXES`。
`test/infrastructure/configuration/test_config_prefix_registry.py` 是**防漂移守卫**：
它自动发现全项目所有 `PropertiesBaseSettings` 子类（`common`/`infrastructure`/`application`）
的 `env_prefix`，断言与 `_CONFIG_ENV_PREFIXES` 完全一致——漏登记（隔离漏洞）或残留死前缀
都会使该测试失败并直接列出差异。故隔离夹具本身保持零运行时开销（只读静态元组），
正确性由守卫测试兜底。

### `config_factory` 干净配置源工厂夹具

`test/conftest.py` 另提供 `config_factory` 夹具，用于**声明式**地测试「从 properties
文件加载某值」，免去每处手写 `tmp_path` + `monkeypatch.setattr`：

```python
def test_loads_custom_trigger(config_factory):
    cfg = config_factory(ChatConfig, "chat.compaction_trigger_tokens=4096")
    assert cfg.compaction_trigger_tokens == 4096   # 来自文本
    assert cfg.compaction_encoding == "cl100k_base"  # 未给出 → 代码默认值
```

工厂把主配置源重定向到写入了给定文本的临时文件、本地覆盖源指向不存在路径；
未在文本中出现的字段回落到代码默认值。空文本（默认参数）等价于「纯代码默认值」。
它与全局隔离夹具协同，环境变量始终处于清理后的干净状态。
