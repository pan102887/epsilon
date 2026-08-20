"""健康检查相关接口。

提供存活探针（Liveness Probe）和就绪探针（Readiness Probe）的 HTTP 端点，
以及 Prometheus 指标暴露接口。

- ``GET /health.json``：存活探针，始终返回 ``{"status": "UP"}``，不检查外部依赖。
- ``GET /readiness``：就绪探针，通过 ReadinessAggregator 检查所有外部依赖的连通性。
- ``GET /prometheus``：Prometheus 指标暴露接口。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from application.api.presenters.health_presenter import readiness_result_to_response_body
from common.container import inject
from domain.health.aggregator import ReadinessAggregator
from domain.health.value_objects import HealthStatus

router = APIRouter(tags=["health"])
READINESS_AGGREGATOR_DEPENDENCY = Depends(inject(ReadinessAggregator))


@router.get("/health.json")
async def health_check() -> dict[str, str]:
    """Logan 平台健康检查接口（存活探针）。

    始终返回 ``{"status": "UP"}``，不依赖任何外部服务的可用性。
    """
    return {"status": "UP"}


@router.get("/readiness")
async def readiness_check(
    aggregator: ReadinessAggregator = READINESS_AGGREGATOR_DEPENDENCY,
) -> JSONResponse:
    """就绪探针接口。

    通过 ReadinessAggregator 检查所有外部依赖的连通性，
    返回整体就绪状态和逐项检查结果。

    - 全部 UP → HTTP 200，``{"status": "UP", "checks": {...}}``
    - 任一 DOWN → HTTP 503，``{"status": "DOWN", "checks": {...}}``

    Args:
        aggregator: 由 DI 容器注入的就绪状态聚合器

    Returns:
        包含就绪状态和逐项检查结果的 JSON 响应
    """
    result = await aggregator.check_readiness()
    status_code = 200 if result.status == HealthStatus.UP else 503
    return JSONResponse(
        content=readiness_result_to_response_body(result),
        status_code=status_code,
    )


@router.get("/prometheus")
async def prometheus_metrics() -> Response:
    """Prometheus 指标暴露接口。"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
