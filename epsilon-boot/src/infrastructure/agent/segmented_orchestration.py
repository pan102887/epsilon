"""分段执行编排决策向后兼容垫片。

判定逻辑已平移至 domain/agent/segmented_orchestration.py（ADR-0015），本模块
保留为向后兼容垫片，re-export 领域实现，保护既有 import 路径与测试引用（参照
ADR-0011/0014 垫片范式）；后续片可按 change-discipline 删除本垫片并改所有引用点。
此处 re-export 的 SegmentContinuationDecision 与领域模块为同一类对象、
decide_next_segment 为同一函数对象，isinstance/== 语义不破裂。
"""

from __future__ import annotations

from domain.agent.segmented_orchestration import (
    SegmentContinuationDecision,
    decide_next_segment,
)

__all__ = ["SegmentContinuationDecision", "decide_next_segment"]
