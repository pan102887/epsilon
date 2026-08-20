"""Epsilon_Home schema 版本元数据写入模块。

在本地文件后端就绪时向 Epsilon_Home 写入 meta.json，记录当前产物 schema
版本（Schema_Version），供未来产物结构不兼容变更时的迁移判定。写入幂等且
故障隔离：任何 IO 异常仅记录 warning，不中断主流程（需求 6.3、Property 7）。
属纯 infrastructure 实现细节，仅依赖标准库。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
"""当前产物 schema 版本；产物结构不兼容变更时递增。"""


def write_schema_meta(home: Path) -> None:
    """幂等写入 <home>/meta.json（含 schema_version）；已存在且版本一致时跳过。

    先确保 home 目录存在（mkdir(parents=True, exist_ok=True)），再检查已有
    meta.json 的 schema_version：若与当前版本一致则跳过重写，保证幂等；否则
    写入 {"schema_version": SCHEMA_VERSION}。任何 IO 异常仅记录 warning，不
    向调用方抛出（故障隔离，Property 7），schema 元数据缺失不影响主流程。

    Args:
        home: 目标 Epsilon_Home 顶层运行目录。
    """
    try:
        home.mkdir(parents=True, exist_ok=True)
        meta_path = home / "meta.json"
        payload = {"schema_version": SCHEMA_VERSION}
        if meta_path.exists():
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            if existing.get("schema_version") == SCHEMA_VERSION:
                return
        meta_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.warning("写入 schema meta 失败：%s", home, exc_info=True)
