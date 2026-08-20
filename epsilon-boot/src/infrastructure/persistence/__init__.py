"""本地文件持久化基础设施包。

本包承载 ``local-file-persistence`` 特性的基础设施共享工具与适配器，
具体子包：

- ``local_file/``：跨平台路径策略、文件锁、原子写入、临时文件清理、配置类等。

严格遵循 DDD 分层：本包位于 ``infrastructure/`` 层，不被领域层导入；
对外通过 ``infrastructure/session/`` 与 ``infrastructure/health/`` 中的
Adapter 以 Port 协议的结构化子类型形式接入组合根 ``container_config.py``。
"""
