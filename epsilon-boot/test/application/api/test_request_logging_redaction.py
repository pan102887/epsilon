"""请求日志脱敏策略测试。"""

from application.api.middlewares.request_logging import _safe_decode_body


def test_safe_decode_body_redacts_sensitive_json_fields() -> None:
    """日志输出不得暴露常见敏感字段值。"""

    raw = b'{"api_key":"sk-test-secret","password":"root123","message":"hello"}'

    decoded = _safe_decode_body(raw)

    assert "sk-test-secret" not in decoded
    assert "root123" not in decoded
    assert '"api_key":"***"' in decoded or '"api_key": "***"' in decoded
    assert '"password":"***"' in decoded or '"password": "***"' in decoded
    assert "hello" in decoded
