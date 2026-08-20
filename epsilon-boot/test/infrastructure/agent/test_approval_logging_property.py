"""审批日志脱敏属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from infrastructure.agent.approval_logging import redact_approval_value


def _case_variants(word: str) -> st.SearchStrategy[str]:
    """生成简单大小写变体。"""
    return st.sampled_from([word, word.upper(), word.title()])


@settings(max_examples=100, deadline=5000)
@given(
    key=st.one_of(
        _case_variants("api_key"),
        _case_variants("password"),
        _case_variants("secret"),
        _case_variants("token"),
        _case_variants("authorization"),
    ),
    secret_value=st.text(alphabet="0123456789", min_size=4, max_size=40),
)
def test_sensitive_dict_values_are_redacted(key: str, secret_value: str) -> None:
    """验证大小写变体敏感键的原始值不会出现在输出中。"""
    output = redact_approval_value({"nested": [{key: secret_value}]})

    assert secret_value not in output
    assert "***" in output
