"""路由模块集合"""

from .artifacts import router as artifacts_router
from .chat import router as chat_router
from .health import router as health_router
from .models import router as models_router
from .runs import router as runs_router
from .task import router as task_router
from .test_router import router as test_router
from .traces import router as traces_router

__all__ = [
    "artifacts_router",
    "chat_router",
    "health_router",
    "models_router",
    "runs_router",
    "task_router",
    "test_router",
    "traces_router",
]
