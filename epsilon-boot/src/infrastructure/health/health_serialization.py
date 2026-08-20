"""健康检查领域值对象的基础设施层序列化映射器。

抽取 ``domain/health/value_objects.py`` 中 ``HealthCheckResult`` /
``ReadinessResult`` 原有 ``to_dict`` 的线格式产出逻辑为基础设施层模块级
独立函数，供健康探针 HTTP 响应构建复用。

本模块属基础设施层内部 helper，依赖方向为 infrastructure→domain：仅从
``domain.health`` 导入值对象类型，不向 ``domain/`` 反向暴露任何符号，
遵循 `docs/steering/ddd-architecture.md` 的分层依赖约束。
"""

from __future__ import annotations

from domain.health.value_objects import HealthCheckResult, ReadinessResult


def health_check_result_to_dict(value: HealthCheckResult) -> dict[str, object]:
    """将单个依赖的健康检查结果序列化为字典。

    返回的字典始终包含 ``"status"`` 键（取自 ``status.value``）；仅当
    ``reason`` 不为 ``None`` 时才追加 ``"reason"`` 键，与领域侧原
    ``HealthCheckResult.to_dict`` 的线格式逐字段等价。

    Args:
        value: 单个依赖的健康检查结果值对象。

    Returns:
        含 ``status`` 及可选 ``reason`` 的字典。
    """
    result: dict[str, object] = {"status": value.status.value}
    if value.reason is not None:
        result["reason"] = value.reason
    return result


def readiness_result_to_dict(value: ReadinessResult) -> dict[str, object]:
    """将就绪探针聚合结果序列化为 HTTP 响应体格式。

    返回格式为 ``{"status": "UP/DOWN", "checks": {<依赖名>: {...}}}``，其中
    各逐项检查结果复用本模块 ``health_check_result_to_dict``（替代领域侧原
    ``check.to_dict()``），保持线格式与领域旧实现字面等价。

    Args:
        value: 就绪探针聚合结果值对象。

    Returns:
        符合就绪探针响应规范的字典。
    """
    return {
        "status": value.status.value,
        "checks": {
            check.name: health_check_result_to_dict(check)
            for check in value.checks
        },
    }
