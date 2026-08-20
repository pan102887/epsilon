# 实现计划：后端静态检查与 DDD import guard

## 概述

本阶段只产出可执行任务拆解，不实现代码。任务顺序遵循“先静态测试守卫、再依赖与 lint 基线、后 CI 接入、最后统一验证”的落地路径，确保 `domain/` 与 `common/` 的导入边界先被 AST 测试固定，再把 `ruff` 作为最小后端 lint 门禁接入现有 CI。整个特性不触碰前端、运行时配置、业务行为与数据库迁移。

## Tasks

- [x] 1. 建立 AST 导入边界静态测试
  - [x] 1.1 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 创建静态测试骨架与扫描辅助函数
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中创建模块级中文 docstring，并定义 `BOOT_ROOT`、`REPO_ROOT`、`SRC_ROOT`、`DOMAIN_ROOT`、`COMMON_ROOT`、`ALLOWED_COMMON_INFRASTRUCTURE_EXCEPTION`、`FORBIDDEN_APPLICATION_PREFIX`、`FORBIDDEN_INFRASTRUCTURE_PREFIX`
    - 实现 `_python_files(root: Path) -> list[Path]`、`_parse(path: Path) -> ast.Module`、`_imports(path: Path) -> set[str]`、`_has_prefix(module: str, prefix: str) -> bool`、`_relative(path: Path) -> str`、`_collect_violations(...) -> dict[str, list[str]]`
    - `_parse` 必须使用 `ast.parse(path.read_text(encoding="utf-8"), filename=str(path))`，`_imports` 同时处理 `ast.Import` 与 `ast.ImportFrom`，`_has_prefix` 只接受 `module == prefix` 或 `module.startswith(prefix + ".")`
    - `_python_files` 递归扫描 `src/domain/**/*.py` 与 `src/common/**/*.py`，包含 `__init__.py`；`_collect_violations` 输出“仓库相对路径 -> 命中禁止模块列表”的映射
    - _需求: 1.1, 1.6, 2.1_
  - [x] 1.2 在同一测试模块实现四个公开导入边界测试
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中新增 `test_domain_layer_does_not_import_application_layer() -> None`
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中新增 `test_domain_layer_does_not_import_infrastructure_layer() -> None`
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中新增 `test_common_layer_does_not_import_application_layer() -> None`
    - 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中新增 `test_common_layer_does_not_import_infrastructure_layer_except_legacy_common_tools() -> None`
    - 四个公开测试函数都要使用中文 docstring，并统一采用 `assert violations == {}`；仅 `common -> infrastructure` 调用点允许传入 `{ALLOWED_COMMON_INFRASTRUCTURE_EXCEPTION}`，不得为 `domain` 规则或 `common -> application` 规则扩展白名单
    - 如果扫描到 `epsilon-boot/src/common/tools/common_tools.py` 之外的新违规文件，只记录失败输出与后续修复范围，不得在本特性内新增额外例外
    - _需求: 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4_
  - [x] 1.3 验证 AST 导入边界测试可被现有 pytest 配置收集并通过
    - 在 `epsilon-boot/` 下执行 `uv run pytest test/static/test_architecture_import_boundaries.py -v`
    - 若出现新的 `domain` 或 `common` 违规，先按失败输出定位具体文件与模块，再以修复生产代码或确认历史现状为后续工作项；不要放宽 `_collect_violations` 规则或扩大例外名单
    - 记录该命令仍依赖现有 `[tool.pytest] asyncio_mode = "auto"`、`pythonpath = ["src"]`、`testpaths = ["test"]`，不改动 pytest 配置
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 5.1, 5.3, 5.6_
  - [x] 1.4 检查点：确认静态测试层边界守卫未执行生产导入
    - 复核 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 只使用 `Path.read_text()`、`ast.parse()`、AST 遍历与路径比较，不引入 `importlib`、运行时容器启动或任何生产模块执行
    - 复核唯一历史例外仍固定为 `epsilon-boot/src/common/tools/common_tools.py`，且只作用于 `common -> infrastructure` 规则
    - _需求: 1.1, 2.1, 2.4_

- [x] 2. 通过 uv 接入 Ruff 并建立最小 lint 基线
  - [x] 2.1 在 `epsilon-boot/` 中使用 uv 添加 Ruff 开发依赖
    - 在 `epsilon-boot/` 下执行 `uv add --dev ruff`
    - 由 `uv` 自动更新 `epsilon-boot/pyproject.toml` 与 `epsilon-boot/uv.lock`，不得手工编辑 `uv.lock` 或猜测 Ruff 版本约束
    - 保持变更仅限后端 dev dependency 集合，不新增运行时依赖、不修改 `config.properties`、不引入 `.env` 键
    - _需求: 3.1, 3.2, 3.7_
  - [x] 2.2 在 `epsilon-boot/pyproject.toml` 补齐 Ruff 最小配置
    - 在 `epsilon-boot/pyproject.toml` 中新增 `[tool.ruff]` 与 `[tool.ruff.lint]`
    - 精确配置 `line-length = 100`、`target-version = "py311"`、`select = ["E", "F", "I", "UP", "B"]`、`ignore = []`
    - 保持现有 `[tool.pytest]` 不变，不新增 `per-file-ignores`、`[tool.ruff.format]`、默认 `fix` 开关或其他超范围 lint 策略
    - _需求: 3.3, 3.4, 3.5, 3.6, 3.7_
  - [x] 2.3 验证 Ruff 最小规则集，并按受控边界收敛历史 lint 问题
    - 在 `epsilon-boot/` 下执行 `uv run ruff check src test`
    - 仅当发现 Ruff 可安全自动修复的问题时，才执行 `uv run ruff check src test --fix`；随后逐文件人工复核 diff，确认没有语义改动、分层边界变化或大范围无关清理
    - 历史问题处理只允许做安全自动修复和必要的最小手工修复，不得借机开展跨模块语义重构、统一风格大扫除或与本特性无关的 lint 清零
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.1, 5.2, 5.4, 5.5, 5.7_
  - [x] 2.4 检查点：确认依赖与锁文件变更受控
    - 复核 `epsilon-boot/pyproject.toml` 中 dev 依赖与 Ruff 配置完整一致
    - 复核 `epsilon-boot/uv.lock` 仅承载 `uv add --dev ruff` 生成的锁文件差异，没有手工改写痕迹
    - 复核本组任务未触碰运行时业务源码、前端文件或配置来源文件
    - _需求: 3.1, 3.2, 3.7, 5.5_

- [x] 3. 将后端 lint 门禁插入现有 CI 工作流
  - [x] 3.1 在 `.github/workflows/ci.yml` 的后端 `test` job 中插入 Ruff 检查步骤
    - 在 `.github/workflows/ci.yml` 中保留现有 `test` job 名称、OS matrix、`working-directory: epsilon-boot`、`shell: bash`、`uv sync --frozen` 与 `uv run pytest -m "not benchmark"`
    - 在 `Sync dependencies` 之后、`Run tests (includes static secret hygiene checks)` 之前新增 `Run backend lint gate` 步骤，命令为 `uv run ruff check src test`
    - 不修改 `frontend` job，不新增前端 lint/typecheck/build/test 步骤，也不拆分新的独立 backend lint job
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5_
  - [x] 3.2 检查点：确认 CI 门禁顺序与失败语义符合设计
    - 复核 `.github/workflows/ci.yml` 中后端执行顺序固定为 `uv sync --frozen` → `uv run ruff check src test` → `uv run pytest -m "not benchmark"`
    - 复核 lint 失败时不会继续执行 pytest，且该失败仍会直接形成 pull request 门禁失败
    - 复核 Ubuntu 与 Windows 矩阵均复用同一后端 lint 命令，前端 job 行为保持不变
    - _需求: 4.2, 4.3, 4.4, 4.5_

- [x] 4. 完成最终验证并收口变更范围
  - [x] 4.1 执行本特性的最小验证命令集合
    - 在 `epsilon-boot/` 下执行 `uv run ruff check src test`
    - 在 `epsilon-boot/` 下执行 `uv run pytest test/static/test_architecture_import_boundaries.py -v`
    - 若 2.3 中使用过 `uv run ruff check src test --fix`，在此步骤前完成全部人工 diff 复核，并确认最终结果仍满足导入边界与 lint 门禁要求
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [x] 4.2 复核本特性的修改范围与非目标保持不变
    - 复核修改文件仅限 `.github/workflows/ci.yml`、`epsilon-boot/pyproject.toml`、`epsilon-boot/uv.lock`、`epsilon-boot/test/static/test_architecture_import_boundaries.py`
    - 明确确认未修改前端文件、`epsilon-boot/config.properties`、运行时业务行为代码、evaluation/nightly 配置，以及任何数据库迁移、DDL、索引或回填脚本
    - 对照最终 diff 检查 `common/tools/common_tools.py` 仍只是被测试白名单引用，而不是在本特性内被重构或删除
    - _需求: 2.1, 3.7, 4.1, 5.5, 5.7_
  - [x] 4.3 检查点：准备进入后续审批或实现阶段
    - 汇总本阶段产物已覆盖 AST 边界守卫、Ruff 依赖与配置、CI lint 顺序、最终验证命令四个设计组件
    - 确认每个子任务都可独立 review，且没有遗留“先放宽规则再补修复”的临时方案
    - _需求: 1.1, 2.4, 3.7, 4.2, 5.7_

## 备注

- 本特性不包含数据库 DDL、索引、迁移或数据回填任务；仓库的 SQL / 迁移目录为 `epsilon-boot/migrations/`，本次不得新增或修改其中内容。
- 所有后端命令必须在 `epsilon-boot/` 目录下通过 `uv` 执行，禁止使用 `pip`、`poetry`、`pipenv` 或 `conda`。
- 新增 Python 测试模块与公开测试函数必须使用中文 docstring；若 Ruff 安全自动修复产生额外 diff，必须逐文件人工复核后再进入下一阶段。
