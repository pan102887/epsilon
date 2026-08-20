# 实施总结：Human-in-the-loop 工具审批

## 完成范围

- 领域层新增审批值对象、审批异常、Agent/Chat 审批端口与状态联合返回模型。
- 基础设施层新增 HITL 配置、静态审批策略、本地文件/Redis 审批状态存储、审批日志脱敏工具。
- `ReActAgentAdapter` 支持同步、流式、事件流审批中断与 `resume(...)` 恢复执行，保持默认关闭兼容。
- `ChatServiceAdapter` 支持 `approval_required` 编排、审批恢复、会话保存/清理语义。
- HTTP 层新增 `/api/chat/sessions/{session_id}/approvals/{approval_id}/resume`，同步/SSE 输出审批状态。
- TUI v1 展示 approval_required 提示，不实现交互式审批表单。
- 容器装配 `ApprovalPolicyPort`、`ApprovalStateStorePort`，`config.properties` 新增 HITL 配置项。
- 更新 `docs/agent.md`、`docs/api.md`、`docs/tools.md`。

## 关键变更路径

- `epsilon-boot/src/domain/agent/value_objects.py`
- `epsilon-boot/src/domain/agent/exceptions.py`
- `epsilon-boot/src/domain/agent/ports.py`
- `epsilon-boot/src/domain/chat/value_objects.py`
- `epsilon-boot/src/domain/chat/ports.py`
- `epsilon-boot/src/infrastructure/agent/`
- `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
- `epsilon-boot/src/application/api/routers/chat.py`
- `epsilon-boot/src/application/container_config.py`
- `epsilon-boot/src/application/cli/tui.py`
- `epsilon-boot/config.properties`
- `docs/agent.md`
- `docs/api.md`
- `docs/tools.md`

## 验证结果

- `uv run --frozen pytest test`
- 结果：`1275 passed, 2 skipped`

## 残余风险

- v1 未实现 Web 审批弹窗、TUI 交互式审批表单、子 Agent 内部审批传播和组织级审批流。
- HITL 仅是工具执行前控制，不能替代 Workspace、工具权限、工具参数校验、网络访问控制、命令沙箱或 OS 权限。
