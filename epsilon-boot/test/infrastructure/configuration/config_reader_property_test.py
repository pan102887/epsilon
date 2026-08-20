"""pydantic-settings 配置属性测试。

使用 Hypothesis 进行基于属性的测试，验证配置系统在各种输入下的正确性。
重点验证：环境变量注入的一致性、类型转换的鲁棒性、多配置类并发读取的安全性。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings


class _PropTestConfig(PropertiesBaseSettings):
    """属性测试专用配置类。"""

    model_config = SettingsConfigDict(env_prefix="PROP_TEST_")

    name: str = "default"
    port: int = 8080
    debug: bool = False
    ratio: float = 1.0


class TestConcurrentReadConsistency:
    """并发读取一致性测试。

    pydantic-settings 实例创建后字段值不可变，
    多线程并发读取同一实例应始终返回一致的值。
    """

    @given(
        num_threads=st.integers(min_value=2, max_value=20),
        num_reads_per_thread=st.integers(min_value=5, max_value=50),
    )
    @settings(
        max_examples=10,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_concurrent_reads_return_consistent_values(self, num_threads, num_reads_per_thread):
        """验证多线程并发读取同一配置实例时，所有线程获得一致的值。"""
        config = _PropTestConfig()
        errors = []

        def read_fields(thread_id: int):
            """单个线程的读取操作。"""
            results = []
            try:
                for _ in range(num_reads_per_thread):
                    results.append((config.name, config.port, config.debug, config.ratio))
            except Exception as e:
                errors.append((thread_id, str(e)))
            return results

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(read_fields, i) for i in range(num_threads)]
            all_results = []
            for future in as_completed(futures):
                all_results.extend(future.result())

        assert not errors, f"并发读取时发生错误: {errors}"

        # 所有读取结果应完全一致
        expected = (config.name, config.port, config.debug, config.ratio)
        for result in all_results:
            assert result == expected, f"读取结果不一致: {result} != {expected}"

    @given(
        num_threads=st.integers(min_value=3, max_value=10),
    )
    @settings(
        max_examples=5,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_multiple_config_instances_independent(self, num_threads):
        """验证多个配置实例在并发场景下互不干扰。"""

        class _ConfigA(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="PROP_A_")
            value: str = "a"

        class _ConfigB(PropertiesBaseSettings):
            model_config = SettingsConfigDict(env_prefix="PROP_B_")
            value: str = "b"

        config_a = _ConfigA()
        config_b = _ConfigB()
        errors = []

        def read_both(thread_id: int):
            try:
                for _ in range(20):
                    assert config_a.value == "a"
                    assert config_b.value == "b"
            except AssertionError as e:
                errors.append((thread_id, str(e)))

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(read_both, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()

        assert not errors, f"并发读取多配置实例时发生错误: {errors}"
