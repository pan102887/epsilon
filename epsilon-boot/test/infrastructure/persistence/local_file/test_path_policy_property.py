"""``CrossPlatformPathPolicy`` property-based 测试。

覆盖需求 10.2 与正确性属性 Property 7：**路径策略必须拒绝所有 Windows
非法输入**——即 Windows 保留名（CON / PRN / AUX / NUL / COM1-9 / LPT1-9）
与非法字符（``\x00 / \\ : * ? " < > |``）。

同时反向锁死：经过 ``hash_session_id`` 哈希后的 ``bucket`` / ``stem``（纯
十六进制小写字符）必然通过 ``check_dirname`` 校验（不抛），这是会话文件
命名方案（``Session_File_Name_Scheme``）能天然规避 Windows 保留名与非法
字符冲突的核心不变量。

Hypothesis 策略做了 bounded 约束（``max_size`` ≤ 32），以免 CI 时间被
Hypothesis 爆搜放大。
"""

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from infrastructure.persistence.local_file.path_policy import (
    CrossPlatformPathPolicy,
    PathPolicyViolation,
)

# ── Hypothesis 策略 ──


# 需求 4.3：Windows 保留名集合（大小写无关）。
_RESERVED_NAMES: list[str] = (
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


# 保留名策略：随机大小写 + 可选后缀扩展名。
def _randomize_case(word: str, toggles: tuple[bool, ...]) -> str:
    """按 toggles 对 word 的每个字符做大小写翻转。"""
    out = []
    for ch, flip in zip(word, toggles[: len(word)], strict=True):
        out.append(ch.upper() if flip else ch.lower())
    return "".join(out)


reserved_name_st = st.builds(
    _randomize_case,
    st.sampled_from(_RESERVED_NAMES),
    st.tuples(st.booleans(), st.booleans(), st.booleans(), st.booleans()),
)


# 带扩展名的保留名："CON.txt" / "NUL.json" 等——需求 4.3 要求前缀命中即拒绝。
reserved_name_with_ext_st = st.builds(
    lambda base, ext: f"{base}.{ext}",
    reserved_name_st,
    st.sampled_from(["txt", "json", "log", "dat", "cfg"]),
)


# 非法字符串策略：至少包含一个 Windows 非法字符；其它位置为任意非非法字符。
_ILLEGAL_CHAR_ALPHABET = '\x00/\\:*?"<>|'


illegal_char_name_st = st.text(alphabet=_ILLEGAL_CHAR_ALPHABET, min_size=1, max_size=8)


# 混合策略：在合法前缀后插入至少一个非法字符。
mixed_illegal_name_st = st.builds(
    lambda prefix, illegal_char, suffix: f"{prefix}{illegal_char}{suffix}",
    st.text(alphabet="abcdefgh0123456789-_", min_size=0, max_size=8),
    st.sampled_from(list(_ILLEGAL_CHAR_ALPHABET)),
    st.text(alphabet="abcdefgh0123456789-_", min_size=0, max_size=8),
)


# 任意 session_id 策略（给 hash_session_id 做反向断言用）。
session_id_st = st.text(min_size=1, max_size=64)


# ── Property: 保留名一律被 check_dirname 拒绝 ──


@settings(max_examples=80, deadline=None)
@given(name=reserved_name_st)
def test_check_dirname_rejects_windows_reserved_names(name: str):
    """对任意 Windows 保留名（任意大小写）``check_dirname`` 必抛。"""
    policy = CrossPlatformPathPolicy()
    with pytest.raises(PathPolicyViolation):
        policy.check_dirname(name)


@settings(max_examples=80, deadline=None)
@given(name=reserved_name_with_ext_st)
def test_check_dirname_rejects_reserved_names_with_extension(name: str):
    """``CON.txt`` / ``nul.json`` 等带扩展名的保留名前缀同样被拒。

    需求 4.3：``name.split(".", 1)[0].upper()`` 命中保留名集合即拒。
    """
    policy = CrossPlatformPathPolicy()
    with pytest.raises(PathPolicyViolation):
        policy.check_dirname(name)


# ── Property: 非法字符一律被 check_dirname 拒绝 ──


@settings(max_examples=80, deadline=None)
@given(name=illegal_char_name_st)
def test_check_dirname_rejects_pure_illegal_chars(name: str):
    """纯非法字符拼接成的名字必抛。

    策略 alphabet 限定在 ``\x00 / \\ : * ? " < > |`` 集合中，按需求 4.2
    必须被拒（pytest 自身生成的字符串不可能命中保留名集合，因保留名均为
    英文字母）。
    """
    policy = CrossPlatformPathPolicy()
    with pytest.raises(PathPolicyViolation):
        policy.check_dirname(name)


@settings(max_examples=80, deadline=None)
@given(name=mixed_illegal_name_st)
def test_check_dirname_rejects_names_containing_illegal_char(name: str):
    """合法前缀 + 非法字符 + 合法后缀的组合也必抛。"""
    policy = CrossPlatformPathPolicy()
    with pytest.raises(PathPolicyViolation):
        policy.check_dirname(name)


# ── Property: hash_session_id 产出的名称一定通过 check_dirname ──


@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(session_id=session_id_st)
def test_hashed_bucket_and_stem_pass_check_dirname(session_id: str):
    """``hash_session_id`` 的输出（bucket/stem）必然通过 ``check_dirname``。

    这是 ``Session_File_Name_Scheme`` 的核心不变量：sha256 十六进制小写
    串（共 64 位，仅含 ``0-9a-f``）不可能命中 Windows 保留名集合（保留名
    含字母 ``P/R/N/C/O/A/U/L/M/T``，以及数字 ``1-9``——其中 ``C/A/F/B/D/E``
    虽在十六进制字母表中，但保留名中含 ``P/R/N/O/U/L/M/T`` 这些非十六进制
    字母，所以 sha256 的纯十六进制串不可能整体命中任何保留名）；也不包含
    任何 Windows 非法字符。

    正确性属性 Property 7 依赖此不变量：哈希后的文件名天然安全。
    """
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id(session_id)
    # 不抛即通过
    policy.check_dirname(bucket)
    policy.check_dirname(stem)
    policy.check_dirname(f"{stem}.json")


@settings(max_examples=80, deadline=None)
@given(session_id=session_id_st)
def test_hash_session_id_output_is_pure_hex_lowercase(session_id: str):
    """``hash_session_id`` 输出必为纯十六进制小写字符（长度 2 + 62）。"""
    policy = CrossPlatformPathPolicy()
    bucket, stem = policy.hash_session_id(session_id)
    assert len(bucket) == 2
    assert len(stem) == 62
    allowed = set("0123456789abcdef")
    assert set(bucket).issubset(allowed)
    assert set(stem).issubset(allowed)


# ── Property: 保留名与非法字符集合在策略层对称 ──


@settings(max_examples=40, deadline=None)
@given(case_toggles=st.tuples(*[st.booleans()] * 4))
def test_all_reserved_names_rejected_case_insensitive(case_toggles: tuple[bool, ...]):
    """遍历所有保留名，任意大小写组合均必抛（反向断言：不漏判）。"""
    policy = CrossPlatformPathPolicy()
    for base in _RESERVED_NAMES:
        name = _randomize_case(base, case_toggles)
        with pytest.raises(PathPolicyViolation):
            policy.check_dirname(name)
