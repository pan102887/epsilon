"""domain/agent 运行时配置规范化领域服务。

承载 Agent 运行时配置中「委派深度上限」的规范化领域规则，为零基础设施
依赖的领域服务（Domain_Service）：无框架、无 Pydantic、无 I/O、无 logging、
无 ContextVar、无 OTel，可脱离配置框架单元测试。不变量：归一判据
（``<= 0`` 回退默认值 3）、``None`` 不改动、无法转 int 时保留原值三分支与
上提前逐一等价（Behavior_Equivalent_Refactor）。

与 ``domain/task/policy.py::DelegationDepthPolicy`` 的边界：本服务做「配置
取值的规范化/归一」（一元变换 ``object -> int``，配置装配期一次性归一），
后者做「运行期深度是否超限」的二元比较判定（``current vs max``）；语义不同、
不合并、互不修改（详见 ADR-0015）。
"""

from __future__ import annotations

DEFAULT_MAX_DELEGATION_DEPTH = 3
"""Agent 委派递归深度默认值（自 infrastructure 上提）。"""


class DelegationDepthNormalizationPolicy:
    """委派深度上限规范化领域服务。

    无字段的无状态领域服务，仅承载「委派深度上限归一」这一单一职责
    （对齐 ``srp-principle.md``）。所有判定为纯函数，不触发任何 I/O、
    不 ``raise`` 异常（保留原 validator 的吞异常语义）。
    """

    @staticmethod
    def default_max_delegation_depth() -> int:
        """返回委派深度默认值 3。"""
        return DEFAULT_MAX_DELEGATION_DEPTH

    @staticmethod
    def normalize(raw: object) -> object:
        """把配置原始值归一为有效委派深度。

        与 ``AgentRuntimeConfig._clamp_max_delegation_depth`` 现有三分支逐一等价：

        - ``raw is None``：原样返回（交回 pydantic 用字段默认值）；
        - 可转 int 且 ``int(raw) <= 0``：返回 ``DEFAULT_MAX_DELEGATION_DEPTH``（3）；
        - 转 int 抛 ``TypeError``/``ValueError``：原样返回（吞异常、保留原值）；
        - 可转 int 且 ``int(raw) > 0``：原样返回（保持）。

        Args:
            raw: 配置原始值（可能是 ``int``/``str``/``None``/非法类型）。

        Returns:
            归一后的值：命中 ``<= 0`` 分支返回 int 3，其余分支返回入参原值。
        """
        if raw is None:
            return raw
        try:
            if int(raw) <= 0:  # type: ignore[call-overload]
                return DEFAULT_MAX_DELEGATION_DEPTH
        except (TypeError, ValueError):
            return raw
        return raw
