"""模型列表查询路由模块。

提供 OpenAI 兼容的 ``/v1/models`` 接口，查询注册中心中所有可用模型列表。
响应格式遵循 OpenAI API 规范，便于与现有客户端工具集成。

端点列表：
- ``GET /v1/models``：查询所有可用模型列表。
"""

import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from common.container import inject
from domain.model_access.ports import ModelRegistryPort

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])
MODEL_REGISTRY_DEPENDENCY = Depends(inject(ModelRegistryPort))


@router.get("/v1/models")
async def list_models(
    registry: ModelRegistryPort = MODEL_REGISTRY_DEPENDENCY,
) -> JSONResponse:
    """查询所有可用模型列表。

    返回格式兼容 OpenAI ``/v1/models`` API 规范::

        {
            "object": "list",
            "data": [
                {
                    "id": "glm-4",
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "cliproxy",
                    "providers": ["cliproxy"]
                },
                ...
            ]
        }

    ``providers`` 字段为扩展字段，列出提供该模型的所有提供商名称。

    Args:
        registry: 模型注册中心实例，由 DI 容器注入。

    Returns:
        包含可用模型列表的 JSON 响应。
    """
    models = registry.list_models()
    created = int(time.time())

    data = [
        {
            "id": model.id,
            "object": model.object,
            "created": created,
            "owned_by": model.owned_by,
            "providers": sorted(model.providers),
        }
        for model in models
    ]

    return JSONResponse(content={"object": "list", "data": data})
