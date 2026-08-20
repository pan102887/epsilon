"""Run 领域异常定义模块。

所有异常继承 `common.exceptions.BizException`，错误码使用 61001 至
61019。异常消息只包含 run_id、状态、原因等可定位信息，不拼接完整
payload，避免把用户提示词、工具参数或模型上下文泄露给客户端错误体。
"""

from __future__ import annotations

from common.exceptions import BizException

_SENSITIVE_REASON_TOKENS = (
    "{",
    "}",
    "[",
    "]",
    '"',
    "'",
    "password",
    "api_key",
    "token",
    "secret prompt",
    "messages",
    "tool_args",
)


def _safe_reason(reason: str, *, max_length: int = 120) -> str:
    """返回适合对外错误体展示的原因摘要。"""
    lowered = reason.lower()
    if any(token in lowered for token in _SENSITIVE_REASON_TOKENS):
        return "敏感详情已隐藏"
    if len(reason) > max_length:
        return f"{reason[:max_length]}..."
    return reason


class RunNotFoundError(BizException):
    """指定 Run 不存在。"""

    def __init__(self, run_id: str) -> None:
        super().__init__(code=61001, message=f"运行 {run_id} 不存在")
        self.run_id = run_id


class RunQueueFullError(BizException):
    """后台 Run 队列或并发容量已满。"""

    def __init__(self, limit_name: str, limit: int) -> None:
        super().__init__(code=61002, message=f"运行容量已满：{limit_name}={limit}")
        self.limit_name = limit_name
        self.limit = limit


class RunInvalidTransitionError(BizException):
    """Run 状态迁移不符合领域状态机规则。"""

    def __init__(self, current_status: str, target_status: str) -> None:
        super().__init__(
            code=61003,
            message=f"运行状态不可从 {current_status} 迁移到 {target_status}",
        )
        self.current_status = current_status
        self.target_status = target_status


class RunContinuationUnavailableError(BizException):
    """当前 Run 不满足继续执行前置条件。"""

    def __init__(self, run_id: str, reason: str) -> None:
        super().__init__(code=61004, message=f"运行 {run_id} 不可继续：{reason}")
        self.run_id = run_id
        self.reason = reason


class RunCancelUnavailableError(BizException):
    """当前 Run 不接受取消请求。"""

    def __init__(self, run_id: str, reason: str) -> None:
        super().__init__(code=61005, message=f"运行 {run_id} 不可取消：{reason}")
        self.run_id = run_id
        self.reason = reason


class RunLeaseConflictError(BizException):
    """worker 租约 owner 或有效期校验失败。"""

    def __init__(self, run_id: str, owner_id: str) -> None:
        super().__init__(code=61006, message=f"运行 {run_id} 租约冲突：{owner_id}")
        self.run_id = run_id
        self.owner_id = owner_id


class RunEventReplayExpiredError(BizException):
    """请求的事件游标早于事件保留窗口。"""

    def __init__(self, run_id: str, after_cursor: int | None) -> None:
        super().__init__(
            code=61007,
            message=f"运行 {run_id} 的事件历史已过期：after_cursor={after_cursor}",
        )
        self.run_id = run_id
        self.after_cursor = after_cursor


class RunPayloadValidationError(BizException):
    """Run 创建或继续请求载荷不符合领域约束。"""

    def __init__(self, reason: str) -> None:
        super().__init__(code=61008, message=f"运行请求无效：{reason}")
        self.reason = reason


class RunStoreUnavailableError(BizException):
    """Run 存储端口当前不可用。"""

    def __init__(self, operation: str, reason: str) -> None:
        super().__init__(code=61009, message=f"运行存储不可用：{operation} {reason}")
        self.operation = operation
        self.reason = reason


class RunIdempotencyConflictError(BizException):
    """同一幂等键对应的 payload 摘要发生冲突。"""

    def __init__(self, client_request_id: str) -> None:
        super().__init__(
            code=61010,
            message=f"运行幂等请求冲突：client_request_id={client_request_id}",
        )
        self.client_request_id = client_request_id


class RunCheckpointWriteError(BizException):
    """检查点或工具 pending 账本写入失败。"""

    def __init__(self, run_id: str, checkpoint_id: str, reason: str) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61011,
            message=(
                f"运行 {run_id} 检查点写入失败："
                f"checkpoint_id={checkpoint_id} reason={safe_reason}"
            ),
        )
        self.run_id = run_id
        self.checkpoint_id = checkpoint_id
        self.reason = reason


class RunCheckpointSchemaError(BizException):
    """检查点 schema 版本不兼容或无法反序列化。"""

    def __init__(
        self,
        run_id: str,
        checkpoint_id: str,
        schema_version: int,
        reason: str,
    ) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61012,
            message=(
                f"运行 {run_id} 检查点 schema 不兼容："
                f"checkpoint_id={checkpoint_id} "
                f"schema_version={schema_version} reason={safe_reason}"
            ),
        )
        self.run_id = run_id
        self.checkpoint_id = checkpoint_id
        self.schema_version = schema_version
        self.reason = reason


class RunRecoveryUnavailableError(BizException):
    """当前运行不满足自动恢复前置条件。"""

    def __init__(self, run_id: str, reason: str) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61013,
            message=f"运行 {run_id} 不可自动恢复：reason={safe_reason}",
        )
        self.run_id = run_id
        self.reason = reason


class RunToolReplayBlockedError(BizException):
    """工具结果账本不允许自动重放。"""

    def __init__(
        self,
        run_id: str,
        tool_name: str,
        tool_execution_key: str,
        reason: str,
    ) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61014,
            message=(
                f"运行 {run_id} 工具结果不可自动重放："
                f"tool_name={tool_name} "
                f"tool_execution_key={tool_execution_key} reason={safe_reason}"
            ),
        )
        self.run_id = run_id
        self.tool_name = tool_name
        self.tool_execution_key = tool_execution_key
        self.reason = reason


class RunCheckpointPayloadTooLargeError(BizException):
    """检查点载荷超过配置上限。"""

    def __init__(
        self,
        run_id: str,
        checkpoint_id: str,
        payload_size: int,
        max_payload_size: int,
    ) -> None:
        super().__init__(
            code=61015,
            message=(
                f"运行 {run_id} 检查点载荷过大："
                f"checkpoint_id={checkpoint_id} payload_size={payload_size} "
                f"max_payload_size={max_payload_size}"
            ),
        )
        self.run_id = run_id
        self.checkpoint_id = checkpoint_id
        self.payload_size = payload_size
        self.max_payload_size = max_payload_size


class RunCheckpointStoreUnavailableError(BizException):
    """检查点存储端口当前不可用。"""

    def __init__(self, operation: str, reason: str) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61016,
            message=f"检查点存储不可用：operation={operation} reason={safe_reason}",
        )
        self.operation = operation
        self.reason = reason


class RunUnknownWorkflowError(BizException):
    """显式指定未知或未启用 workflow。"""

    def __init__(self, workflow_name: str) -> None:
        safe_name = _safe_reason(workflow_name, max_length=80)
        super().__init__(
            code=61017,
            message=f"未知运行工作流：workflow_name={safe_name}",
        )
        self.workflow_name = workflow_name


class RunWorkflowDefinitionError(BizException):
    """工作流定义重复、缺少阶段或角色引用非法。"""

    def __init__(self, reason: str) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61018,
            message=f"运行工作流定义无效：reason={safe_reason}",
        )
        self.reason = reason


class RunCollaborationLimitExceededError(BizException):
    """协作限制命中时抛出或转换为失败结果。"""

    def __init__(self, run_id: str, reason: str) -> None:
        safe_reason = _safe_reason(reason)
        super().__init__(
            code=61019,
            message=f"运行 {run_id} 协作限制已命中：reason={safe_reason}",
        )
        self.run_id = run_id
        self.reason = reason
