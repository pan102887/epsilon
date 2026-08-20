"""模型 usage 合并工具。"""


def merge_usage(*usages: dict[str, int] | None) -> dict[str, int]:
    """合并多个 usage 字典。

    ``None`` 会被跳过；缺失 key 按 0 处理；所有出现过的 key 都会保留。
    usage 值必须是非负 int。
    """
    merged: dict[str, int] = {}
    for usage in usages:
        if usage is None:
            continue
        for key, value in usage.items():
            if not isinstance(value, int):
                raise ValueError(f"usage[{key!r}] 必须为 int")
            if value < 0:
                raise ValueError(f"usage[{key!r}] 必须为非负整数")
            merged[key] = merged.get(key, 0) + value
    return merged
