"""评测参数配置包。

本包承载 spec-ai-evaluation 自身的纯评测参数（如样本数量、回归阈值、
滑动窗口默认值等），与业务运行时配置 `epsilon-boot/config.properties`
完全隔离，避免相互污染。

主文件为 `eval.toml`（纯数据，使用 Python 标准库 `tomllib` 解析）。
"""
