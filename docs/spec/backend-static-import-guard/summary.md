# 交付总结：后端静态检查与 DDD import guard

## Feature Slug

`backend-static-import-guard`

## 最终产物

- `docs/spec/backend-static-import-guard/requirement.md`：从 `docs/plan2.md` Task 6 抽取的需求文档。
- `docs/spec/backend-static-import-guard/design.md`：技术设计文档，覆盖 AST 导入边界、Ruff 配置、CI 接入、错误处理和验证策略。
- `docs/spec/backend-static-import-guard/tasks.md`：实现计划；所有任务与子任务均已完成。
- `docs/spec/backend-static-import-guard/review-log.md`：评审记录；Task 1-4 最终 evaluator verdict 均为 PASS。
- `epsilon-boot/test/static/test_architecture_import_boundaries.py`：新增 DDD 分层导入边界静态测试。
- `epsilon-boot/pyproject.toml`：新增 Ruff dev dependency 与最小 Ruff 配置。
- `epsilon-boot/uv.lock`：通过 `uv` 更新的 Ruff 锁文件。
- `.github/workflows/ci.yml`：后端 `test` job 在 pytest 前新增 Ruff lint 门禁。

## 关键设计决策

- 架构边界检查使用 Python AST 静态解析源码，不导入生产模块、不启动容器或外部服务。
- `domain/` 递归扫描全部 Python 文件，禁止导入 `application` 与 `infrastructure`，且不设置例外。
- `common/` 递归扫描全部 Python 文件，禁止导入 `application` 与 `infrastructure`；唯一临时例外限定为 `common/tools/common_tools.py` 的 `common -> infrastructure` 历史薄壳。
- 导入前缀匹配采用精确分段规则：`module == prefix or module.startswith(prefix + ".")`，避免误伤 `application_utils`、`infrastructurex` 等名称。
- Ruff 通过 `uv add --dev ruff` 接入后端 dev dependency，配置保持最小规则集：`E`、`F`、`I`、`UP`、`B`，`line-length = 100`、`target-version = "py311"`、`ignore = []`。
- 为满足批准的 `uv run ruff check src test` 全量门禁，执行了必要的广泛机械化 Ruff 清理；该范围已由 Task 2 evaluator PASS 接受并记录。
- CI 保留既有后端 `test` job、OS matrix、`epsilon-boot` 工作目录和 pytest 命令，仅在 `uv sync --frozen` 后、pytest 前插入 `uv run ruff check src test`。
- 本特性不新增运行时配置、不修改 `config.properties`、不引入 `.env` 依赖、不新增 DDL / migration / backfill，不接入 evaluation/nightly。

## 验证覆盖

最终验证已通过：

```bash
cd epsilon-boot
uv run ruff check src test
```

结果：`All checks passed!`

```bash
cd epsilon-boot
uv run pytest test/static/test_architecture_import_boundaries.py -v
```

结果：`4 passed`。

评审记录：

- Task 1：AST 导入边界测试实现，evaluator PASS。
- Task 2：Ruff 依赖、配置与全量 lint 基线收敛，evaluator PASS。
- Task 3：CI 后端 lint gate 插入，初次评审因错误基线误判，修正后 evaluator PASS。
- Task 4：最终验证与范围复核，evaluator PASS。

## 后续事项

- `common/tools/common_tools.py` 仍是唯一被记录的历史薄壳例外；后续重构可单独清理该例外并移除白名单。
- 后续若扩展 Ruff 规则，应作为独立 spec / task 切片执行，避免与功能变更混合。
- 本次 Ruff 基线引入触达较多后端文件，审阅时应重点关注其为机械化 lint 收敛，不应混同于业务行为变更。
