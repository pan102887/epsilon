# Python 类型与 Lint 规范

后端 `epsilon-boot` 使用 Python 3.11+，以 `ruff` 统一 lint 与格式化、以 `pyright` 保障类型正确性。所有 Python 代码提交前必须通过 lint 与类型检查基线。

## 类型注解

- 所有公开的函数/方法必须标注参数类型与返回类型；模块级公开变量应有类型标注
- 禁止使用裸 `Any` 规避类型检查；确需动态类型时优先用 `object`、`unknown` 语义的泛型或显式协议（`Protocol`）
- 禁止随意使用 `# type: ignore`；确需忽略时必须在同行注明具体错误码与原因，如 `# type: ignore[arg-type]  # 原因`
- 领域值对象优先使用不可变模型（Pydantic `frozen` 或 `@dataclass(frozen=True)`）
- 保持 `pyright` 现有基线零新增错误，禁止提交引入新的类型错误

## Lint 与格式化

- 统一使用 `ruff` 进行检查与格式化，禁止手动排版对抗格式化结果
- 遵循项目 `pyproject.toml` 中的配置：`line-length = 100`、`target-version = "py311"`、启用规则集 `E`（pycodestyle）、`F`（pyflakes）、`I`（isort）、`UP`（pyupgrade）、`B`（bugbear）、`SIM`（flake8-simplify）、`C4`（flake8-comprehensions）、`RUF`（ruff 专有规则）、`ASYNC`（flake8-async，检查异步代码陷阱）
- 已禁用 `RUF001`/`RUF002`/`RUF003`（歧义 Unicode 字符）：它们会将中文全角标点判为可疑，与本项目强制的中文 docstring/注释规范冲突
- import 顺序交由 `ruff`（isort 规则）自动整理，禁止手动打乱分组

## 异常与惯例

- 禁止裸 `except:`，必须捕获具体异常类型
- 异常链必须显式传递：`raise NewError(...) from err`
- 禁止使用 `print` 输出，统一走项目 logger

## 常用命令

- 检查：`uv run ruff check .`
- 格式化：`uv run ruff format .`
- 类型检查：`uv run pyright`（命令须在 `epsilon-boot/` 目录下执行）
