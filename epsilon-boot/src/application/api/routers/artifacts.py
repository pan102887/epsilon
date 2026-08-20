"""任务产物查询路由模块。

暴露 ``ArtifactStorePort`` 的读取侧能力，供 Web 控制台按 session 查询
由工具或任务记录的产物摘要。写入侧仍由运行时各入口通过同一 Port 完成。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from common.container import inject
from domain.agent.ports import ArtifactStorePort

router = APIRouter(tags=["artifacts"])
ARTIFACT_STORE_DEPENDENCY = Depends(inject(ArtifactStorePort))


def _artifact_to_dict(artifact: object) -> dict[str, Any]:
    """将 ArtifactTrace 转为 JSON 响应字典。"""

    return asdict(artifact)  # type: ignore[arg-type]


@router.get("/api/artifacts/{session_id}")
async def list_artifacts(
    session_id: str,
    artifact_store: ArtifactStorePort | None = ARTIFACT_STORE_DEPENDENCY,
) -> JSONResponse:
    """列出指定 session 已记录的 artifact 摘要。

    Artifact 存储关闭时返回空列表，保持与 trace 列表接口的降级语义一致。
    """

    if artifact_store is None:
        return JSONResponse(content={"object": "list", "data": []})

    artifacts = await artifact_store.list_artifacts(session_id)
    return JSONResponse(
        content={
            "object": "list",
            "session_id": session_id,
            "data": [_artifact_to_dict(item) for item in artifacts],
        }
    )
