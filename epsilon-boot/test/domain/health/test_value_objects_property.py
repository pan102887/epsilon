"""健康检查值对象属性测试。

使用 Hypothesis 对 HealthCheckResult 和 ReadinessResult 的序列化行为
进行属性测试，验证序列化输出始终满足正确性属性。
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.health.value_objects import (
    HealthCheckResult,
    HealthStatus,
    ReadinessResult,
)
from infrastructure.health.health_serialization import (
    health_check_result_to_dict,
    readiness_result_to_dict,
)

# ── Hypothesis 生成策略 ──

health_status_st = st.sampled_from(HealthStatus)

name_st = st.text(min_size=1, max_size=50).filter(lambda s: s.strip() != "")

reason_st = st.one_of(st.none(), st.text(min_size=1, max_size=200))

health_check_result_st = st.builds(
    HealthCheckResult,
    name=name_st,
    status=health_status_st,
    reason=reason_st,
)

readiness_result_st = st.builds(
    ReadinessResult,
    status=health_status_st,
    checks=st.lists(health_check_result_st, min_size=0, max_size=10).map(tuple),
)


# ── Property 3: HealthCheckResult 序列化包含必要字段 ──
# Feature: readiness-probe, Property 3: HealthCheckResult 序列化包含必要字段


@settings(max_examples=100)
@given(result=health_check_result_st)
def test_health_check_result_to_dict_always_contains_status(
    result: HealthCheckResult,
) -> None:
    """验证 HealthCheckResult 序列化后始终包含 "status" 键。

    对于任意随机生成的 HealthCheckResult，序列化后的字典必须包含
    "status" 键，且其值等于原始 status 枚举的字符串值。
    """
    d = health_check_result_to_dict(result)
    assert "status" in d
    assert d["status"] == result.status.value


@settings(max_examples=100)
@given(result=health_check_result_st)
def test_health_check_result_to_dict_reason_presence(
    result: HealthCheckResult,
) -> None:
    """验证 HealthCheckResult 序列化中 reason 字段的存在性规则。

    当 reason 不为 None 时，序列化字典必须包含 "reason" 键；
    当 reason 为 None 时，序列化字典不得包含 "reason" 键。
    """
    d = health_check_result_to_dict(result)
    if result.reason is not None:
        assert "reason" in d
        assert d["reason"] == result.reason
    else:
        assert "reason" not in d


# ── Property 4: ReadinessResult 序列化往返一致性 ──
# Feature: readiness-probe, Property 4: ReadinessResult 序列化往返一致性


@settings(max_examples=100)
@given(result=readiness_result_st)
def test_readiness_result_to_dict_status_matches(
    result: ReadinessResult,
) -> None:
    """验证 ReadinessResult 序列化的 status 字段与原始对象一致。

    对于任意随机生成的 ReadinessResult，序列化后字典的 "status"
    字段应等于原始对象 status 枚举的字符串值。
    """
    d = readiness_result_to_dict(result)
    assert "status" in d
    assert d["status"] == result.status.value


@settings(max_examples=100)
@given(result=readiness_result_st)
def test_readiness_result_to_dict_checks_keys_match_names(
    result: ReadinessResult,
) -> None:
    """验证 ReadinessResult 序列化的 checks 键集合与检查项名称集合一致。

    序列化后字典的 "checks" 字段的键集合应等于所有检查结果的
    name 集合，确保每个检查项都被正确映射。
    """
    d = readiness_result_to_dict(result)
    assert "checks" in d
    expected_names = {check.name for check in result.checks}
    assert set(d["checks"].keys()) == expected_names
