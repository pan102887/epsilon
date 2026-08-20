# epsilon CLI Runtime 任务

- [x] 1. 评估 `docs/suggestions/epsilon-tui-cli-cloud-evolution.md` 并产出本期 spec。
- [x] 2. 新增 `application.cli` 模块骨架与 TUI 会话状态。
- [x] 3. 实现 `CliRuntime`，直接复用 DI 容器和领域 Port。
- [x] 4. 实现 slash 命令路由：`/help`、`/new`、`/model`、`/config doctor`、`/quit`。
- [x] 5. 实现 `TuiApp` 流式 chat 循环。
- [x] 6. 实现 `epsilon` console script、`exec` 与 `serve` 命令。
- [x] 7. 增加新增 CLI 模块的单元测试。
- [x] 8. 运行验证命令并记录结果。
- [x] 9. 修订 TUI 交互为主 Agent 会话，移除 CLI 直接工具暴露。

## 补充：修复 application 顶层导入副作用

- [x] 10. 修订 spec，明确顶层 `application` 包不得在导入时创建 FastAPI app 或配置容器。
- [x] 11. 将 `application/__init__.py` 改为 `app` / `service_config` lazy export。
- [x] 12. 增加导入副作用回归测试，覆盖 CLI runtime 导入与兼容导出。
- [x] 13. 运行 `test/application/cli` 与新增导入副作用测试。
