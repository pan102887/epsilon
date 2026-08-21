"""静态 Agent guardrail 策略向后兼容垫片。

判定逻辑已上提至 ``domain/agent/guardrail_policy.py``（ADR-0014），本模块
保留为向后兼容临时垫片，re-export 领域实现，保护既有 import 路径与测试引用
（参照 ADR-0011 round_outcome 垫片范式）；后续片可按 change-discipline 删除
本垫片并改所有引用点。此处 re-export 的 ``StaticAgentGuardrailPolicy`` 与领域
模块为同一类对象，isinstance/== 语义不破裂。
"""

from __future__ import annotations

from domain.agent.guardrail_policy import (
    StaticAgentGuardrailPolicy,
)
from domain.agent.guardrail_policy import (
    looks_batch as _looks_batch,
)
from domain.agent.guardrail_policy import (
    segment_count as _segment_count,
)

__all__ = ["StaticAgentGuardrailPolicy", "_looks_batch", "_segment_count"]
