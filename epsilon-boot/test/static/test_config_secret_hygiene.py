"""配置文件敏感默认值静态检查。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_ROOT / "config.properties"


def test_config_properties_does_not_ship_known_weak_passwords() -> None:
    """主配置文件不得提交常见弱口令或示例真实密码。"""

    content = CONFIG_FILE.read_text(encoding="utf-8")
    forbidden_fragments = [
        "DB_PASSWORD=root123",
        "DB_PASSWORD=password",
        "DB_PASSWORD=admin",
        "DB_PASSWORD=123456",
        "API_KEY=sk-",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in content
