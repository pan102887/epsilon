"""Windows 平台特性单元测试。

覆盖需求 4.3、4.4、4.5 与正确性属性 Property 7 在 Windows 侧的落地：

- **Windows 保留名拒绝**：``CON`` / ``PRN`` / ``AUX`` / ``NUL`` /
  ``COM1``-``COM9`` / ``LPT1``-``LPT9``，以及带扩展名的形如 ``CON.txt``；
- **Windows 非法字符拒绝**：``< > : " / \\ | ? *`` + NUL；
- **260 字符绝对路径长度上限**：Windows 下无长路径支持时拒绝。

策略：

- **保留名与非法字符**的拒绝逻辑是纯字符串判定，**不依赖当前运行平台**
  （``check_dirname`` 在 Linux 上也会判同样的集合），因此这部分用例**在
  所有平台上均运行**，覆盖率不因 runner 类型缩水；
- **260 字符长度上限**是 ``os.name == "nt"`` 专属分支，通过 ``monkeypatch``
  把 ``path_policy`` 模块级 ``os`` 引用替换为一个 ``name == "nt"`` 的伪
  对象，在 Linux runner 下也能驱动 Windows 分支；该手法与既有
  ``test_path_policy_unit.py`` 保持一致。
- 额外的 ``os.name != "nt"`` gate 在少数用例上保留（``@pytest.mark.skipif``）
  用于标记"仅在真实 Windows 上才语义完整"的断言。

需求：4.3、4.4、4.5；正确性属性：Property 7。
"""

import os
import sys
from pathlib import Path

import pytest

import infrastructure.persistence.local_file.path_policy as pp_module
from infrastructure.persistence.local_file.path_policy import (
    CrossPlatformPathPolicy,
    PathPolicyViolation,
)


@pytest.fixture
def policy() -> CrossPlatformPathPolicy:
    """返回一个新的 ``CrossPlatformPathPolicy`` 实例。"""
    return CrossPlatformPathPolicy()


@pytest.fixture
def as_windows(monkeypatch: pytest.MonkeyPatch):
    """把 ``path_policy.os.name`` 伪造为 ``"nt"``，以驱动 Windows 分支。

    仅 patch ``path_policy`` 模块内对 ``os`` 的引用，避免影响 ``pathlib``
    的全局 ``os.name`` 判定（否则会导致 pytest 内部 ``WindowsPath`` 实例
    化崩溃）。
    """

    class _FakeOs:
        name = "nt"

    monkeypatch.setattr(pp_module, "os", _FakeOs)


# ── Windows 保留名拒绝（跨平台运行） ──


@pytest.mark.parametrize(
    "reserved",
    [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    ],
)
def test_reserved_names_all_upper_rejected(policy: CrossPlatformPathPolicy, reserved: str):
    """需求 4.3：所有大写形式的保留名均须被 ``check_dirname`` 拒绝。"""
    with pytest.raises(PathPolicyViolation, match="Windows 保留名"):
        policy.check_dirname(reserved)


@pytest.mark.parametrize(
    "reserved",
    ["con", "prn", "aux", "nul", "com1", "lpt9", "Con", "PrN", "CoM2"],
)
def test_reserved_names_case_insensitive_rejected(policy: CrossPlatformPathPolicy, reserved: str):
    """需求 4.3：保留名**大小写无关**；混合大小写也必被拒。"""
    with pytest.raises(PathPolicyViolation, match="Windows 保留名"):
        policy.check_dirname(reserved)


@pytest.mark.parametrize(
    "name",
    [
        "CON.txt",
        "PRN.json",
        "AUX.log",
        "NUL.dat",
        "COM1.cfg",
        "LPT9.bin",
        "con.txt",
        "nul.JSON",
    ],
)
def test_reserved_names_with_extension_rejected(policy: CrossPlatformPathPolicy, name: str):
    """需求 4.3：保留名 + 扩展名（如 ``CON.txt``）仍被拒；前缀命中即拒。"""
    with pytest.raises(PathPolicyViolation, match="Windows 保留名"):
        policy.check_dirname(name)


# ── Windows 非法字符拒绝（跨平台运行） ──


@pytest.mark.parametrize(
    "illegal_char",
    ["<", ">", ":", '"', "/", "\\", "|", "?", "*", "\x00"],
)
def test_each_windows_illegal_char_rejected(policy: CrossPlatformPathPolicy, illegal_char: str):
    """需求 4.2：逐一校验每个 Windows 非法字符均被拒。"""
    name = f"a{illegal_char}b.json"
    with pytest.raises(PathPolicyViolation, match="非法字符"):
        policy.check_dirname(name)


@pytest.mark.parametrize(
    "name",
    [
        "a:b.json",  # 盘符冒号
        "name<tag>",  # 重定向符
        'path"quoted"',  # 双引号
        "what?",  # 通配符
        "star*",  # 通配符
        "pipe|here",  # 管道符
        "has/slash",  # 正斜杠（路径分隔符）
        "has\\backslash",  # 反斜杠（Windows 路径分隔符）
        "has\x00null",  # NUL 字节
    ],
)
def test_mixed_illegal_char_names_rejected(policy: CrossPlatformPathPolicy, name: str):
    """需求 4.2：不同位置的非法字符（不只在中间）均被拒。"""
    with pytest.raises(PathPolicyViolation, match="非法字符"):
        policy.check_dirname(name)


# ── 260 字符绝对路径长度上限（Windows 分支） ──


def test_path_length_over_260_rejected_on_windows(
    policy: CrossPlatformPathPolicy, as_windows: None
):
    """需求 4.4：Windows 下长度 > 260 必须抛 ``PathPolicyViolation``。"""
    long_path = Path("C:/" + "a" * 300)
    assert len(str(long_path)) > 260
    with pytest.raises(PathPolicyViolation, match="路径过长"):
        policy.check_absolute_path_length(long_path)


def test_path_length_exactly_261_rejected_on_windows(
    policy: CrossPlatformPathPolicy, as_windows: None
):
    """需求 4.4：长度恰好 261（> 260）必须被拒。"""
    path = Path("/" + "a" * 260)
    assert len(str(path)) == 261
    with pytest.raises(PathPolicyViolation, match="路径过长"):
        policy.check_absolute_path_length(path)


def test_path_length_at_260_accepted_on_windows(policy: CrossPlatformPathPolicy, as_windows: None):
    """需求 4.4：长度 == 260 恰好不抛（严格 ``>`` 才拒绝）。"""
    path = Path("/" + "a" * 259)
    assert len(str(path)) == 260
    policy.check_absolute_path_length(path)


def test_path_length_under_260_accepted_on_windows(
    policy: CrossPlatformPathPolicy, as_windows: None
):
    """需求 4.4：短路径在 Windows 下不拦截。"""
    path = Path("C:/work/data.json")
    policy.check_absolute_path_length(path)


def test_path_length_over_260_accepted_on_posix(
    policy: CrossPlatformPathPolicy, monkeypatch: pytest.MonkeyPatch
):
    """需求 4.4：POSIX 平台**不触发**长度检查，>260 也应通过。"""

    class _FakeOs:
        name = "posix"

    monkeypatch.setattr(pp_module, "os", _FakeOs)
    long_path = Path("/tmp/" + "a" * 500)
    assert len(str(long_path)) > 260
    policy.check_absolute_path_length(long_path)


# ── 真实 Windows runner 下的额外行为断言（@skipif） ──


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="该断言仅在真实 Windows runner 下有语义意义",
)
def test_real_windows_os_name_is_nt():
    """真实 Windows runner 下 ``os.name == 'nt'``。

    本测试作为 CI 矩阵中 Windows runner 真正参与的证据；Linux CI 会 skip。
    """
    assert os.name == "nt"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="该断言仅在真实 Windows runner 下对 >260 路径触发运行期校验",
)
def test_real_windows_length_check_triggers_for_long_path(
    policy: CrossPlatformPathPolicy,
):
    """真实 Windows 下 >260 路径在 ``check_absolute_path_length`` 中被拒。

    （与 ``as_windows`` fixture 版本的差别：本用例不 patch ``os``，
    依赖真实运行平台；在 Linux CI 会被 skip。）
    """
    long_path = Path("C:/" + "a" * 300)
    with pytest.raises(PathPolicyViolation, match="路径过长"):
        policy.check_absolute_path_length(long_path)


# ── 哈希文件名天然安全（Property 7 在 Windows 维度的反向锁死） ──


@pytest.mark.parametrize(
    "session_id",
    [
        "CON",
        "PRN",
        "NUL.txt",
        "path/with/slash",
        "path\\with\\backslash",
        "a:b:c",
        "star*",
        "\x00abcd",
        "会话ID-中文",
        "a" * 1000,  # 超长输入
    ],
)
def test_hash_session_id_outputs_name_pass_check_dirname(
    policy: CrossPlatformPathPolicy, session_id: str
):
    """任意输入（含 Windows 保留名 / 非法字符 / 超长）经过 ``hash_session_id``
    后的 ``bucket`` / ``stem`` 必然通过 ``check_dirname``。

    这是 ``Session_File_Name_Scheme`` 的核心不变量（正确性属性 Property 7）。
    """
    bucket, stem = policy.hash_session_id(session_id)
    policy.check_dirname(bucket)
    policy.check_dirname(stem)
    policy.check_dirname(f"{stem}.json")


def test_hash_output_never_matches_reserved_names(
    policy: CrossPlatformPathPolicy,
):
    """``hash_session_id`` 输出（纯 16 进制）永不可能命中 Windows 保留名。

    保留名集合：CON / PRN / AUX / NUL / COM1-9 / LPT1-9。保留名含
    ``P / R / N / O / U / L / M / T`` 等非十六进制字母，而 sha256 输出
    只含 ``0-9a-f``，因此整体不可能相等。本测试作为反向锁死：遍历若干
    输入，断言输出的 ``stem`` 与保留名集合无交集。
    """
    reserved_set = (
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )
    samples = ["a", "b", "session-1", "测试", "\x00", "*", ":"]
    for s in samples:
        _, stem = policy.hash_session_id(s)
        assert stem.upper() not in reserved_set
        # 前缀也不会命中：stem 以十六进制字符开头
        assert stem[:3].upper() not in reserved_set
