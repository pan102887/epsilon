"""Rubric 一致性自测。

覆盖点：
- `load_rubric()` 返回 7 个维度；
- 权重之和与 1.0 的偏差 ≤ 1e-9；
- 每个维度 5 级齐全（score 1..5）；
- 每个维度跨级去重后框架数 ≥ 2（对齐需求 4.1）。

对应 Property：Property 5、Property 6；对应需求：2.2、4.1。
"""

from __future__ import annotations

import pytest

from tests.evaluation.errors import RubricConsistencyError
from tests.evaluation.rubric import DimensionId, load_rubric


def test_rubric_has_seven_dimensions() -> None:
    """Rubric 必须恰好包含 7 个维度，且与 DimensionId 一一对应。"""

    rubrics = load_rubric()
    assert len(rubrics) == 7
    ids = {r.id for r in rubrics}
    assert ids == set(DimensionId)


def test_rubric_weights_sum_to_one() -> None:
    """全部维度权重之和必须等于 1.0（允许浮点误差 ≤ 1e-9）。"""

    rubrics = load_rubric()
    weight_sum = sum(r.weight for r in rubrics)
    assert abs(weight_sum - 1.0) <= 1e-9, (
        f"权重和 {weight_sum!r} 与 1.0 偏差超阈"
    )


def test_rubric_levels_complete() -> None:
    """每个维度必须有 5 级，score 按 1..5 升序。"""

    rubrics = load_rubric()
    for r in rubrics:
        assert len(r.levels) == 5, r.id
        assert [lvl.score for lvl in r.levels] == [1, 2, 3, 4, 5], r.id


def test_every_level_has_at_least_two_citations() -> None:
    """每一级 citations 长度必须 ≥ 2，对齐需求 4.1。"""

    rubrics = load_rubric()
    for r in rubrics:
        for lvl in r.levels:
            assert len(lvl.citations) >= 2, (r.id, lvl.score)


def test_each_dimension_covers_two_distinct_frameworks() -> None:
    """每个维度跨 5 级去重后框架数 ≥ 2，对齐需求 4.1 的"至少 2 个不同框架"约束。"""

    rubrics = load_rubric()
    for r in rubrics:
        frameworks = {c.framework for lvl in r.levels for c in lvl.citations}
        assert len(frameworks) >= 2, (r.id, frameworks)


def test_weight_snapshot_matches_design() -> None:
    """权重快照与 design.md 硬约束完全一致，防止未来被静默改写。"""

    expected = {
        DimensionId.ARCHITECTURE: 0.18,
        DimensionId.AGENT_CORE: 0.22,
        DimensionId.MODEL_PROMPT: 0.14,
        DimensionId.SECURITY: 0.16,
        DimensionId.RELIABILITY: 0.12,
        DimensionId.TESTABILITY: 0.10,
        DimensionId.FRONTEND_UX: 0.08,
    }
    actual = {r.id: r.weight for r in load_rubric()}
    assert actual == expected


def test_rubric_consistency_error_is_subclass_of_evaluation_error() -> None:
    """RubricConsistencyError 必须属于 EvaluationError 家族，便于统一 except。"""

    from tests.evaluation.errors import EvaluationError

    assert issubclass(RubricConsistencyError, EvaluationError)
