"""配置前缀登记守卫测试。

``test/conftest.py`` 的全局隔离夹具 ``isolate_config_sources`` 依赖手工维护的
``_CONFIG_ENV_PREFIXES`` 元组来清理宿主/CI 环境变量。手工列表存在长期漂移风险：

- **遗漏**：新增配置类引入新 ``env_prefix`` 却忘记登记 → 该前缀的宿主环境变量
  不会被清理，隔离出现**静默漏洞**，对应配置的测试可能被环境污染而误红；
- **多余**：配置类被删除/重命名后前缀仍留在列表 → 无害但误导。

本模块通过**自动发现**全项目所有 ``PropertiesBaseSettings`` 子类的 ``env_prefix``，
断言其与 ``_CONFIG_ENV_PREFIXES`` 完全一致。任何漂移都会使本测试失败，
从而把「手工维护」降级为「一处声明 + 自动校验」，兼顾隔离夹具的零运行时开销
（夹具仍读静态元组，不做导入扫描）与列表的长期正确性。
"""

import importlib
import pkgutil

# application 层同样存在 PropertiesBaseSettings 子类（如 ServerConfig、
# RequestLoggingConfig），与 common/infrastructure 一并作为扫描根，缺一会漏登记前缀。
import application
import common
import infrastructure
from common.configuration.configuration_utils import PropertiesBaseSettings
from test.conftest import _CONFIG_ENV_PREFIXES


def _discover_config_prefixes() -> set[str]:
    """递归导入配置类所在的各顶层包，返回所有子类声明的非空 env_prefix 集合。

    Returns:
        全项目 ``PropertiesBaseSettings`` 子类使用的 ``env_prefix`` 字符串集合。
    """
    for pkg in (common, infrastructure, application):
        for module_info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            try:
                importlib.import_module(module_info.name)
            except Exception:
                # 个别模块可能因可选依赖等原因无法导入；跳过不影响前缀发现的完整性，
                # 缺失前缀会在断言处暴露，而非在此静默掩盖。
                continue

    prefixes: set[str] = set()

    def _collect(cls: type) -> None:
        for subclass in cls.__subclasses__():
            # 仅统计生产代码（src/ 下的包）定义的配置类；测试模块内为验证加载行为
            # 而临时定义的 PropertiesBaseSettings 子类会残留在 __subclasses__() 中，
            # 必须按模块名排除，否则会被误判为「遗漏登记的生产前缀」。
            module = getattr(subclass, "__module__", "") or ""
            is_test_class = module == "__main__" or module.startswith(("test.", "test_"))
            if not is_test_class:
                prefix = subclass.model_config.get("env_prefix", "")
                if prefix:
                    prefixes.add(prefix)
            _collect(subclass)

    _collect(PropertiesBaseSettings)
    return prefixes


def test_config_env_prefixes_matches_discovered() -> None:
    """conftest 手工登记的前缀必须与自动发现的前缀集合完全一致。

    失败信息会分别列出「已声明但项目中不存在」和「项目中存在但漏登记」的前缀，
    便于直接据此增删 ``_CONFIG_ENV_PREFIXES``。
    """
    discovered = _discover_config_prefixes()
    declared = set(_CONFIG_ENV_PREFIXES)

    stale = declared - discovered
    missing = discovered - declared

    assert not stale and not missing, (
        "test/conftest.py 的 _CONFIG_ENV_PREFIXES 与自动发现的配置前缀不一致，"
        "请同步更新：\n"
        f"  多余（已声明但无对应配置类，应删除）: {sorted(stale)}\n"
        f"  遗漏（存在配置类但未登记，会导致隔离漏洞，应补充）: {sorted(missing)}"
    )


def test_config_prefixes_tuple_has_no_duplicates() -> None:
    """``_CONFIG_ENV_PREFIXES`` 元组不应含重复项。"""
    assert len(_CONFIG_ENV_PREFIXES) == len(set(_CONFIG_ENV_PREFIXES))
