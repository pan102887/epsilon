"""FastAPI API adapter layout compatibility tests."""

from __future__ import annotations


def test_new_and_legacy_app_imports_resolve_same_instance() -> None:
    """新旧 FastAPI app 导入路径应指向同一个应用实例。"""
    from application.api.server_app import app as api_app
    from application.server_app import app as legacy_app

    assert legacy_app is api_app


def test_legacy_router_imports_reexport_new_router_instances() -> None:
    """旧 routers 包应只兼容转发到 application.api.routers。"""
    from application.api.routers import chat_router as api_chat_router
    from application.api.routers import health_router as api_health_router
    from application.routers import chat_router as legacy_chat_router
    from application.routers import health_router as legacy_health_router

    assert legacy_chat_router is api_chat_router
    assert legacy_health_router is api_health_router


def test_legacy_middleware_import_reexports_new_middleware_class() -> None:
    """旧 middlewares 包应兼容转发到 application.api.middlewares。"""
    from application.api.middlewares import RequestLoggingMiddleware as api_middleware
    from application.middlewares import RequestLoggingMiddleware as legacy_middleware

    assert legacy_middleware is api_middleware
