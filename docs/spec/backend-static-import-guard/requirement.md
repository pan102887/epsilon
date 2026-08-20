# 需求文档：后端静态检查与 DDD import guard

## 简介

当前仓库已经通过 `docs/steering/ddd-architecture.md` 明确规定 DDD 分层依赖方向，但后端生产代码中仍存在边界泄漏风险，尤其是 `common/` 反向依赖 `infrastructure/` 的历史遗留，以及后续开发可能继续引入 `domain/ -> application/`、`domain/ -> infrastructure/`、`common/ -> application/`、`common/ -> infrastructure/` 的新破窗。如果这些约束只停留在文档层，代码审查很难稳定阻断回归。

本特性从 `docs/plan2.md` 的 Task 6 抽取，目标是在不扩大业务功能面的前提下，为后端建立两类自动化门禁：

- 基于 AST 的 `Architecture_Import_Boundary_Test`，静态检查 `Domain_Layer` 与 `Common_Layer` 的导入边界。
- 基于 `ruff` 的 `Backend_Lint_Gate`，在现有 `Backend_CI_Job` 中于 pytest 之前执行最小 Python lint 门禁。

本特性范围内包含：

- 在 `epsilon-boot/test/static/test_architecture_import_boundaries.py` 新增 AST 静态测试模块。
- 在 `epsilon-boot/pyproject.toml` 通过 `UV_Dev_Dependency_Workflow` 引入 `Ruff_Dev_Dependency`，并增加最小 `Ruff_Config`。
- 在 `.github/workflows/ci.yml` 的现有 `Backend_CI_Job` 中新增 `Backend_Lint_Gate`，并保持其先于 `Backend_Pytest_Gate` 执行。
- 以 `Verification_Command_Set` 明确本特性的完成验证命令与历史 lint 问题收敛方式。

明确不在本特性范围内：

- 不修改任何前端文件，不新增前端 lint、typecheck、build、test 或其他前端门禁。
- 不改变 `Runtime_Business_Behavior`，包括聊天、任务执行、模型路由、工具调用、会话存储、Run runtime、HTTP API 与工作流编排行为。
- 不新增运行时配置项，不修改 `config.properties`，也不引入新的 `.env` 依赖。
- 不引入 `ruff` 与 `Architecture_Import_Boundary_Test` 之外的独立安全扫描器、SAST 平台或通用依赖图分析平台。
- 不新增 evaluation CI、nightly schedule、定时任务或基线评估流程。
- 不在本阶段重构或删除 `Legacy_CommonTools_Exception`，只允许将其作为被显式记录的临时例外继续受控存在。
- 不在本阶段实现覆盖全部层间关系的全仓库架构守卫；本次只针对 `Domain_Layer` 与 `Common_Layer` 的禁止导入规则建立自动化门禁。

本特性的完成判断依赖 `Verification_Command_Set`：实现完成后，应能够在 `Backend_Working_Directory` 中运行最小 lint 与静态测试命令，并使对应门禁在本地或 CI 中稳定通过。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 后端源码树 | Backend_Source_Tree | `epsilon-boot/src` 下的生产 Python 源码目录。 |
| 领域层 | Domain_Layer | `Backend_Source_Tree/domain`，DDD 领域模型与 Port 所在层。 |
| 公共层 | Common_Layer | `Backend_Source_Tree/common`，共享内核与跨层公共能力所在层。 |
| 应用层 | Application_Layer | `Backend_Source_Tree/application`，应用编排与适配入口所在层。 |
| 基础设施层 | Infrastructure_Layer | `Backend_Source_Tree/infrastructure`，Adapter 与外部系统集成所在层。 |
| 后端工作目录 | Backend_Working_Directory | `epsilon-boot/`，所有后端 `uv` 命令必须在此目录执行。 |
| 架构导入边界静态测试 | Architecture_Import_Boundary_Test | `epsilon-boot/test/static/test_architecture_import_boundaries.py` 中的 AST 静态测试模块。 |
| 领域导入边界规则 | Domain_Import_Boundary_Rule | `Domain_Layer` 不得导入 `Application_Layer` 或 `Infrastructure_Layer` 的规则。 |
| 公共导入边界规则 | Common_Import_Boundary_Rule | `Common_Layer` 不得导入 `Application_Layer` 或 `Infrastructure_Layer` 的规则。 |
| 历史薄壳例外 | Legacy_CommonTools_Exception | 当前仅允许 `common/tools/common_tools.py` 暂时保留的 `Common_Layer` 例外导入。 |
| 中文文档约定 | Chinese_Docstring_Convention | 新增 Python 模块与公开函数使用中文 docstring 的强制规范。 |
| 后端项目配置 | Backend_Pyproject_File | `epsilon-boot/pyproject.toml`。 |
| uv 开发依赖流程 | UV_Dev_Dependency_Workflow | 通过 `uv` 向后端 dev dependency 增减工具的流程。 |
| Ruff 开发依赖 | Ruff_Dev_Dependency | 作为后端开发依赖引入的 `ruff`。 |
| Ruff 配置 | Ruff_Config | `Backend_Pyproject_File` 中的 `[tool.ruff]` 与 `[tool.ruff.lint]` 配置。 |
| CI 工作流 | CI_Workflow | 仓库中的 `.github/workflows/ci.yml`。 |
| 后端 CI 任务 | Backend_CI_Job | `CI_Workflow` 中负责后端依赖同步、静态检查与 pytest 的 job。 |
| 后端静态 Lint 门禁 | Backend_Lint_Gate | `Backend_CI_Job` 中执行的 `uv run ruff check src test`。 |
| 后端 Pytest 门禁 | Backend_Pytest_Gate | `Backend_CI_Job` 中已有的 pytest 测试步骤。 |
| PR 门禁 | Pull_Request_CI_Gate | Pull request 上由 CI 状态形成的合并阻断信号。 |
| 验证命令集合 | Verification_Command_Set | 实现完成后用于本地或 CI 验证本特性的最小命令集合。 |
| 安全自动修复 | Safe_Ruff_Autofix | `uv run ruff check src test --fix` 仅应用 Ruff 安全自动修复的受限用法。 |
| 人工差异复核 | Manual_Diff_Review | 对 `Safe_Ruff_Autofix` 产生的 diff 进行人工确认的步骤。 |
| 运行时业务行为 | Runtime_Business_Behavior | 聊天、任务执行、模型路由、工具调用、会话存储、Run runtime 等线上业务行为。 |

## 需求

### 需求 1：为领域层与公共层建立 AST 导入边界守卫

**用户故事：** 作为后端维护者，我希望后端仓库具备自动化导入边界静态测试，以便在提交时及时发现 DDD 分层依赖回归。

#### 验收标准

1. THE Architecture_Import_Boundary_Test SHALL parse Python source files in Domain_Layer and Common_Layer by AST without importing production modules for execution.
2. FOR ALL files in Domain_Layer, THE Domain_Import_Boundary_Rule SHALL reject imports that target Application_Layer.
3. FOR ALL files in Domain_Layer, THE Domain_Import_Boundary_Rule SHALL reject imports that target Infrastructure_Layer.
4. FOR ALL files in Common_Layer, THE Common_Import_Boundary_Rule SHALL reject imports that target Application_Layer.
5. FOR ALL files in Common_Layer, THE Common_Import_Boundary_Rule SHALL reject imports that target Infrastructure_Layer.
6. THE Architecture_Import_Boundary_Test SHALL implement Chinese_Docstring_Convention for its module docstring and public test functions.

### 需求 2：以显式例外控制当前历史薄壳泄漏

**用户故事：** 作为架构治理负责人，我希望历史遗留边界泄漏被显式圈定为最小例外，以便在不放大破窗的前提下推进静态门禁落地。

#### 验收标准

1. IF Legacy_CommonTools_Exception IN active state, THEN THE Common_Import_Boundary_Rule SHALL allow only `common/tools/common_tools.py` as the temporary exception file.
2. FOR ALL files in Common_Layer, THE Legacy_CommonTools_Exception SHALL NOT expand beyond one file.
3. WHILE Legacy_CommonTools_Exception IN active state, WHEN Architecture_Import_Boundary_Test detects a Common_Layer violation, THE Common_Import_Boundary_Rule SHALL report offending files other than `common/tools/common_tools.py` as failures.
4. THE Architecture_Import_Boundary_Test SHALL keep Domain_Import_Boundary_Rule fully enforced without any Legacy_CommonTools_Exception-style exemption.

### 需求 3：以 uv 管理 Ruff 依赖并固定最小配置

**用户故事：** 作为后端开发者，我希望后端 lint 工具通过统一的 uv 依赖流程接入并具备最小稳定配置，以便本地与 CI 使用一致的静态检查基线。

#### 验收标准

1. WHEN Ruff_Dev_Dependency is introduced, THE UV_Dev_Dependency_Workflow SHALL add it from Backend_Working_Directory by using `uv`.
2. THE Backend_Pyproject_File SHALL declare Ruff_Dev_Dependency in the backend dev dependency set.
3. THE Ruff_Config SHALL be defined in Backend_Pyproject_File with `line-length = 100`.
4. THE Ruff_Config SHALL be defined in Backend_Pyproject_File with `target-version = "py311"`.
5. THE Ruff_Config SHALL select `E`, `F`, `I`, `UP`, and `B` lint rule families.
6. THE Ruff_Config SHALL define an empty ignore list.
7. THE Ruff_Config SHALL NOT add runtime configuration or modify Runtime_Business_Behavior.

### 需求 4：将后端 lint 门禁接入现有 CI 工作流

**用户故事：** 作为代码审查者，我希望 pull request 在运行后端 pytest 之前先执行后端 lint，以便更早阻断静态问题和架构回归。

#### 验收标准

1. THE CI_Workflow SHALL keep a Backend_CI_Job for the backend project.
2. THE Backend_CI_Job SHALL execute Backend_Lint_Gate before Backend_Pytest_Gate.
3. WHEN Backend_Lint_Gate executes, THE Backend_CI_Job SHALL run `uv run ruff check src test` from Backend_Working_Directory.
4. IF Backend_Lint_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.
5. IF Backend_Pytest_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.

### 需求 5：定义本特性的最小验证闭环

**用户故事：** 作为交付负责人，我希望本特性有明确的最小验证命令与历史问题处理边界，以便实现者和评审者能一致判断任务是否完成。

#### 验收标准

1. THE Verification_Command_Set SHALL be executable from Backend_Working_Directory.
2. THE Verification_Command_Set SHALL include `uv run ruff check src test`.
3. THE Verification_Command_Set SHALL include `uv run pytest test/static/test_architecture_import_boundaries.py -v`.
4. WHEN historical Ruff violations are detected, THE Safe_Ruff_Autofix SHALL permit only `uv run ruff check src test --fix` for safe autofixes.
5. WHEN Safe_Ruff_Autofix is used, THE Manual_Diff_Review SHALL be required before the feature is considered complete.
6. WHEN Verification_Command_Set completes successfully, THE Architecture_Import_Boundary_Test SHALL be in passing state.
7. WHEN Verification_Command_Set completes successfully, THE Backend_Lint_Gate SHALL be in passing state.
