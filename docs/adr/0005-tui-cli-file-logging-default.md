---
status: Accepted
date: 2026-07-05
deciders: [spec-designer, 平台架构负责人]
supersedes:
superseded-by:
---

# ADR-0005：TUI/CLI 本地文件日志的默认策略

## 背景与问题（Context）

TUI/CLI 当前无文件日志（P0.2）：日志仅在终端，排障与审计困难。需求 4 要求引入 `Local_File_Log_Sink` 把日志写入对应 tier 的 `logs/` 目录，并要求「默认开启还是默认关闭」由 ADR 定夺。核心权衡：可排障性（默认开）vs 静默写盘的隐私 / 磁盘占用 / 用户意外（默认关）。TUI 使用 Textual 全屏渲染，`logging` 输出到 stderr 会破坏界面——这本身也倾向于「把日志导向文件」。

## 决策（Decision）

我们将令本地文件日志 **默认开启（`EPSILON_LOG_TO_FILE=true`）**，但仅在 TUI/CLI 入口（`epsilon` 命令）装配，不影响 `serve`（FastAPI）既有日志链路：

- 新增 `LogSinkConfig`（`env_prefix="EPSILON_LOG_"`，继承 `PropertiesBaseSettings`）：`to_file: bool = True`、`level: str = "INFO"`、`rotation_max_bytes: int = 10_485_760`（10MB）、`rotation_backup_count: int = 5`。
- `Local_File_Log_Sink` 经 `LocalFileTierResolver` 解析 **`USER` tier** 的 `logs/` 目录（`~/.epsilon/<project-hash>/logs/`，决策 2b）：日志属运行时排障产物、随用户走，不污染项目工作区的 `git status` 与文件工具扫描面，且与会话主状态（ADR-0006）同落 USER tier、共享 `LocalFileTierResolver.project_hash()` 分区键以避免跨项目日志混淆。文件名 `epsilon-{date}.log`，用标准库 `logging.handlers.RotatingFileHandler`。
- **脱敏**：复用既有 `RequestLoggingConfig` 的敏感字段约定（`authorization,cookie,api_key,token,secret` 等），通过一个 `SensitiveRedactionFilter`（logging.Filter）在写盘前对消息做正则脱敏，禁止明文写入凭证（需求 4.4）。
- 未显式配置时按默认（开启）行为；`EPSILON_LOG_TO_FILE=false` 可关闭。

## 后果（Consequences）

- **正面**：TUI 全屏渲染不被日志破坏、开箱即得可排障的本地日志；tier + resolver 统一定位、脱敏复用既有约定。
- **负面 / 代价**：默认写盘带来磁盘占用与「用户未预期产生文件」的可能——以轮转上限（10MB×5）+ 落 USER tier（`~/.epsilon/`，不进项目目录、不污染 `git status`）+ 文档说明 + 可关闭开关缓解；日志跨项目聚合在用户目录，须依赖 `<project-hash>` 分区避免不同项目日志混淆。
- **后续影响**：日志装配点在 CLI 入口（`main.py` / `runtime.py`），不进入 domain；`serve` 不受影响；`docs/configuration.md` 须记录 `EPSILON_LOG_*` 配置组与默认策略。

## 备选方案（Alternatives）

- **方案 A：默认关闭** —— 未采纳：与「补齐本地闭环排障能力」的特性目标相悖，且 TUI 场景日志本就不宜留在终端；关闭会让绝大多数用户拿不到排障线索。
- **方案 B：日志落 PROJECT tier（`<workspace>/.epsilon/logs/`）** —— 未采纳：日志写在项目目录会污染 `git status` 与文件工具扫描面，对不知情用户产生项目内意外产物；虽可靠 `.gitignore` 缓解，但「排障产物随用户走」语义更清晰，故最终采用 USER tier（决策 2b）。
- **方案 C：自研轮转** —— 未采纳：标准库 `RotatingFileHandler` 已够用，自研违反最小改动。
