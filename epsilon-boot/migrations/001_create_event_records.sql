-- 事件存储数据库迁移脚本
-- 创建 event_records 和 handler_results 表，用于领域事件持久化和处理器结果追踪。

-- 创建事件记录表
CREATE TABLE IF NOT EXISTS event_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(255) NOT NULL,
    event_data TEXT NOT NULL,
    occurred_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_event_type (event_type),
    INDEX idx_occurred_at (occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 创建处理器结果记录表
CREATE TABLE IF NOT EXISTS handler_results (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_record_id INT NOT NULL,
    handler_name VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    executed_at DATETIME,
    INDEX idx_handler_event_record_id (event_record_id),
    INDEX idx_handler_handler_name (handler_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
