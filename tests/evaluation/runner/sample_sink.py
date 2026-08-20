"""进程级评测样本收集器。

:class:`SampleSink` 由 pytest session fixture 注入到各指标评测用例，
用例通过 ``sink.append(EvalSampleResult(...))`` 回传样本；
:class:`EvalRunner` 在 ``pytest.main`` 返回后从 :class:`SampleSink`
中 ``drain`` 出全部样本并聚合。

为什么需要进程级单例：
    pytest 收集样本时 fixture 作用域最大为 ``session``；但 Runner 在
    同一进程内以函数方式调用 ``pytest.main``，若用 session fixture 直接
    持有列表，Runner 无法跨越 pytest session 读取列表。本模块提供一个
    模块级单例 :func:`get_sample_sink`，fixture 与 Runner 均通过它获取
    同一实例，确保样本不丢失。

线程安全：
    评测脚本默认单进程单线程；本实现不加锁，调用方若未来以 pytest-xdist
    并发执行，需要改为进程间消息队列或文件持久化，本期不作承诺。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.evaluation.runner.models import EvalSampleResult


@dataclass
class SampleSink:
    """线性追加 / 一次性弹出的样本收集器。

    Attributes:
        _samples: 内部样本列表；通过 :meth:`append` 追加，通过
            :meth:`drain` 清空返回，通过 :meth:`clear` 原地清空。
    """

    _samples: list[EvalSampleResult] = field(default_factory=list)

    def append(self, sample: EvalSampleResult) -> None:
        """追加一条样本。

        Args:
            sample: 评测用例产出的 :class:`EvalSampleResult`。
        """

        self._samples.append(sample)

    def drain(self) -> list[EvalSampleResult]:
        """返回当前全部样本并清空内部缓冲区。

        Returns:
            追加顺序的 :class:`EvalSampleResult` 列表；调用后 sink 为空。
        """

        samples = list(self._samples)
        self._samples.clear()
        return samples

    def clear(self) -> None:
        """丢弃全部样本，恢复到空状态。"""

        self._samples.clear()

    def __len__(self) -> int:
        """当前已追加尚未 drain 的样本数。"""

        return len(self._samples)


_GLOBAL_SINK: SampleSink | None = None


def get_sample_sink() -> SampleSink:
    """获取进程级单例 :class:`SampleSink`。

    Returns:
        进程内共享的 :class:`SampleSink` 实例；首次调用时懒加载创建。
    """

    global _GLOBAL_SINK
    if _GLOBAL_SINK is None:
        _GLOBAL_SINK = SampleSink()
    return _GLOBAL_SINK


def reset_sample_sink() -> SampleSink:
    """清空并返回进程级 :class:`SampleSink`，用于 session 级 fixture 初始化。

    Returns:
        已被清空的 :class:`SampleSink` 实例。
    """

    sink = get_sample_sink()
    sink.clear()
    return sink
