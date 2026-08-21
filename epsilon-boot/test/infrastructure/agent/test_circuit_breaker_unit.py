"""ToolCircuitBreaker 单元测试。

覆盖：
- CLOSED → 失败累积达阈值 → OPEN
- OPEN → 抛 ToolCircuitOpenError
- OPEN → 时钟推进达 recovery_timeout → HALF_OPEN
- HALF_OPEN 探测成功 → CLOSED + 计数清零
- HALF_OPEN 探测失败 → OPEN + 重新计时
- HALF_OPEN 并发探测：第 1 个放行、第 2 个拒绝
- 不计入异常类型正常通过
"""

import pytest

from domain.agent.exceptions import (
    ToolCircuitOpenError,
    ToolNotFoundError,
    ToolParameterValidationError,
)
from infrastructure.agent.circuit_breaker import ToolCircuitBreaker


class TestClosedToOpen:
    """CLOSED 状态下失败累积达阈值后转 OPEN。"""

    @pytest.mark.asyncio
    async def test_transitions_to_open_at_threshold(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=3, recovery_timeout=10.0, time_fn=lambda: clock[0]
        )
        for _ in range(3):
            with pytest.raises(RuntimeError):
                async with breaker.guard("tool_a"):
                    raise RuntimeError("fail")

        # 第 4 次应被拒绝（OPEN 状态）
        with pytest.raises(ToolCircuitOpenError):
            async with breaker.guard("tool_a"):
                pass  # pragma: no cover


class TestOpenState:
    """OPEN 状态行为。"""

    @pytest.mark.asyncio
    async def test_open_rejects_immediately(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=2, recovery_timeout=30.0, time_fn=lambda: clock[0]
        )
        # 2 次失败 → OPEN
        for _ in range(2):
            with pytest.raises(RuntimeError):
                async with breaker.guard("x"):
                    raise RuntimeError("fail")

        with pytest.raises(ToolCircuitOpenError):
            async with breaker.guard("x"):
                pass  # pragma: no cover


class TestOpenToHalfOpen:
    """OPEN → 时钟推进 → HALF_OPEN。"""

    @pytest.mark.asyncio
    async def test_recovery_timeout_triggers_half_open(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=2, recovery_timeout=10.0, time_fn=lambda: clock[0]
        )
        for _ in range(2):
            with pytest.raises(RuntimeError):
                async with breaker.guard("t"):
                    raise RuntimeError("fail")

        # 推进时钟
        clock[0] = 11.0

        # HALF_OPEN 放行一次探测
        async with breaker.guard("t"):
            pass  # 成功

        # 成功后应回到 CLOSED
        bs = breaker.state_for("t")
        assert bs.state == "CLOSED"
        assert bs.failure_count == 0


class TestHalfOpenSuccess:
    """HALF_OPEN 探测成功 → CLOSED。"""

    @pytest.mark.asyncio
    async def test_probe_success_resets(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=1, recovery_timeout=5.0, time_fn=lambda: clock[0]
        )
        with pytest.raises(RuntimeError):
            async with breaker.guard("s"):
                raise RuntimeError("fail")

        clock[0] = 6.0
        async with breaker.guard("s"):
            pass

        # CLOSED, counter reset
        bs = breaker.state_for("s")
        assert bs.state == "CLOSED"
        assert bs.failure_count == 0


class TestHalfOpenFailure:
    """HALF_OPEN 探测失败 → OPEN + 重新计时。"""

    @pytest.mark.asyncio
    async def test_probe_failure_reopens(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=1, recovery_timeout=5.0, time_fn=lambda: clock[0]
        )
        with pytest.raises(RuntimeError):
            async with breaker.guard("f"):
                raise RuntimeError("fail")

        clock[0] = 6.0
        with pytest.raises(RuntimeError):
            async with breaker.guard("f"):
                raise RuntimeError("probe fail")

        bs = breaker.state_for("f")
        assert bs.state == "OPEN"
        assert bs.opened_at == 6.0  # 重新计时


class TestHalfOpenConcurrency:
    """HALF_OPEN 并发探测：超出 max_calls 的请求被拒绝。"""

    @pytest.mark.asyncio
    async def test_second_probe_rejected(self):
        clock = [0.0]
        breaker = ToolCircuitBreaker(
            failure_threshold=1,
            recovery_timeout=5.0,
            half_open_max_calls=1,
            time_fn=lambda: clock[0],
        )
        with pytest.raises(RuntimeError):
            async with breaker.guard("c"):
                raise RuntimeError("fail")

        clock[0] = 6.0

        # 模拟并发：先进入第一个 guard
        gen = breaker.guard("c")
        await gen.__aenter__()

        # 第二个探测应被拒绝
        with pytest.raises(ToolCircuitOpenError):
            async with breaker.guard("c"):
                pass  # pragma: no cover

        # 完成第一个探测
        await gen.__aexit__(None, None, None)


class TestNonFailureExceptions:
    """不计入异常类型（ToolNotFound / ParamValidation）正常通过不累积计数。"""

    @pytest.mark.asyncio
    async def test_tool_not_found_not_counted(self):
        breaker = ToolCircuitBreaker(failure_threshold=2, recovery_timeout=10.0)

        for _ in range(5):
            with pytest.raises(ToolNotFoundError):
                async with breaker.guard("n"):
                    raise ToolNotFoundError(tool_name="n")

        # 仍是 CLOSED（未累积失败）
        bs = breaker.state_for("n")
        assert bs.state == "CLOSED"
        assert bs.failure_count == 0

    @pytest.mark.asyncio
    async def test_param_validation_not_counted(self):
        breaker = ToolCircuitBreaker(failure_threshold=2, recovery_timeout=10.0)

        for _ in range(5):
            with pytest.raises(ToolParameterValidationError):
                async with breaker.guard("p"):
                    raise ToolParameterValidationError(tool_name="p", errors=["bad"])

        bs = breaker.state_for("p")
        assert bs.state == "CLOSED"
