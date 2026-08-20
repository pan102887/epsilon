# epsilon CLI Runtime Review Log

## 2026-05-27

- Coordinator 自评：建议文档范围已收敛到阶段 1，未把 Approval、Skill/MCP、云沙箱混入本期。
- 验证通过：`uv run --frozen epsilon --help` 可显示 CLI 帮助；`env PYTHONPATH=src uv run --frozen pytest -q test/application/cli` 通过 9 个测试。
- 修订反馈：TUI 不应直接暴露工具列表；已调整为默认主 Agent 会话，工具选择只发生在 Agent Loop 内部。
