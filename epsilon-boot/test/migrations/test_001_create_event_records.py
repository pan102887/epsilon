"""迁移脚本验证测试。

验证 001_create_event_records.sql 迁移脚本的完整性和正确性，
确保脚本包含所有必要的表定义、字段定义、索引定义和幂等语法。
"""

from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "001_create_event_records.sql"


@pytest.fixture()
def sql_content() -> str:
    """读取迁移脚本内容并返回。"""
    return MIGRATION_PATH.read_text(encoding="utf-8")


class TestMigrationFileExists:
    """验证迁移脚本文件存在。"""

    def test_migration_file_exists(self) -> None:
        """迁移脚本文件应存在于 migrations/ 目录下。"""
        assert MIGRATION_PATH.exists(), f"迁移脚本不存在: {MIGRATION_PATH}"

    def test_migration_file_is_not_empty(self, sql_content: str) -> None:
        """迁移脚本文件不应为空。"""
        assert len(sql_content.strip()) > 0


class TestEventRecordsTable:
    """验证 event_records 表的字段定义。"""

    def test_contains_create_table_event_records(self, sql_content: str) -> None:
        assert "CREATE TABLE" in sql_content and "event_records" in sql_content

    def test_id_field(self, sql_content: str) -> None:
        assert "id INT AUTO_INCREMENT PRIMARY KEY" in sql_content

    def test_event_type_field(self, sql_content: str) -> None:
        assert "event_type VARCHAR(255) NOT NULL" in sql_content

    def test_event_data_field(self, sql_content: str) -> None:
        assert "event_data TEXT NOT NULL" in sql_content

    def test_occurred_at_field(self, sql_content: str) -> None:
        assert "occurred_at DATETIME NOT NULL" in sql_content

    def test_created_at_field(self, sql_content: str) -> None:
        assert "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql_content


class TestHandlerResultsTable:
    """验证 handler_results 表的字段定义。"""

    def test_contains_create_table_handler_results(self, sql_content: str) -> None:
        assert "CREATE TABLE" in sql_content and "handler_results" in sql_content

    def test_id_field(self, sql_content: str) -> None:
        # handler_results 表也有 id 主键
        assert "handler_results" in sql_content
        # 在 handler_results 块中查找 id 字段
        hr_block = sql_content.split("handler_results")[1]
        assert "id INT AUTO_INCREMENT PRIMARY KEY" in hr_block

    def test_event_record_id_field(self, sql_content: str) -> None:
        assert "event_record_id INT NOT NULL" in sql_content

    def test_handler_name_field(self, sql_content: str) -> None:
        assert "handler_name VARCHAR(255) NOT NULL" in sql_content

    def test_status_field(self, sql_content: str) -> None:
        assert "status VARCHAR(20) NOT NULL" in sql_content

    def test_error_message_field(self, sql_content: str) -> None:
        assert "error_message TEXT" in sql_content

    def test_executed_at_field(self, sql_content: str) -> None:
        assert "executed_at DATETIME" in sql_content


class TestIndexDefinitions:
    """验证所有索引定义。"""

    def test_event_type_index(self, sql_content: str) -> None:
        """event_records 表应包含 event_type 索引。"""
        assert "idx_event_type" in sql_content

    def test_occurred_at_index(self, sql_content: str) -> None:
        """event_records 表应包含 occurred_at 索引。"""
        assert "idx_occurred_at" in sql_content

    def test_handler_event_record_id_index(self, sql_content: str) -> None:
        """handler_results 表应包含 event_record_id 索引。"""
        assert "idx_handler_event_record_id" in sql_content

    def test_handler_handler_name_index(self, sql_content: str) -> None:
        """handler_results 表应包含 handler_name 索引。"""
        assert "idx_handler_handler_name" in sql_content


class TestIdempotency:
    """验证迁移脚本使用 IF NOT EXISTS 语法。"""

    def test_event_records_if_not_exists(self, sql_content: str) -> None:
        """event_records 表创建应使用 IF NOT EXISTS。"""
        assert "CREATE TABLE IF NOT EXISTS event_records" in sql_content

    def test_handler_results_if_not_exists(self, sql_content: str) -> None:
        """handler_results 表创建应使用 IF NOT EXISTS。"""
        assert "CREATE TABLE IF NOT EXISTS handler_results" in sql_content
