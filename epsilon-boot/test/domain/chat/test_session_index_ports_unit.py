"""会话索引端口静态契约测试模块。"""

import inspect

from domain.chat.ports import SessionContextStorePort, SessionIndexPort


def _annotation_text(annotation: object) -> str:
    """把签名注解转为稳定可断言的文本。"""
    return str(annotation).replace('"', "").replace("'", "")


def test_session_context_store_exists_signature() -> None:
    """验证 SessionContextStorePort.exists(...) 参数与返回注解。"""
    signature = inspect.signature(SessionContextStorePort.exists)

    assert list(signature.parameters) == ["self", "session_id"]
    assert _annotation_text(signature.parameters["session_id"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "bool"


def test_session_index_port_upsert_signature() -> None:
    """验证 SessionIndexPort.upsert(...) 参数与返回注解。"""
    signature = inspect.signature(SessionIndexPort.upsert)

    assert list(signature.parameters) == ["self", "metadata"]
    assert _annotation_text(signature.parameters["metadata"].annotation) == "SessionMetadata"
    assert _annotation_text(signature.return_annotation) == "None"


def test_session_index_port_get_signature() -> None:
    """验证 SessionIndexPort.get(...) 参数与返回注解。"""
    signature = inspect.signature(SessionIndexPort.get)

    assert list(signature.parameters) == ["self", "session_id"]
    assert _annotation_text(signature.parameters["session_id"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "SessionMetadata | None"


def test_session_index_port_list_recent_signature() -> None:
    """验证 SessionIndexPort.list_recent(...) 参数与返回注解。"""
    signature = inspect.signature(SessionIndexPort.list_recent)

    assert list(signature.parameters) == ["self", "limit"]
    assert _annotation_text(signature.parameters["limit"].annotation) == "int"
    assert signature.parameters["limit"].default == 20
    assert _annotation_text(signature.return_annotation) == "list[SessionMetadata]"


def test_session_index_port_delete_signature() -> None:
    """验证 SessionIndexPort.delete(...) 参数与返回注解。"""
    signature = inspect.signature(SessionIndexPort.delete)

    assert list(signature.parameters) == ["self", "session_id"]
    assert _annotation_text(signature.parameters["session_id"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "None"
