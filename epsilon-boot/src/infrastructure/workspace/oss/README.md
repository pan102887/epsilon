# OSS 后端占位目录

本目录是 `Workspace` 抽象的对象存储（OSS）后端预留位置。**本期不实现 OSS 后端**，该目录仅声明未来扩展点；当前任何 Workspace 调用方都必须走 `infrastructure.workspace.local_filesystem.LocalFilesystemWorkspace`。

## 为什么不放 `__init__.py`

本目录**刻意不放置 `__init__.py`**，以免 Python 把它识别为一个空包被 `pytest` 发现（阶段 1 仅落地目录占位，没有任何可 import 的符号）。当未来真正实现 OSS 适配器时，再补充 `__init__.py` 与对应模块。

## 扩展点与契约

未来 OSS 后端实现应当满足：

- **后端定位参数 `Backend_Location = (bucket, key)`**：OSS 后端以 `bucket + key` 作为物理定位；与本地后端的宿主绝对路径一样，**不对工具层暴露**。`WorkspacePath` 作为唯一逻辑路径出口，由 `WorkspacePolicy.resolve` 构造。
- **流式读写**：OSS 对象读取应提供按字节范围 / 行范围读取；写入应在可行时使用分片上传（Multipart Upload）以支持大对象。
- **分片上传**：大文件写入通过 OSS 分片上传 API（如 阿里云 OSS `InitiateMultipartUpload` / `UploadPart` / `CompleteMultipartUpload`），与本地后端 `tempfile + os.replace` 的原子性实现分道而行。
- **`supports_atomic_write=False` 的降级契约**：OSS 不具备跨对象的原子写语义；`WorkspaceCapabilities.supports_atomic_write` 必须如实声明为 `False`，工具层须依据 `capabilities()` 自行决定拒绝或降级，而不得对 `Workspace` 做 `isinstance(..., LocalFilesystemWorkspace)` 之类的类型分支。
- **`local_materialization=False`**：OSS 对象没有宿主文件路径，`ShellExecTool` / `PythonExecTool` 在 OSS 后端下直接拒绝执行。
- **错误翻译**：OSS SDK 异常须在后端内部翻译为 `domain.workspace.exceptions` 中定义的领域错误（`WorkspaceNotFoundError` / `WorkspaceIoError` / `WorkspaceConfinementViolation` / `WorkspaceUnsupportedOperationError`），不得让 SDK 原生异常穿透到工具层。

## 参考设计

详见 `docs/spec/workspace/design.md` 的"组件与接口"及"开放问题（已决策）"章节。
