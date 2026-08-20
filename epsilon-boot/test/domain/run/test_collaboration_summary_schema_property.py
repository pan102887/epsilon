"""协作摘要 schema 属性测试。"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.run.workflow import canonicalize_collaboration_summary

_JSON_VALUE_ST = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=0, max_value=1000),
        st.text(max_size=30),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=4),
    ),
    max_leaves=12,
)

_STEP_ST = st.dictionaries(st.text(min_size=1, max_size=10), _JSON_VALUE_ST, max_size=4)
_STEPS_ST = st.lists(_STEP_ST, max_size=5)
_EXTRA_FIELDS_ST = st.dictionaries(
    st.text(min_size=1, max_size=12).filter(
        lambda key: key not in {"recent_steps", "latest_steps"}
    ),
    _JSON_VALUE_ST,
    max_size=4,
)


@settings(max_examples=120, deadline=5000)
@given(
    latest_steps=st.one_of(_STEPS_ST, _JSON_VALUE_ST),
    recent_steps=st.one_of(_STEPS_ST, _JSON_VALUE_ST),
    extra_fields=_EXTRA_FIELDS_ST,
)
def test_collaboration_summary_canonicalizes_to_latest_steps_only(
    latest_steps: object,
    recent_steps: object,
    extra_fields: dict[str, object],
) -> None:
    """任意 recent_steps/latest_steps 输入都必须归一为仅保留 latest_steps。"""

    payload = dict(extra_fields)
    payload["latest_steps"] = latest_steps
    payload["recent_steps"] = recent_steps

    canonical = canonicalize_collaboration_summary(payload)

    assert canonical is not None
    assert "recent_steps" not in canonical
    assert "latest_steps" in canonical
    if isinstance(latest_steps, list):
        assert canonical["latest_steps"] == latest_steps
    elif isinstance(recent_steps, list):
        assert canonical["latest_steps"] == recent_steps
    else:
        assert canonical["latest_steps"] == []
    for key, value in extra_fields.items():
        assert canonical[key] == value


def test_collaboration_summary_maps_legacy_recent_steps_when_latest_missing() -> None:
    """历史摘要仅含 recent_steps 时必须映射到 latest_steps。"""

    legacy = {
        "recent_steps": [{"run_id": "run-1", "action": "handoff"}],
        "handoff_count": 1,
    }

    canonical = canonicalize_collaboration_summary(legacy)

    assert canonical == {
        "latest_steps": [{"run_id": "run-1", "action": "handoff"}],
        "handoff_count": 1,
    }
