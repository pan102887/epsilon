"""``CrossPlatformPathPolicy`` 单元测试。

覆盖需求 4.1-4.6 与正确性属性 Property 7：路径策略对 Windows 非法输入
的拒绝、``hash_session_id`` 的稳定性、``ensure_within_root`` 的越权阻止。
"""

from pathlib import Path

import pytest

from infrastructure.persistence.local_file.path_policy import (
    CrossPlatformPathPolicy,
    PathPolicyViolation,
)


@pytest.fixture
def policy() -> CrossPlatformPathPolicy:
    """返回一个新的 ``CrossPlatformPathPolicy`` 实例（纯函数式，可共享）。"""
    return CrossPlatformPathPolicy()


# ── hash_session_id ──


def test_hash_session_id_returns_bucket_and_stem(policy: CrossPlatformPathPolicy):
    """``hash_session_id`` 必须返回 2 位 bucket + 62 位 stem 的十六进制串。"""
    bucket, stem = policy.hash_session_id("session-abc-123")
    assert len(bucket) == 2
    assert len(stem) == 62
    assert all(c in "0123456789abcdef" for c in bucket + stem)


def test_hash_session_id_is_stable(policy: CrossPlatformPathPolicy):
    """相同输入必然得到相同输出（纯函数）。"""
    r1 = policy.hash_session_id("同一个会话ID")
    r2 = policy.hash_session_id("同一个会话ID")
    assert r1 == r2


def test_hash_session_id_differs_for_different_inputs(policy: CrossPlatformPathPolicy):
    """不同输入应得到不同哈希（天文数字概率冲突视为不可能）。"""
    r1 = policy.hash_session_id("a")
    r2 = policy.hash_session_id("b")
    assert r1 != r2


def test_hash_session_id_accepts_unicode(policy: CrossPlatformPathPolicy):
    """含 Unicode、NUL、Windows 保留字符的 session_id 也应可哈希。"""
    bucket, stem = policy.hash_session_id("CON\x00*会话")
    # 输出仍是十六进制，天然规避非法字符
    assert all(c in "0123456789abcdef" for c in bucket + stem)


# ── check_dirname ──


@pytest.mark.parametrize("reserved", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "con", "Com3"])
def test_check_dirname_rejects_windows_reserved_names(
    policy: CrossPlatformPathPolicy, reserved: str
):
    """Windows 保留名（大小写无关）必须被拒绝。"""
    with pytest.raises(PathPolicyViolation, match="Windows 保留名"):
        policy.check_dirname(reserved)


@pytest.mark.parametrize("name", ["CON.txt", "nul.json", "COM1.log"])
def test_check_dirname_rejects_reserved_with_extension(policy: CrossPlatformPathPolicy, name: str):
    """带扩展名但前缀是保留名（如 ``CON.txt``）仍需拒绝。"""
    with pytest.raises(PathPolicyViolation, match="Windows 保留名"):
        policy.check_dirname(name)


@pytest.mark.parametrize(
    "name",
    ["a:b.json", "a*b", "a?b", "a<b", "a>b", 'a"b', "a|b", "a\x00b", "a/b", "a\\b"],
)
def test_check_dirname_rejects_illegal_chars(policy: CrossPlatformPathPolicy, name: str):
    """含 NUL / 路径分隔符 / Windows 非法字符必须被拒绝。"""
    with pytest.raises(PathPolicyViolation, match="非法字符"):
        policy.check_dirname(name)


def test_check_dirname_accepts_hex_stem(policy: CrossPlatformPathPolicy):
    """``hash_session_id`` 的输出作为单段 name 必须被接受。"""
    bucket, stem = policy.hash_session_id("abc")
    policy.check_dirname(bucket)
    policy.check_dirname(f"{stem}.json")


# ── check_absolute_path_length ──


def test_check_absolute_path_length_pass_on_posix(
    policy: CrossPlatformPathPolicy, monkeypatch: pytest.MonkeyPatch
):
    """POSIX 平台不触发长度检查。

    只 patch ``path_policy`` 模块内对 ``os`` 的引用，避免影响 ``pathlib``
    的全局 ``os.name`` 判定（否则会导致 pytest 内部 ``WindowsPath`` 实例化
    崩溃）。
    """
    import infrastructure.persistence.local_file.path_policy as pp_module

    class _FakeOs:
        name = "posix"

    monkeypatch.setattr(pp_module, "os", _FakeOs)
    # 长度超过 260，也不该抛
    long_path = Path("/tmp/" + "a" * 500)
    policy.check_absolute_path_length(long_path)


def test_check_absolute_path_length_rejects_too_long_on_windows(
    policy: CrossPlatformPathPolicy, monkeypatch: pytest.MonkeyPatch
):
    """Windows 平台 >260 必须拒绝。"""
    import infrastructure.persistence.local_file.path_policy as pp_module

    class _FakeOs:
        name = "nt"

    monkeypatch.setattr(pp_module, "os", _FakeOs)
    long_path = Path("/tmp/" + "a" * 300)
    with pytest.raises(PathPolicyViolation, match="路径过长"):
        policy.check_absolute_path_length(long_path)


def test_check_absolute_path_length_accepts_at_limit_on_windows(
    policy: CrossPlatformPathPolicy, monkeypatch: pytest.MonkeyPatch
):
    """260 字符恰好等于上限不应拒绝（严格大于才抛）。"""
    import infrastructure.persistence.local_file.path_policy as pp_module

    class _FakeOs:
        name = "nt"

    monkeypatch.setattr(pp_module, "os", _FakeOs)
    # 构造一个长度正好等于 260 的路径
    path = Path("/" + "a" * (260 - 1))
    assert len(str(path)) == 260
    policy.check_absolute_path_length(path)


# ── ensure_within_root ──


def test_ensure_within_root_accepts_child(policy: CrossPlatformPathPolicy, tmp_path: Path):
    """正常子路径应被接受并返回规范化后的绝对路径。"""
    child = tmp_path / "sessions" / "ab" / "cd.json"
    resolved = policy.ensure_within_root(tmp_path, child)
    assert resolved.is_absolute()
    assert resolved == child.resolve()


def test_ensure_within_root_rejects_traversal(policy: CrossPlatformPathPolicy, tmp_path: Path):
    """``..`` 逃逸必须被拒绝。"""
    candidate = Path("../../etc/passwd")
    with pytest.raises(PathPolicyViolation, match="越出 LOCAL_PERSISTENCE_ROOT"):
        policy.ensure_within_root(tmp_path, candidate)
